import atexit
import collections
import re
import shutil
import tempfile
import threading
from utils.logger import get_logger
import time
import queue
import os
import uuid
import wave
import struct
import winsound
import requests

from piper import PiperVoice
from piper.config import SynthesisConfig
from core.settings import get as get_setting
from core import speech_state

logger = get_logger()

# --- TTS Queue ---
_speech_queue = queue.Queue()
_tts_worker_thread = None
_tts_engine_ready = True

# --- Piper (base TTS) config ---
PIPER_MODEL_PATH = "en_US-amy-medium.onnx"
# Her own trained voice: two speakers in one model, "calm" for normal lines and "scared" for danger warnings
MARIA_MODEL_PATH = "maria.onnx"
MARIA_SPEAKERS = {"talk": "calm", "alarm": "scared"}      # mood -> speaker name in maria.onnx.json
MARIA_SPEAKER_IDS = {"calm": 0, "scared": 1}               # used when the json has no speaker names

SENTENCE_SILENCE = 0.2

# --- Voice styles (picked by TTS_VOICE in the Status window) ---
# Each style has a normal line ("calm") and a danger warning ("alarm"):
#   speed     how fast Piper speaks (multiplies the Speed slider)
#   noise     Piper's variation in pitch and rhythm (higher = more varied)
#   noise_w   Piper's variation in word timing
#   rate      then the sound is played this much faster AND higher (1.1 = 10% more energetic)
#   volume    multiplies the Volume slider
def _style(speed, noise, noise_w, rate, volume=1.0):
    return {"speed": speed, "noise": noise, "noise_w": noise_w, "rate": rate, "volume": volume}


VOICE_STYLES = {
    "now": {"calm": _style(1.0, 1.0, 1.5, 1.0), "alarm": _style(1.0, 1.0, 1.5, 1.0)},
    "livelier": {"calm": _style(1.08, 0.8, 1.0, 1.06), "alarm": _style(1.15, 0.8, 1.0, 1.14, 1.25)},
    "bright": {"calm": _style(1.1, 0.9, 1.2, 1.12), "alarm": _style(1.2, 0.9, 1.2, 1.22, 1.3)},
}


def voice_style(mood: str = "talk") -> dict:
    """The current voice style for a line: the danger version for mood "alarm", the normal one otherwise."""
    styles = VOICE_STYLES.get(get_setting("TTS_VOICE"), VOICE_STYLES["now"])
    return styles["alarm" if mood == "alarm" else "calm"]

# --- RVC (Maria voice conversion) — DISABLED for now, kept for future re-enable ---
# Reason: RVC ran resident on cuda:0 alongside Ollama, splitting a 6GB GPU
# between two consumers. Removing it frees the whole GPU for Ollama again.
# To re-enable: uncomment this block AND the RVC calls inside speak() below.
#
# RVC_SERVER_URL = "http://127.0.0.1:5050"
# RVC_CONVERT_ENDPOINT = f"{RVC_SERVER_URL}/convert_file"
# RVC_TIMEOUT = 60  # seconds — first request after server start can be slow (rmvpe model lazy-loads)

# --- Danger warnings: urgent speech and a small cache of finished warning clips ---
_synthesis_lock = threading.Lock()   # one synthesis at a time: warm-up, chat and warnings share the model and phonemizer
_urgent_waiting = threading.Event()   # set while a danger warning is queued; older chatter is then dropped, not spoken
_warning_cache = {}                   # (line, voice settings) -> finished wav, so a repeated warning starts at once
_WARNING_CACHE_MAX = 60
_cache_dir = None
_cache_lock = threading.Lock()


def _cache_folder() -> str:
    global _cache_dir
    with _cache_lock:
        if _cache_dir is None:
            _cache_dir = tempfile.mkdtemp(prefix="maria_warnings_")
            atexit.register(shutil.rmtree, _cache_dir, True)
        return _cache_dir


def _warning_key(text: str, style: dict) -> tuple:
    """Everything that changes how a line sounds, so a changed setting never plays an old clip."""
    return (text, voice_target("alarm"), tuple(sorted(style.items())), get_setting("TTS_SPEED"), get_setting("TTS_VOLUME"))


