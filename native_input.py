import platform
import threading
import ctypes
from ctypes import wintypes

# --- Platform Check ---
IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # --- Constants ---
    WH_KEYBOARD_LL = 13
    WH_MOUSE_LL = 14
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONDOWN = 0x0204
    WM_RBUTTONUP = 0x0205
    WM_MBUTTONDOWN = 0x0207
    WM_MBUTTONUP = 0x0208
    WM_XBUTTONDOWN = 0x020B
    WM_XBUTTONUP = 0x020C

    # --- Structs ---
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

    # --- Callback Prototypes ---
    LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
        ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )
    LowLevelMouseProc = ctypes.WINFUNCTYPE(
        ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )

    class NativeInputListener:
        def __init__(self, on_event=None):
            """
            on_event: callback function(key_code_or_button, timestamp, is_pressed)
            """
            self.on_event = on_event
            self._thread = None
            self._kb_hook = None
            self._ms_hook = None
            self._thread_id = None
            self._stop_event = threading.Event()

        def _run(self):
            # We must set the hooks in the same thread that runs the message loop
            self._thread_id = kernel32.GetCurrentThreadId()
            
            self._kb_callback = LowLevelKeyboardProc(self._keyboard_handler)
            self._ms_callback = LowLevelMouseProc(self._mouse_handler)

            h_module = kernel32.GetModuleHandleW(None)
            
            self._kb_hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._kb_callback, h_module, 0
            )
            self._ms_hook = user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._ms_callback, h_module, 0
            )

            if not self._kb_hook or not self._ms_hook:
                print("Failed to set hooks")
                return

            msg = wintypes.MSG()
            while not self._stop_event.is_set():
                # GetMessageW will block until a message arrives.
                # To stop cleanly, we can use PostThreadMessage to send a WM_QUIT.
                res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if res <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            # Cleanup
            if self._kb_hook:
                user32.UnhookWindowsHookEx(self._kb_hook)
            if self._ms_hook:
                user32.UnhookWindowsHookEx(self._ms_hook)

        def _keyboard_handler(self, nCode, wParam, lParam):
            if nCode == 0:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                is_pressed = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                timestamp = kb.time
                self._process_event(kb.vkCode, timestamp, is_pressed)
            return user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)

        def _mouse_handler(self, nCode, wParam, lParam):
            if nCode == 0:
                ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                is_pressed = wParam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN)
                is_released = wParam in (WM_LBUTTONUP, WM_RBUTTONUP, WM_MBUTTONUP, WM_XBUTTONUP)
                
                if is_pressed or is_released:
                    button = "unknown"
                    if wParam in (WM_LBUTTONDOWN, WM_LBUTTONUP): button = "left"
                    elif wParam in (WM_RBUTTONDOWN, WM_RBUTTONUP): button = "right"
                    elif wParam in (WM_MBUTTONDOWN, WM_MBUTTONUP): button = "middle"
                    elif wParam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                        xbutton = (ms.mouseData >> 16) & 0xFFFF
                        button = f"xbutton_{xbutton}"
                    
                    timestamp = ms.time
                    self._process_event(button, timestamp, is_pressed)
            return user32.CallNextHookEx(self._ms_hook, nCode, wParam, lParam)

        def _process_event(self, key_or_button, timestamp, is_pressed):
            """Internal processing logic that calls the user callback."""
            if self.on_event:
                self.on_event(key_or_button, timestamp, is_pressed)

        def start(self):
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        def stop(self):
            self._stop_event.set()
            if self._thread_id:
                # Post WM_QUIT to the thread's message queue to break GetMessageW
                user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0) # 0x0012 is WM_QUIT
            if self._thread:
                self._thread.join(timeout=1.0)

else:
    # --- macOS / Other Placeholder ---
    class NativeInputListener:
        def __init__(self, on_event=None):
            """
            on_event: callback function(key_code_or_button, timestamp, is_pressed)
            """
            self.on_event = on_event
            print(f"WARNING: NativeInputListener is not supported on {platform.system()}.")

        def start(self):
            pass

        def stop(self):
            pass
