"""Listening to the player: voice input.

Voice is switched on and off with ONE button in the chat window (or Ctrl+Alt+V) and always starts OFF when the app
starts. While it is ON the player can talk to Maria in one of two ways (setting PUSH_TO_TALK):
  open mic       (default) the microphone stays open; every phrase, ended by a short pause, becomes a chat message
  push to talk   the microphone records only while the player HOLDS a key (PUSH_TO_TALK_KEY, "v" by default)
What was said is turned into text on this PC and goes through exactly the same path as a typed message, so she
answers (and speaks) the same way.

The player can interrupt her: the moment their voice is heard (or the push-to-talk key is pressed) whatever she was
saying stops. Danger warnings are never cut. Her own voice can leak into the microphone when she plays through
speakers, so while she is speaking the player's voice has to be clearly louder to count, and text that only repeats
what she just said is thrown away. A headset avoids the problem completely.

PRIVACY: the microphone is open only while voice is ON (open mic) or while the key is held (push to talk). It is
closed at once when voice is switched off, on Stop, and when the app closes. The sound stays in memory, is never
written to disk and is never sent anywhere: the speech-to-text model runs on this PC. (The model itself is downloaded
once, the first time voice is switched on.)

Small parts so each can be tested without a microphone or a model:
  Recorder        reads the microphone and finds where a phrase starts and stops (a simple loudness rule)
  Transcriber     turns that sound into text (faster-whisper, loaded on first use)
  VoiceInput      the controller: off / ready / listening / transcribing, never raises
  PushToTalkKey   watches the push-to-talk key (pynput)
"""
import queue
import re
import threading
import time
from typing import Callable, List, Optional, Tuple

import numpy as np

from core import settings
from utils.logger import get_logger

logger = get_logger()

SAMPLE_RATE = 16000                 # what Whisper expects
BLOCK_SECONDS = 0.03                # the microphone is read in 30 ms pieces
CALIBRATE_SECONDS = 0.4             # the first moments set the room's background loudness (they are kept: the first word may be in them)
PRE_ROLL_SECONDS = 0.35             # keep this much sound from before the first word was noticed (so it is not cut)
NO_SPEECH_SECONDS = 6.0             # give up when nothing was said for this long after pressing the key
MAX_SECONDS = 20.0                  # never record longer than this in one go
MIN_SPEECH_SECONDS = 0.25           # less voiced sound than this is a click or a cough, not words
SPEECH_FACTOR = 3.0                 # speech = this many times louder than the room...
SPEECH_FLOOR = 0.012                # ...and at least this loud (0..1 scale)
QUIET_FACTOR = 1.8                  # silence = no louder than this many times the room...
QUIET_FLOOR = 0.008                 # ...or this level
ROOM_CAP = 0.03                     # a "room" louder than this is the player already talking, not background noise
READ_TIMEOUT = 1.5                  # the microphone must deliver sound at least this often, or it is reported as stuck
LOUD_BLOCKS = 2                     # this many loud pieces in a row: words have begun
ECHO_FACTOR = 2.5                   # while she is speaking the player must be this much louder (for twice as long) to count
HOLD_MAX_SECONDS = 30.0             # a held key is cut after this long
ROOM_HISTORY_BLOCKS = 100           # the room's loudness is the quiet end of the last ~3 seconds...
ROOM_REFRESH_BLOCKS = 5             # ...worked out again this often, so a long listening keeps up with the room

STATES = ("off", "ready", "listening", "transcribing")


class VoiceError(Exception):
    """Something the player can understand and fix (no microphone, library missing...). Never a crash."""


def rms(block: np.ndarray) -> float:
    """How loud a piece of sound is (0 = silence)."""
    return float(np.sqrt(np.mean(np.square(block, dtype=np.float64)))) if len(block) else 0.0


def voiced_seconds(audio: np.ndarray) -> float:
    """How much of a recording is actually sound (not the pauses around the words)."""
    block = int(SAMPLE_RATE * BLOCK_SECONDS)
    return sum(1 for i in range(0, len(audio), block) if rms(audio[i:i + block]) >= QUIET_FLOOR) * BLOCK_SECONDS