def _remember_warning(key: tuple, wav_path: str):
    """Keep a copy of a finished warning clip (the newest _WARNING_CACHE_MAX of them)."""
    try:
        saved = os.path.join(_cache_folder(), f"{uuid.uuid4().hex[:10]}.wav")
        shutil.copyfile(wav_path, saved)
        with _cache_lock:
            _warning_cache[key] = saved
            while len(_warning_cache) > _WARNING_CACHE_MAX:
                old = _warning_cache.pop(next(iter(_warning_cache)))
                try:
                    os.remove(old)
                except OSError:
                    pass
    except Exception as e:
        logger.debug(f"Could not cache a warning clip: {e}")


_stop_requested_at = 0.0   # when the player last asked her to be quiet (stop_speaking): older chatter is dropped
_recent_spoken = collections.deque(maxlen=8)   # (monotonic time, text) of the lines she began to say: the echo guard
_queue_lock = threading.Lock()   # one thread at a time drains and refills the speech queue
_last_alarm_started = 0.0        # when a danger warning last began to play (the microphone may have heard it)
_silence_path = None       # a few milliseconds of silence as a file, made on first use (see _silence_now)


def _cut_requested(mood: str, since: float) -> bool:
    """Must the line that began at monotonic time `since` be dropped or cut off now? Chatter yields to a danger
    warning that is waiting and to "be quiet" (the player started to talk). A danger warning yields to nothing:
    it is never dropped or cut off."""
    if mood == "alarm":
        return False
    return _urgent_waiting.is_set() or _stop_requested_at > since


def _silence_now():
    """Stop the sound that is playing. A plain PlaySound call cannot be interrupted from another thread (measured:
    it plays to the end), so playback is asynchronous and the speaking thread itself stops it: with the purge
    call, and by starting a few milliseconds of silence from a file, which replaces the sound that was playing."""
    global _silence_path
    try:
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass
    try:
        if _silence_path is None or not os.path.exists(_silence_path):
            path = os.path.join(_cache_folder(), "silence.wav")
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(b"\x00\x00" * 110)
            _silence_path = path
        winsound.PlaySound(_silence_path, winsound.SND_FILENAME | winsound.SND_ASYNC)   # replaces the playing sound
    except Exception as e:
        logger.debug(f"Could not play the stop-silence: {e}")


def _play(path: str, mood: str, since: float):
    """Play a wav file and wait for it to end, or cut it off at once when a warning is waiting or the player asked
    for quiet. Returns True when it played to the end."""
    with wave.open(path, "rb") as wav:
        seconds = wav.getnframes() / wav.getframerate()
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    end = time.monotonic() + seconds + 0.25            # a margin: the device can take a moment to start
    while time.monotonic() < end:
        if _cut_requested(mood, since):
            _silence_now()
            return False
        time.sleep(0.02)
    return True


def warm_up():
    """Load the voice model and run one short synthesis, so the FIRST warning of a session does not pay for
    the model loading (it can take seconds). Run once in a background thread at startup."""
    try:
        scratch = os.path.join(_cache_folder(), "warm.wav")
        _generate_piper_wav("Ready.", scratch, voice_style("alarm"), **_voice_kwargs("alarm"))
        try:
            os.remove(scratch)
        except OSError:
            pass
        logger.info("🗣️ Voice warmed up: the first warning will not wait for the model to load")
    except Exception as e:
        logger.warning(f"Voice warm-up failed (not important): {e}")


# --- Lazy-loaded Piper voices (each model is loaded once, on first use, and reused) ---
_piper_voices = {}        # model path -> loaded voice (the standard voice and her own are each loaded once, on first use)
_voice_load_lock = threading.Lock()
_synthesis_fields = None  # field names this Piper version's SynthesisConfig accepts


def maria_available() -> bool:
    """Is her own trained voice installed (maria.onnx and its json next to the app)?"""
    return os.path.exists(MARIA_MODEL_PATH) and os.path.exists(MARIA_MODEL_PATH + ".json")


def voice_target(mood: str = "talk"):
    """(model file, speaker name or None) for a line. Her own voice when it is chosen and installed, otherwise the
    standard voice, whatever the setting says."""
    if get_setting("TTS_MODEL") == "maria" and maria_available():
        return MARIA_MODEL_PATH, MARIA_SPEAKERS["alarm" if mood == "alarm" else "talk"]
    return PIPER_MODEL_PATH, None


def _voice_kwargs(mood: str) -> dict:
    """Extra arguments for _generate_piper_wav: none for the standard voice (that path is unchanged)."""
    model_path, speaker_name = voice_target(mood)
    return {} if model_path == PIPER_MODEL_PATH else {"model_path": model_path, "speaker_name": speaker_name}


