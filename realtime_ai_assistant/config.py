import os
import json
from dotenv import load_dotenv

# Load variables from a local .env file (if present). This file is
# gitignored, so secrets never get committed.
load_dotenv()

# ============================================================================
# AI MODEL CONFIGURATION
# ============================================================================

# Which AI provider to use
AI_PROVIDER = "local"  # "local" = Roboflow + Llama (fully offline)

# ============================================================================
# ROBOFLOW CONFIGURATION - MULTIPLE MODELS (3 MODELS)
# ============================================================================

# API Key (same for all models) — set this in your local .env file as
# ROBOFLOW_API_KEY=your_key_here (see .env.example)
ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_API_KEY")

if not ROBOFLOW_API_KEY:
    raise RuntimeError(
        "ROBOFLOW_API_KEY is not set. Create a .env file in the project "
        "root (see .env.example) with ROBOFLOW_API_KEY=your_key_here."
    )

# MODEL 1: General Detection (Original - catches everything)
ROBOFLOW_GENERAL_URL = "https://detect.roboflow.com/minecraft-hu5i5-3f8zv/1"

# MODEL 2: Mobs Detection (Specialized for mobs)
ROBOFLOW_MOBS_URL = "https://detect.roboflow.com/duongs-workspace-y3jfp/minecraft-nqdab-hztat-1-rfdetr-large-t2"

# MODEL 3: Blocks Detection (Specialized for blocks/ores)
ROBOFLOW_BLOCKS_URL = "https://detect.roboflow.com/minecraft-blocks-bv4lp/1"

# Which models to use? (True = use it, False = skip it)
USE_GENERAL_MODEL = True   # Always recommended
USE_MOBS_MODEL = True      # Specialized for mobs
USE_BLOCKS_MODEL = True    # Specialized for blocks

# Detection confidence: lower = more detections (more false positives)
# Roboflow API uses 0-100; Jimmy parser uses 0-1 (keep them in sync)
ROBOFLOW_CONFIDENCE_PERCENT = 50   # 50 = 50% — try 20 for more detections, 70 for stricter
ROBOFLOW_CONFIDENCE_THRESHOLD = 0.5  # same as above, for Stage 2 parser

# Confidence thresholds for specialized models
MOB_CONFIDENCE_THRESHOLD = 0.6       # Mobs need 60%+ confidence
BLOCK_CONFIDENCE_THRESHOLD = 0.4     # Blocks can be 40%+ confidence

# ============================================================================
# OLLAMA/LLAMA CONFIGURATION (Local Inference Server)
# ============================================================================

LLAMA_API_URL = "http://localhost:11434"  # Local Ollama server endpoint
LLAMA_MODEL_NAME = "gemma3:4b"   # fits the 6 GB GPU fully; sees images too

# ============================================================================
# SCREENSHOT & ANALYSIS SETTINGS
# ============================================================================

# How often to capture screenshots (seconds)
SCREENSHOT_INTERVAL = 0.5

# How many screenshots to buffer before analyzing
# (More = better context, but slower)
# IMPORTANT: FrameBuffer now handles this internally!
SCREENSHOT_SEQUENCE_LENGTH = 1

# Privacy: only ever capture the Minecraft window, and nothing at all while another window is in
# front (a browser, chat apps, Maria's own windows). Set CAPTURE_ONLY_GAME to False to go back to
# capturing the whole primary monitor.
CAPTURE_ONLY_GAME = True
GAME_WINDOW_TITLE = "Minecraft"                       # the title must start like this, then a version: "Minecraft 1.20.1"...
GAME_PROCESS_NAMES = ("javaw.exe", "java.exe")        # ...and belong to the Java game (Bedrock is not supported)

# Minimum time between AI analyses (seconds)
# Prevents spam of responses
MIN_ANALYSIS_INTERVAL = 2.0

# Startup delay before vision loop starts (seconds)
# Gives you time to switch to Minecraft window
STARTUP_DELAY = 5

