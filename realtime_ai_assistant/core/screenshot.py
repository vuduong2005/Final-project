"""Taking the picture Maria looks at.

PRIVACY: with CAPTURE_ONLY_GAME on (the default) the only thing ever captured is the Minecraft
window's own picture. Windows is asked for that one window's content, so it works while another
window (a browser, Maria's chat box) is in front of the game, and nothing of those other windows
is in the picture. Nothing is captured when the game is closed or minimised.
"""
import ctypes
import os
import re
import sys
import threading
from ctypes import wintypes
from datetime import datetime

import mss
from PIL import Image

from config import CAPTURE_ONLY_GAME, GAME_PROCESS_NAMES, GAME_WINDOW_TITLE
from utils.logger import get_logger

logger = get_logger()

BLANK_MAX_BRIGHTNESS = 2      # a picture whose brightest pixel is below this (0 or 1 of 255) is "all black"
PICTURE_TIMEOUT = 3.0         # seconds PrintWindow may take before the game counts as not answering
_STUCK = object()             # what _read_with_timeout gives back when the game did not answer in time
_picture_job = None           # the worker still waiting on a stuck game window, if any

_last_state = None      # what the last capture attempt found, so a change is logged once and not every cycle


def _log_state(state: str, message: str):
    global _last_state
    if state != _last_state:
        _last_state = state
        logger.info(message)


def matches_game(title: str, exe_name: str) -> bool:
    """Is this window the Minecraft game? The program must be the game's own (Java), and the title must
    look like the game's: "Minecraft 1.20.1", "Minecraft* 1.21 - Singleplayer". A browser tab called
    "Minecraft skins", the launcher, or a server console window titled "Minecraft server" never counts."""
    name = GAME_WINDOW_TITLE.lower()
    wanted = re.compile(r"^" + re.escape(name) + r"\*?\s+\d", re.IGNORECASE)         # name, optional *, then a version number
    return (bool(wanted.match(title.strip()))
            and os.path.basename(exe_name).lower() in {n.lower() for n in GAME_PROCESS_NAMES})


def resize_to_height(image, height=720):
    """Scale an image to `height` px tall, keeping its shape.

    Stretching a non-16:9 screen to 1280x720 would distort every box (the trend math
    assumes an undistorted frame). A 16:9 monitor still gives exactly 1280x720.
    """
    width = max(1, round(image.width * height / image.height))
    return image.resize((width, height))


def is_blank(image) -> bool:
    """True for a picture that is black all over (what Windows hands back when it cannot read a window).
    A real scene is never that black: even a pitch-dark cave keeps its crosshair and hotbar (the darkest
    frames in real play have a brightest pixel of 200), so a dark frame is NOT treated as unreadable."""
    return image.convert("L").getextrema()[1] < BLANK_MAX_BRIGHTNESS


# ============================================================================
# THE GAME WINDOW (Windows only)
# ============================================================================

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


_api = None


