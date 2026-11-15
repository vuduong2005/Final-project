r"""Checks the three voice styles: the setting itself, what each style does to the speech
(speed, pitch/energy, loudness), the danger version of each, and the Status window's switch.
Uses the real Piper voice, but plays no sound and writes no settings file.

Run from the realtime_ai_assistant folder, either way:
    venv\Scripts\python.exe tests\test_voice_styles.py
    venv\Scripts\python.exe -m pytest tests                       (needs pytest installed)
"""
import os
import sys
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ROBOFLOW_API_KEY", "test-key-not-real")   # config refuses to load without one

from core import settings, speech_state
import core.tts as tts

# The player's own saved choices (config_overrides.json) must not change what these tests see:
# start from the defaults, in memory only, and never write the file.
settings._schedule_save = lambda: None
for _key in ("TTS_VOICE", "TTS_MODEL", "TTS_SPEED", "TTS_VOLUME"):
    settings.reset(_key)


class Voice:
    """Switch the voice style for a test without touching config_overrides.json."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.save_off = mock.patch.object(settings, "_schedule_save", lambda: None)
        self.save_off.start()
        self.before = settings.get("TTS_VOICE")
        settings.update("TTS_VOICE", self.name)

    def __exit__(self, *exc):
        settings.update("TTS_VOICE", self.before)
        self.save_off.stop()


def spoken(text, mood):
    """What actually gets played for a line: (seconds of sound, loudest sample, sample rate)."""
    played = {}

    def fake_play(path, flags):
        with wave.open(path, "rb") as wav:
            frames = wav.readframes(wav.getnframes())
            played["seconds"] = wav.getnframes() / wav.getframerate()
            played["rate"] = wav.getframerate()
        import struct
        samples = struct.unpack(f"<{len(frames) // 2}h", frames)
        played["peak"] = max(max(samples), -min(samples))

    with mock.patch.object(tts.winsound, "PlaySound", fake_play):
        tts.speak(text, mood)
    return played


# ---------------------------------------------------------------- the setting

def test_voice_setting_accepts_only_the_three_styles():
    assert settings.get("TTS_VOICE") == "now"                              # the default
    with Voice("livelier"):
        assert settings.get("TTS_VOICE") == "livelier"
    for bad in ("robot", "", None, 2):
        try:
            settings.update("TTS_VOICE", bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not be accepted")
    assert settings.get("TTS_VOICE") == "now"


def test_each_style_has_a_calm_and_a_danger_version():
    for name, styles in tts.VOICE_STYLES.items():
        assert set(styles) == {"calm", "alarm"}, name
        for style in styles.values():
            assert set(style) == {"speed", "noise", "noise_w", "rate", "volume"}, name
    with Voice("bright"):
        assert tts.voice_style("talk") == tts.VOICE_STYLES["bright"]["calm"]
        assert tts.voice_style("alarm") == tts.VOICE_STYLES["bright"]["alarm"]
    with Voice("now"):
        assert tts.voice_style("alarm")["rate"] == 1.0 and tts.voice_style("alarm")["volume"] == 1.0   # unchanged


def test_livelier_styles_get_more_energetic_in_order_and_danger_is_more_urgent():
    calm = [tts.VOICE_STYLES[n]["calm"]["rate"] for n in ("now", "livelier", "bright")]
    alarm = [tts.VOICE_STYLES[n]["alarm"]["rate"] for n in ("now", "livelier", "bright")]
    assert calm == sorted(calm) and alarm == sorted(alarm) and calm[0] < calm[2]
    for name in ("livelier", "bright"):
        assert tts.VOICE_STYLES[name]["alarm"]["rate"] > tts.VOICE_STYLES[name]["calm"]["rate"]
        assert tts.VOICE_STYLES[name]["alarm"]["volume"] > 1.0


# ---------------------------------------------------------------- what the speech is like

def test_the_synthesis_config_follows_the_style_and_the_speed_slider():
    tts._get_piper_voice()                                                 # loads the voice, learns the field names
    style = tts.VOICE_STYLES["bright"]["alarm"]
    config = tts._build_synthesis_config(style)
    assert abs(config.length_scale - 1.0 / (settings.get("TTS_SPEED") * style["speed"])) < 1e-9
    assert config.noise_scale == style["noise"]
    with mock.patch.object(settings, "_schedule_save", lambda: None):
        settings.update("TTS_SPEED", 1.5)
        try:
            faster = tts._build_synthesis_config(style)
        finally:
            settings.update("TTS_SPEED", 1.0)
    assert faster.length_scale < config.length_scale                       # the slider still works on top of the style


def test_rate_speeds_up_and_raises_the_sound_and_volume_scales_it(tmp=None):
    import tempfile
    folder = tempfile.mkdtemp()
    source = os.path.join(folder, "a.wav")
    with wave.open(source, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(22050)
        wav.writeframes(b"\x10\x27" * 22050)                               # one second at 10000
    out = os.path.join(folder, "b.wav")
    assert tts._apply_volume(source, out, 1.5, rate=1.2)
    with wave.open(out, "rb") as wav:
        assert wav.getframerate() == int(22050 * 1.2) and abs(wav.getnframes() / wav.getframerate() - 1 / 1.2) < 0.01
        assert int.from_bytes(wav.readframes(1), "little", signed=True) == 15000


def test_the_same_line_is_shorter_and_louder_in_the_brighter_styles():
    line = "Watch out, zombie on your left!"
    settings.update("TTS_VOLUME", 0.5)             # leave headroom: at full volume every clip clips at the same peak
    with Voice("now"):
        base = spoken(line, "alarm")
    with Voice("bright"):
        calm_line = spoken(line, "talk")
        danger = spoken(line, "alarm")
    assert danger["seconds"] < calm_line["seconds"] < base["seconds"] * 1.02    # urgent < bright < normal
    assert danger["seconds"] < base["seconds"] * 0.9
    assert danger["peak"] > base["peak"] * 1.2                                  # louder
    assert danger["rate"] > base["rate"]


def test_the_mood_reaches_the_face_and_the_voice_together():
    seen = {}

    def fake_play(path, flags):
        seen["mood"] = speech_state.mood()

    with Voice("livelier"), mock.patch.object(tts.winsound, "PlaySound", fake_play):
        tts.speak("Heads up, creeper in front of you!", "alarm")
    assert seen["mood"] == "alarm" and speech_state.mood() is None             # cleared again afterwards


# ---------------------------------------------------------------- the Status window's switch

def test_status_window_switch_changes_the_voice_and_plays_a_sample():
    import tkinter as tk
    from ui.status_window import StatusWindow, VOICE_CHOICES
    assert [value for value, _ in VOICE_CHOICES] == ["now", "livelier", "bright"]
    window = StatusWindow({})
    try:
        with mock.patch.object(settings, "_schedule_save", lambda: None), \
                mock.patch.object(tts, "speak_test") as sample:
            assert window.voice_var.get() == "now"
            window.voice_var.set("bright")
            window._on_voice_change()
            assert settings.get("TTS_VOICE") == "bright" and sample.called      # applied, and she says a sample
            window._on_reset_voice()
            assert settings.get("TTS_VOICE") == "now" and window.voice_var.get() == "now"
    finally:
        settings.update("TTS_VOICE", "now") if settings.get("TTS_VOICE") != "now" else None
        window.window.destroy()


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
