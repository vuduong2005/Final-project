import os
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
LLAMA_MODEL_NAME = "llava:7b"      

# ============================================================================
# SCREENSHOT & ANALYSIS SETTINGS
# ============================================================================

# How often to capture screenshots (seconds)
SCREENSHOT_INTERVAL = 0.5

# How many screenshots to buffer before analyzing
# (More = better context, but slower)
# IMPORTANT: FrameBuffer now handles this internally!
SCREENSHOT_SEQUENCE_LENGTH = 1

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
# FRAME BUFFER SETTINGS (Short-Term Memory)
# ============================================================================

# Maximum frames to keep in buffer (3 = last 3 frames)
FRAME_BUFFER_MAX_FRAMES = 3

# Resize images to this size before storing (width, height)
# Smaller = faster, less memory
# Standard for Roboflow: 640x360
FRAME_BUFFER_TARGET_SIZE = (640, 360)

# JPEG quality for compression (0-100)
# 80 = good balance between quality and file size
FRAME_BUFFER_JPEG_QUALITY = 80

# Movement threshold in pixels (must move this much to be "significant")
# Used for trend detection: approaching/moving_away
FRAME_BUFFER_MOVEMENT_THRESHOLD = 50

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

# ============================================================================
# ADVANCED SETTINGS
# ============================================================================

# Max requests per minute (to avoid rate limiting)
MAX_REQUESTS_PER_MINUTE = 20

# Request timeout (seconds)
REQUEST_TIMEOUT = 30

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