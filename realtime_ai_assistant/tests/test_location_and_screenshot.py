r"""Checks Maria's location words ("left / center / right") and the screenshot resize.

Run from the realtime_ai_assistant folder, either way:
    venv\Scripts\python.exe tests\test_location_and_screenshot.py
    venv\Scripts\python.exe -m pytest tests                       (needs pytest installed)
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ROBOFLOW_API_KEY", "test-key-not-real")   # config refuses to load without one

from PIL import Image

from core.ai_handler import JimmyParser
from core import screenshot
from core.screenshot import resize_to_height, matches_game


def bbox(x, y):
    return {"center_x": x, "center_y": y, "width": 40, "height": 40}


def test_location_uses_thirds_of_a_1280x720_frame():
    p = JimmyParser()
    assert p._get_simple_location(bbox(100, 100)) == "left top"
    assert p._get_simple_location(bbox(640, 360)) == "center middle"
    assert p._get_simple_location(bbox(1200, 650)) == "right bottom"


def test_location_of_your_screenshot_examples():
    p = JimmyParser()
    # In the chicken screenshots (scaled to 1280x720): F1 (638, 349), F2 (865, 253), F3 (718, 163)
    assert p._get_simple_location(bbox(638, 349)) == "center middle"     # old code said "right bottom"
    assert p._get_simple_location(bbox(865, 253)) == "right middle"      # 67.6% across: just inside the right third
    assert p._get_simple_location(bbox(718, 163)) == "center top"


def test_location_follows_the_frame_size_in_parse():
    p = JimmyParser()
    raw = {"detections": [{"class": "zombie", "confidence": .9, "bbox": {**bbox(300, 300), "x1": 0, "y1": 0, "x2": 0, "y2": 0}}],
           "metadata": {"image_width": 1680, "image_height": 720}}     # an ultrawide monitor
    text = p.parse(raw)
    assert p.frame_size == (1680, 720)
    assert "left" in text, text                                         # x = 300 of 1680 is the left third


def test_screenshot_keeps_the_monitor_shape():
    assert resize_to_height(Image.new("RGB", (1920, 1080))).size == (1280, 720)    # 16:9 unchanged
    assert resize_to_height(Image.new("RGB", (2560, 1080))).size == (1707, 720)    # ultrawide not squashed
    assert resize_to_height(Image.new("RGB", (1920, 1200))).size == (1152, 720)    # 16:10 not stretched


# ---------------------------------------------------------------- only the Minecraft window is ever captured

def test_only_the_game_itself_counts_as_minecraft():
    assert matches_game("Minecraft 1.21.1", r"C:\Program Files\Javain\javaw.exe")
    assert matches_game("Minecraft* 1.20.4 - Singleplayer", "javaw.exe")
    assert matches_game("Minecraft 23w14a", "java.exe")                            # a snapshot
    assert not matches_game("Minecraft", r"C:\Program Files\WindowsApps\Minecraft.Windows.exe")   # Bedrock is not supported
    assert not matches_game("Minecraft server", "javaw.exe")                       # the server console is not the game
    assert not matches_game("Minecraft Forge Mod Tool", "javaw.exe")               # nor is a Java tool with the name in it
    assert not matches_game("Minecraft Skins for Kids - Google Chrome", r"C:\Program Files\Google\Chrome\chrome.exe")
    assert not matches_game("Minecraft Launcher", r"C:\Program Files\Minecraft Launcher\MinecraftLauncher.exe")
    assert not matches_game("YouTube - Chrome", "javaw.exe")                      # a Java app that is not the game
    assert not matches_game("", "")


def test_nothing_is_captured_when_the_game_cannot_be_captured():
    real = screenshot._capture_game
    screenshot._capture_game = lambda: None                        # game closed, minimised or unreadable
    try:
        assert screenshot.capture_screenshot() is None
    finally:
        screenshot._capture_game = real


def test_the_game_picture_is_scaled_to_720_tall_keeping_its_shape():
    from PIL import Image
    real = screenshot._capture_game
    screenshot._capture_game = lambda: Image.new("RGB", (400, 200), (10, 120, 30))    # a 2:1 game window
    try:
        image = screenshot.capture_screenshot()
    finally:
        screenshot._capture_game = real
    assert image.size == (1440, 720) and image.getpixel((700, 300)) == (10, 120, 30)


def test_a_black_picture_counts_as_unreadable():
    from PIL import Image
    assert screenshot.is_blank(Image.new("RGB", (64, 64), (0, 0, 0)))
    assert screenshot.is_blank(Image.new("RGB", (64, 64), (1, 1, 1)))
    dark_cave = Image.new("RGB", (64, 64), (0, 0, 0))
    dark_cave.putpixel((5, 5), (90, 90, 90))                                           # one torch is enough
    assert not screenshot.is_blank(dark_cave)


def test_a_dark_scene_is_not_mistaken_for_an_unreadable_window():
    from PIL import Image
    pitch_dark = Image.new("RGB", (64, 64), (4, 4, 5))                                 # HUD hidden, no light at all
    assert not screenshot.is_blank(pitch_dark)
    night = Image.new("RGB", (64, 64), (12, 14, 20))
    night.putpixel((32, 32), (255, 255, 255))                                          # crosshair
    assert not screenshot.is_blank(night)


def test_a_frozen_game_window_cannot_hang_the_capture():
    import threading
    import time
    release = threading.Event()
    calls = []

    def stuck(hwnd):
        calls.append(hwnd)
        release.wait(5)
        return Image.new("RGB", (100, 100), (50, 50, 50))

    real_read, real_timeout = screenshot._window_picture, screenshot.PICTURE_TIMEOUT
    screenshot._window_picture, screenshot.PICTURE_TIMEOUT = stuck, 0.2
    try:
        started = time.time()
        assert screenshot._read_with_timeout(1) is screenshot._STUCK
        assert time.time() - started < 1.5                                             # gave up after about 0.2 s, not 5
        assert screenshot._read_with_timeout(1) is screenshot._STUCK                   # still waiting on the first request...
        assert calls == [1]                                                            # ...and no second request was started
        release.set()
        screenshot._picture_job.join(2)
        image = screenshot._read_with_timeout(1)                                       # the game answers again: back to normal
        assert image is not screenshot._STUCK and image is not None
    finally:
        release.set()
        screenshot._window_picture, screenshot.PICTURE_TIMEOUT = real_read, real_timeout
        screenshot._picture_job = None


def test_the_screen_fallback_notices_another_window_lying_over_the_game():
    import subprocess
    import tkinter as tk
    from ctypes import wintypes
    if sys.platform != "win32":
        return
    user32, _, _ = screenshot._win_api()
    user32.GetParent.argtypes, user32.GetParent.restype = [wintypes.HANDLE], wintypes.HANDLE
    game = tk.Tk()
    game.title("Minecraft 1.20.1")
    game.geometry("320x220+80+80")
    game.attributes("-topmost", True)
    game.update()
    hwnd = user32.GetParent(game.winfo_id())
    cover = None
    try:
        assert not screenshot._anything_on_top_of(hwnd)                                # nothing over it yet
        cover = subprocess.Popen([sys.executable, "-I", "-c",
                                  "import tkinter as tk,time;r=tk.Tk();r.title('Notification');r.geometry('200x120+120+120');"
                                  "r.attributes('-topmost',True);r.update();print('ready',flush=True);time.sleep(8)"],
                                 stdout=subprocess.PIPE, text=True)
        assert cover.stdout.readline().strip() == "ready"
        game.update()
        assert screenshot._anything_on_top_of(hwnd)                                    # another program's window is over it
        cover.kill()
        cover.wait(5)
        cover = None
        time.sleep(0.3)
        game.update()
        assert not screenshot._anything_on_top_of(hwnd)                                # gone again
    finally:
        if cover:
            cover.kill()
        game.destroy()


def test_capture_can_be_switched_back_to_the_whole_monitor():
    real_flag, real_game = screenshot.CAPTURE_ONLY_GAME, screenshot._capture_game
    screenshot.CAPTURE_ONLY_GAME = False
    screenshot._capture_game = lambda: None                                            # would block, but is not asked
    try:
        image = screenshot.capture_screenshot()
    finally:
        screenshot.CAPTURE_ONLY_GAME, screenshot._capture_game = real_flag, real_game
    assert image is not None and image.height == 720


def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{'ALL OK' if not failed else 'SOME CHECKS FAILED'} ({len(tests) - failed} of {len(tests)} tests passed)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
