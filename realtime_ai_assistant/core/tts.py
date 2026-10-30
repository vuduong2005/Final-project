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

logger = get_logger()

# --- TTS Queue ---
_speech_queue = queue.Queue()
_tts_worker_thread = None
_tts_engine_ready = True

# --- Piper (base TTS) config ---
PIPER_MODEL_PATH = "en_US-amy-medium.onnx"

NOISE_SCALE = 1.0
NOISE_W = 1.5
SENTENCE_SILENCE = 0.2

# --- RVC (Maria voice conversion) — DISABLED for now, kept for future re-enable ---
# Reason: RVC ran resident on cuda:0 alongside Ollama, splitting a 6GB GPU
# between two consumers. Removing it frees the whole GPU for Ollama again.
# To re-enable: uncomment this block AND the RVC calls inside speak() below.
#
# RVC_SERVER_URL = "http://127.0.0.1:5050"
# RVC_CONVERT_ENDPOINT = f"{RVC_SERVER_URL}/convert_file"
# RVC_TIMEOUT = 60  # seconds — first request after server start can be slow (rmvpe model lazy-loads)

# --- Lazy-loaded Piper voice (loaded once, reused) ---
_piper_voice = None
_synthesis_fields = None  # field names this Piper version's SynthesisConfig accepts


def _get_piper_voice():
    """Load the Piper voice model once and reuse it for every call."""
    global _piper_voice, _synthesis_fields
    if _piper_voice is None:
        logger.info(f"🗣️ Loading Piper voice model: {PIPER_MODEL_PATH}")
        _piper_voice = PiperVoice.load(PIPER_MODEL_PATH)

        import inspect
        _synthesis_fields = set(inspect.signature(SynthesisConfig.__init__).parameters.keys())

    return _piper_voice


def _build_synthesis_config():
    """Build the Piper config for ONE line of speech. Built per call (it is
    cheap) so a TTS_SPEED change from the admin panel applies to the very next
    line, with no cache to invalidate. Call _get_piper_voice() first."""
    # Piper's length_scale is the inverse of speed: smaller = faster speech.
    # TTS_SPEED 1.0 = normal, 1.5 = 50% faster. core/settings.py limits it to 0.5-2.0.
    speed = get_setting("TTS_SPEED")
    length_scale = 1.0 / speed if speed > 0 else 1.0

    valid_fields = _synthesis_fields or set()
    kwargs = {}
    if "length_scale" in valid_fields:
        kwargs["length_scale"] = length_scale
    if "noise_scale" in valid_fields:
        kwargs["noise_scale"] = NOISE_SCALE
    if "noise_w" in valid_fields:
        kwargs["noise_w"] = NOISE_W
    elif "noise_scale_w" in valid_fields:
        kwargs["noise_scale_w"] = NOISE_W
    if "sentence_silence" in valid_fields:
        kwargs["sentence_silence"] = SENTENCE_SILENCE

    return SynthesisConfig(**kwargs)


# --- RVC health check — DISABLED, kept for future re-enable ---
# def _check_rvc_server() -> bool:
#     """Check if the RVC API server is reachable."""
#     try:
#         response = requests.get(f"{RVC_SERVER_URL}/params", timeout=3)
#         return response.status_code == 200
#     except requests.exceptions.RequestException:
#         return False


def _generate_piper_wav(text: str, piper_wav_path: str) -> bool:
    """Generate base speech audio with Piper. Returns True on success."""
    try:
        voice = _get_piper_voice()
        syn_config = _build_synthesis_config()
        with wave.open(piper_wav_path, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file, syn_config=syn_config)
        return True
    except Exception as e:
        logger.error(f"Piper synthesis failed: {e}", exc_info=True)
        return False


def _apply_volume(input_path: str, output_path: str, volume: float) -> bool:
    """
    Scale a 16-bit PCM wav's loudness by `volume` (1.0 = unchanged) and
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


def speak(text: str):
    """
    (Internal Use by Worker Thread)
    Generates speech using Piper and plays it directly (BLOCKING).
    RVC voice conversion is currently DISABLED — see commented code below
    for how to re-enable Maria's converted voice.
    Uses a unique filename per call to avoid Windows file-lock issues from
    the previous playback still holding a handle open.
    """
    if not _tts_engine_ready:
        logger.warning(f"TTS Engine unavailable, skipping speak: {text}")
        return

    # Muted: also drops lines that were already queued before the mute was switched on
    if get_setting("TTS_MUTED"):
        logger.debug(f"TTS muted, skipping: '{text}'")
        return

    # Read the volume now (not at import) so a change applies to this very line
    volume = get_setting("TTS_VOLUME")

    # Unique filename per call — avoids Windows file-lock conflicts
    call_id = uuid.uuid4().hex[:8]
    piper_wav_path = f"piper_output_{call_id}.wav"
    volume_wav_path = f"piper_output_{call_id}_vol.wav"
    # output_wav_path = f"maria_output_{call_id}.wav"  # RVC output path — unused while RVC is disabled

    try:
        logger.debug(f"TTS Worker generating speech for: '{text}'")

        # Stage 1: Piper generates speech audio
        if not _generate_piper_wav(text, piper_wav_path):
            return

        # --- RVC conversion step — DISABLED, kept for future re-enable ---
        # Stage 2: RVC server converts it to Maria's voice
        # if not _convert_with_rvc(piper_wav_path, output_wav_path):
        #     return
        # winsound.PlaySound(output_wav_path, winsound.SND_FILENAME)

        # Stage 2: apply volume (TTS_VOLUME from config.py). Skip the extra
        # read/write pass entirely when volume is left at the default —
        # no reason to pay that cost on every single line spoken.
        playback_path = piper_wav_path
        if volume != 1.0:
            if _apply_volume(piper_wav_path, volume_wav_path, volume):
                playback_path = volume_wav_path
            else:
                logger.warning("TTS volume scaling failed — playing at original volume")

        # Mute may have been switched on while this line was being generated
        if get_setting("TTS_MUTED"):
            logger.debug("TTS muted during synthesis, not playing")
            return

        logger.debug(f"TTS Worker playing audio (Piper voice, volume={volume})...")
        # winsound doesn't hold a lingering lock on the file like playsound's
        # MCI backend does on Windows, so we don't hit PermissionError on cleanup.
        winsound.PlaySound(playback_path, winsound.SND_FILENAME)

        time.sleep(0.1)

        logger.debug("TTS Worker finished speaking.")

    except Exception as e:
        logger.error(f"TTS Error during speech generation: {e}", exc_info=True)
    finally:
        # Clean up scratch files
        for f in (piper_wav_path, volume_wav_path):  # add output_wav_path back here if RVC is re-enabled
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception as e:
                logger.error(f"Failed to remove {f}: {e}")


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

            if isinstance(text_to_speak, str) and text_to_speak.strip():
                speak(text_to_speak)

            _speech_queue.task_done()

        except queue.Empty:
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"Error in TTS Worker loop: {e}")
            time.sleep(1)


def speak_in_thread(text: str):
    """
    (Public Interface)
    Adds the text to the speech queue.
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
        _speech_queue.put(text)


def speak_test():
    """Speak a short sample line with the current volume / speed settings, so a
    change can be heard right away (for the admin panel's 'Test voice' button).
    Does nothing while muted."""
    speak_in_thread("This is a test of my voice.")


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