def _win_api():
    """user32, gdi32 and kernel32 with the argument types spelled out (handles are 64-bit)."""
    global _api
    if _api is None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        H, P = wintypes.HANDLE, ctypes.c_void_p
        user32.GetForegroundWindow.restype = H
        user32.IsWindowVisible.argtypes = user32.IsIconic.argtypes = [H]
        user32.GetWindowTextW.argtypes = [H, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
        user32.GetClientRect.argtypes = [H, ctypes.POINTER(wintypes.RECT)]
        user32.ClientToScreen.argtypes = [H, ctypes.POINTER(wintypes.POINT)]
        user32.GetDC.argtypes, user32.GetDC.restype = [H], H
        user32.ReleaseDC.argtypes = [H, H]
        user32.PrintWindow.argtypes, user32.PrintWindow.restype = [H, H, wintypes.UINT], wintypes.BOOL
        gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [H], H
        gdi32.CreateDIBSection.argtypes = [H, P, wintypes.UINT, ctypes.POINTER(P), H, wintypes.DWORD]
        gdi32.CreateDIBSection.restype = H
        gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [H, H], H
        gdi32.DeleteObject.argtypes = gdi32.DeleteDC.argtypes = [H]
        kernel32.OpenProcess.argtypes, kernel32.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], H
        kernel32.QueryFullProcessImageNameW.argtypes = [H, wintypes.DWORD, ctypes.c_wchar_p,
                                                        ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [H]
        user32.GetWindowRect.argtypes = [H, ctypes.POINTER(wintypes.RECT)]
        user32.GetCurrentProcessId = kernel32.GetCurrentProcessId
        _api = (user32, gdi32, kernel32)
    return _api


def _program_of(hwnd) -> str:
    """The .exe that owns a window ('' when Windows will not say)."""
    user32, _, kernel32 = _win_api()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = kernel32.OpenProcess(0x1000, False, pid.value)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return ""
    try:
        name, size = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
        return name.value if kernel32.QueryFullProcessImageNameW(process, 0, name, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(process)


def game_is_in_front() -> bool:
    """Is the Minecraft window the one the player is using right now? (The push-to-talk key only works then, so a
    letter typed in a browser or in a chat app is never mistaken for it.)"""
    try:
        user32, _, _ = _win_api()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        return GAME_WINDOW_TITLE.lower() in title.value.lower() and matches_game(title.value, _program_of(hwnd))
    except Exception:
        return False


def _find_game_window():
    """The Minecraft window, wherever it is in the stack of windows, or None when the game is
    closed or minimised. Windows gives every open window's title and program; no folder or
    Task Manager lookup is needed."""
    user32, _, _ = _win_api()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            if GAME_WINDOW_TITLE.lower() in title.value.lower() and matches_game(title.value, _program_of(hwnd)):
                found.append(hwnd)
                return False                                          # stop looking
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else None


_dwm = None


def _is_cloaked(hwnd) -> bool:
    """Windows keeps some windows "visible" but hides them (apps on other virtual desktops)."""
    global _dwm
    try:
        if _dwm is None:
            _dwm = ctypes.WinDLL("dwmapi")
            _dwm.DwmGetWindowAttribute.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        cloaked = wintypes.DWORD(0)
        return _dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4) == 0 and cloaked.value != 0
    except (OSError, AttributeError):
        return False


def _anything_on_top_of(hwnd):
    """The window of another program that is in front of the game over the part of the screen it
    fills (0 when there is none).
    (Our own chat box and overlay do not count: they are kept out of screen captures.)
    Windows lists windows front to back, so everything before the game is in front of it."""
    user32, _, _ = _win_api()
    area = _screen_area_of(hwnd)
    left, top = area["left"], area["top"]
    right, bottom = left + area["width"], top + area["height"]
    mine = user32.GetCurrentProcessId()
    covered = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.LPARAM)
    def each(other, _):
        if other == hwnd:
            return False                                                # reached the game: nothing else is in front
        if not user32.IsWindowVisible(other) or user32.IsIconic(other) or _is_cloaked(other):
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(other, ctypes.byref(owner))
        if owner.value == mine:
            return True
        rect = wintypes.RECT()
        if user32.GetWindowRect(other, ctypes.byref(rect)) and rect.right > rect.left and rect.bottom > rect.top:
            if rect.left < right and rect.right > left and rect.top < bottom and rect.bottom > top:
                covered.append(other)
                return False
        return True

    user32.EnumWindows(each, 0)
    return covered[0] if covered else 0


def _read_with_timeout(hwnd):
    """_window_picture(), but the vision thread never waits for ever: PrintWindow asks the game's own
    window thread for the picture, so a frozen, loading or paused game would block it for good.
    Returns the picture, None (unreadable) or _STUCK (the game did not answer in PICTURE_TIMEOUT s)."""
    global _picture_job
    if _picture_job is not None and _picture_job.is_alive():
        return _STUCK                                                   # still waiting on the last request: do not pile up
    result = {}

    def work():
        try:
            result["image"] = _window_picture(hwnd)
        except Exception as e:
            logger.debug(f"Reading the game window failed: {e}")

    worker = threading.Thread(target=work, name="window-picture", daemon=True)
    worker.start()
    worker.join(PICTURE_TIMEOUT)
    if worker.is_alive():
        _picture_job = worker
        return _STUCK
    _picture_job = None
    return result.get("image")


def _window_picture(hwnd):
    """That one window's own picture, asked from Windows directly: other windows lying on top of
    it are not in it. Returns None when Windows cannot provide it."""
    user32, _, _ = _win_api()
    # Measure and read the window in ITS OWN units. A program that leaves scaling to Windows
    # (old Java versions do) draws at a smaller size than the screen shows; asking in screen
    # pixels would give a picture that is only filled in its top-left part.
    own_units = None
    try:
        user32.GetWindowDpiAwarenessContext.argtypes = [wintypes.HANDLE]
        user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
        own_units = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(user32.GetWindowDpiAwarenessContext(hwnd)))
    except AttributeError:
        pass                                                          # older Windows: no per-window scaling
    try:
        return _read_window(hwnd)
    finally:
        if own_units:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(own_units))


