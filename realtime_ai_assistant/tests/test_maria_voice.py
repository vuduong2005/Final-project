r"""Checks that Maria's own trained voice (maria.onnx, two speakers: calm and scared) is used correctly:
which model and speaker a line gets, the fallback to the standard voice when the model is missing, the
setting, and the Status window's switch. The last test uses the real model when it is installed.
Plays no sound and writes no settings file.

Run from the realtime_ai_assistant folder:
    venv\Scripts\python.exe tests\test_maria_voice.py
"""
import os
import sys
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ROBOFLOW_API_KEY", "test-key-not-real")

from core import settings
import core.tts as tts

# Start from the defaults, in memory only (the player's own saved choices must not change these tests)
settings._schedule_save = lambda: None
for _key in ("TTS_MODEL", "TTS_VOICE", "TTS_SPEED", "TTS_VOLUME"):
    settings.reset(_key)


class Model:
    """Pick the voice model for a test without touching config_overrides.json."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.before = settings.get("TTS_MODEL")
        settings.update("TTS_MODEL", self.name)

    def __exit__(self, *exc):
        settings.update("TTS_MODEL", self.before)


def installed(yes=True):
    return mock.patch.object(tts, "maria_available", lambda: yes)


# ---------------------------------------------------------------- the setting

def test_the_voice_model_setting_accepts_only_amy_or_maria():
    assert settings.get("TTS_MODEL") == "amy"                                  # the default: nothing changes until chosen
    with Model("maria"):
        assert settings.get("TTS_MODEL") == "maria"
    for bad in ("bob", "", None, 1):
        try:
            settings.update("TTS_MODEL", bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not be accepted")
    assert settings.get("TTS_MODEL") == "amy"


# ---------------------------------------------------------------- which voice and speaker a line gets

def test_amy_is_used_unless_maria_is_chosen_and_installed():
    with installed(True):
        assert tts.voice_target("talk") == (tts.PIPER_MODEL_PATH, None)         # setting is amy
        assert tts.voice_target("alarm") == (tts.PIPER_MODEL_PATH, None)
    with Model("maria"), installed(False):
        assert tts.voice_target("talk") == (tts.PIPER_MODEL_PATH, None)         # chosen, but the file is missing: Amy
        assert tts.voice_target("alarm") == (tts.PIPER_MODEL_PATH, None)


def test_maria_speaks_calm_for_normal_lines_and_scared_for_warnings():
    with Model("maria"), installed(True):
        assert tts.voice_target("talk") == (tts.MARIA_MODEL_PATH, "calm")
        assert tts.voice_target("alarm") == (tts.MARIA_MODEL_PATH, "scared")


def test_the_speaker_is_found_by_name_with_a_fallback_to_the_known_numbers():
    named = mock.Mock()
    named.config.speaker_id_map = {"calm": 5, "scared": 7}
    assert tts._speaker_id(named, "scared") == 7 and tts._speaker_id(named, "calm") == 5
    unnamed = mock.Mock()
    unnamed.config.speaker_id_map = {}
    assert tts._speaker_id(unnamed, "calm") == 0 and tts._speaker_id(unnamed, "scared") == 1
    assert tts._speaker_id(unnamed, None) is None                              # a single-speaker voice has no speaker


def test_the_synthesis_config_carries_the_speaker():
    tts._get_piper_voice()                                                     # learns this Piper version's field names
    assert tts._build_synthesis_config(None, 1).speaker_id == 1
    assert tts._build_synthesis_config(None, None).speaker_id in (None, 0)


# ---------------------------------------------------------------- speak() end to end (fake voices, no sound)

def spoken_with(mood):
    """Run speak() and report which model file and speaker_id it asked Piper for."""
    seen = {}

    class FakeVoice:
        class config:
            speaker_id_map = {"calm": 0, "scared": 1}

        def synthesize_wav(self, text, wav_file, syn_config=None):
            seen["speaker"] = syn_config.speaker_id
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\x00\x10" * 2000)

    def fake_load(path, *a, **k):
        seen["model"] = path
        return FakeVoice()

    with mock.patch.object(tts, "_piper_voices", {}), mock.patch.object(tts.PiperVoice, "load", fake_load), \
            mock.patch.object(tts.winsound, "PlaySound", lambda *a: None):
        tts.speak("Watch out!", mood)
    return seen


def test_speak_uses_the_chosen_model_and_speaker():
    with Model("maria"), installed(True):
        talk, alarm = spoken_with("talk"), spoken_with("alarm")
    assert talk["model"] == alarm["model"] == tts.MARIA_MODEL_PATH
    assert talk["speaker"] == 0 and alarm["speaker"] == 1
    with installed(True):
        amy = spoken_with("alarm")
    assert amy["model"] == tts.PIPER_MODEL_PATH and amy["speaker"] in (None, 0)


# ---------------------------------------------------------------- the Status window's switch

def test_status_window_shows_the_voice_switch_only_when_maria_is_installed():
    from ui.status_window import StatusWindow, MODEL_CHOICES
    assert [value for value, _ in MODEL_CHOICES] == ["amy", "maria"]
    with installed(True):
        window = StatusWindow({})
        try:
            with mock.patch.object(tts, "speak_test") as sample:
                window.model_var.set("maria")
                window._on_model_change()
                assert settings.get("TTS_MODEL") == "maria" and sample.called   # applied, and she says a sample
                window._on_reset_voice()
                assert settings.get("TTS_MODEL") == "amy" and window.model_var.get() == "amy"
        finally:
            settings.reset("TTS_MODEL")
            window.window.destroy()


# ---------------------------------------------------------------- the real model (only when it is installed)

def test_the_real_maria_model_has_two_different_speakers():
    if not tts.maria_available():
        return                                                                  # not installed on this machine
    import struct
    import tempfile
    folder = tempfile.mkdtemp()
    peaks, lengths = {}, {}
    for mood in ("talk", "alarm"):
        model, speaker = tts.voice_target(mood)
        with Model("maria"):
            model, speaker = tts.voice_target(mood)
        path = os.path.join(folder, mood + ".wav")
        assert tts._generate_piper_wav("Watch out, zombie on your left!", path, tts.voice_style(mood), model, speaker)
        with wave.open(path, "rb") as wav:
            frames = wav.readframes(wav.getnframes())
            lengths[mood] = wav.getnframes() / wav.getframerate()
        samples = struct.unpack(f"<{len(frames) // 2}h", frames)
        peaks[mood] = max(max(samples), -min(samples))
        assert 0.8 < lengths[mood] < 8 and peaks[mood] > 1000                   # real speech, not silence
    assert tts._get_piper_voice(tts.MARIA_MODEL_PATH).config.speaker_id_map == {"calm": 0, "scared": 1}
    assert abs(lengths["talk"] - lengths["alarm"]) > 0.01 or peaks["talk"] != peaks["alarm"]   # the two speakers differ


def test_text_with_nothing_to_pronounce_is_skipped_quietly_not_logged_as_an_error():
    path = os.path.join(os.environ.get("TEMP", "."), "nothing_to_say.wav")
    with mock.patch.object(tts, "_get_piper_voice", side_effect=AssertionError("the model must not even be asked")),             mock.patch.object(tts, "logger") as log:
        for text in ("", "   ", "...", "!!!", "-", None):
            assert tts._generate_piper_wav(text, path, None) is False, repr(text)
    assert not log.error.called and not os.path.exists(path)                    # no error, no empty file left behind


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