def _speaker_id(voice, speaker_name):
    """The numeric speaker for a name in this model (None for a single-speaker voice)."""
    if speaker_name is None:
        return None
    names = getattr(getattr(voice, "config", None), "speaker_id_map", None) or {}
    return names.get(speaker_name, MARIA_SPEAKER_IDS.get(speaker_name))


def _get_piper_voice(model_path: str = None):
    """Load a Piper voice model once and reuse it for every call (the standard voice by default)."""
    global _synthesis_fields
    model_path = model_path or PIPER_MODEL_PATH
    with _voice_load_lock:
        if model_path not in _piper_voices:
            logger.info(f"🗣️ Loading Piper voice model: {model_path}")
            _piper_voices[model_path] = PiperVoice.load(model_path)
        voice = _piper_voices[model_path]

        if _synthesis_fields is None:
            import inspect
            _synthesis_fields = set(inspect.signature(SynthesisConfig.__init__).parameters.keys())

    return voice


def _build_synthesis_config(style: dict = None, speaker_id: int = None):
    """Build the Piper config for ONE line of speech. Built per call (it is
    cheap) so a TTS_SPEED or voice change from the Status window applies to the very next
    line, with no cache to invalidate. Call _get_piper_voice() first."""
    style = style or voice_style()
    # Piper's length_scale is the inverse of speed: smaller = faster speech.
    # TTS_SPEED 1.0 = normal, 1.5 = 50% faster. core/settings.py limits it to 0.5-2.0.
    speed = get_setting("TTS_SPEED") * style["speed"]
    length_scale = 1.0 / speed if speed > 0 else 1.0

    valid_fields = _synthesis_fields or set()
    kwargs = {}
    if "length_scale" in valid_fields:
        kwargs["length_scale"] = length_scale
    if "noise_scale" in valid_fields:
        kwargs["noise_scale"] = style["noise"]
    if "noise_w" in valid_fields:
        kwargs["noise_w"] = style["noise_w"]
    elif "noise_scale_w" in valid_fields:
        kwargs["noise_scale_w"] = style["noise_w"]
    if "sentence_silence" in valid_fields:
        kwargs["sentence_silence"] = SENTENCE_SILENCE
    if speaker_id is not None and "speaker_id" in valid_fields:
        kwargs["speaker_id"] = speaker_id

    return SynthesisConfig(**kwargs)


# --- RVC health check — DISABLED, kept for future re-enable ---
# def _check_rvc_server() -> bool:
#     """Check if the RVC API server is reachable."""
#     try:
#         response = requests.get(f"{RVC_SERVER_URL}/params", timeout=3)
#         return response.status_code == 200
#     except requests.exceptions.RequestException:
#         return False


def _generate_piper_wav(text: str, piper_wav_path: str, style: dict = None,
                        model_path: str = None, speaker_name: str = None) -> bool:
    """Generate base speech audio with Piper, with the given voice model and speaker (the standard voice by
    default). Returns True on success."""
    if not any(ch.isalnum() for ch in (text or "")):
        logger.debug(f"TTS: nothing to pronounce in {text!r}, skipping")   # "...", "!!!", "-": Piper makes no sound for these
        return False
    try:
        with _synthesis_lock:
            if model_path is None:
                voice = _get_piper_voice()
                syn_config = _build_synthesis_config(style)
            else:
                voice = _get_piper_voice(model_path)
                syn_config = _build_synthesis_config(style, _speaker_id(voice, speaker_name))
            with wave.open(piper_wav_path, "wb") as wav_file:
                voice.synthesize_wav(text, wav_file, syn_config=syn_config)
        return True
    except Exception as e:
        logger.error(f"Piper synthesis failed: {e}", exc_info=True)
        return False


