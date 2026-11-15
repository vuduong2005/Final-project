r"""Checks voice input without a microphone or a speech model: the settings, finding where speech starts and stops
in the sound (and noticing the player interrupting), tidying the text, the on/off controller in its two ways of
working (open mic, and hold-a-key push to talk), the key listener, the problems the player can fix (no mic, library
missing), the chat window's one Voice button, and that the microphone is never touched while voice is off. Fakes
stand in for the microphone and the model; no sound is played, no settings saved.

Run from the realtime_ai_assistant folder:
    venv\Scripts\python.exe tests\test_voice_input.py
"""
import os
import queue
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ROBOFLOW_API_KEY", "test-key-not-real")

from core import settings, voice_input
from core.voice_input import (BLOCK_SECONDS, SAMPLE_RATE, Recorder, Transcriber, VoiceError, VoiceInput,
                              clean_transcript, rms)

# Start from the defaults, in memory only (the player's saved choices must not change these tests)
settings._schedule_save = lambda: None
for _key in ("PUSH_TO_TALK", "PUSH_TO_TALK_KEY", "MIC_DEVICE", "STT_MODEL", "VOICE_SILENCE_SECONDS", "VOICE_BEEPS"):
    settings.reset(_key)

BLOCK = int(SAMPLE_RATE * BLOCK_SECONDS)


def quiet(n, level=0.001):
    return [np.full(BLOCK, level, dtype=np.float32) for _ in range(n)]


def talk(n, level=0.2):
    t = np.arange(BLOCK) / SAMPLE_RATE
    return [(level * np.sin(2 * np.pi * 220 * t)).astype(np.float32) for _ in range(n)]


class FakeMic:
    """Stands in for the microphone: plays back prepared blocks, then silence for ever."""

    def __init__(self, blocks):
        self.blocks = list(blocks)
        self.reads = 0
        self.closed = False

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def read(self, frames):
        self.reads += 1
        return self.blocks.pop(0) if self.blocks else np.full(frames, 0.001, dtype=np.float32)

    def __exit__(self, *exc):
        self.closed = True


class Push:
    """Set push to talk on or off for a test without touching config_overrides.json."""

    def __init__(self, on):
        self.on = on

    def __enter__(self):
        self.before = settings.get("PUSH_TO_TALK")
        settings.update("PUSH_TO_TALK", self.on)

    def __exit__(self, *exc):
        settings.update("PUSH_TO_TALK", self.before)