def _read_window(hwnd):
    user32, gdi32, _ = _win_api()
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right, rect.bottom
    if width < 50 or height < 50:
        return None
    header = _BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    header.biWidth, header.biHeight = width, -height                  # negative: top row first
    header.biPlanes, header.biBitCount = 1, 32
    window_dc = user32.GetDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(memory_dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    try:
        if not bitmap or not bits:
            return None
        previous = gdi32.SelectObject(memory_dc, bitmap)
        ok = user32.PrintWindow(hwnd, memory_dc, 0x1 | 0x2)           # client area only | full (GPU-drawn) content
        gdi32.SelectObject(memory_dc, previous)
        if not ok:
            return None
        data = ctypes.string_at(bits, width * height * 4)
        return Image.frombuffer("RGB", (width, height), data, "raw", "BGRX", 0, 1)
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def _screen_area_of(hwnd):
    """Where the window's picture is on the screen, as an mss region."""
    user32, _, _ = _win_api()
    rect, origin = wintypes.RECT(), wintypes.POINT(0, 0)
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return {"left": origin.x, "top": origin.y, "width": rect.right, "height": rect.bottom}


def _capture_game():
    """A picture of the Minecraft window only, or None when there is nothing safe to capture."""
    if sys.platform != "win32":
        return None
    user32, _, _ = _win_api()
    # Sizes and positions must be in real screen pixels, whatever scaling the app started with
    previous = None
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-2))      # system DPI aware
    except AttributeError:
        pass                                                                       # older Windows: nothing to switch
    try:
        hwnd = _find_game_window()
        if hwnd is None:
            _log_state("no-game", "🔒 Minecraft is not open or is minimised (its window title must be 'Minecraft <version>') - not capturing anything")
            return None
        image = _read_with_timeout(hwnd)
        if image is _STUCK:
            _log_state("stuck", "⏳ Minecraft is not answering (frozen or loading) - skipping the picture for now")
            return None
        if image is not None and not is_blank(image):
            _log_state("window", "🎮 Capturing only the Minecraft window (works even when it is behind another window)")
            return image
        # Windows could not hand over the window's picture (some fullscreen modes). The screen
        # itself may only be read where the game is, only while the game is in front, and only when
        # no other program's window (a chat overlay, a notification) lies over the game's area.
        in_front = user32.GetForegroundWindow() == hwnd
        cover = _anything_on_top_of(hwnd) if in_front else 0
        if cover:
            # log only the program's name, never another window's title (it may be private)
            _log_state("covered-by", "🔒 Another program's window ("
                       + (os.path.basename(_program_of(cover)) or "unknown") + ") is over the game - not grabbing the screen")
        if in_front and not cover:
            _log_state("front-only", "🎮 Capturing Minecraft from the screen (only while it is the window in front)")
            with mss.mss() as sct:
                shot = sct.grab(_screen_area_of(hwnd))
                return Image.frombytes("RGB", (shot.width, shot.height), shot.rgb, "raw", "RGB")
        _log_state("covered", "🔒 Minecraft is behind or under another window and cannot be read safely - not capturing anything")
        return None
    finally:
        if previous is not None:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


# ============================================================================
# PUBLIC
# ============================================================================

def capture_screenshot(save=False):
    """
    Returns the picture Maria should look at as a PIL Image scaled to 720 px tall, or None.

    With CAPTURE_ONLY_GAME (the default) that is the Minecraft window only; None means the game
    is closed, minimised or unreadable, and nothing was captured. With it off, the whole primary
    monitor is captured as before.

    Thread-safe: everything is created inside the call (the vision loop runs it in its own thread).
    """
    try:
        if CAPTURE_ONLY_GAME:
            screenshot_pil = _capture_game()
            if screenshot_pil is None:
                return None
        else:
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])                      # the primary monitor
                screenshot_pil = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb, "raw", "RGB")

        screenshot_pil = resize_to_height(screenshot_pil)

        if save:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_pil.save(f"screenshot_{timestamp}.png")

        return screenshot_pil

    except Exception as e:
        logger.error(f"ERROR: Failed to capture screenshot: {e}")
        return None