# ============================================================================
# CHAT MODE — HYBRID VISION & PROACTIVE COMMENTS
# ============================================================================

# Only attach the current screenshot to a Chat Mode request (hybrid vision)
# if the player's message contains one of these phrases. Keeps normal chat
# fast/cheap and only pays the vision cost when the player is actually
# asking Maria to look at something.
VISION_TRIGGER_KEYWORDS = [
    "can you see",
    "do you see",
    "what do you see",
    "look at",
    "check out",
    "what does this look like",
    "what am i looking at",
]

# How many seconds the player must stay on the same activity (from
# activity_context/Jimmy detection) before Maria proactively comments in
# Chat Mode, unprompted (e.g. "You've been mining a while, find anything?").
# Time-based rather than cycle-based, so this stays consistent even if
# MIN_ANALYSIS_INTERVAL is tuned later.
PROACTIVE_STREAK_SECONDS = 30

# Suppress proactive comments if the player sent a real chat message more
# recently than this many seconds ago, so Maria doesn't talk over an
# actual conversation.
PROACTIVE_CHAT_COOLDOWN_SECONDS = 60

# ============================================================================
# MARIA'S MEMORY, PLAYER PROFILE AND WHEN SHE SPEAKS
# ============================================================================

# One shared memory for Chat Mode and Game Mode. When it is full the oldest game
# lines go first, so the player's own messages stay.
MEMORY_MAX_ENTRIES = 40
MEMORY_CHAT_LINES = 8      # recent chat lines sent to the model
MEMORY_GAME_LINES = 3      # her own recent game lines (so she does not repeat herself)

# Saved between runs: nickname, standing requests, permanent watch list. Local only.
PLAYER_PROFILE_PATH = "player_profile.json"
# True: every time the app starts, Maria forgets the nickname, standing requests and saved watch list,
# so each play session starts fresh. False: she keeps them in player_profile.json between sessions.
PROFILE_RESET_EACH_SESSION = True
PROFILE_MAX_REQUESTS = 10

# Things the player asked her to watch for ("tell me if you see iron") end when the
# player says they are good, or after this long if they forget.
WATCH_EXPIRE_SECONDS = 900

# How long before she mentions the same thing again
ANNOUNCE_COOLDOWN_THREAT = 15
ANNOUNCE_COOLDOWN_ORE = 45
ANNOUNCE_COOLDOWN_WATCHED = 30

# This many ores in one frame count as a vein (one combined comment)
VEIN_MIN_COUNT = 4

# What she saw recently is remembered this long (for "do you see the zombie?")
RECENT_EVENTS_SECONDS = 180

# Sampling options for her replies (Ollama /api/chat)
LLAMA_OPTIONS = {"temperature": 0.8, "top_p": 0.95, "repeat_penalty": 1.15}
LLAMA_MAX_TOKENS_CHAT = 120
LLAMA_MAX_TOKENS_GAME = 45
# How long Ollama keeps the model loaded in the GPU after her last reply. Its own default is 5 minutes, and loading
# the model again takes 20-30 seconds, which showed up as a very slow reply after every quiet stretch.
LLAMA_KEEP_ALIVE = "60m"

# ============================================================================
# FRAME BUFFER SETTINGS (Short-Term Memory)
# ============================================================================

# Maximum frames to keep in buffer (3 = last 3 frames)
FRAME_BUFFER_MAX_FRAMES = 3

# Size change in percent (the detection box must look this much bigger or smaller
# between the first and last frame to count as "approaching" or "moving away")
FRAME_BUFFER_SIZE_CHANGE_PERCENT = 15

# Confidence trend threshold
# If avg confidence changes by more than this, it's "increasing" or "decreasing"
FRAME_BUFFER_CONFIDENCE_THRESHOLD = 0.1

# ============================================================================
# UI SETTINGS
# ============================================================================

# Hotkey to toggle chat window
# Format: '<ctrl>+<alt>+g' means Ctrl+Alt+G
TOGGLE_HOTKEY = "<ctrl>+<alt>+g"

