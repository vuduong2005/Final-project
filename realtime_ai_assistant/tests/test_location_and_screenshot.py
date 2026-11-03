r"""Checks Maria's location words ("left / center / right") and the screenshot resize.

Run from the realtime_ai_assistant folder, either way:
    venv\Scripts\python.exe tests\test_location_and_screenshot.py
    venv\Scripts\python.exe -m pytest tests                       (needs pytest installed)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ROBOFLOW_API_KEY", "test-key-not-real")   # config refuses to load without one

from PIL import Image

from core.ai_handler import JimmyParser
from core.screenshot import resize_to_height


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
