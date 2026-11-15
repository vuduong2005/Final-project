r"""Maria on top of the game: a small pixel-art portrait in a corner of the screen.

While she is silent she waits, dimmed, and blinks. While she talks she smiles with her eyes
closed and her mouth follows her voice. For a danger warning she looks alarmed, shakes a
little and gets a red outline. The pictures come from ui/maria_sprite.py.

The window is click-through (the mouse goes straight to Minecraft), never takes keyboard
focus, and is hidden from screen capture, so it never shows up in the screenshots that go
to the detectors. Windows only; on other systems it simply does nothing.

Try it without the game (run from the realtime_ai_assistant folder):
    venv\Scripts\python.exe ui\speaking_overlay.py
"""
import ctypes
import os
import random
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import speech_state
from ui import maria_sprite
from utils.logger import get_logger

logger = get_logger()

# ---- Edit these to taste ----------------------------------------------------
OVERLAY_ENABLED = True
OVERLAY_SIZE = 128                  # rough size in pixels; snapped so every block is a whole number of pixels
OVERLAY_POSITION = "bottom-right"   # top-left, top-right, bottom-left or bottom-right
OVERLAY_MARGIN = 24                 # gap to the screen edge
SHOW_WHEN_IDLE = True               # False: she only appears while speaking
IDLE_OPACITY = 0.6                  # how solid she is while silent (1.0 = fully solid)
HIDE_FROM_CAPTURE = True            # keep her out of the screenshots sent to the detectors
# ------------------------------------------------------------------------------


def compose(sprite: Image.Image, block: int, mood: str = "idle", level: float = 0.0,
            speaking: bool = False, shake: int = 0) -> Image.Image:
    """One finished window picture: the sprite frame, with its glow while speaking, enlarged so
    each block is `block` pixels, lifted a little with the voice and nudged sideways by `shake`."""
    if speaking:
        sprite = maria_sprite.with_glow(sprite, mood, level)
    size = sprite.width * block
    big = sprite.resize((size, size), Image.NEAREST)
    lift = round(level * block * 0.75) if speaking else 0
    if not lift and not shake:
        return big
    moved = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    moved.paste(big, (shake, -lift))
    return moved


# ============================================================================
# THE WINDOW (Windows only: a layered window with real per-pixel transparency)
# ============================================================================

_GA_ROOT = 2
_GWL_EXSTYLE = -20
_WS_EX_TOPMOST, _WS_EX_TRANSPARENT, _WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
_WS_EX_LAYERED, _WS_EX_NOACTIVATE = 0x80000, 0x08000000
_WDA_EXCLUDEFROMCAPTURE = 0x11
_ULW_ALPHA = 2
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE = 0x1, 0x2, 0x10
_HWND_TOPMOST = -1


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