# Chat window dimensions
CHAT_WINDOW_WIDTH = 500
CHAT_WINDOW_HEIGHT = 400

# ============================================================================
# OUTPUT & LOGGING
# ============================================================================

# Directory for debug screenshots
DEBUG_SCREENSHOT_DIR = "debug_captures"

# Keep only the newest N debug screenshots; older ones are deleted automatically
DEBUG_SCREENSHOT_MAX_FILES = 50

# CSV report file
REPORT_CSV = "test_report.csv"

# Log level: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL = "INFO"

# Log directory
LOG_DIR = "logs"

# ============================================================================
# TEXT-TO-SPEECH SETTINGS
# ============================================================================

# Enable text-to-speech output
TTS_ENABLED = True

# TTS language
TTS_LANGUAGE = "en"

# Speech rate (0.5 = slow, 1.0 = normal, 2.0 = fast)
TTS_SPEED = 1.0

# Speech volume (0.0 = silent, 1.0 = normal/unchanged, 2.0 = double loudness)
# Applied by scaling the raw audio samples after Piper generates them.
TTS_VOLUME = 1.0

# Mute: when True, Maria's lines still show in the chat window but are not spoken
TTS_MUTED = False

# Which voice style she uses (switchable live in the Status window):
#   "now"      Voice 1 - her normal voice
#   "livelier" Voice 2 - a bit faster and higher; danger warnings faster and louder
#   "bright"   Voice 3 - more excited; danger warnings even more urgent
TTS_VOICE = "now"

# Which voice MODEL she speaks with (switchable live in the Status window):
#   "amy"    the standard Piper voice
#   "maria"  her own trained voice (maria.onnx + maria.onnx.json in this folder); it has a calm and a
#            scared speaker, so danger warnings sound frightened and still like her.
# If maria.onnx is missing she speaks with Amy whatever this says.
TTS_MODEL = "amy"

# --- Talking to Maria ---
# Voice is switched on and off with the Voice button in the chat window (or Ctrl+Alt+V). It always starts OFF
# when the app starts. While it is on, what you say becomes a chat message (turned into text on this PC;
# nothing is sent anywhere or saved), and your voice cuts her off when she is talking.
PUSH_TO_TALK = False           # False: the microphone stays open while voice is on. True: it records only while you HOLD the key
PUSH_TO_TALK_KEY = "v"         # the key to hold: "v", "b", "f8" or "f9"
MIC_DEVICE = ""                # which microphone: "" = the Windows default one, or the name of one (picked in the Status window)
STT_MODEL = "base.en"          # speech-to-text model: "tiny.en" (fastest), "base.en", "small.en" (most accurate, slower)
VOICE_SILENCE_SECONDS = 1.2    # a pause this long ends your phrase
VOICE_BEEPS = True             # a short tone when she starts and stops listening

# ============================================================================
# ADVANCED SETTINGS
# ============================================================================

# Max requests per minute (to avoid rate limiting)
MAX_REQUESTS_PER_MINUTE = 20

# Request timeout (seconds)
REQUEST_TIMEOUT = 30

# --- Danger warnings: speed first ---------------------------------------------------------------
# A warning is only worth something if it reaches the player before the mob does.
FAST_DANGER_LINES = True            # build a danger line straight from the facts instead of waiting seconds for the language model
THREAT_INSTANT_CONFIDENCE = 0.75    # live: a dangerous mob this sure is announced at once. Between the mob
                                    # threshold (0.6) and this it must be seen again in the next picture (about
                                    # 2-3 s later) so one wrong detection is not a false alarm. 0.5 = always at once.
THREAT_CONFIRM_SECONDS = 8          # how long a first sighting waits for its second look
ALERT_SECONDS = 10                  # after a danger was seen, look more often for this long...
ALERT_ANALYSIS_INTERVAL = 1.0       # ...analysing a picture every this many seconds instead of MIN_ANALYSIS_INTERVAL

# Retry failed requests
RETRY_FAILED_REQUESTS = True
MAX_RETRIES = 2

