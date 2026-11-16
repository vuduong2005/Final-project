"""Keep Maria's windows from taking keyboard focus away from the game.

Clicking an ordinary window makes it the active window. Minecraft then loses focus and opens
its pause menu (or, in exclusive fullscreen, minimises). The windows set up here do not become
active when clicked or shown, so buttons, sliders and scrolling work while the game keeps focus.

Typing is the one thing that needs focus. A field registered with typing_field() takes focus
when it is clicked and hands it back to the game on Enter or Escape.

Windows only. On other systems these fall back to the ordinary Tk show / hide.
"""
import ctypes
import sys
import tkinter as tk
from ctypes import wintypes

from utils.logger import get_logger

logger = get_logger()

_GA_ROOT = 2
_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_SW_HIDE, _SW_SHOWNOACTIVATE = 0, 4
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE = 0x1, 0x2, 0x10
_WDA_EXCLUDEFROMCAPTURE = 0x11
_HWND_TOPMOST = -1

_user32 = None
if sys.platform == "win32":
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        H = wintypes.HANDLE
        _user32.GetAncestor.argtypes, _user32.GetAncestor.restype = [H, wintypes.UINT], H
        _user32.GetWindowLongW.argtypes, _user32.GetWindowLongW.restype = [H, ctypes.c_int], wintypes.LONG
        _user32.SetWindowLongW.argtypes, _user32.SetWindowLongW.restype = [H, ctypes.c_int, wintypes.LONG], wintypes.LONG
        _user32.ShowWindow.argtypes, _user32.ShowWindow.restype = [H, ctypes.c_int], wintypes.BOOL
        _user32.IsWindowVisible.argtypes, _user32.IsWindowVisible.restype = [H], wintypes.BOOL
        _user32.SetWindowPos.argtypes = [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        _user32.SetWindowPos.restype = wintypes.BOOL
        _user32.SetWindowDisplayAffinity.argtypes, _user32.SetWindowDisplayAffinity.restype = [H, wintypes.DWORD], wintypes.BOOL
        _user32.GetForegroundWindow.argtypes, _user32.GetForegroundWindow.restype = [], H
        _user32.SetForegroundWindow.argtypes, _user32.SetForegroundWindow.restype = [H], wintypes.BOOL
    except Exception as e:                       # very unlikely; fall back to ordinary windows
        logger.warning(f"Focus-free windows are not available: {e}")
        _user32 = None

AVAILABLE = _user32 is not None


def handle(window: tk.Misc):
    """The real Windows handle of a Tk window (looked up each time: Tk may rebuild it)."""
    return _user32.GetAncestor(window.winfo_id(), _GA_ROOT)


def _never_activate(window: tk.Misc):
    hwnd = handle(window)
    style = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    if not style & _WS_EX_NOACTIVATE:
        _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_NOACTIVATE)
    return hwnd


def start_hidden(window: tk.Misc):
    """Call once when the window is built: it becomes focus-free and starts out hidden."""
    if not AVAILABLE:
        window.withdraw()
        return
    window.update()                              # Tk has to create the real window before it can be changed
    hwnd = _never_activate(window)
    # Keep this window out of screenshots too (Maria must never "see" her own chat box or panels)
    if not _user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE):
        logger.warning("Could not hide a Maria window from screen capture (needs Windows 10 version 2004 or newer).")
    _user32.ShowWindow(hwnd, _SW_HIDE)


def is_shown(window: tk.Misc) -> bool:
    if not AVAILABLE:
        return window.state() == "normal"
    return bool(_user32.IsWindowVisible(handle(window)))


def show(window: tk.Misc):
    """Show the window on top of the game without taking focus from it."""
    if not AVAILABLE:
        window.deiconify()
        window.lift()
        return
    hwnd = _never_activate(window)
    _user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
    _user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)


def hide(window: tk.Misc):
    if not AVAILABLE:
        window.withdraw()
        return
    _user32.ShowWindow(handle(window), _SW_HIDE)


def toggle(window: tk.Misc) -> bool:
    """Show the window if hidden, hide it if shown. Returns True when it is now shown."""
    if is_shown(window):
        hide(window)
        return False
    show(window)
    return True


# The window that had focus before the player started typing (normally the game)
_focus_before_typing = None


def typing_field(field: tk.Entry):
    """Let the player type in this field: a click takes keyboard focus, and Enter or Escape
    gives it back to whatever had it before (normally the game)."""
    if not AVAILABLE:
        return

    def take(event=None):
        global _focus_before_typing
        own = handle(field.winfo_toplevel())
        current = _user32.GetForegroundWindow()
        if current != own:
            _focus_before_typing = current
            _user32.SetForegroundWindow(own)
        field.focus_force()

    def give_back(event=None):
        global _focus_before_typing
        previous, _focus_before_typing = _focus_before_typing, None
        if previous and previous != handle(field.winfo_toplevel()):
            # after_idle: let the field's own Enter handler (sending the message) run first
            field.after_idle(lambda: _user32.SetForegroundWindow(previous))

    field.bind("<Button-1>", take, add="+")
    field.bind("<Return>", give_back, add="+")
    field.bind("<Escape>", give_back, add="+")