# ============================================================================
# THE MICROPHONE
# ============================================================================

def _input_devices(sd=None) -> List[Tuple[int, str]]:
    """(index, name) of every microphone Windows offers, each once. Windows lists one device under several audio systems
    (MME, DirectSound, WASAPI...), so only the default system's entries are kept. [] when nothing can be listed."""
    try:
        if sd is None:
            import sounddevice as sd
        devices = sd.query_devices()
        default_in = sd.default.device[0]
        api = devices[default_in]["hostapi"] if isinstance(default_in, int) and 0 <= default_in < len(devices) else None
        found, names = [], set()
        for index, info in enumerate(devices):
            if info.get("max_input_channels", 0) < 1 or (api is not None and info.get("hostapi") != api):
                continue
            name = str(info.get("name", "")).strip()
            if name and name not in names and not name.startswith("Microsoft Sound Mapper"):   # (that one is "the default")
                names.add(name)
                found.append((index, name))
        return found
    except Exception as e:                               # no library, no audio system: the list is just empty
        logger.debug(f"Could not list the microphones: {e!r}")
        return []


def input_device_names(sd=None) -> List[str]:
    """The microphone names to show in the Status window."""
    return [name for _, name in _input_devices(sd)]


def find_input_device(name: str, sd=None) -> Optional[int]:
    """The device index for a saved microphone name, or None for "use the Windows default one": no name saved, or that
    microphone is not plugged in right now. (Names are matched exactly first; Windows cuts long names short, so a
    name that starts the other is accepted too.)"""
    if not name:
        return None
    devices = _input_devices(sd)
    wanted = name.casefold()
    for matches in (lambda n: n.casefold() == wanted,
                    lambda n: n.casefold().startswith(wanted) or wanted.startswith(n.casefold())):
        for index, device_name in devices:
            if matches(device_name):
                return index
    logger.warning(f"Microphone '{name}' is not connected, using the Windows default one instead")
    return None


class _Microphone:
    """The real microphone (sounddevice). Used as a context manager; read(n) gives the next 30 ms of float samples,
    16 kHz mono. The device fills a queue from its own thread, so a read can time out instead of blocking for ever
    when the microphone hangs or is unplugged."""

    def __enter__(self):
        try:
            import sounddevice as sd
        except ImportError as e:
            raise VoiceError("The microphone library is not installed (pip install sounddevice).") from e
        self._blocks = queue.Queue()
        self._stream = None
        try:
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                    device=find_input_device(settings.get("MIC_DEVICE"), sd),   # None = the Windows default
                                    blocksize=int(SAMPLE_RATE * BLOCK_SECONDS),
                                    callback=lambda indata, frames, time_info, status: self._blocks.put(indata[:, 0].copy()))
            self._stream = stream
            stream.start()
        except Exception as e:                       # PortAudioError, no device, blocked by Windows privacy settings
            logger.debug(f"Microphone could not be opened: {e!r}")
            self._close()
            raise VoiceError("I can't open the microphone. Check that one is plugged in and that Windows allows "
                             "apps to use it (Settings > Privacy > Microphone).") from e
        return self

    def read(self, frames: int) -> np.ndarray:
        try:
            return np.asarray(self._blocks.get(timeout=READ_TIMEOUT), dtype=np.float32)
        except queue.Empty as e:
            raise VoiceError("The microphone stopped sending sound. Is it still plugged in?") from e

    def _close(self):
        stream, self._stream = getattr(self, "_stream", None), None
        if stream is None:
            return
        for step in (stream.stop, stream.close):     # each in its own try: a failing stop must not skip the close
            try:
                step()
            except Exception as e:
                logger.debug(f"Microphone close step failed: {e!r}")

    def __exit__(self, *exc):
        self._close()


