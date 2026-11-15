r"""Checks Maria's on-screen portrait: the loudness read from a voice file, the speaking state
and mood over time, the drawn frames (mouth opens with loudness, a different face for danger),
the mood passed along with each spoken line, and the focus-free chat / status windows.
The overlay window itself needs a real screen, so it is tried by hand with:
    venv\Scripts\python.exe ui\speaking_overlay.py

Run from the realtime_ai_assistant folder, either way:
    venv\Scripts\python.exe tests\test_speaking_overlay.py
    venv\Scripts\python.exe -m pytest tests                       (needs pytest installed)
"""
import math
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ROBOFLOW_API_KEY", "test-key-not-real")   # config refuses to load without one

from core import speech_state
from ui import maria_sprite as sprite
from ui.speaking_overlay import _to_premultiplied_bgra, compose

RATE = 22050


def write_wav(samples, channels=1, width=2):
    path = os.path.join(tempfile.mkdtemp(), "voice.wav")
    with wave.open(path, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(RATE)
        if width == 2:
            wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        else:
            wav.writeframes(bytes(samples))
    return path


def tone(seconds, amplitude):
    return [int(amplitude * math.sin(i * 0.3)) for i in range(int(RATE * seconds))]


def count(image, colour):
    return sum(1 for pixel in image.get_flattened_data() if pixel[:3] == colour[:3] and pixel[3] > 0)


# ---------------------------------------------------------------- loudness from the voice file

def test_envelope_follows_the_loudness():
    envelope = speech_state.envelope_from_wav(write_wav(tone(0.5, 20000) + [0] * (RATE // 2) + tone(0.5, 5000)))
    assert 44 <= len(envelope) <= 47                       # 1.5 s at 30 values per second
    loud, silent, soft = envelope[5], envelope[22], envelope[38]
    assert loud > 0.95 and silent == 0.0 and 0.2 < soft < loud
    assert all(0.0 <= value <= 1.0 for value in envelope)


def test_envelope_of_silence_and_bad_files():
    assert set(speech_state.envelope_from_wav(write_wav([0] * RATE))) == {0.0}
    assert speech_state.envelope_from_wav("no_such_file.wav") == []
    assert speech_state.envelope_from_wav(write_wav([128] * 1000, width=1)) == []     # not 16-bit


def test_stereo_is_measured_in_time_not_in_samples():
    mono = speech_state.envelope_from_wav(write_wav(tone(1.0, 9000)))
    stereo = speech_state.envelope_from_wav(write_wav([s for s in tone(1.0, 9000) for _ in (0, 1)], channels=2))
    assert len(stereo) == len(mono)


# ---------------------------------------------------------------- speaking state

def test_level_is_none_while_silent_and_tracks_the_line():
    speech_state.end()
    assert speech_state.level() is None
    speech_state.begin([0.1, 0.9, 0.4], step=0.1, now=100.0)
    assert speech_state.level(now=100.05) == 0.1
    assert speech_state.level(now=100.15) == 0.9
    assert speech_state.level(now=100.25) == 0.4
    assert speech_state.level(now=100.90) == 0.0           # past the end, still speaking until end()
    speech_state.end()
    assert speech_state.level(now=100.95) is None


def test_unknown_loudness_still_moves_the_mouth():
    speech_state.begin([], now=50.0)
    values = [speech_state.level(now=50.0 + i * 0.05) for i in range(20)]
    speech_state.end()
    assert all(0.0 <= v <= 1.0 for v in values) and max(values) - min(values) > 0.5


def test_mood_travels_with_the_line():
    speech_state.end()
    assert speech_state.mood() is None
    speech_state.begin([0.5])
    assert speech_state.mood() == "talk"                   # the default
    speech_state.begin([0.5], mood="alarm")
    assert speech_state.mood() == "alarm"
    speech_state.end()
    assert speech_state.mood() is None


# ---------------------------------------------------------------- the drawn portrait

FRAMES = sprite.build_frames()


def mouth(image):
    return count(image, sprite.MOUTH_IN) + count(image, sprite.TONGUE)


def eye_white(image):
    """White pixels in the band of the picture where her eyes are (the white top is far below it)."""
    band = image.crop((0, 24, sprite.CELLS, 37))
    return count(band, sprite.WHITE) + count(band, sprite.TOP)


def test_every_frame_is_drawn_and_is_the_right_shape():
    assert len(FRAMES) == 2 + sprite.TALK_STEPS + sprite.ALARM_STEPS
    for key, image in FRAMES.items():
        assert image.size == (sprite.CELLS, sprite.CELLS) and image.mode == "RGBA", key
        assert image.getpixel((0, 0))[3] == 0, key                         # empty corner above her head
        assert set(image.getchannel("A").get_flattened_data()) <= {0, 255}, key     # crisp edges, no half-clear pixels
        assert count(image, sprite.CARDIGAN) > 100 and count(image, sprite.HAIR) > 300, key


def test_talking_mouth_opens_with_loudness():
    sizes = [mouth(FRAMES[("talk", i)]) for i in range(sprite.TALK_STEPS)]
    assert sizes[0] == 0                                   # a closed smile
    assert sizes == sorted(sizes) and sizes[-1] > sizes[1] > 0


def test_the_three_moods_look_different():
    idle, blink = FRAMES[("idle", False)], FRAMES[("idle", True)]
    talk, alarm = FRAMES[("talk", 3)], FRAMES[("alarm", 1)]
    assert count(idle, sprite.IRIS) > 0 and count(blink, sprite.IRIS) == 0          # the blink closes her eyes
    assert count(talk, sprite.IRIS) == 0 and eye_white(talk) == 0                   # happy closed eyes
    assert eye_white(alarm) > eye_white(idle) + 20                                  # huge white eyes
    assert mouth(alarm) > mouth(FRAMES[("talk", sprite.TALK_STEPS - 1)])            # the biggest mouth of all
    assert count(talk, sprite.BLUSH) > count(idle, sprite.BLUSH)


def test_picking_a_frame():
    assert sprite.frame_key("idle", 0.9, blink=True) == ("idle", True)
    assert sprite.frame_key("talk", 0.0) == ("talk", 0)
    assert sprite.frame_key("talk", 1.0) == ("talk", sprite.TALK_STEPS - 1)
    assert sprite.frame_key("alarm", 0.5) == ("alarm", 1)
    assert sprite.frame_key("talk", 7.0) == ("talk", sprite.TALK_STEPS - 1)          # out of range is safe
    assert sprite.frame_key("alarm", -1.0) == ("alarm", 0)
    assert all(sprite.frame_key(m, l, b) in FRAMES for m in ("idle", "talk", "alarm")
               for l in (0.0, 0.33, 0.5, 0.99) for b in (False, True))


def test_glow_is_warm_for_talking_and_red_for_danger():
    frame = FRAMES[("talk", 2)]
    assert count(frame, sprite.GLOW) == 0 and count(frame, sprite.GLOW_ALARM) == 0
    talk = sprite.with_glow(frame, "talk", 0.5)
    alarm = sprite.with_glow(FRAMES[("alarm", 1)], "alarm", 0.5)
    assert count(talk, sprite.GLOW) > 50 and count(talk, sprite.GLOW_ALARM) == 0
    assert count(alarm, sprite.GLOW_ALARM) > count(talk, sprite.GLOW)                # the danger ring is thicker


def test_window_picture_is_enlarged_in_whole_blocks():
    frame = FRAMES[("idle", False)]
    still = compose(frame, block=3)
    assert still.size == (sprite.CELLS * 3, sprite.CELLS * 3)
    assert count(still, sprite.GLOW) == 0
    assert still.getpixel((96, 90)) == still.getpixel((98, 92)) == frame.getpixel((32, 30))     # one block = 3x3 pixels
    speaking = compose(FRAMES[("talk", 4)], block=3, mood="talk", level=0.8, speaking=True)
    assert speaking.size == still.size and count(speaking, sprite.GLOW) > 0
    shaken = compose(FRAMES[("alarm", 2)], block=3, mood="alarm", level=1.0, speaking=True, shake=3)
    assert shaken.size == still.size and count(shaken, sprite.GLOW_ALARM) > 0


def test_frame_bytes_are_bgra_with_colour_scaled_by_alpha():
    from PIL import Image
    data = _to_premultiplied_bgra(Image.new("RGBA", (1, 1), (200, 100, 50, 128)))
    blue, green, red, alpha = data
    assert alpha == 128 and (abs(red - 100), abs(green - 50), abs(blue - 25)) <= (1, 1, 1)


# ---------------------------------------------------------------- the mood goes with each spoken line

def test_speech_queue_carries_the_mood():
    import core.tts as tts
    spoken = []
    real_speak, real_muted = tts.speak, tts.get_setting
    tts.get_setting = lambda key: False if key == "TTS_MUTED" else real_muted(key)
    tts.speak = lambda text, mood="talk", captured_at=None: spoken.append((text, mood))
    try:
        tts.speak_in_thread("Creeper behind you!", mood="alarm")
        tts.speak_in_thread("Nice, gold over there.")
        tts._speech_queue.put("an old-style plain string")        # still accepted
        tts._speech_queue.join()
    finally:
        tts.speak, tts.get_setting = real_speak, real_muted
    assert spoken == [("Creeper behind you!", "alarm"), ("Nice, gold over there.", "talk"),
                      ("an old-style plain string", "talk")]


# ---------------------------------------------------------------- windows that leave the game's focus alone

def test_windows_show_and_hide_without_taking_focus():
    import tkinter as tk
    from ui import win_focus
    root = tk.Tk()
    try:
        win_focus.start_hidden(root)
        assert not win_focus.is_shown(root)
        assert win_focus.toggle(root) is True and win_focus.is_shown(root)
        if win_focus.AVAILABLE:
            style = win_focus._user32.GetWindowLongW(win_focus.handle(root), -20)
            assert style & 0x08000000                                      # WS_EX_NOACTIVATE
            assert win_focus._user32.GetForegroundWindow() != win_focus.handle(root)
        assert win_focus.toggle(root) is False and not win_focus.is_shown(root)
        entry = tk.Entry(root)
        win_focus.typing_field(entry)                                      # must not raise
    finally:
        root.destroy()


def test_one_failed_frame_does_not_stop_the_overlay_for_good():
    import time
    import tkinter as tk
    if sys.platform != "win32":
        return
    from ui import speaking_overlay as so
    root = tk.Tk()
    root.withdraw()
    overlay = so.SpeakingOverlay(root, hide_from_capture=False)
    try:
        assert overlay.active
        def pump(seconds):
            end = time.time() + seconds
            while time.time() < end:
                root.update()
                time.sleep(0.03)
        pump(2.5)                                                    # the sprite frames are built in a helper thread
        assert overlay._frames is not None
        real_push = overlay._push
        calls = []

        def broken(image, opacity):
            calls.append(1)
            raise OSError("Windows said no")

        overlay._push = broken
        overlay._last_frame = None                                   # force a redraw
        pump(1.5)
        assert calls and overlay._failures >= 1
        assert overlay.active                                        # it keeps trying instead of stopping for good
        overlay._push = real_push
        overlay._last_frame = None
        before = overlay._failures
        pump(1.0)
        assert overlay._failures == before and overlay.active        # and works again once Windows does
    finally:
        overlay.active = False
        root.destroy()


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