def _apply_volume(input_path: str, output_path: str, volume: float, rate: float = 1.0) -> bool:
    """
    Scale a 16-bit PCM wav's loudness by `volume` (1.0 = unchanged), play it `rate` times faster and
    higher (1.0 = unchanged; done by raising the sample rate in the file header), and
    write the result to output_path. Uses only the stdlib `wave` + `struct`
    modules (not `audioop`, which is deprecated/removed in newer Python
    versions) so this stays reliable long-term.
    Returns True on success.
    """
    try:
        with wave.open(input_path, "rb") as wav_in:
            params = wav_in.getparams()
            frames = wav_in.readframes(wav_in.getnframes())

        if params.sampwidth != 2:
            # Piper always outputs 16-bit PCM; if that ever changes, skip
            # scaling rather than risk corrupting the audio.
            logger.warning(f"TTS volume: unexpected sample width {params.sampwidth}, skipping scaling")
            return False

        sample_count = len(frames) // 2
        samples = struct.unpack(f"<{sample_count}h", frames)
        scaled = [max(-32768, min(32767, int(s * volume))) for s in samples]
        scaled_frames = struct.pack(f"<{sample_count}h", *scaled)

        with wave.open(output_path, "wb") as wav_out:
            wav_out.setparams(params)
            wav_out.setframerate(int(params.framerate * rate))
            wav_out.writeframes(scaled_frames)
        return True
    except Exception as e:
        logger.error(f"TTS volume scaling failed: {e}", exc_info=True)
        return False



# def _convert_with_rvc(piper_wav_path: str, output_wav_path: str) -> bool:
#     """Send the Piper WAV to the RVC server and save Maria's converted voice. Returns True on success."""
#     try:
#         with open(piper_wav_path, "rb") as f:
#             files = {"file": (os.path.basename(piper_wav_path), f, "audio/wav")}
#             response = requests.post(RVC_CONVERT_ENDPOINT, files=files, timeout=RVC_TIMEOUT)
#
#         response.raise_for_status()
#
#         with open(output_wav_path, "wb") as out_f:
#             out_f.write(response.content)
#         return True
#     except requests.exceptions.ConnectionError:
#         logger.error(f"❌ Cannot connect to RVC server at {RVC_SERVER_URL}. Is it running?")
#         return False
#     except requests.exceptions.Timeout:
#         logger.error("❌ RVC server request timed out.")
#         return False
#     except Exception as e:
#         logger.error(f"RVC conversion failed: {e}", exc_info=True)
#         return False