class Recorder:
    """Records one spoken phrase: from the first word until the player pauses."""

    def __init__(self, microphone_factory: Callable = _Microphone):
        self._microphone_factory = microphone_factory

    def record(self, stop_event: threading.Event, silence_seconds: float,
               on_level: Optional[Callable[[float], None]] = None,
               on_speech_start: Optional[Callable[[], None]] = None,
               harder: Optional[Callable[[], bool]] = None, give_up: bool = True) -> Optional[np.ndarray]:
        """The sound of one phrase (float32, 16 kHz, mono), or None when nothing was said or it was only a click.
        Stops when the player pauses for silence_seconds, when stop_event is set (whatever was said so far is kept),
        or after MAX_SECONDS. on_speech_start is called once, the moment words are noticed (the player is
        interrupting). While harder() is true (she is speaking) the voice must be ECHO_FACTOR times louder and last
        twice as long to count. give_up=False waits for ever for the first word (open mic)."""
        block = int(SAMPLE_RATE * BLOCK_SECONDS)
        pre_blocks = max(1, int(PRE_ROLL_SECONDS / BLOCK_SECONDS))
        quiet_needed = max(1, int(silence_seconds / BLOCK_SECONDS))
        with self._microphone_factory() as mic:
            # The first moments tell how loud the room is. The player usually starts talking right after the start
            # tone, so these pieces are kept and go through the same rule as everything after them, and the room
            # level is a low value (the quietest fifth) that is capped, so words in this window are not mistaken for it.
            calibration = [mic.read(block) for _ in range(max(1, int(CALIBRATE_SECONDS / BLOCK_SECONDS)))]
            room = min(float(np.percentile([rms(b) for b in calibration], 20)), ROOM_CAP)
            loud, quiet = max(room * SPEECH_FACTOR, SPEECH_FLOOR), max(room * QUIET_FACTOR, QUIET_FLOOR)
            history = []                                       # recent levels while nobody was speaking
            recent, kept = [], []
            speaking, loud_run, quiet_run, voiced, heard, began = False, 0, 0, 0, 0, 0
            while True:
                if stop_event.is_set():
                    break
                waited = (heard - began) if speaking else heard    # how long a phrase has run / how long nothing was said
                limit = MAX_SECONDS if speaking else (NO_SPEECH_SECONDS if give_up else None)
                if limit is not None and waited * BLOCK_SECONDS > limit:
                    break                                          # (a microphone delivers sound in real time, so
                data = calibration[heard] if heard < len(calibration) else mic.read(block)   # blocks counted = time)
                heard += 1
                level = rms(data)
                if on_level:
                    on_level(level)
                if not speaking:
                    recent = (recent + [data])[-pre_blocks:]
                    history = (history + [level])[-ROOM_HISTORY_BLOCKS:]
                    if heard % ROOM_REFRESH_BLOCKS == 0:       # (a loud start, like her own voice, fades from the room level)
                        room = min(float(np.percentile(history, 20)), ROOM_CAP)
                        loud, quiet = max(room * SPEECH_FACTOR, SPEECH_FLOOR), max(room * QUIET_FACTOR, QUIET_FLOOR)
                    echo_risk = bool(harder and harder())              # she is speaking: her voice may be in the sound
                    loud_run = loud_run + 1 if level > loud * (ECHO_FACTOR if echo_risk else 1.0) else 0
                    if loud_run >= LOUD_BLOCKS * (2 if echo_risk else 1):  # a few loud pieces in a row: words have begun
                        speaking, kept, quiet_run, voiced, began = True, list(recent), 0, loud_run, heard
                        if on_speech_start:
                            try:
                                on_speech_start()
                            except Exception as e:
                                logger.debug(f"on_speech_start failed: {e}")
                    continue
                kept.append(data)
                if level >= quiet:
                    voiced += 1
                quiet_run = quiet_run + 1 if level < quiet else 0
                if quiet_run >= quiet_needed:
                    break
        if not kept or voiced * BLOCK_SECONDS < MIN_SPEECH_SECONDS:        # nothing said, or only a click or a cough
            return None
        return np.concatenate(kept)

    def record_held(self, stop_event: threading.Event, on_level: Optional[Callable[[float], None]] = None,
                    on_open: Optional[Callable[[], None]] = None) -> Optional[np.ndarray]:
        """Push to talk: everything between the key going down and stop_event being set (the key coming up), kept
        whole: pauses do not end it. None when it was only a click or nothing."""
        block = int(SAMPLE_RATE * BLOCK_SECONDS)
        kept, voiced = [], 0
        with self._microphone_factory() as mic:
            if on_open:
                try:
                    on_open()                                      # the microphone is listening: nothing is lost from here
                except Exception as e:
                    logger.debug(f"on_open failed: {e}")
            while not stop_event.is_set() and len(kept) * BLOCK_SECONDS < HOLD_MAX_SECONDS:
                data = mic.read(block)
                kept.append(data)
                level = rms(data)
                if on_level:
                    on_level(level)
                if level >= QUIET_FLOOR:
                    voiced += 1
        if not kept or voiced * BLOCK_SECONDS < MIN_SPEECH_SECONDS:
            return None
        return np.concatenate(kept)


