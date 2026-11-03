r"""Checks the FrameBuffer trend math against a simulated 3D scene.

A sheep is placed in the world, projected onto the screen like the game camera would
(pinhole model, FOV 100 on the vertical axis like Minecraft's slider, 1280x720), and the
resulting detections are fed to FrameBuffer. The answer is compared with the real distance.

Run from the realtime_ai_assistant folder, either way:
    venv\Scripts\python.exe tests\test_frame_buffer_trends.py     (prints ok / FAIL per test)
    venv\Scripts\python.exe -m pytest tests                       (needs pytest installed)
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

import core.frame_buffer as fb
from core.frame_buffer import FrameBuffer
from core.mob_classes import normalize_class

# Fixed settings, so a saved config_overrides.json on this machine cannot change the results
SETTINGS = {"FRAME_BUFFER_SIZE_CHANGE_PERCENT": 15, "FRAME_BUFFER_CONFIDENCE_THRESHOLD": 0.1}
fb.get_setting = lambda key: SETTINGS[key]

W, H = 1280, 720
PLAYER_FOV = 100
F = (H / 2) / math.tan(math.radians(PLAYER_FOV / 2))   # focal length in pixels
CAM_H, SHEEP_H, SHEEP_W = 1.62, 1.3, 0.9   # blocks
META = {"image_width": W, "image_height": H}
IMG = Image.new("RGB", (W, H))
EMPTY = {"detections": [], "metadata": META}


def project(x, z, yaw_deg, pitch_deg=0):
    """World point (x right, z forward, in blocks) seen by a camera turned yaw_deg to the right
    and tilted pitch_deg downwards. The sheep's centre is CAM_H - SHEEP_H / 2 below the eye."""
    y = -(CAM_H - SHEEP_H / 2)
    t, p = math.radians(yaw_deg), math.radians(pitch_deg)
    xc = x * math.cos(t) - z * math.sin(t)
    z1 = x * math.sin(t) + z * math.cos(t)
    yc = y * math.cos(p) + z1 * math.sin(p)
    zc = -y * math.sin(p) + z1 * math.cos(p)
    return {"center_x": int(W / 2 + F * xc / zc), "center_y": int(H / 2 - F * yc / zc),
            "width": int(F * SHEEP_W / zc), "height": int(F * SHEEP_H / zc)}


def run(p1, p3, yaw1=0, yaw3=0, confidence=0.9, cls="sheep", pitch1=0, pitch3=0):
    buf = FrameBuffer(3)
    for k in (0, 0.5, 1):
        x = p1[0] + (p3[0] - p1[0]) * k
        z = p1[1] + (p3[1] - p1[1]) * k
        yaw = yaw1 + (yaw3 - yaw1) * k
        pitch = pitch1 + (pitch3 - pitch1) * k
        buf.add_frame(IMG, {"detections": [{"class": cls, "confidence": confidence,
                                            "bbox": project(x, z, yaw, pitch)}], "metadata": META})
    return buf.analyze_trends()


def frames(*dets):
    buf = FrameBuffer(3)
    for d in dets:
        buf.add_frame(IMG, d)
    return buf


def zombie_frame(confidence=0.95):
    return {"detections": [{"class": "zombie", "confidence": confidence,
                            "bbox": {"center_x": 640, "center_y": 400, "width": 150, "height": 300}}], "metadata": META}


# ---------------------------------------------------------------- distance trend

def test_walks_away():
    t = run((2, 5), (3, 14))                       # 5.4 -> 14.3 blocks
    assert t["position_trend"] == "moving_away"
    assert abs(t["size_change_percent"] - 62) <= 2, t["size_change_percent"]
    assert t["threat_level"] == "low"              # a sheep is passive: capped at low


def test_walks_toward_you():
    t = run((3, 14), (2, 5))
    assert t["position_trend"] == "approaching"
    assert abs(t["size_change_percent"] - 166) <= 4, t["size_change_percent"]


def test_head_on_charge():
    assert run((0, 14), (0, 5))["position_trend"] == "approaching"


def test_camera_turn_only_is_stable():
    assert run((3.5, 10), (3.5, 10), yaw1=-20, yaw3=0)["position_trend"] == "stable"


def test_walks_across_view_is_stable():
    assert run((-5, 10), (5, 10))["position_trend"] == "stable"


def test_low_confidence_still_approaching():
    assert run((3, 14), (2, 5), confidence=0.4)["position_trend"] == "approaching"


def test_pitch_only_is_stable():
    for label, p, a, b in (("down 0-60", (1.5, 4), 0, 60), ("up 60-0", (1.5, 4), 60, 0),
                           ("down 0-85", (1.5, 4), 0, 85), ("far sheep 0-60", (1.0, 6), 0, 60)):
        assert run(p, p, pitch1=a, pitch3=b)["position_trend"] == "stable", label


def test_yaw_and_pitch_together_is_stable():
    assert run((3.5, 10), (3.5, 10), yaw1=-20, yaw3=0, pitch1=20, pitch3=-10)["position_trend"] == "stable"


def test_real_approach_while_looking_down():
    assert run((3, 14), (2, 5), pitch1=30, pitch3=30)["position_trend"] == "approaching"


def test_single_sighting_is_unknown():
    assert frames(EMPTY, EMPTY, zombie_frame()).analyze_trends()["position_trend"] == "unknown"


