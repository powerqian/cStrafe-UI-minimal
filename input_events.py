import threading
import time
import sys
import ctypes
from ctypes import wintypes
from typing import Optional, Set, Callable

from classifier import MovementClassifier, ShotClassification

try:
    from movement_keys import FORWARD, BACKWARD, LEFT, RIGHT
except Exception:
    FORWARD, BACKWARD, LEFT, RIGHT = 'E', 'D', 'S', 'F'

# --- Windows Native Hooks Setup ---

if sys.platform == 'win32':
    WH_KEYBOARD_LL = 13
    WH_MOUSE_LL = 14
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_QUIT = 0x0012
    VK_F6 = 0x75
    VK_F8 = 0x77
    VK_OEM_PLUS = 0xBB
    VK_OEM_MINUS = 0xBD

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wintypes.POINT),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    LowLevelKeyboardProc = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    LowLevelMouseProc = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

class InputListener:
    def __init__(self, overlay: "Overlay") -> None:
        self.overlay = overlay
        try:
            forward = str(FORWARD)
            backward = str(BACKWARD)
            left = str(LEFT)
            right = str(RIGHT)
        except Exception:
            forward, backward, left, right = 'W', 'S', 'A', 'D'
        
        self._forward = (forward[0] if forward else 'W').upper()
        self._backward = (backward[0] if backward else 'S').upper()
        self._left = (left[0] if left else 'A').upper()
        self._right = (right[0] if right else 'D').upper()
        self._movement_keys = {self._forward, self._backward, self._left, self._right}
        
        # Map characters to virtual key codes for easier lookup in the hook
        self._char_to_vk = {c: ord(c) for c in self._movement_keys if len(c) == 1}

        try:
            self.classifier = MovementClassifier(
                vertical_keys=(self._forward, self._backward), 
                horizontal_keys=(self._left, self._right)
            )
        except Exception:
            self.classifier = MovementClassifier()

        self._lock = threading.Lock()
        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        
        # Windows Hook Handles
        self._kb_hook = None
        self._ms_hook = None
        # Keep references to callbacks to prevent GC
        self._kb_callback_ref = None
        self._ms_callback_ref = None

    def start(self) -> None:
        if sys.platform != 'win32':
            print(f"WARNING: Native global hooks are only implemented for Windows. Current platform: {sys.platform}")
            return
        
        if not self._is_running:
            self._is_running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._is_running = False
        if sys.platform == 'win32' and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        
        self._thread_id = kernel32.GetCurrentThreadId()
        self._kb_callback_ref = LowLevelKeyboardProc(self._keyboard_handler)
        self._ms_callback_ref = LowLevelMouseProc(self._mouse_handler)
        
        h_module = kernel32.GetModuleHandleW(None)
        self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_callback_ref, h_module, 0)
        self._ms_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_callback_ref, h_module, 0)
        
        if not self._kb_hook or not self._ms_hook:
            print("Failed to set native Windows hooks.")
            return

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
            
        if self._kb_hook:
            user32.UnhookWindowsHookEx(self._kb_hook)
        if self._ms_hook:
            user32.UnhookWindowsHookEx(self._ms_hook)

    def _keyboard_handler(self, nCode: int, wParam: int, lParam: int) -> int:
        if nCode == 0:
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode
            timestamp = float(kb.time)
            is_pressed = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_released = wParam in (WM_KEYUP, WM_SYSKEYUP)
            
            if is_pressed:
                if vk == VK_F6:
                    self.overlay.toggle_visibility()
                elif vk == VK_F8:
                    self.stop()
                    self.overlay.terminate()
                elif vk == VK_OEM_PLUS:
                    self.overlay.increase_size()
                elif vk == VK_OEM_MINUS:
                    self.overlay.decrease_size()
                else:
                    char = self._vk_to_char(vk)
                    if char in self._movement_keys:
                        with self._lock:
                            self.classifier.on_press(char, timestamp)
            elif is_released:
                char = self._vk_to_char(vk)
                if char in self._movement_keys:
                    with self._lock:
                        self.classifier.on_release(char, timestamp)
                        
        return ctypes.windll.user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)

    def _mouse_handler(self, nCode: int, wParam: int, lParam: int) -> int:
        if nCode == 0:
            ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            timestamp = float(ms.time)
            if wParam == WM_LBUTTONDOWN:
                with self._lock:
                    base_result = self.classifier.classify_shot(timestamp)
                final_result = self._build_classification(base_result, timestamp)
                self.overlay.update_result(final_result)
        return ctypes.windll.user32.CallNextHookEx(self._ms_hook, nCode, wParam, lParam)

    def _vk_to_char(self, vk: int) -> str:
        # Simple mapping for A-Z
        if 0x41 <= vk <= 0x5A:
            return chr(vk)
        # Mapping for Arrow keys
        vk_map = {
            0x25: "LEFT",
            0x26: "UP",
            0x27: "RIGHT",
            0x28: "DOWN",
        }
        return vk_map.get(vk, "")

    def _build_classification(self, base: ShotClassification, shot_time: float) -> ShotClassification:
        if base.label == "Overlap":
            return ShotClassification(label="Overlap", overlap_time=base.overlap_time)
        if base.label == "Counter‑strafe":
            cs_time = base.cs_time
            shot_delay = base.shot_delay
            if cs_time is not None and shot_delay is not None:
                if shot_delay > 230.0 or (cs_time > 215.0 and shot_delay > 215.0):
                    return ShotClassification(label="Bad", cs_time=cs_time, shot_delay=shot_delay)
                return ShotClassification(label="Counter‑strafe", cs_time=cs_time, shot_delay=shot_delay)
            return ShotClassification(label="Bad")
        return ShotClassification(label="Bad")