# ============================================================================
# SPEECH TO TEXT
# ============================================================================

# What Whisper tends to "hear" in near-silence (it was trained on videos): never treat these as the player's words
_PHANTOM = {"you", "thank you", "thanks for watching", "bye", "thank you for watching", "the end"}


def clean_transcript(text: str, seconds: float = 3.0) -> str:
    """Tidy what the speech model returned. `seconds` is how much real sound there was. Empty when it was only
    noise tags, punctuation, or a phrase the model invents out of near-silence."""
    text = re.sub(r"[\[\(][^\]\)]{0,40}[\]\)]", " ", text or "")       # [BLANK_AUDIO], (music), [noise]
    text = re.sub(r"\s+", " ", text).strip()
    if not re.search(r"[A-Za-z0-9]", text):
        return ""
    if seconds < 2.0 and text.lower().strip(" .!") in _PHANTOM:
        return ""
    return text


class Transcriber:
    """Turns sound into text with a local Whisper model (faster-whisper). The model loads on first use (a few
    seconds; the very first time it also downloads, about 150 MB for base.en), so call preload() when voice mode
    is switched on."""

    def __init__(self, model_loader: Optional[Callable[[str], object]] = None):
        self._loader = model_loader or self._load_whisper
        self._model, self._model_name = None, None
        self._lock = threading.Lock()

    @staticmethod
    def _load_whisper(name: str):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise VoiceError("The speech-to-text library is not installed (pip install faster-whisper).") from e
        try:
            return WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4)
        except Exception as e:
            logger.warning(f"Could not load the speech model '{name}': {e!r}")
            raise VoiceError(f"I could not load the speech model '{name}'. The first use needs an internet "
                             "connection to download it once.") from e

    def preload(self):
        """Load the model now (call it from a background thread)."""
        self._ready_model()

    def _ready_model(self):
        name = settings.get("STT_MODEL")
        with self._lock:
            if self._model is None or self._model_name != name:
                logger.info(f"🎤 Loading the speech-to-text model '{name}' (first use takes a few seconds)...")
                self._model, self._model_name = self._loader(name), name
                logger.info("🎤 Speech-to-text model ready")
            return self._model

    def transcribe(self, audio: np.ndarray) -> str:
        model = self._ready_model()
        segments, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=True,
                                       condition_on_previous_text=False, temperature=0.0)
        text = " ".join(segment.text.strip() for segment in segments)
        return clean_transcript(text, seconds=voiced_seconds(audio))


# ============================================================================
# THE CONTROLLER
# ============================================================================