# ============================================================================
# MINECRAFT-SPECIFIC SETTINGS
# ============================================================================

# Object classes to detect (matches your trained models)
MINECRAFT_CLASSES = {
    # Hostile mobs (threats)
    "creeper", "zombie", "skeleton", "enderman", "witch",
    "spider", "cave_spider", "ghast", "blaze", "wither",
    "drowned", "husk", "stray",
    
    # Passive mobs
    "pig", "cow", "sheep", "chicken", "horse", "villager",
    
    # Resources (valuable)
    "diamond_ore", "gold_ore", "iron_ore", "copper_ore",
    "coal_ore", "lapis_ore", "emerald_ore", "ancient_debris",
    "diamond", "gold", "iron", "copper", "coal", "lapis",
    
    # Status bars
    "health_bar", "hunger_bar", "exp_bar",
    
    # Hazards
    "water", "lava", "fire", "cactus", "void",
    
    # Block/environment
    "torch", "bedrock", "obsidian", "crafting_table", "furnace"
}

# Threat priority (used for response generation)
THREAT_PRIORITY = {
    "creeper": 10,  # CRITICAL - instant threat
    "enderman": 8,
    "zombie": 7,
    "skeleton": 6,
    "spider": 5,
    "lava": 9,
}

# Resource priority (used for response generation)
RESOURCE_PRIORITY = {
    "diamond_ore": 10,  # Most valuable
    "emerald_ore": 9,
    "ancient_debris": 8,
    "gold_ore": 6,
    "iron_ore": 4,
    "coal_ore": 3,
}

# ============================================================================
# PERFORMANCE TUNING
# ============================================================================

# Use GPU if available (requires CUDA-capable GPU)
USE_GPU = True

# Optimize for speed vs accuracy
# "speed" = faster but less accurate
# "balanced" = good mix
# "accuracy" = slower but more accurate
OPTIMIZATION_MODE = "balanced"

# Maximum image size for inference (pixels)
# Smaller = faster, larger = better accuracy
MAX_IMAGE_SIZE = 416

# ============================================================================
# DEBUGGING & DEVELOPMENT
# ============================================================================

# Enable debug mode (verbose logging)
DEBUG_MODE = False

# Save all screenshots (for debugging)
SAVE_ALL_SCREENSHOTS = False

# Print timing information for each stage
PRINT_TIMING = True

# ============================================================================
# ADMIN OVERRIDES (config_overrides.json)
# ============================================================================

def _as_bool(value):
    """Accept only real booleans (so the string "false" is not treated as True)."""
    if not isinstance(value, bool):
        raise ValueError("expected true/false")
    return value


# Keys that config_overrides.json may override, with their type. Includes every
# key in ADMIN_CONFIG_FIELDS (ui/status_window.py) plus the live settings
# managed by core/settings.py.
_ADMIN_OVERRIDABLE_KEYS = {
    "ROBOFLOW_CONFIDENCE_PERCENT": int,
    "MOB_CONFIDENCE_THRESHOLD": float,
    "THREAT_INSTANT_CONFIDENCE": float,
    "BLOCK_CONFIDENCE_THRESHOLD": float,
    "SCREENSHOT_INTERVAL": float,
    "MIN_ANALYSIS_INTERVAL": float,
    "STARTUP_DELAY": float,
    "TTS_SPEED": float,
    "TTS_VOLUME": float,
    "TTS_MUTED": _as_bool,
    "TTS_VOICE": str,
    "TTS_MODEL": str,
    "PUSH_TO_TALK": _as_bool,
    "PUSH_TO_TALK_KEY": str,
    "MIC_DEVICE": str,
    "STT_MODEL": str,
    "VOICE_SILENCE_SECONDS": float,
    "VOICE_BEEPS": _as_bool,
    "USE_GENERAL_MODEL": _as_bool,
    "USE_MOBS_MODEL": _as_bool,
    "USE_BLOCKS_MODEL": _as_bool,
    "FRAME_BUFFER_SIZE_CHANGE_PERCENT": int,
    "FRAME_BUFFER_CONFIDENCE_THRESHOLD": float,
}

