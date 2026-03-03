# config.py

# --- AI Provider Configuration ---
AI_PROVIDER = "ollama" #"ollama" "google_gemini"

# --- NEW: Google Gemini Configuration ---
GOOGLE_API_KEY = "AIzaSyAk7Oc74J9_IEYyURz1BQAKiytuVFllWFo" 
GEMINI_MODEL_NAME = "gemini-2.5-flash" 
# -----------------------------------------------------------------------

# --- Ollama Configuration (if AI_PROVIDER is "ollama") ---
LLAVA_API_URL = "http://localhost:11434" # Base URL ONLY
OLLAMA_MODEL_NAME = "llava" #"llama3.2-vision" 

# --- Application Settings ---
SCREENSHOT_INTERVAL = 3 
SCREENSHOT_SEQUENCE_LENGTH = 3
TOGGLE_HOTKEY = "<ctrl>+<alt>+g"
STARTUP_DELAY = 10

# --- Tesseract OCR Configuration ---
TESSERACT_CMD_PATH = r"E:\my future\tesseract.exe" 