class VoiceInput:
    """Voice on/off. While on: open mic (every phrase is sent) or push to talk (hold a key). The text is handed to
    on_text. All callbacks run on background threads: the user interface must hand them over to its own thread.

    States (on_state): "off", "ready" (on, push to talk, waiting for the key), "listening", "transcribing"."""

    def __init__(self, on_text: Callable[[str], None], on_state: Optional[Callable[[str], None]] = None,
                 on_message: Optional[Callable[[str], None]] = None, recorder: Optional[Recorder] = None,
                 transcriber: Optional[Transcriber] = None, stop_speaking: Optional[Callable[[], None]] = None,
                 beep: Optional[Callable[[str], None]] = None, warned_since: Optional[Callable[[float], bool]] = None,
                 maria_speaking: Optional[Callable[[], bool]] = None, is_echo: Optional[Callable[[str], bool]] = None):
        self.on_text = on_text
        self.on_state = on_state or (lambda state: None)
        self.on_message = on_message or (lambda message: None)
        self.recorder = recorder or Recorder()
        self.transcriber = transcriber or Transcriber()
        self.stop_speaking = stop_speaking or (lambda: None)     # the player's voice cuts her chatter off
        self.beep = beep or (lambda kind: None)
        self.warned_since = warned_since or (lambda t: False)    # did a danger warning play since t? (the mic heard it)
        self.maria_speaking = maria_speaking or (lambda: False)  # is she talking now? (her voice may leak into the mic)
        self.is_echo = is_echo or (lambda text: False)           # does this text only repeat what she just said?
        self._on = False                                         # always starts OFF: never saved between runs
        self._lock = threading.RLock()
        self._epoch = 0                                          # bumped on every switch: older work is dropped
        self._session_stop = threading.Event()                   # ends the open-mic loop
        self._hold_stop = threading.Event()                      # ends the recording of a held key
        self._holding = False
        self._busy = False
        self._work = queue.Queue()
        self._worker = None
        self._last_state = None
        self._state_queue = queue.Queue()                        # states waiting to be handed to the user interface, in order
        self._state_thread = None

    # ---- state ----

    def is_on(self) -> bool:
        return self._on

    @property
    def state(self) -> str:
        if not self._on:
            return "off"
        if not settings.get("PUSH_TO_TALK"):
            return "listening"                                   # the microphone is open the whole time
        if self._holding:
            return "listening"
        return "transcribing" if self._busy else "ready"

    def _publish(self, force: bool = False):
        """Tell the user interface the state changed. The state is worked out and queued under the lock (so the order
        is right) but handed over by one delivery thread WITHOUT the lock: the window thread may be waiting for this
        lock (the Voice button) while the delivery waits for the window thread, and that must never be a deadlock."""
        with self._lock:
            state = self.state
            if state == self._last_state and not force:
                return
            self._last_state = state
            self._state_queue.put(state)
            if self._state_thread is None or not self._state_thread.is_alive():
                self._state_thread = threading.Thread(target=self._deliver_states, daemon=True, name="VoiceStates")
                self._state_thread.start()

    def _deliver_states(self):
        while True:
            state = self._state_queue.get()
            try:
                self.on_state(state)
            except Exception as e:
                logger.debug(f"Voice state callback failed: {e}")

    def _say(self, message: str):
        try:
            self.on_message(message)
        except Exception as e:
            logger.debug(f"Voice message callback failed: {e}")

    # ---- on / off ----

    def switch(self) -> bool:
        """The button and Ctrl+Alt+V: turn voice on, or off. Returns whether it is on now."""
        with self._lock:
            now_on = not self._on
        if now_on:
            self.turn_on()
        else:
            self.turn_off()
        return now_on

    def turn_on(self):
        with self._lock:
            if self._on:
                return
            self._on = True
            self._epoch += 1
            epoch, stop = self._epoch, threading.Event()
            self._session_stop = stop
            open_mic = not settings.get("PUSH_TO_TALK")
        self.preload()
        self.beep("start")
        if open_mic:
            self._start_loop(epoch, stop)
        self._publish()

    def turn_off(self, quiet: bool = False):
        """Voice off: the microphone closes at once and anything not yet sent is thrown away."""
        with self._lock:
            if not self._on:
                return
            self._on = False
            self._epoch += 1
            self._session_stop.set()
            self._hold_stop.set()
        if not quiet:
            self.beep("stop")
        self._publish()

    def cancel(self):
        """Stop, and on exit: the microphone closes at once, nothing is transcribed or sent."""
        self.turn_off(quiet=True)

    def ptt_changed(self, value=None):
        """Push to talk was switched (or its key changed) while voice is on: change how the microphone is used."""
        with self._lock:
            if not self._on:
                return
            self._epoch += 1
            self._session_stop.set()                             # the old open-mic loop (if any) ends and the mic closes
            self._hold_stop.set()
            epoch, self._session_stop = self._epoch, threading.Event()
            stop = self._session_stop
            open_mic = not settings.get("PUSH_TO_TALK")
        if open_mic:
            self._start_loop(epoch, stop)
        self._publish(force=True)

    def preload(self):
        """Load the speech model in the background so the first phrase is not slow."""
        epoch = self._epoch

        def work():
            try:
                self.transcriber.preload()
            except VoiceError as e:
                self._say(str(e))
                self._end_session(epoch)                         # nothing can be understood: do not sit with the mic open
            except Exception as e:
                logger.warning(f"Speech model preload failed: {e}")
        threading.Thread(target=work, daemon=True, name="VoicePreload").start()

    def _end_session(self, epoch: int):
        """Something went wrong that repeating would not fix: voice goes off (the player has been told)."""
        if self._epoch == epoch:
            self.turn_off(quiet=True)

    def _barge_in(self):
        """The player started to talk (or pressed the key): she stops, so she does not talk over them."""
        try:
            self.stop_speaking()
        except Exception as e:
            logger.debug(f"stop_speaking failed: {e}")

    # ---- open mic ----

    def _start_loop(self, epoch: int, stop: threading.Event):
        threading.Thread(target=self._loop, args=(epoch, stop), daemon=True, name="VoiceListen").start()

    def _loop(self, epoch: int, stop: threading.Event):
        try:
            while not stop.is_set() and self._epoch == epoch:
                words_began = []

                def first_word():
                    # (the recording starts a little before the words were noticed; the wait before them can last for
                    # ever, so a warning from long before must not count)
                    words_began.append(time.monotonic() - PRE_ROLL_SECONDS - 3 * BLOCK_SECONDS)
                    self._barge_in()

                audio = self.recorder.record(stop, settings.get("VOICE_SILENCE_SECONDS"),
                                             on_speech_start=first_word, harder=self.maria_speaking, give_up=False)
                if stop.is_set() or self._epoch != epoch:
                    return                                       # switched off: whatever was heard is dropped
                if audio is not None:
                    self._submit(epoch, audio, words_began[0] if words_began else time.monotonic() - 1.0)
                else:
                    time.sleep(0.02)
        except VoiceError as e:
            logger.warning(f"Voice input: {e}")
            self._say(str(e))
            self._end_session(epoch)
        except Exception as e:
            logger.error(f"Voice input failed: {e}", exc_info=True)
            self._say("Voice input hit a problem and was switched off. Details are in the log.")
            self._end_session(epoch)

    # ---- push to talk (the key listener calls these) ----

    def ptt_down(self):
        with self._lock:
            if not self._on or not settings.get("PUSH_TO_TALK") or self._holding:
                return
            self._holding = True
            self._hold_stop = stop = threading.Event()
            epoch = self._epoch
        self._publish()
        threading.Thread(target=self._hold, args=(epoch, stop), daemon=True, name="VoiceHold").start()

    def ptt_up(self):
        with self._lock:
            if self._holding:
                self._hold_stop.set()

    def _hold(self, epoch: int, stop: threading.Event):
        began = time.monotonic()
        try:
            self._barge_in()
            # The tone is played once the microphone is open (and on its own thread, it takes ~70 ms), so the first
            # syllable is not lost waiting for it
            audio = self.recorder.record_held(
                stop, on_open=lambda: threading.Thread(target=self.beep, args=("start",), daemon=True).start())
            self.beep("stop")
            if self._epoch != epoch:
                return
            if audio is None:
                self._say("I didn't hear anything. Hold the key while you speak.")
                return
            self._submit(epoch, audio, began)
        except VoiceError as e:
            logger.warning(f"Voice input: {e}")
            self._say(str(e))
            self._end_session(epoch)
        except Exception as e:
            logger.error(f"Voice input failed: {e}", exc_info=True)
            self._say("Voice input hit a problem and was switched off. Details are in the log.")
            self._end_session(epoch)
        finally:
            with self._lock:
                self._holding = False
            self._publish()

    # ---- from sound to a chat message ----

    def _submit(self, epoch: int, audio: np.ndarray, began: float):
        if self._epoch != epoch:
            return
        if self.warned_since(began):
            self._say("A danger warning played while I was listening, so I dropped that. Say it again.")
            return                                               # the warning is in the recording: never send it as the player's words
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work_loop, daemon=True, name="VoiceUnderstand")
                self._worker.start()
        self._work.put((epoch, audio))

    def _work_loop(self):
        while True:
            epoch, audio = self._work.get()
            if epoch != self._epoch:
                continue
            self._busy = True
            self._publish()
            try:
                text = self.transcriber.transcribe(audio)
                if epoch != self._epoch:
                    continue                                     # switched off while it was being understood
                if not text:
                    if settings.get("PUSH_TO_TALK"):
                        self._say("I couldn't make that out. Try again a little closer to the microphone.")
                    else:
                        logger.debug("Voice input: nothing recognised in a sound")
                    continue
                if self.is_echo(text):
                    logger.info(f"🎤 Ignored (it was her own voice): {text}")
                    continue
                logger.info(f"🎤 Heard: {text}")
                self.on_text(text)
            except VoiceError as e:
                logger.warning(f"Voice input: {e}")
                self._say(str(e))
                self._end_session(epoch)
            except Exception as e:
                logger.error(f"Voice input failed: {e}", exc_info=True)
                self._say("Voice input hit a problem. Details are in the log.")
            finally:
                self._busy = False
                self._publish()