def speak(text: str, mood: str = "talk", captured_at: float = None):
    """
    (Internal Use by Worker Thread)
    `mood` ("talk" or "alarm") picks the face Maria shows on screen while this line plays.
    Generates speech using Piper and plays it directly (BLOCKING).
    RVC voice conversion is currently DISABLED — see commented code below
    for how to re-enable Maria's converted voice.
    Uses a unique filename per call to avoid Windows file-lock issues from
    the previous playback still holding a handle open.
    """
    if mood == "alarm":
        _urgent_waiting.clear()                     # this warning is the one that was waiting (cleared first, whatever happens next)

    if not _tts_engine_ready:
        logger.warning(f"TTS Engine unavailable, skipping speak: {text}")
        return

    # Muted: also drops lines that were already queued before the mute was switched on
    if get_setting("TTS_MUTED"):
        logger.debug(f"TTS muted, skipping: '{text}'")
        return

    # Read the settings now (not at import) so a change applies to this very line
    style = voice_style(mood)
    volume = get_setting("TTS_VOLUME") * style["volume"]
    rate = style["rate"]
    # A danger warning heard before with the same voice settings is played from the cache: no wait to make it
    key = _warning_key(text, style) if mood == "alarm" else None
    cached = _warning_cache.get(key) if key else None
    if cached and not os.path.exists(cached):
        cached = None

    since = time.monotonic()
    if _cut_requested(mood, since):
        logger.debug("TTS: dropping a line before making it, a danger warning is waiting or quiet was asked")
        return

    # Unique filename per call — avoids Windows file-lock conflicts
    call_id = uuid.uuid4().hex[:8]
    piper_wav_path = f"piper_output_{call_id}.wav"
    volume_wav_path = f"piper_output_{call_id}_vol.wav"
    # output_wav_path = f"maria_output_{call_id}.wav"  # RVC output path — unused while RVC is disabled

    try:
        logger.debug(f"TTS Worker generating speech for: '{text}'")

        # Stage 1: Piper generates speech audio
        if cached:
            logger.debug("TTS: playing a cached warning clip")
        elif not _generate_piper_wav(text, piper_wav_path, style, **_voice_kwargs(mood)):
            return

        # --- RVC conversion step — DISABLED, kept for future re-enable ---
        # Stage 2: RVC server converts it to Maria's voice
        # if not _convert_with_rvc(piper_wav_path, output_wav_path):
        #     return
        # winsound.PlaySound(output_wav_path, winsound.SND_FILENAME)

        # Stage 2: apply volume (TTS_VOLUME from config.py). Skip the extra
        # read/write pass entirely when volume is left at the default —
        # no reason to pay that cost on every single line spoken.
        playback_path = cached or piper_wav_path
        if not cached and (volume != 1.0 or rate != 1.0):
            if _apply_volume(piper_wav_path, volume_wav_path, volume, rate):
                playback_path = volume_wav_path
            else:
                logger.warning("TTS volume scaling failed — playing at original volume")
        if key and not cached:
            _remember_warning(key, playback_path)

        # Mute may have been switched on while this line was being generated
        if get_setting("TTS_MUTED"):
            logger.debug("TTS muted during synthesis, not playing")
            return
        # A danger warning arrived (or the player asked for quiet) while this was being made: drop it
        if _cut_requested(mood, since):
            logger.debug("TTS: dropping a line, a danger warning is waiting or quiet was asked")
            return

        logger.debug(f"TTS Worker playing audio (Piper voice, volume={volume})...")
        # winsound doesn't hold a lingering lock on the file like playsound's
        # MCI backend does on Windows, so we don't hit PermissionError on cleanup.
        # Tell the on-screen face she is speaking, with the loudness over time for its mouth
        if captured_at:
            logger.info(f"⏱️  {'Danger warning' if mood == 'alarm' else 'Line'} started "
                        f"{time.time() - captured_at:.2f}s after the picture was taken")
        envelope = speech_state.envelope_from_wav(playback_path)
        if _cut_requested(mood, since):                           # it arrived while the sound was being read
            logger.debug("TTS: dropping a line at the last moment")
            return
        global _last_alarm_started
        if mood == "alarm":
            _last_alarm_started = time.monotonic()                   # (a recording made now would contain it)
        speech_state.begin(envelope, mood=mood)
        _recent_spoken.append((time.monotonic(), text))
        try:
            if not _play(playback_path, mood, since):
                logger.debug("TTS: the line was cut off while playing")      # expected: a warning or "quiet" came
        finally:
            speech_state.end()

        time.sleep(0.1)

        logger.debug("TTS Worker finished speaking.")

    except Exception as e:
        logger.error(f"TTS Error during speech generation: {e}", exc_info=True)
    finally:
        # Clean up scratch files (the sound may still hold a file for a moment after it ends)
        for f in (piper_wav_path, volume_wav_path):  # add output_wav_path back here if RVC is re-enabled
            for attempt in range(4):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                    break
                except Exception as e:
                    if attempt == 3:
                        logger.error(f"Failed to remove {f}: {e}")
                    else:
                        time.sleep(0.2)


def _tts_worker():
    """
    Worker function that runs in a separate thread.
    Processes the speech queue sequentially.
    """
    threading.current_thread().name = "TTSWorker"
    logger.info("🗣️ Piper TTS Worker thread started (RVC disabled).")

    # --- RVC startup check — DISABLED, kept for future re-enable ---
    # if not _check_rvc_server():
    #     logger.warning(f"⚠️  RVC server not reachable at {RVC_SERVER_URL} — start it before speaking.")
    #     logger.warning("   (rvc_env) python -m rvc_python api --port 5050 --models_dir rvc_models --preload-model Maria --device cuda:0")

    while True:
        try:
            text_to_speak = _speech_queue.get()

            if text_to_speak is None:
                logger.info("TTS Worker received stop signal. Exiting.")
                break

            captured_at = None
            if isinstance(text_to_speak, tuple):                 # (text, mood[, captured_at]) from speak_in_thread
                text_to_speak, mood, *rest = text_to_speak
                captured_at = rest[0] if rest else None
            else:
                mood = "talk"
            if isinstance(text_to_speak, str) and text_to_speak.strip():
                speak(text_to_speak, mood, captured_at)

            _speech_queue.task_done()

        except queue.Empty:
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"Error in TTS Worker loop: {e}")
            time.sleep(1)


