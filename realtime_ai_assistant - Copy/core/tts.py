import threading
from utils.logger import get_logger
import time
import queue 
import os
from gtts import gTTS
from playsound import playsound
from mutagen.mp3 import MP3

logger = get_logger()

# --- TTS Queue ---
_speech_queue = queue.Queue() # Create a queue for speech requests
_tts_worker_thread = None     # Placeholder for the worker thread
_tts_engine_ready = True # Assume gTTS is ready if imported
_speech_file = "speech.mp3" # Temp file to store speech

def speak(text: str):
    """
    (Internal Use by Worker Thread)
    Generates speech using gTTS, saves to MP3, and plays it (BLOCKING).
    """
    if not _tts_engine_ready:
        logger.warning(f"TTS Engine unavailable, skipping speak: {text}")
        return 

    try:
        logger.debug(f"TTS Worker generating speech for: '{text}'")
        # Generate speech
        tts = gTTS(text=text, lang='en')
        tts.save(_speech_file)
        
        # Get duration
        try:
            audio = MP3(_speech_file)
            duration = audio.info.length
        except Exception as e:
            logger.warning(f"Could not get MP3 duration: {e}. Defaulting to wait.")
            duration = 5.0 # Default wait time if mutagen fails
            
        logger.debug(f"TTS Worker playing audio (duration: {duration:.2f}s)...")
        playsound(_speech_file, block=True) # Play the file (blocking)
        
        # Wait a fraction of a second *after* playsound finishes
        # This can prevent race conditions on the file handle
        time.sleep(0.1) 
        
        logger.debug("TTS Worker finished speaking.")

    except Exception as e:
        logger.error(f"gTTS/playsound Error during speech generation: {e}", exc_info=True)
    finally:
        # Clean up the file if it exists
        try:
            if os.path.exists(_speech_file):
                os.remove(_speech_file)
        except Exception as e:
            logger.error(f"Failed to remove speech file: {e}")


def _tts_worker():
    """
    Worker function that runs in a separate thread.
    Processes the speech queue sequentially.
    """
    threading.current_thread().name = "TTSWorker"
    logger.info("🗣️ gTTS Worker thread started.")
    while True:
        try:
            text_to_speak = _speech_queue.get() 
            
            if text_to_speak is None: # Stop signal
                logger.info("TTS Worker received stop signal. Exiting.")
                break
                
            if isinstance(text_to_speak, str) and text_to_speak.strip():
                speak(text_to_speak) # Call the blocking speak function
            
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
        
    if isinstance(text, str) and text.strip():
        logger.debug(f"Adding to speech queue: '{text}'")
        _speech_queue.put(text)

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
# ---------------------------------------------