def wait_for(condition, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------- the settings

def test_the_voice_settings_have_safe_defaults_and_validate():
    assert settings.get("PUSH_TO_TALK") is False and settings.get("PUSH_TO_TALK_KEY") == "v"
    assert settings.get("STT_MODEL") == "base.en" and settings.get("VOICE_SILENCE_SECONDS") == 1.2
    assert settings.get("VOICE_BEEPS") is True
    assert "INPUT_MODE" not in settings.SPECS                                     # voice is a button now, never saved
    with Push(True):
        assert settings.get("PUSH_TO_TALK") is True
    for key, bad in (("PUSH_TO_TALK_KEY", "space"), ("PUSH_TO_TALK_KEY", None), ("PUSH_TO_TALK", "maybe"),
                     ("STT_MODEL", "huge.en"), ("VOICE_BEEPS", "yes")):
        try:
            settings.update(key, bad)
        except ValueError:
            continue
        raise AssertionError(f"{key}={bad!r} should not be accepted")
    assert settings.update("VOICE_SILENCE_SECONDS", 99) == 3.0                    # clamped into the allowed range
    assert settings.update("VOICE_SILENCE_SECONDS", 0.0) == 0.5
    settings.reset("VOICE_SILENCE_SECONDS")
    for key in ("v", "b", "f8", "f9"):
        assert settings.update("PUSH_TO_TALK_KEY", key) == key
    settings.reset("PUSH_TO_TALK_KEY")


# ---------------------------------------------------------------- finding the phrase in the sound

def test_the_recorder_keeps_the_phrase_with_a_little_before_it_and_stops_after_the_pause():
    mic = FakeMic(quiet(20) + quiet(8) + talk(30) + quiet(60))
    levels = []
    audio = Recorder(mic).record(threading.Event(), 0.5, on_level=levels.append)
    seconds = len(audio) / SAMPLE_RATE
    assert 0.9 + 0.3 <= seconds <= 0.9 + 0.35 + 0.5 + 0.1, seconds              # the words + pre-roll + the pause
    assert rms(audio[:BLOCK * 4]) < 0.05 and rms(audio[BLOCK * 10:BLOCK * 25]) > 0.1   # starts quiet, then the words
    assert mic.closed and len(levels) > 30                                       # the mic was released; a level meter had data
    assert mic.reads < 20 + 8 + 30 + 60                                          # it stopped at the pause, not at the end


def test_nothing_said_gives_none_and_a_click_is_not_speech():
    with mock.patch.object(voice_input, "NO_SPEECH_SECONDS", 0.2):
        assert Recorder(FakeMic(quiet(5000))).record(threading.Event(), 0.5) is None
        click = quiet(20) + talk(1) + quiet(80)
        assert Recorder(FakeMic(click)).record(threading.Event(), 0.5) is None   # one 30 ms bump is not a word


def test_a_noisy_room_does_not_count_as_speech_but_louder_words_do():
    hum = quiet(20, level=0.02) + quiet(80, level=0.02)                           # a loud fan, no words
    with mock.patch.object(voice_input, "NO_SPEECH_SECONDS", 0.2):
        assert Recorder(FakeMic(hum)).record(threading.Event(), 0.5) is None
    with_words = quiet(20, 0.02) + talk(25, level=0.3) + quiet(60, 0.02)
    assert Recorder(FakeMic(with_words)).record(threading.Event(), 0.5) is not None


def test_pressing_the_key_again_ends_the_phrase_and_keeps_what_was_said():
    stop = threading.Event()
    mic = FakeMic(quiet(20) + talk(40))                                           # still talking when the key is pressed

    class StopMidSpeech(FakeMic):
        def read(self, frames):
            if self.reads == 50:
                stop.set()
            return super().read(frames)

    audio = Recorder(StopMidSpeech(mic.blocks)).record(stop, 5.0)
    assert audio is not None and len(audio) / SAMPLE_RATE > 0.3                   # what was said so far is kept
    cancelled = threading.Event()
    cancelled.set()
    assert Recorder(FakeMic(quiet(50))).record(cancelled, 0.5) is None           # stopped before any words: nothing


def test_a_very_long_speech_is_cut_at_the_maximum():
    with mock.patch.object(voice_input, "MAX_SECONDS", 0.6):
        audio = Recorder(FakeMic(quiet(15) + talk(10000))).record(threading.Event(), 5.0)
    assert audio is not None and len(audio) / SAMPLE_RATE < 1.5


# ---------------------------------------------------------------- the text

def test_the_transcript_is_tidied_and_phantom_phrases_from_silence_are_dropped():
    assert clean_transcript("  Hey   Maria,  can you see this? ") == "Hey Maria, can you see this?"
    assert clean_transcript("[BLANK_AUDIO]") == "" and clean_transcript("(music) ") == "" and clean_transcript("...") == ""
    assert clean_transcript("Thank you.", seconds=0.8) == ""                      # Whisper invents this from near-silence
    assert clean_transcript("you", seconds=1.0) == ""
    assert clean_transcript("Thank you.", seconds=5.0) == "Thank you."            # a real "thank you" in a long phrase stays
    assert clean_transcript("Where is the zombie [noise] now?") == "Where is the zombie now?"
    assert clean_transcript("") == "" and clean_transcript(None) == ""


class FakeModel:
    def __init__(self, text):
        self.text, self.calls = text, []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return iter([mock.Mock(text=part) for part in self.text.split("|")]), None


def test_the_transcriber_loads_the_model_once_and_again_when_the_choice_changes():
    loaded = []
    model = FakeModel(" help me | with this ")

    def loader(name):
        loaded.append(name)
        return model

    t = Transcriber(loader)
    audio = np.zeros(SAMPLE_RATE * 3, dtype=np.float32)
    assert t.transcribe(audio) == "help me with this"
    t.transcribe(audio)
    assert loaded == ["base.en"]                                                  # loaded once
    kwargs = model.calls[0]
    assert kwargs["language"] == "en" and kwargs["vad_filter"] is True and kwargs["beam_size"] == 1
    settings.update("STT_MODEL", "tiny.en")
    try:
        t.transcribe(audio)
    finally:
        settings.reset("STT_MODEL")
    assert loaded == ["base.en", "tiny.en"]                                       # the player picked another model


# ---------------------------------------------------------------- problems the player can fix

def test_a_missing_microphone_library_or_speech_library_is_explained_not_a_crash():
    with mock.patch.dict(sys.modules, {"sounddevice": None}):
        try:
            with voice_input._Microphone():
                pass
        except VoiceError as e:
            assert "sounddevice" in str(e)
        else:
            raise AssertionError("a missing library must raise VoiceError")
    with mock.patch.dict(sys.modules, {"faster_whisper": None}):
        try:
            Transcriber._load_whisper("base.en")
        except VoiceError as e:
            assert "faster-whisper" in str(e)
        else:
            raise AssertionError("a missing library must raise VoiceError")


def test_no_microphone_or_blocked_access_is_explained_with_the_windows_setting_to_check():
    fake = mock.Mock()
    fake.InputStream.side_effect = RuntimeError("no default input device")
    with mock.patch.dict(sys.modules, {"sounddevice": fake}):
        try:
            with voice_input._Microphone():
                pass
        except VoiceError as e:
            assert "Privacy" in str(e) and "Microphone" in str(e)
        else:
            raise AssertionError("must raise VoiceError")


# ---------------------------------------------------------------- interrupting, and holding a key

def test_the_recorder_tells_when_words_begin_so_the_player_can_interrupt_her():
    began = []
    audio = Recorder(FakeMic(quiet(20) + talk(30) + quiet(80))).record(threading.Event(), 0.5,
                                                                     on_speech_start=lambda: began.append(1))
    assert audio is not None and began == [1]                                     # exactly once, when the words began
    with mock.patch.object(voice_input, "NO_SPEECH_SECONDS", 0.2):
        Recorder(FakeMic(quiet(300))).record(threading.Event(), 0.5, on_speech_start=lambda: began.append(2))
    assert began == [1]                                                           # silence is not an interruption


def test_while_she_is_speaking_the_players_voice_must_be_clearly_louder_to_count():
    leak = quiet(15) + talk(40, level=0.04) + quiet(80)                           # her own voice leaking into the mic
    with mock.patch.object(voice_input, "NO_SPEECH_SECONDS", 1.5):
        assert Recorder(FakeMic(leak)).record(threading.Event(), 0.5) is not None            # alone: counts as speech
        assert Recorder(FakeMic(leak)).record(threading.Event(), 0.5, harder=lambda: True) is None   # while she speaks: ignored
    loud = quiet(20) + talk(40, level=0.3) + quiet(80)
    began = []
    audio = Recorder(FakeMic(loud)).record(threading.Event(), 0.5, harder=lambda: True, on_speech_start=lambda: began.append(1))
    assert audio is not None and began == [1]                                     # a clearly louder voice still interrupts


def test_open_mic_waits_for_ever_for_the_first_word_but_a_normal_press_gives_up():
    long_wait = quiet(300) + talk(30) + quiet(60)
    with mock.patch.object(voice_input, "NO_SPEECH_SECONDS", 0.2):
        assert Recorder(FakeMic(long_wait)).record(threading.Event(), 0.5, give_up=False) is not None
        assert Recorder(FakeMic(long_wait)).record(threading.Event(), 0.5) is None


def test_a_held_key_records_everything_including_pauses_until_it_is_let_go():
    stop = threading.Event()

    class LetGoAt(FakeMic):
        def read(self, frames):
            if self.reads == 60:
                stop.set()
            return super().read(frames)

    mic = LetGoAt(talk(10) + quiet(30) + talk(10))                                # a long pause in the middle
    audio = Recorder(mic).record_held(stop)
    assert audio is not None and len(audio) >= BLOCK * 55                         # the pause did not end it
    assert mic.closed
    done = threading.Event()
    done.set()
    assert Recorder(FakeMic(talk(40))).record_held(done) is None                  # let go before anything was heard
    assert Recorder(FakeMic(quiet(10) + talk(2) + quiet(40))).record_held(_stopped_after(45)) is None   # only a click
    with mock.patch.object(voice_input, "HOLD_MAX_SECONDS", 0.3):
        long = Recorder(FakeMic(talk(500))).record_held(threading.Event())
    assert long is not None and len(long) / SAMPLE_RATE < 0.6                     # a key stuck down is cut off


def _stopped_after(blocks):
    class Event(threading.Event):
        reads = 0

        def is_set(self):
            Event.reads += 1
            return Event.reads > blocks
    return Event()


# ---------------------------------------------------------------- the controller

class FakeRecorder:
    """Stands in for the Recorder: delivers `phrases` phrases (each one counts as the player starting to talk, after
    `delay` seconds of waiting), then waits for the stop signal like a quiet room."""

    def __init__(self, phrases=1, error=None, held_audio=True, delay=0.0):
        self.phrases_left, self.error, self.held_audio, self.delay = phrases, error, held_audio, delay
        self.calls = self.held_calls = 0
        self.silence_seen = self.give_up_seen = self.harder_seen = None
        self.events = None

    def record(self, stop_event, silence_seconds, on_level=None, on_speech_start=None, harder=None, give_up=True):
        self.calls += 1
        self.silence_seen, self.give_up_seen, self.harder_seen = silence_seconds, give_up, harder
        if self.error:
            raise self.error
        if self.phrases_left > 0:
            self.phrases_left -= 1
            if self.delay:
                time.sleep(self.delay)
            if on_speech_start:
                on_speech_start()
            return np.ones(SAMPLE_RATE, dtype=np.float32)
        stop_event.wait(0.05)
        return None

    def record_held(self, stop_event, on_level=None, on_open=None):
        self.held_calls += 1
        if self.error:
            raise self.error
        if self.events is not None:
            self.events.append("open")
        if on_open:
            on_open()
        stop_event.wait(3)
        return np.ones(SAMPLE_RATE, dtype=np.float32) if self.held_audio else None


class FakeTranscriber:
    def __init__(self, text="where is the zombie", error=None):
        self.text, self.error, self.calls, self.preloaded = text, error, 0, 0

    def transcribe(self, audio):
        self.calls += 1
        if self.error:
            raise self.error
        return self.text

    def preload(self):
        self.preloaded += 1
        if self.error:
            raise self.error


def controller(recorder=None, transcriber=None):
    log = {"text": [], "states": [], "messages": [], "stopped": 0, "beeps": [], "warned": False,
           "speaking": False, "echo": set(), "warn_at": 0.0}
    voice = VoiceInput(
        on_text=log["text"].append, on_state=log["states"].append, on_message=log["messages"].append,
        recorder=recorder or FakeRecorder(), transcriber=transcriber or FakeTranscriber(),
        stop_speaking=lambda: log.__setitem__("stopped", log["stopped"] + 1), beep=log["beeps"].append,
        warned_since=lambda t: log["warned"] or log["warn_at"] > t, maria_speaking=lambda: log["speaking"],
        is_echo=lambda text: text in log["echo"])
    return voice, log


def test_voice_starts_off_and_the_microphone_is_never_touched_until_it_is_switched_on():
    recorder = FakeRecorder()
    voice, log = controller(recorder)
    assert voice.state == "off" and not voice.is_on()
    voice.ptt_down()                                                              # a key press while voice is off does nothing
    voice.ptt_up()
    time.sleep(0.2)
    assert recorder.calls == 0 and recorder.held_calls == 0 and log["states"] == [] and log["text"] == []


def test_open_mic_sends_what_is_said_and_her_chatter_stops_when_the_player_starts_to_talk():
    voice, log = controller()
    with Push(False):
        assert voice.switch() is True and voice.is_on()
        assert wait_for(lambda: log["text"]), log
        assert voice.state == "listening" and log["states"][0] == "listening"
        assert voice.recorder.give_up_seen is False                               # it waits for the next phrase for ever
        assert voice.recorder.harder_seen is not None                             # and knows when she is speaking
        assert voice.recorder.silence_seen == settings.get("VOICE_SILENCE_SECONDS")
    assert log["text"] == ["where is the zombie"] and log["stopped"] >= 1        # she was cut off when the player began
    assert log["beeps"][0] == "start"
    voice.switch()
    assert voice.state == "off" and log["beeps"][-1] == "stop"
    assert wait_for(lambda: log["states"][-1:] == ["off"])                        # (delivered to the window in order, a moment later)


def test_open_mic_keeps_listening_so_phrase_after_phrase_arrives_without_pressing_anything():
    voice, log = controller(FakeRecorder(phrases=3))
    with Push(False):
        voice.switch()
        assert wait_for(lambda: len(log["text"]) == 3), log
        assert voice.is_on() and voice.state == "listening"
    voice.cancel()


def test_switching_voice_off_closes_the_microphone_and_throws_away_what_was_not_yet_sent():
    recorder = FakeRecorder(phrases=0)
    voice, log = controller(recorder)
    with Push(False):
        voice.switch()
        assert wait_for(lambda: recorder.calls >= 1)
        voice.switch()                                                            # off
        time.sleep(0.3)
        calls = recorder.calls
        time.sleep(0.3)
        assert recorder.calls == calls                                            # the loop ended: the mic is closed
    assert log["text"] == [] and voice.state == "off"
    gate = threading.Event()

    class SlowTranscriber(FakeTranscriber):
        def transcribe(self, audio):
            gate.wait(3)
            return super().transcribe(audio)

    voice2, log2 = controller(FakeRecorder(phrases=1), SlowTranscriber())
    with Push(False):
        voice2.switch()
        assert wait_for(lambda: voice2._busy)                                     # being understood...
        voice2.switch()                                                           # ...and the player turns voice off
        gate.set()
        time.sleep(0.3)
    assert log2["text"] == []                                                     # never sent


def test_stop_and_exit_cancel_voice_quietly():
    voice, log = controller(FakeRecorder(phrases=0))
    with Push(False):
        voice.switch()
        assert wait_for(lambda: voice.recorder.calls >= 1)
        beeps = len(log["beeps"])
        voice.cancel()
        assert voice.state == "off" and len(log["beeps"]) == beeps                # no tone on the way out
        voice.cancel()                                                            # and doing it twice is harmless


def test_text_that_only_repeats_her_own_voice_is_ignored_and_other_text_is_sent():
    voice, log = controller(FakeRecorder(phrases=2), FakeTranscriber(text="watch out zombie on your left"))
    log["echo"].add("watch out zombie on your left")
    with Push(False):
        voice.switch()
        assert wait_for(lambda: voice.transcriber.calls == 2)
        time.sleep(0.2)
        assert log["text"] == []                                                  # she heard herself: nothing is sent
        voice.transcriber.text = "i see a creeper"
        voice.recorder.phrases_left = 1
        assert wait_for(lambda: log["text"] == ["i see a creeper"])
    voice.cancel()


def test_a_danger_warning_that_played_while_listening_is_never_sent_as_the_players_words():
    voice, log = controller()
    log["warned"] = True                                                          # a warning started during the recording
    with Push(False):
        voice.switch()
        assert wait_for(lambda: log["messages"])
    assert log["text"] == [] and voice.transcriber.calls == 0
    assert "danger warning" in log["messages"][0] and "Say it again" in log["messages"][0]
    voice.cancel()


def test_an_unclear_sound_is_quiet_in_open_mic_but_explained_in_push_to_talk():
    voice, log = controller(FakeRecorder(phrases=1), FakeTranscriber(text=""))
    with Push(False):
        voice.switch()
        assert wait_for(lambda: voice.transcriber.calls == 1)
        time.sleep(0.2)
    assert log["messages"] == [] and log["text"] == []                            # noise must not nag
    voice.cancel()
    voice2, log2 = controller(transcriber=FakeTranscriber(text=""))
    with Push(True):
        voice2.switch()
        voice2.ptt_down()
        assert wait_for(lambda: voice2.state == "listening")
        voice2.ptt_up()
        assert wait_for(lambda: log2["messages"])
    assert "couldn't make that out" in log2["messages"][0] and log2["text"] == []
    voice2.cancel()


def test_problems_become_messages_switch_voice_off_and_never_escape():
    for error, expected in ((VoiceError("I can't open the microphone."), "can't open the microphone"),
                            (RuntimeError("boom"), "hit a problem")):
        voice, log = controller(FakeRecorder(error=error))
        with Push(False):
            voice.switch()
            assert wait_for(lambda: log["messages"] and voice.state == "off"), error
        assert expected in log["messages"][0] and log["text"] == []
    voice, log = controller(transcriber=FakeTranscriber(error=VoiceError("model missing")))
    with Push(False):
        voice.switch()
        assert wait_for(lambda: log["messages"] and voice.state == "off")
    assert "model missing" in log["messages"][0]
    voice, log = controller(transcriber=FakeTranscriber(error=RuntimeError("model broke")))
    with Push(False):
        voice.switch()
        assert wait_for(lambda: log["messages"])
    assert "hit a problem" in log["messages"][0] and voice.is_on()               # one bad phrase does not end voice
    voice.cancel()


def test_a_failing_text_handler_cannot_stop_voice_from_working():
    voice, log = controller(FakeRecorder(phrases=2))
    calls = []

    def handler(text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("chat closed")

    voice.on_text = handler
    with Push(False):
        voice.switch()
        assert wait_for(lambda: len(calls) == 2), calls                           # the second phrase still arrived
    voice.cancel()


def test_push_to_talk_records_only_while_the_key_is_held_and_ignores_key_repeat():
    recorder = FakeRecorder()
    voice, log = controller(recorder)
    with Push(True):
        voice.switch()
        assert voice.state == "ready"
        time.sleep(0.2)
        assert recorder.calls == 0 and recorder.held_calls == 0                   # the microphone is closed while waiting
        voice.ptt_down()
        assert wait_for(lambda: voice.state == "listening" and recorder.held_calls == 1)
        voice.ptt_down()                                                          # the key repeating while held
        voice.ptt_down()
        voice.ptt_up()
        assert wait_for(lambda: log["text"] == ["where is the zombie"])
        assert wait_for(lambda: voice.state == "ready")
        assert recorder.held_calls == 1 and log["stopped"] >= 1                   # she was cut off when the key went down
        voice.ptt_down()
        assert wait_for(lambda: recorder.held_calls == 2)
        voice.ptt_up()
        assert wait_for(lambda: len(log["text"]) == 2)
    voice.cancel()


def test_push_to_talk_key_does_nothing_in_open_mic_mode_and_a_quick_tap_is_explained():
    voice, log = controller()
    with Push(False):
        voice.switch()
        voice.ptt_down()
        voice.ptt_up()
        time.sleep(0.2)
        assert voice.recorder.held_calls == 0
    voice.cancel()
    voice2, log2 = controller(FakeRecorder(held_audio=False))
    with Push(True):
        voice2.switch()
        voice2.ptt_down()
        assert wait_for(lambda: voice2.state == "listening")
        voice2.ptt_up()
        assert wait_for(lambda: log2["messages"])
    assert "Hold the key" in log2["messages"][0] and log2["text"] == []
    voice2.cancel()


def test_switching_off_while_the_key_is_held_drops_the_recording():
    voice, log = controller()
    with Push(True):
        voice.switch()
        voice.ptt_down()
        assert wait_for(lambda: voice.state == "listening")
        voice.switch()                                                            # voice off with the key still down
        time.sleep(0.4)
    assert log["text"] == [] and voice.transcriber.calls == 0 and voice.state == "off"


def test_changing_push_to_talk_while_voice_is_on_changes_how_the_microphone_is_used():
    recorder = FakeRecorder(phrases=0)
    voice, log = controller(recorder)
    with Push(False):
        voice.switch()
        assert wait_for(lambda: recorder.calls >= 1) and voice.state == "listening"
        settings.update("PUSH_TO_TALK", True)
        voice.ptt_changed(True)                                                   # (main registers this as a listener)
        assert voice.state == "ready"
        time.sleep(0.3)
        calls = recorder.calls
        time.sleep(0.3)
        assert recorder.calls == calls                                            # the open-mic loop ended, the mic is closed
        settings.update("PUSH_TO_TALK", False)
        voice.ptt_changed(False)
        assert wait_for(lambda: recorder.calls > calls) and voice.state == "listening"
    voice.cancel()


def test_turning_voice_on_loads_the_model_in_the_background_and_reports_a_missing_library():
    voice, log = controller()
    voice.preload()
    assert wait_for(lambda: voice.transcriber.preloaded == 1)
    voice2, log2 = controller(transcriber=FakeTranscriber(error=VoiceError("not installed")))
    voice2.preload()
    assert wait_for(lambda: log2["messages"]) and "not installed" in log2["messages"][0]


def test_the_beep_only_sounds_when_enabled():
    played = []
    fake = mock.Mock(Beep=lambda freq, ms: played.append(freq))
    with mock.patch.dict(sys.modules, {"winsound": fake}):
        voice_input.beep("start")
        voice_input.beep("stop")
        assert played == [1000, 650]
        settings.update("VOICE_BEEPS", False)
        try:
            voice_input.beep("start")
        finally:
            settings.reset("VOICE_BEEPS")
        assert played == [1000, 650]


# ---------------------------------------------------------------- the push-to-talk key

class K:
    """A key as pynput reports it: letters have .char, special keys have .name."""

    def __init__(self, char=None, name=None):
        self.char, self.name = char, name


def key_listener(blocked=False, mods=lambda: False):
    log = []
    keys = voice_input.PushToTalkKey(lambda: log.append("down"), lambda: log.append("up"), blocked=lambda: blocked,
                                     modifiers_down=mods)
    return keys, log


def test_the_key_goes_down_and_up_once_even_when_windows_repeats_it():
    keys, log = key_listener()
    keys.press(K("v"))
    keys.press(K("v"))
    keys.press(K("V"))                                                            # repeating while held
    keys.release(K("v"))
    keys.release(K("v"))
    assert log == ["down", "up"]
    keys.press(K("b"))                                                            # any other key is nothing to do with it
    keys.release(K("b"))
    assert log == ["down", "up"]


def test_the_key_can_be_changed_and_special_keys_work():
    keys, log = key_listener()
    settings.update("PUSH_TO_TALK_KEY", "f8")
    try:
        keys.press(K("v"))
        assert log == []
        keys.press(K(name="f8"))
        keys.release(K(name="f8"))
        assert log == ["down", "up"]
    finally:
        settings.reset("PUSH_TO_TALK_KEY")


def test_changing_the_key_while_it_is_held_cannot_leave_push_to_talk_stuck():
    keys, log = key_listener()
    keys.press(K("v"))
    settings.update("PUSH_TO_TALK_KEY", "f8")                                     # changed in the Status window mid-hold
    try:
        keys.release(K("v"))                                                      # the key that went down is the one that ends it
        assert log == ["down", "up"]
        keys.press(K(name="f8"))
        keys.release(K(name="f8"))
        assert log == ["down", "up", "down", "up"]                                # and the new key works at once
    finally:
        settings.reset("PUSH_TO_TALK_KEY")


def test_the_key_is_ignored_while_typing_and_when_ctrl_alt_or_windows_is_really_held():
    keys, log = key_listener(blocked=True)
    keys.press(K("v"))
    keys.release(K("v"))
    assert log == []                                                              # typing a word with a v in it
    held = {"now": True}
    keys, log = key_listener(mods=lambda: held["now"])
    keys.press(K("v"))                                                            # Ctrl+Alt+V switches voice on/off instead
    keys.release(K("v"))
    assert log == []
    held["now"] = False       # Windows is asked every time, so a missed Ctrl release can never leave it stuck
    keys.press(K("v"))
    keys.release(K("v"))
    assert log == ["down", "up"]
    assert isinstance(voice_input._modifier_keys_down(), bool)


def test_the_key_listener_starts_and_stops_and_a_failure_is_not_a_crash():
    started = []

    class FakeListener:
        def __init__(self, on_press, on_release):
            started.append(("made", on_press, on_release))

        def start(self):
            started.append("start")

        def stop(self):
            started.append("stop")

    keys = voice_input.PushToTalkKey(lambda: None, lambda: None, listener_factory=FakeListener)
    keys.start()
    keys.stop()
    keys.stop()
    assert started[1:] == ["start", "stop"]
    voice_input.PushToTalkKey(lambda: None, lambda: None, listener_factory=lambda **kw: 1 / 0).start()


# ---------------------------------------------------------------- the chat window and Status window

def make_chat(**kwargs):
    from ui.chat_window import ChatWindow
    got = []
    chat = ChatWindow(queue.Queue(), {}, user_input_callback=got.append, **kwargs)
    return chat, got


def shown_text(chat):
    chat.window.update()
    return chat.chat_display.get("1.0", "end")


def test_the_chat_window_has_one_voice_button_that_starts_off():
    chat, _ = make_chat()
    try:
        chat.window.update()
        assert "Voice: OFF" in chat.voice_button.cget("text")
        assert chat.status_label.cget("text").startswith("Click the box to type")
        assert not hasattr(chat, "talk_button") and not hasattr(chat, "mode_button")   # no second control
        chat._on_voice_click()                                                    # no controller yet: a polite message
        assert "not available" in shown_text(chat)
        calls = []
        chat.set_voice(mock.Mock(state="off", switch=lambda: calls.append("switch")))
        chat._on_voice_click()
        assert calls == ["switch"]
        chat.window.update()                                                      # let the pending redraw run first
    finally:
        chat.window.destroy()


def test_the_voice_button_and_hint_follow_the_state():
    chat, _ = make_chat()
    try:
        def show(state):
            chat.on_voice_state(state)
            chat.window.update()
            return chat.voice_button.cget("text"), chat.status_label.cget("text")

        with Push(False):
            text, hint = show("listening")
            assert "Voice: ON" in text and "talk over her" in hint.lower()
            text, hint = show("transcribing")
            assert "Understanding" in text
        with Push(True):
            text, hint = show("ready")
            assert "Hold V to talk" in text and "hold V" in hint
            text, hint = show("listening")
            assert "Listening" in text and "let go of V" in hint
        text, hint = show("off")
        assert "Voice: OFF" in text and hint.startswith("Click the box to type")
        chat.on_voice_message("I can't open the microphone.")
        assert "System: I can't open the microphone." in shown_text(chat)
    finally:
        chat.window.destroy()


def test_spoken_text_arrives_like_typed_text_and_typing_always_works():
    chat, got = make_chat()
    try:
        chat.submit_voice_text("can you see my house")
        text = shown_text(chat)
        assert got == ["can you see my house"] and "You (voice): can you see my house" in text
        chat.input_field.insert(0, "I typed this")
        chat._on_input_send()
        assert got[-1] == "I typed this" and "You: I typed this" in shown_text(chat) and chat.input_field.get() == ""
    finally:
        chat.window.destroy()


def test_typing_or_sending_a_message_cuts_off_what_she_is_saying():
    cut = []
    chat, got = make_chat(interrupt_callback=lambda: cut.append(1))
    try:
        chat._on_key(mock.Mock(char="a", keysym="a"))
        assert cut == [1]
        chat._on_key(mock.Mock(char="b", keysym="b"))                             # not again within half a second
        assert cut == [1]
        chat._last_interrupt = 0.0
        chat._on_key(mock.Mock(char="", keysym="Shift_L"))                        # a modifier is not typing
        assert cut == [1]
        chat._on_key(mock.Mock(char="", keysym="BackSpace"))                      # deleting is
        assert cut == [1, 1]
        chat.input_field.insert(0, "stop talking")
        chat._on_input_send()                                                     # sending a message cuts her too
        assert cut == [1, 1, 1] and got == ["stop talking"]
        chat.submit_voice_text("hey")                                             # and so does a spoken one
        shown_text(chat)
        assert len(cut) == 4
    finally:
        chat.window.destroy()


def test_the_chat_window_knows_when_the_player_is_typing_in_its_box():
    chat, _ = make_chat()
    try:
        chat.window.update()
        assert chat.is_typing() is False
        chat._typing_focus = True                                                 # (set by the box's FocusIn binding)
        assert chat.is_typing() is True
        bindings = chat.input_field.bind()
        assert "<Key>" in bindings and "<FocusIn>" in bindings and "<FocusOut>" in bindings
    finally:
        chat.window.destroy()


def test_the_chat_window_keeps_stop_at_the_far_right_and_everything_fits():
    chat, _ = make_chat()
    try:
        chat.window.update()
        for push in (False, True):
            with Push(push):
                for state in ("off", "ready", "listening", "transcribing"):
                    chat.on_voice_state(state)
                    chat.window.update()
                    assert chat.stop_button.winfo_x() + chat.stop_button.winfo_width() <= chat.window.winfo_width()
                    assert chat.stop_button.winfo_x() > chat.voice_button.winfo_x() > chat.status_label.winfo_x()
                    assert chat.voice_button.winfo_x() + chat.voice_button.winfo_width() <= chat.stop_button.winfo_x()
                    assert chat.voice_button.winfo_width() >= chat.voice_button.winfo_reqwidth()     # not squeezed
    finally:
        chat.window.destroy()


def test_the_status_window_has_push_to_talk_and_its_key_but_no_second_on_off_switch():
    from ui.status_window import StatusWindow
    window = StatusWindow({})
    try:
        assert not hasattr(window, "input_var") and "VOICE_SILENCE_SECONDS" in window.live_sliders
        assert window.ptt_var.get() is False and window.ptt_key_var.get() == "V"
        window.ptt_var.set(True)
        window._on_ptt_change()
        assert settings.get("PUSH_TO_TALK") is True
        window.ptt_key_var.set("F8")
        window._on_ptt_key_change()
        assert settings.get("PUSH_TO_TALK_KEY") == "f8"
    finally:
        settings.reset("PUSH_TO_TALK")
        settings.reset("PUSH_TO_TALK_KEY")
        window.window.destroy()


def test_the_voice_hotkey_is_registered_and_the_default_is_still_only_chat_and_status():
    import main
    seen = {}

    class FakeHotkeys:
        def __init__(self, mapping):
            seen["map"] = mapping

        def start(self):
            pass

    with mock.patch.object(main, "GlobalHotKeys", FakeHotkeys):
        main.start_hotkey_listener(lambda: None, lambda: None, lambda: None)
        assert set(seen["map"]) == {"<ctrl>+<alt>+g", "<ctrl>+<alt>+s", "<ctrl>+<alt>+v"}
        main.start_hotkey_listener(lambda: None, lambda: None)
        assert set(seen["map"]) == {"<ctrl>+<alt>+g", "<ctrl>+<alt>+s"}


def test_the_status_window_does_not_fail_after_it_is_closed_when_settings_change():
    from ui.status_window import StatusWindow
    window = StatusWindow({})
    window.window.destroy()
    with mock.patch.object(settings, "logger") as log:
        settings.update("PUSH_TO_TALK", True)
        settings.update("PUSH_TO_TALK", False)
    assert "invalid command name" not in " ".join(str(c) for c in log.error.mock_calls)


def test_a_player_who_is_already_talking_when_listening_starts_is_not_mistaken_for_the_room():
    audio = Recorder(FakeMic(talk(40) + quiet(80))).record(threading.Event(), 0.5)  # no quiet moment at the start at all
    assert audio is not None and len(audio) / SAMPLE_RATE > 1.2
    assert rms(audio[:BLOCK * 3]) > 0.1                                           # the first word is in the recording


def test_the_first_word_said_right_after_the_start_tone_is_kept():
    audio = Recorder(FakeMic(quiet(6) + talk(30) + quiet(80))).record(threading.Event(), 0.5)   # speech inside the calibration time
    assert audio is not None and rms(audio[:BLOCK * 8]) > 0.05 or rms(audio) > 0.05
    assert len(audio) / SAMPLE_RATE >= 0.9


def test_a_click_a_cough_and_a_keyboard_thump_are_not_phrases_but_a_short_word_is():
    for blocks in (2, 4, 6):                                                      # up to 0.18 s of sound
        assert Recorder(FakeMic(quiet(20) + talk(blocks) + quiet(80))).record(threading.Event(), 0.5) is None, blocks
    assert Recorder(FakeMic(quiet(20) + talk(10) + quiet(80))).record(threading.Event(), 0.5) is not None   # 0.3 s: a word


def test_the_speech_filter_counts_the_sound_not_the_pauses_around_it():
    audio = np.concatenate(quiet(10) + talk(8) + quiet(80))                       # 0.24 s of sound, 3 s of clip
    assert voice_input.voiced_seconds(audio) < 0.4
    t = Transcriber(lambda name: FakeModel("Thank you."))
    assert t.transcribe(audio) == ""                                              # Whisper's invention from near-silence
    longer = np.concatenate(talk(80) + quiet(10))                                 # 2.4 s of real sound
    assert t.transcribe(longer) == "Thank you."


class FakeStream:
    def __init__(self, fail_start=False, fail_stop=False):
        self.fail_start, self.fail_stop = fail_start, fail_stop
        self.started, self.stopped, self.closed = False, False, False

    def start(self):
        if self.fail_start:
            raise RuntimeError("no device")
        self.started = True

    def stop(self):
        self.stopped = True
        if self.fail_stop:
            raise RuntimeError("stop failed")

    def close(self):
        self.closed = True


def fake_sounddevice(stream):
    fake = mock.Mock()
    fake.InputStream.side_effect = lambda **kwargs: (setattr(stream, "callback", kwargs["callback"]), stream)[1]
    return fake


def test_the_microphone_is_always_closed_even_when_stopping_it_fails_or_it_never_started():
    stream = FakeStream(fail_stop=True)
    with mock.patch.dict(sys.modules, {"sounddevice": fake_sounddevice(stream)}):
        with voice_input._Microphone():
            pass
    assert stream.started and stream.stopped and stream.closed                    # close ran although stop raised
    stream2 = FakeStream(fail_start=True)
    with mock.patch.dict(sys.modules, {"sounddevice": fake_sounddevice(stream2)}):
        try:
            with voice_input._Microphone():
                pass
        except VoiceError:
            pass
        else:
            raise AssertionError("a microphone that cannot start must raise VoiceError")
    assert stream2.closed                                                         # the half-opened stream was released


def test_a_microphone_that_stops_sending_sound_is_reported_not_waited_on_for_ever():
    stream = FakeStream()
    with mock.patch.dict(sys.modules, {"sounddevice": fake_sounddevice(stream)}), \
            mock.patch.object(voice_input, "READ_TIMEOUT", 0.05):
        with voice_input._Microphone() as mic:
            stream.callback(np.full((BLOCK, 1), 0.1, dtype=np.float32), BLOCK, None, None)
            assert rms(mic.read(BLOCK)) > 0.05                                    # sound that arrived is returned
            started = time.time()
            try:
                mic.read(BLOCK)
            except VoiceError as e:
                assert "stopped sending sound" in str(e) and time.time() - started < 1.0
            else:
                raise AssertionError("a silent device must time out")


# ---------------------------------------------------------------- the fixes after the review of the voice redesign

def test_a_warning_from_long_before_the_phrase_does_not_make_open_mic_drop_it():
    voice, log = controller(FakeRecorder(delay=1.0))
    with Push(False):
        voice.switch()
        time.sleep(0.15)
        log["warn_at"] = time.monotonic()                     # a warning played while she was just waiting for the player
        assert wait_for(lambda: log["text"], 4.0), log
    assert log["text"] == ["where is the zombie"] and log["messages"] == []
    voice.cancel()
    voice2, log2 = controller(FakeRecorder(delay=0.6))
    with Push(False):
        voice2.switch()
        time.sleep(0.45)
        log2["warn_at"] = time.monotonic()                    # a warning right before the words: it is in the recording
        assert wait_for(lambda: log2["messages"], 4.0)
    assert log2["text"] == [] and "danger warning" in log2["messages"][0]
    voice2.cancel()


def test_a_loud_start_does_not_deafen_the_recorder_for_ever():
    # her voice (or an explosion) was playing when listening began, then the room went quiet and the player spoke normally
    mic = FakeMic(talk(14, level=0.1) + quiet(200) + talk(30, level=0.06) + quiet(80))
    audio = Recorder(mic).record(_stopped_after(450), 0.5, give_up=False)
    assert audio is not None and rms(audio) > 0.03, "the quiet room after a loud start must be learned"


def test_the_start_tone_of_push_to_talk_plays_after_the_microphone_is_open_not_before():
    mic = FakeMic(talk(20))
    seen = []
    Recorder(mic).record_held(_stopped_after(10), on_open=lambda: seen.append(mic.reads))
    assert seen == [0]                                        # called with the mic open and nothing read yet
    events = []
    recorder = FakeRecorder()
    recorder.events = events
    voice, log = controller(recorder)
    voice.beep = lambda kind: events.append(("beep", kind))
    with Push(True):
        voice.switch()
        events.clear()
        voice.ptt_down()
        assert wait_for(lambda: ("beep", "start") in events)
        voice.ptt_up()
        assert wait_for(lambda: ("beep", "stop") in events)
    assert events.index("open") < events.index(("beep", "start"))             # the tone never delays the first word
    voice.cancel()


def test_a_speech_model_that_cannot_load_switches_voice_off_instead_of_leaving_the_mic_open():
    class BadPreload(FakeTranscriber):
        def preload(self):
            raise VoiceError("I could not load the speech model.")

    voice, log = controller(FakeRecorder(phrases=0), BadPreload())
    with Push(False):
        voice.switch()
        assert wait_for(lambda: voice.state == "off" and log["messages"])
    assert "could not load" in log["messages"][0]


def test_handing_the_state_to_a_busy_window_never_holds_the_lock_so_the_voice_button_cannot_freeze_it():
    voice, log = controller()
    gate, inside = threading.Event(), threading.Event()

    def slow_window(state):                                   # the window thread is busy and cannot take the state yet
        inside.set()
        gate.wait(3)

    voice.on_state = slow_window
    with Push(False):
        voice.switch()
        assert inside.wait(2)                                 # a delivery is now stuck waiting for the window
        got_it = voice._lock.acquire(timeout=1.0)             # the window thread presses the Voice button meanwhile
        assert got_it, "the lock was held while waiting for the window: this is the deadlock"
        voice._lock.release()
        gate.set()
    voice.cancel()


def test_states_reach_the_window_in_the_order_they_happened():
    voice, log = controller(FakeRecorder(phrases=0))
    with Push(False):
        for _ in range(5):                                    # on, off, on, off... as fast as possible
            voice.switch()
            voice.switch()
    assert wait_for(lambda: len(log["states"]) == 10), log["states"]
    assert all(a != b for a, b in zip(log["states"], log["states"][1:]))     # never the same state twice in a row
    assert log["states"][0] == "listening" and log["states"][-1] == "off" and len(log["states"]) == 10


def test_the_push_to_talk_key_only_works_when_voice_is_on_in_push_mode_and_minecraft_is_in_front():
    import main
    chat = mock.Mock(is_typing=lambda: False)
    voice = mock.Mock(is_on=lambda: True)
    with Push(True):
        with mock.patch.object(main, "game_is_in_front", lambda: True):
            assert main.push_to_talk_blocked(chat, voice) is False
            assert main.push_to_talk_blocked(mock.Mock(is_typing=lambda: True), voice) is True        # typing in her box
            assert main.push_to_talk_blocked(chat, mock.Mock(is_on=lambda: False)) is True            # voice is off
        with mock.patch.object(main, "game_is_in_front", lambda: False):
            assert main.push_to_talk_blocked(chat, voice) is True                                     # another app is in front
    with Push(False), mock.patch.object(main, "game_is_in_front", lambda: True):
        assert main.push_to_talk_blocked(chat, voice) is True                                         # open mic: the key is not used


def test_game_is_in_front_is_true_only_for_the_real_minecraft_window():
    import types
    from core import screenshot

    def fake_windows(hwnd, title):
        user32 = types.SimpleNamespace(GetForegroundWindow=lambda: hwnd,
                                       GetWindowTextW=lambda h, buf, n: setattr(buf, "value", title))
        return lambda: (user32, None, None)

    cases = ((5, "Minecraft 1.21 - Singleplayer", r"C:\Java\bin\javaw.exe", True),
             (5, "Minecraft skins - Google Chrome", r"C:\Program Files\chrome.exe", False),
             (0, "", "", False))
    for hwnd, title, program, expected in cases:
        with mock.patch.object(screenshot, "_win_api", fake_windows(hwnd, title)), \
                mock.patch.object(screenshot, "_program_of", lambda h, program=program: program):
            assert screenshot.game_is_in_front() is expected, (title, program)
    with mock.patch.object(screenshot, "_win_api", side_effect=OSError("no windows")):
        assert screenshot.game_is_in_front() is False                        # never raises



# ---------------------------------------------------------------- choosing the microphone

class FakeSd:
    """Stands in for sounddevice: a list of devices as query_devices() gives them, and the default input index."""

    def __init__(self, devices, default_in=0):
        self._devices = devices
        self.default = mock.Mock(device=(default_in, 1))
        self.opened = []

    def query_devices(self):
        return self._devices

    def InputStream(self, **kw):
        self.opened.append(kw)
        stream = mock.Mock()
        return stream


def dev(name, api=0, inputs=1):
    return {"name": name, "hostapi": api, "max_input_channels": inputs}


def test_the_microphone_list_shows_each_input_once_and_skips_speakers_and_other_audio_systems():
    sd = FakeSd([dev("Microsoft Sound Mapper - Input"), dev("Microphone (Realtek)"), dev("Speakers (Realtek)", inputs=0), dev("Headset Mic (USB)"),
                 dev("Microphone (Realtek)", api=1), dev("Headset Mic (USB)", api=2)], default_in=1)
    assert voice_input.input_device_names(sd) == ["Microphone (Realtek)", "Headset Mic (USB)"]
    assert voice_input.input_device_names(None) is not None                       # the real library: a list, whatever the PC has
    broken = mock.Mock()
    broken.query_devices.side_effect = RuntimeError("no audio system")
    assert voice_input.input_device_names(broken) == []                           # never an error


def test_a_saved_microphone_is_found_by_name_and_a_missing_one_means_the_default():
    sd = FakeSd([dev("Microphone (Realtek)"), dev("Headset Mic (USB Audio Device)")])
    assert voice_input.find_input_device("", sd) is None                           # nothing saved: the default
    assert voice_input.find_input_device("Headset Mic (USB Audio Device)", sd) == 1
    assert voice_input.find_input_device("headset mic (usb audio device)", sd) == 1
    assert voice_input.find_input_device("Headset Mic (USB Audio", sd) == 1        # Windows cuts long names short
    with mock.patch.object(voice_input, "logger") as log:
        assert voice_input.find_input_device("Earphones Mic", sd) is None          # unplugged: the default, with a warning
        assert log.warning.called


def test_the_real_microphone_opens_the_chosen_device_and_the_default_when_none_is_chosen():
    sd = FakeSd([dev("Microphone (Realtek)"), dev("Headset Mic (USB)")])
    with mock.patch.dict(sys.modules, {"sounddevice": sd}):
        try:
            settings.update("MIC_DEVICE", "Headset Mic (USB)")
            with voice_input._Microphone():
                pass
            settings.update("MIC_DEVICE", "")
            with voice_input._Microphone():
                pass
            settings.update("MIC_DEVICE", "Something unplugged")
            with voice_input._Microphone():
                pass
        finally:
            settings.reset("MIC_DEVICE")
    assert [call["device"] for call in sd.opened] == [1, None, None]


def test_the_microphone_setting_takes_any_name_trims_it_and_rejects_non_text():
    try:
        assert settings.update("MIC_DEVICE", "  Headset Mic (USB)  ") == "Headset Mic (USB)"
        assert settings.update("MIC_DEVICE", "x" * 500) == "x" * 120
        try:
            settings.update("MIC_DEVICE", 5)
            assert False, "a number is not a microphone name"
        except ValueError:
            pass
    finally:
        settings.reset("MIC_DEVICE")
    assert settings.get("MIC_DEVICE") == ""


def test_picking_another_microphone_while_voice_is_on_reopens_the_microphone():
    import inspect, main
    source = inspect.getsource(main.main)
    assert 'settings.on_change("MIC_DEVICE", voice.ptt_changed)' in source
    mics = []
    class Mic(FakeMic):
        def __init__(self, blocks):
            super().__init__(blocks)
            mics.append(self)
    voice, got = controller(recorder=Recorder(lambda: Mic(quiet(2000))))
    try:
        voice.turn_on()
        wait_for(lambda: len(mics) == 1)
        settings.on_change("MIC_DEVICE", voice.ptt_changed)
        settings.update("MIC_DEVICE", "Headset Mic (USB)")
        wait_for(lambda: len(mics) == 2)                                           # the old loop ended, a new one opened a microphone
    finally:
        voice.cancel()
        settings.reset("MIC_DEVICE")


def test_the_status_window_lists_the_microphones_and_a_pick_is_applied_and_remembered():
    from ui import status_window
    from ui.status_window import StatusWindow, MIC_DEFAULT_LABEL
    with mock.patch.object(voice_input, "input_device_names", lambda sd=None: ["Microphone (Realtek)", "Headset Mic (USB)"]):
        window = StatusWindow({})
        try:
            assert window.mic_var.get() == MIC_DEFAULT_LABEL
            assert list(window._mic_options) == [MIC_DEFAULT_LABEL, "Microphone (Realtek)", "Headset Mic (USB)"]
            window._on_mic_pick("Headset Mic (USB)")
            assert settings.get("MIC_DEVICE") == "Headset Mic (USB)" and window.mic_var.get() == "Headset Mic (USB)"
            window._on_mic_pick(MIC_DEFAULT_LABEL)
            assert settings.get("MIC_DEVICE") == ""
            # a saved microphone that is unplugged stays visible, marked, and is not lost
            settings.update("MIC_DEVICE", "Earphones Mic")
            window._refresh_mics()
            assert window.mic_var.get() == "Earphones Mic (not connected)" and settings.get("MIC_DEVICE") == "Earphones Mic"
            # plugging it in and pressing Refresh shows it as a normal entry
            with mock.patch.object(voice_input, "input_device_names", lambda sd=None: ["Microphone (Realtek)", "Earphones Mic"]):
                window._refresh_mics()
            assert window.mic_var.get() == "Earphones Mic"
        finally:
            settings.reset("MIC_DEVICE")
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