def _make_way_for(item: tuple):
    """Put a danger warning at the front: everything still waiting is dropped (chatter, and older warnings, which
    are out of date), and whatever she is saying right now is cut off, unless that is a warning too."""
    _urgent_waiting.set()
    with _queue_lock:
        stop_signals = []
        try:
            while True:
                old = _speech_queue.get_nowait()
                _speech_queue.task_done()
                if old is None:
                    stop_signals.append(old)                      # the worker's stop signal must survive
                # everything else waiting is out of date: chatter, and also an older warning ("zombie on your left"
                # is no use after "zombie right in front of you")
        except queue.Empty:
            pass
        _speech_queue.put(item)
        for old in stop_signals:
            _speech_queue.put(old)
    # The line being spoken (if it is chatter) notices the flag within 20 ms and stops itself (see _play): a
    # PlaySound call from here could not interrupt it.


def speak_in_thread(text: str, mood: str = "talk", urgent: bool = False, captured_at: float = None):
    """
    (Public Interface)
    Adds the text to the speech queue. Pass mood="alarm" for a danger warning, so the
    on-screen face looks alarmed while she says it. urgent=True makes it go first and cut off
    what she is saying (danger warnings). captured_at is when the picture behind a warning was
    taken, used only to log how fast the warning reached the player.
    """
    if not _tts_engine_ready:
        logger.warning(f"TTS Engine not ready, skipping queue add: {text}")
        return

    # Muted: do not queue at all (the vision loop pauses captures while the queue is non-empty)
    if get_setting("TTS_MUTED"):
        logger.debug(f"TTS muted, not queueing: '{text}'")
        return

    if isinstance(text, str) and text.strip():
        logger.debug(f"Adding to speech queue: '{text}'")
        item = (text, mood, captured_at)
        if urgent:
            _make_way_for(item)
        else:
            _speech_queue.put(item)


def is_speaking() -> bool:
    """Is she saying something right now?"""
    return speech_state.mood() is not None


def _words(text: str) -> list:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def sounds_like_her_own_voice(text: str, within: float = 25.0) -> bool:
    """Does text (what the microphone heard) just repeat something she said in the last `within` seconds? With
    speakers her voice leaks into the microphone; this stops her from answering herself. Needs 3+ words."""
    words = _words(text)
    if len(words) < 3:
        return False
    now = time.monotonic()
    for when, line in list(_recent_spoken):
        if now - when > within:
            continue
        said = set(_words(line))
        if said and sum(1 for w in words if w in said) / len(words) >= 0.7:
            return True
    return False


def warning_played_since(t: float) -> bool:
    """Did a danger warning start playing after monotonic time t? (The microphone may have heard it.)"""
    return _last_alarm_started > t


def stop_speaking():
    """Silence Maria's chatter right now: drop every chat line still waiting and cut off the one being spoken. Used when
    the player starts to talk, so she does not talk over them or hear herself. Danger warnings are kept: a warning that
    is waiting still gets spoken, and one being spoken is not cut off (so is the worker's stop signal)."""
    global _stop_requested_at
    _stop_requested_at = time.monotonic()                    # the line being spoken stops itself (see _play)
    with _queue_lock:
        kept = []
        try:
            while True:
                old = _speech_queue.get_nowait()
                _speech_queue.task_done()
                if old is None or (isinstance(old, tuple) and len(old) >= 2 and old[1] == "alarm"):
                    kept.append(old)
        except queue.Empty:
            pass
        for old in kept:
            _speech_queue.put(old)


def speak_test():
    """Speak a short sample line with the current volume / speed settings, so a
    change can be heard right away (for the admin panel's 'Test voice' button).
    Does nothing while muted."""
    speak_in_thread("This is a test of my voice.")
    speak_in_thread("And this is how I sound when I warn you, zombie on your left!", mood="alarm")


def start_tts_worker():
    """Starts the TTS worker thread."""
    global _tts_worker_thread
    if not _tts_engine_ready:
        logger.error("Cannot start TTS worker: Engine not initialized.")
        return

    if _tts_worker_thread is None or not _tts_worker_thread.is_alive():
        _tts_worker_thread = threading.Thread(target=_tts_worker, daemon=True, name="TTSWorkerThread")
        _tts_worker_thread.start()


def stop_tts_worker():
    """Signals the TTS worker thread to stop."""
    logger.info("Sending stop signal to TTS worker...")
    _speech_queue.put(None)
    if _tts_worker_thread and _tts_worker_thread.is_alive():
        _tts_worker_thread.join(timeout=2.0)
        if _tts_worker_thread.is_alive():
            logger.warning("TTS worker thread did not stop gracefully.")


def is_ready():
    return _tts_engine_ready


# --- Automatically start the worker thread ---
start_tts_worker()