def _win_api():
    """user32 and gdi32 with the argument types spelled out (handles are 64-bit)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    H, P = wintypes.HANDLE, ctypes.c_void_p
    user32.GetAncestor.argtypes, user32.GetAncestor.restype = [H, wintypes.UINT], H
    user32.GetWindowLongW.argtypes, user32.GetWindowLongW.restype = [H, ctypes.c_int], wintypes.LONG
    user32.SetWindowLongW.argtypes, user32.SetWindowLongW.restype = [H, ctypes.c_int, wintypes.LONG], wintypes.LONG
    user32.SetWindowDisplayAffinity.argtypes, user32.SetWindowDisplayAffinity.restype = [H, wintypes.DWORD], wintypes.BOOL
    user32.SetWindowPos.argtypes = [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetSystemMetrics.argtypes, user32.GetSystemMetrics.restype = [ctypes.c_int], ctypes.c_int
    user32.GetDC.argtypes, user32.GetDC.restype = [H], H
    user32.ReleaseDC.argtypes, user32.ReleaseDC.restype = [H, H], ctypes.c_int
    user32.UpdateLayeredWindow.argtypes = [H, H, P, P, H, P, wintypes.DWORD, P, wintypes.DWORD]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [H], H
    gdi32.CreateDIBSection.argtypes = [H, P, wintypes.UINT, ctypes.POINTER(P), H, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = H
    gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [H, H], H
    gdi32.DeleteObject.argtypes, gdi32.DeleteObject.restype = [H], wintypes.BOOL
    gdi32.DeleteDC.argtypes, gdi32.DeleteDC.restype = [H], wintypes.BOOL
    return user32, gdi32


def _to_premultiplied_bgra(image: Image.Image) -> bytes:
    """Windows wants BGRA with the colours already multiplied by the alpha."""
    red, green, blue, alpha = image.convert("RGBa").split()
    return Image.merge("RGBA", (blue, green, red, alpha)).tobytes()


class SpeakingOverlay:
    """Create once, after the main Tk window exists. It then animates itself."""

    def __init__(self, root: tk.Misc, hide_from_capture: bool = HIDE_FROM_CAPTURE):
        self.active = False
        self._failures = 0                              # frames that could not be drawn (see _tick)
        if not OVERLAY_ENABLED or sys.platform != "win32":
            return
        try:
            self._user32, self._gdi32 = _win_api()
            self.block = max(1, round(OVERLAY_SIZE / maria_sprite.CELLS))
            self.size = self.block * maria_sprite.CELLS
            self.window = tk.Toplevel(root)
            self.window.overrideredirect(True)
            self.window.attributes("-topmost", True)
            self.window.geometry(f"{self.size}x{self.size}+0+0")   # _place() moves it to its corner
            self.window.update_idletasks()

            self.hwnd = self._user32.GetAncestor(self.window.winfo_id(), _GA_ROOT)
            style = self._user32.GetWindowLongW(self.hwnd, _GWL_EXSTYLE)
            style |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW | _WS_EX_TOPMOST
            self._user32.SetWindowLongW(self.hwnd, _GWL_EXSTYLE, style)
            if hide_from_capture and not self._user32.SetWindowDisplayAffinity(self.hwnd, _WDA_EXCLUDEFROMCAPTURE):
                logger.warning("Speaking overlay could not be hidden from screen capture "
                               "(needs Windows 10 version 2004 or newer); it may appear in screenshots.")

            self._frames = None                     # filled in by a helper thread (takes a second or two)
            threading.Thread(target=self._draw_frames, daemon=True, name="OverlayFrames").start()
            self._shown = 0.0                       # the smoothed mouth opening
            self._opacity = IDLE_OPACITY if SHOW_WHEN_IDLE else 0.0
            self._blink_until = 0.0
            self._next_blink = time.monotonic() + random.uniform(2.5, 5.0)
            self._last_frame = None
            self._last_place = 0.0
            self._placed = None
            self.active = True
            self._place()
            self._tick()
            logger.info("🙂 Speaking overlay ready")
        except Exception as e:
            logger.error(f"Speaking overlay could not start: {e}", exc_info=True)

    def _draw_frames(self):
        try:
            self._frames = maria_sprite.build_frames()
        except Exception as e:
            logger.error(f"Speaking overlay could not draw Maria: {e}", exc_info=True)

    # ------------------------------------------------------------------
    def _place(self):
        """Put the window in its corner, on top of everything.

        Repeated every half second for two reasons. A fullscreen game can cover it. And the
        screen-capture library switches the whole app to real screen pixels on its first
        screenshot (on a display scaled to 150%, the screen goes from 1707 to 2560 wide for us),
        which would leave her small and away from her corner unless she is placed again.
        """
        user32 = self._user32
        try:
            scale = user32.GetDpiForSystem() / 96      # 1.0 while Windows still scales us itself
        except AttributeError:                          # Windows older than 10 (1607)
            scale = 1.0
        block = max(1, round(OVERLAY_SIZE * scale / maria_sprite.CELLS))   # whole pixels per block: crisp art
        size, margin = block * maria_sprite.CELLS, round(OVERLAY_MARGIN * scale)
        screen_w, screen_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        x = margin if "left" in OVERLAY_POSITION else screen_w - size - margin
        y = margin if "top" in OVERLAY_POSITION else screen_h - size - margin
        if (x, y, size) != self._placed:
            self._placed = (x, y, size)
            self.block, self.size = block, size
            self._last_frame = None                     # draw again at the new size
            user32.SetWindowPos(self.hwnd, _HWND_TOPMOST, x, y, size, size, _SWP_NOACTIVATE)
        else:
            user32.SetWindowPos(self.hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)

    def _tick(self):
        if not self.active:
            return
        try:
            now = time.monotonic()
            if now - self._last_place > 0.5:
                self._last_place = now
                self._place()
            level = speech_state.level()
            speaking = level is not None
            mood = (speech_state.mood() or "talk") if speaking else "idle"
            target = level or 0.0
            # open fast, close a little slower: reads as speech instead of flicker
            self._shown += (target - self._shown) * (0.6 if target > self._shown else 0.35)
            want = 1.0 if speaking else (IDLE_OPACITY if SHOW_WHEN_IDLE else 0.0)
            self._opacity += (want - self._opacity) * 0.3

            if now >= self._next_blink:
                self._blink_until = now + 0.13
                self._next_blink = now + random.uniform(2.5, 5.0)
            blink = now < self._blink_until
            shake = random.choice((-1, 0, 1)) * self.block if mood == "alarm" else 0

            if self._frames is not None:
                key = maria_sprite.frame_key(mood, self._shown, blink)
                frame = (key, round(self._shown * 8) if speaking else 0, shake, self.block,
                         round(self._opacity * 40))
                if frame != self._last_frame:
                    self._last_frame = frame
                    self._push(compose(self._frames[key], self.block, mood, self._shown, speaking, shake),
                               self._opacity)

            moving = speaking or self._shown > 0.02 or abs(want - self._opacity) > 0.02 or blink
            self.window.after(33 if moving or self._frames is None else 120, self._tick)
        except tk.TclError:
            self.active = False                     # the app is closing
        except Exception as e:
            # One bad frame must not end the overlay for the rest of the session: log it (the first few
            # times, then now and then) and try again shortly.
            self._failures += 1
            if self._failures <= 3 or self._failures % 100 == 0:
                logger.error(f"Speaking overlay hiccup #{self._failures}: {e}", exc_info=True)
            try:
                self.window.after(500, self._tick)
            except tk.TclError:
                self.active = False

    def _push(self, image: Image.Image, opacity: float):
        """Hand one RGBA frame to Windows as the window's whole content."""
        user32, gdi32 = self._user32, self._gdi32
        width, height = image.size
        data = _to_premultiplied_bgra(image)

        header = _BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        header.biWidth, header.biHeight = width, -height          # negative: top row first
        header.biPlanes, header.biBitCount = 1, 32

        screen_dc = user32.GetDC(None)
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(memory_dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        try:
            if not bitmap or not bits:
                raise ctypes.WinError(ctypes.get_last_error())
            ctypes.memmove(bits, data, len(data))
            previous = gdi32.SelectObject(memory_dc, bitmap)
            size = wintypes.SIZE(width, height)
            origin = wintypes.POINT(0, 0)
            blend = _BLENDFUNCTION(0, 0, max(0, min(255, round(opacity * 255))), 1)
            shown = user32.UpdateLayeredWindow(self.hwnd, screen_dc, None, ctypes.byref(size), memory_dc,
                                               ctypes.byref(origin), 0, ctypes.byref(blend), _ULW_ALPHA)
            gdi32.SelectObject(memory_dc, previous)
            if not shown:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if bitmap:
                gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(None, screen_dc)


# ============================================================================
# DEMO: python ui\speaking_overlay.py
# She talks for 3 s, rests for 2 s, gives a danger warning for 3 s, rests, and repeats.
# ============================================================================
if __name__ == "__main__":
    import math

    root = tk.Tk()
    root.title("Overlay demo - close this window to stop")
    root.geometry("380x60+40+40")
    tk.Label(root, text="Look at the bottom-right corner of the screen.").pack(pady=18)
    SpeakingOverlay(root)

    def fake_speech():
        while True:
            for mood in ("talk", "alarm"):
                wave_shape = [abs(math.sin(i * 0.45)) * random.uniform(0.3, 1.0) for i in range(90)]
                speech_state.begin(wave_shape, mood=mood)
                time.sleep(3)
                speech_state.end()
                time.sleep(2)

    threading.Thread(target=fake_speech, daemon=True).start()
    root.mainloop()