def test_missing_box_size_does_not_crash():
    bare = {"detections": [{"class": "x", "confidence": .9, "bbox": {"center_x": 1, "center_y": 1}}], "metadata": META}
    assert frames(bare, bare, bare).analyze_trends()["position_trend"] == "unknown"


def test_size_change_setting_is_live():
    def verdict(pct):
        box = lambda s: {"detections": [{"class": "cow", "confidence": .9,
                                         "bbox": {"center_x": 640, "center_y": 360, "width": s, "height": s}}],
                         "metadata": META}
        old = SETTINGS["FRAME_BUFFER_SIZE_CHANGE_PERCENT"]
        SETTINGS["FRAME_BUFFER_SIZE_CHANGE_PERCENT"] = pct
        try:
            return frames(box(100), box(115), box(130)).analyze_trends()["position_trend"]   # looks 30% bigger
        finally:
            SETTINGS["FRAME_BUFFER_SIZE_CHANGE_PERCENT"] = old
    assert verdict(15) == "approaching"
    assert verdict(40) == "stable"


# ---------------------------------------------------------------- threat level

def test_threat_zombie_vs_sheep_vs_others():
    assert run((3, 14), (2, 5), cls="zombie")["threat_level"] == "critical"
    assert run((3, 14), (2, 5), cls="zombie")["threat_class"] == "zombie"
    assert run((3, 14), (2, 5), cls="sheep")["threat_level"] == "low"        # passive: never above low
    assert run((2, 5), (3, 14), cls="zombie")["threat_level"] == "high"      # 0.9 confidence alone
    assert run((3, 14), (2, 5), cls="health_bar")["threat_level"] == "none"
    assert run((3, 14), (2, 5), cls="diamond_ore")["threat_level"] == "none"
    assert run((3, 14), (2, 5), cls="lava")["threat_level"] == "critical"


def test_threat_depends_on_confidence_and_approach():
    assert run((3, 14), (2, 5), cls="zombie", confidence=0.6)["threat_level"] == "high"    # approach >= 25%
    assert run((3.5, 10), (3.5, 10), yaw1=-20, yaw3=0, cls="zombie", confidence=0.6)["threat_level"] == "low"


def test_sheep_plus_approaching_zombie():
    def both(zomb_z):
        return {"detections": [
            {"class": "sheep", "confidence": .9, "bbox": project(-3, 6, 0)},
            {"class": "zombie", "confidence": .9, "bbox": project(3, zomb_z, 0)}], "metadata": META}
    t = frames(both(14), both(9.5), both(5)).analyze_trends()
    assert t["threat_level"] == "critical" and t["threat_class"] == "zombie"


def test_new_arrival_is_not_diluted():
    t = frames(EMPTY, EMPTY, zombie_frame()).analyze_trends()
    assert t["threat_level"] == "high" and t["threat_class"] == "zombie"
    assert frames(EMPTY, zombie_frame(), zombie_frame()).analyze_trends()["threat_level"] == "high"
    assert frames(zombie_frame(), zombie_frame(), EMPTY).analyze_trends()["threat_level"] == "none"   # it left
    assert frames(zombie_frame(), EMPTY, zombie_frame()).analyze_trends()["threat_level"] == "high"   # flicker


def test_class_name_spellings():
    for name in ("Cave Spider", "cave-spider", "Zombie Villager", "Creaking", "ZOMBIE"):
        assert run((3, 14), (2, 5), cls=name)["threat_level"] == "critical", name
    assert run((3, 14), (2, 5), cls="Pillager")["threat_level"] == "none"   # unknown class: documented default
    assert [normalize_class(n) for n in (" Cow ", "Cave Spider", "zombie-villager", "SKELETON")] == \
           ["cow", "cave_spider", "zombie_villager", "skeleton"]


def test_float_edge_at_exactly_080():
    buf = FrameBuffer(3)
    assert buf._calculate_threat_level([0.8, 0.8, 0.8], 0) == "medium"   # was 'high' (0.8000000000000002)


# ---------------------------------------------------------------- result shape

def test_result_has_the_same_keys_with_few_frames():
    full = set(frames(EMPTY, zombie_frame(), zombie_frame()).analyze_trends())
    assert set(FrameBuffer(3).analyze_trends()) == full
    assert set(frames(zombie_frame()).analyze_trends()) == full


def test_stored_frames_keep_the_screenshot_size():
    buf = frames(zombie_frame(), zombie_frame())
    assert buf.get_latest_frame().size == (W, H)
    assert [f.size for f in buf.get_frames()] == [(W, H), (W, H)]
    assert buf.get_frame_at(0).size == (W, H) and buf.get_frame_at(5) is None
    wide = Image.new("RGB", (1680, 720))                       # an ultrawide screenshot
    b2 = FrameBuffer(3)
    b2.add_frame(wide, {"detections": [], "metadata": {"image_width": 1680, "image_height": 720}})
    assert b2.get_latest_frame().size == (1680, 720) and list(b2.frame_sizes) == [(1680, 720)]


def test_buffer_holds_the_last_three_frames_only():
    buf = FrameBuffer(3)
    for _ in range(5):
        buf.add_frame(IMG, zombie_frame())
    assert buf.size() == 3 and buf.is_full() and len(buf.get_frames()) == 3
    buf.clear()
    assert buf.is_empty() and buf.get_latest_frame() is None


def test_summary_text():
    text = frames(zombie_frame(), zombie_frame(), zombie_frame()).get_summary()
    assert "Distance Trend" in text and "Threat Level" in text


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