def beep(kind: str):
    """A short tone so the player knows without looking: high = listening, lower = stopped listening."""
    if not settings.get("VOICE_BEEPS"):
        return
    try:
        import winsound
        winsound.Beep(1000 if kind == "start" else 650, 70)
    except Exception:
        pass


def _modifier_keys_down() -> bool:
    """Is Ctrl, Alt (AltGr is both) or a Windows key held right now? Asks Windows itself, so a key release that was
    missed (Alt-Tab, the lock screen) can never leave this stuck."""
    try:
        import ctypes
        get = ctypes.windll.user32.GetAsyncKeyState
        return any(get(vk) & 0x8000 for vk in (0x11, 0x12, 0x5B, 0x5C))     # Ctrl, Alt, left Win, right Win
    except Exception:
        return False


class PushToTalkKey:
    """Watches one key anywhere on the PC (pynput) and tells on_down / on_up when it goes down and up. The key is
    the PUSH_TO_TALK_KEY setting ("v", "b", "f8", "f9"). It is ignored when `blocked()` says so (the player is typing
    in Maria's chat box, Minecraft is not the active window...), and when Ctrl, Alt or a Windows key is held (so
    Ctrl+Alt+V still just switches voice on and off). The key that went down is the key whose release ends it, even if
    the setting was changed in between."""

    def __init__(self, on_down: Callable[[], None], on_up: Callable[[], None],
                 blocked: Optional[Callable[[], bool]] = None, listener_factory: Optional[Callable] = None,
                 modifiers_down: Optional[Callable[[], bool]] = None):
        self.on_down, self.on_up = on_down, on_up
        self.blocked = blocked or (lambda: False)
        self._factory = listener_factory
        self._modifiers_down = modifiers_down or _modifier_keys_down
        self._listener = None
        self._down = False
        self._down_key = None

    @staticmethod
    def _name(key) -> str:
        char = getattr(key, "char", None)
        return char.lower() if isinstance(char, str) and char else str(getattr(key, "name", "")).lower()

    def _is_the_key(self, key) -> bool:
        return self._name(key) == str(settings.get("PUSH_TO_TALK_KEY")).lower()

    def press(self, key):
        try:
            if (self._is_the_key(key) and not self._down and not self._modifiers_down() and not self.blocked()):
                self._down, self._down_key = True, self._name(key)
                self.on_down()
        except Exception as e:
            logger.debug(f"Push-to-talk key press failed: {e}")

    def release(self, key):
        try:
            if self._down and self._name(key) == self._down_key:
                self._down, self._down_key = False, None
                self.on_up()
        except Exception as e:
            logger.debug(f"Push-to-talk key release failed: {e}")

    def start(self):
        try:
            factory = self._factory
            if factory is None:
                from pynput import keyboard
                factory = keyboard.Listener
            self._listener = factory(on_press=self.press, on_release=self.release)
            self._listener.start()
        except Exception as e:
            logger.error(f"Could not start the push-to-talk key listener: {e}")

    def stop(self):
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception as e:
                logger.debug(f"Could not stop the push-to-talk key listener: {e}")