CONFIG_OVERRIDES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config_overrides.json"
)


def _apply_admin_overrides():
    """Read config_overrides.json and overwrite the matching module globals.
    Runs once on import, so every `from config import X` sees the final value.
    A missing or broken file / bad value is skipped and the default stays."""
    if not os.path.isfile(CONFIG_OVERRIDES_PATH):
        return

    try:
        with open(CONFIG_OVERRIDES_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except Exception as e:
        print(f"[config] Could not read config_overrides.json, using defaults: {e}")
        return

    if not isinstance(saved, dict):
        print("[config] config_overrides.json is not a JSON object, using defaults")
        return

    for key, caster in _ADMIN_OVERRIDABLE_KEYS.items():
        if key not in saved:
            continue
        try:
            globals()[key] = caster(saved[key])
        except (ValueError, TypeError):
            print(f"[config] Ignoring invalid override for {key}: {saved[key]!r}")


# Hard-coded defaults of the overridable keys, captured BEFORE the overrides
# are applied. core/settings.py uses these for reset() and to decide which
# values need saving.
ADMIN_DEFAULTS = {key: globals()[key] for key in _ADMIN_OVERRIDABLE_KEYS}

_apply_admin_overrides()

# Keep the 0-1 threshold used by the Stage 2 parser in sync with the percent
# value, in case the percent was overridden above.
ROBOFLOW_CONFIDENCE_THRESHOLD = ROBOFLOW_CONFIDENCE_PERCENT / 100

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def validate_config():
    """Validate configuration on startup"""
    errors = []
    warnings = []
    
    # Check output directories can be created
    try:
        os.makedirs(DEBUG_SCREENSHOT_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception as e:
        errors.append(f"Cannot create output directories: {e}")
    
    # Warn about settings
    if SCREENSHOT_INTERVAL < 1:
        warnings.append("Screenshot interval < 1s may cause performance issues")
    
    if SCREENSHOT_SEQUENCE_LENGTH > 10:
        warnings.append("Large screenshot sequence may slow down analysis")
    
    if not TTS_ENABLED:
        warnings.append("Text-to-speech is disabled - only chat UI will show responses")
    
    # Check Roboflow model URLs
    if not (ROBOFLOW_GENERAL_URL or ROBOFLOW_MOBS_URL or ROBOFLOW_BLOCKS_URL):
        errors.append("No Roboflow model URLs configured!")
    
    if not (USE_GENERAL_MODEL or USE_MOBS_MODEL or USE_BLOCKS_MODEL):
        warnings.append("All models are disabled! Enable at least one model to use Roboflow.")
    
    return errors, warnings


# ============================================================================
# PRINT CONFIGURATION (on import)
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("MINECRAFT AI COMPANION - CONFIGURATION")
    print("=" * 70)
    print(f"AI Provider: {AI_PROVIDER}")
    print(f"\nRoboflow Models:")
    print(f"  General: {'ENABLED' if USE_GENERAL_MODEL else 'disabled'}")
    print(f"  Mobs: {'ENABLED' if USE_MOBS_MODEL else 'disabled'}")
    print(f"  Blocks: {'ENABLED' if USE_BLOCKS_MODEL else 'disabled'}")
    print(f"\nLlama Server: {LLAMA_API_URL}")
    print(f"Screenshot Interval: {SCREENSHOT_INTERVAL}s")
    print(f"Chat Hotkey: {TOGGLE_HOTKEY}")
    print(f"TTS Enabled: {TTS_ENABLED}")
    print("=" * 70)
    
    errors, warnings = validate_config()
    
    if errors:
        print("❌ ERRORS:")
        for e in errors:
            print(f"   - {e}")
    
    if warnings:
        print("⚠️  WARNINGS:")
        for w in warnings:
            print(f"   - {w}")
    
    if not errors:
        print("✅ Configuration looks good!")
    
    print("=" * 70)