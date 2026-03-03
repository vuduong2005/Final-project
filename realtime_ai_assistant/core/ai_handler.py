import requests
import base64
import io
import json
import threading
import re 
from PIL import Image
from typing import Optional, List # Import List
from queue import Queue 
# --- Import Google Gemini Library ---
import google.generativeai as genai
# ----------------------------------

# Import necessary config values
from config import (OLLAMA_MODEL_NAME, LLAVA_API_URL, AI_PROVIDER, 
                    GOOGLE_API_KEY, GEMINI_MODEL_NAME) # No Tesseract
from utils.logger import get_logger 
from core.tts import speak_in_thread 

logger = get_logger()

# --- State Variables ---
_current_context_is_minecraft = True 

# --- CONFIGURE GEMINI API (Library Version) ---
if AI_PROVIDER == "google_gemini":
    if not GOOGLE_API_KEY or GOOGLE_API_KEY == "YOUR_NEW_API_KEY_HERE":
        logger.error("FATAL: GOOGLE_API_KEY is missing. Disabling Gemini.")
        GEMINI_CLIENT = None
    else:
        try:
            genai.configure(api_key=GOOGLE_API_KEY)
            generation_config = {
                "temperature": 0.7,
                "max_output_tokens": 2048,
            }
            safety_settings = {
                'HARM_CATEGORY_HARASSMENT':'BLOCK_NONE',
                'HARM_CATEGORY_HATE_SPEECH':'BLOCK_NONE',
                'HARM_CATEGORY_SEXUALLY_EXPLICIT':'BLOCK_NONE',
                'HARM_CATEGORY_DANGEROUS_CONTENT':'BLOCK_NONE'
            }
            # Initialize the model using the library
            GEMINI_CLIENT = genai.GenerativeModel(
                model_name=GEMINI_MODEL_NAME, #gemini-2.5-flash
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            logger.info(f"✅ Google Gemini API configured successfully with model: {GEMINI_MODEL_NAME}")
        except Exception as e:
            logger.error(f"FATAL: Failed to configure Google Gemini API: {e}", exc_info=True)
            GEMINI_CLIENT = None
else:
    GEMINI_CLIENT = None
# ----------------------------

# --- Prompts ---
CONTEXT_CHECK_PROMPT = (
    "Analyze this image. Does it primarily show gameplay from the block-based sandbox video game Minecraft? "
    "Look for elements like a blocky world, first-person view with a hotbar, or a crafting grid. "
    "Answer ONLY with the single word 'Yes' or the single word 'No'."
)
PROACTIVE_MINECRAFT_PROMPT = (
   "You are 'Ember', a friendly, witty, and deeply knowledgeable Minecraft companion. "
   "You are observing a sequence of screenshots showing the player's last 9 seconds of gameplay. "
   "Analyze the sequence to understand the player's *action, goal, or situation*. "
   "Provide a *single, concise, conversational, and insightful* comment (under 20 words). "
   "Examples: 'Looks like you're gathering a lot of wood! Building something big?', "
   "'Careful, that Creeper is getting too close!', 'Oh, nice find! That's a diamond ore.', "
   "'Just exploring this cave system, huh?', 'Hey, you're drowning! Get to the surface!', 'You Died! Oh no, that was a rough one!'"
   "If nothing noteworthy is happening, just respond with 'NONE'."
)
CHAT_SYSTEM_PROMPT = ( 
    "You are 'Ember', a friendly, witty, and supportive Minecraft companion. "
    "The user is asking you a direct question. Use the provided screenshot for visual context. "
    "Answer their question naturally and conversationally."
)

# --- Utilities ---
def _image_to_base64(image: Image.Image) -> str:
    # Only used for Ollama
    buffer = io.BytesIO(); image.save(buffer, format="JPEG"); return base64.b64encode(buffer.getvalue()).decode("utf-8") 

# --- OLLAMA Provider Specific Logic (Fallback) ---
def _call_ollama_api(prompt: str, images: Optional[list[str]] = None, num_predict: int = 150, temperature: float = 0.5) -> str: 
    threading.current_thread().name = "OllamaRequest" 
    payload = { "model": OLLAMA_MODEL_NAME, "prompt": prompt, "images": images if images else [], "stream": False, "options": { "temperature": temperature, "num_predict": num_predict } }
    api_endpoint = f"{LLAVA_API_URL.rstrip('/')}/api/generate"
    try:
        response = requests.post(api_endpoint, json=payload, timeout=30); response.raise_for_status(); data = response.json()
        raw_response = data.get("response", "").strip(); logger.debug(f"Raw Ollama Response: '{raw_response}'"); return raw_response
    except requests.exceptions.RequestException as e: logger.error(f"Ollama Request Failed: {e}"); return f"Error: Failed connect. ({e})" 
    except Exception as e: logger.error(f"Error during Ollama API call: {e}"); return f"Error: Could not process. ({e})"
# ----------------------------------------------------

# --- Google Gemini Library Logic ---
def _call_gemini_api(prompt: str, images: List[Image.Image]) -> str:
    """Sends a request to the Google Gemini API using the Python library."""
    threading.current_thread().name = "GeminiRequest"
    if GEMINI_CLIENT is None:
        logger.error("Gemini client not initialized. Check API key or AI_PROVIDER setting.")
        return "Error: Gemini client not initialized."

    try:
        # Build the content list (Prompt first, then images for multi-image)
        content = [prompt] + images 
        
        # Call the API using the library client
        response = GEMINI_CLIENT.generate_content(content)
        
        # Handle potential safety blocks before accessing .text
        if not response.candidates:
             block_reason = "Unknown"; 
             try: block_reason = response.prompt_feedback.block_reason
             except Exception: pass
             logger.warning(f"Gemini call blocked. Reason: {block_reason}")
             return f"Error: Request blocked by safety settings ({block_reason})."

        raw_response = response.text.strip()
        logger.debug(f"Raw Gemini Response: '{raw_response}'")
        return raw_response
        
    except Exception as e:
        logger.error(f"Google Gemini API call failed: {e}", exc_info=True)
        # This is where the 404 error was happening with the old library
        return f"Error: Gemini API call failed. Check logs."
# ---------------------------------------

def _is_minecraft_screen(image: Image.Image) -> bool:
    """ Calls the VLM to check if the image is likely Minecraft. """
    if AI_PROVIDER == "ollama":
        try:
            encoded_image = _image_to_base64(image)
            response = _call_ollama_api(prompt=CONTEXT_CHECK_PROMPT, images=[encoded_image], num_predict=5, temperature=0.0)
            is_mc = "yes" in response.lower(); logger.debug(f"Ollama Context Check: Minecraft? {is_mc} (Resp: '{response}')")
            return is_mc
        except Exception as e: logger.error(f"Error during context check: {e}"); return False 
    
    elif AI_PROVIDER == "google_gemini":
        if GEMINI_CLIENT is None: return False 
        try:
            # Use the library client
            response = _call_gemini_api(prompt=CONTEXT_CHECK_PROMPT, images=[image]) 
            is_mc = "yes" in response.lower()
            logger.debug(f"Gemini Context Check: Minecraft? {is_mc} (Resp: '{response}')")
            return is_mc
        except Exception as e:
            logger.error(f"Error during Gemini context check: {e}")
            return False 
    else: return True 

# --- Main Interface Functions ---
def analyze_image(image_sequence: List[Image.Image]) -> Optional[str]:
    """
    Analyzes a SEQUENCE of screenshots for proactive advice.
    Relies on Gemini for OCR.
    """
    global _current_context_is_minecraft 
    latest_image = image_sequence[-1] # Use latest image for checks

    # 1. Context Check
    is_mc_now = _is_minecraft_screen(latest_image)

    # State Transition Logic
    if is_mc_now and not _current_context_is_minecraft:
        _current_context_is_minecraft = True; logger.info("Context changed: Now detecting Minecraft.")
        return "Alright, looks like we're back in Minecraft!" 
    elif not is_mc_now and _current_context_is_minecraft:
        _current_context_is_minecraft = False; logger.info("Context changed: No longer detecting Minecraft.")
        return "Hey, doesn't look like we're in Minecraft right now. What are you up to?" 
    elif not is_mc_now: 
        logger.debug("Not a Minecraft screen. Staying silent."); return None 
    
    # If Minecraft context is confirmed, proceed with analysis
    ai_response = None
    if AI_PROVIDER == "ollama":
        try:
            encoded_image = _image_to_base64(latest_image) 
            prompt = f"{PROACTIVE_MINECRAFT_PROMPT}" 
            ai_response = _call_ollama_api(prompt=prompt, images=[encoded_image], num_predict=30, temperature=0.5)
        except Exception as e: logger.error(f"Error in analyze_image (Ollama): {e}"); return None

    elif AI_PROVIDER == "google_gemini":
        if GEMINI_CLIENT is None: return None 
        try:
            prompt = f"{PROACTIVE_MINECRAFT_PROMPT}" 
            # Use the library client
            ai_response = _call_gemini_api(prompt=prompt, images=image_sequence) 
        except Exception as e: logger.error(f"Error in analyze_image (Gemini): {e}"); return None

    else:
        logger.error(f"Unsupported AI_PROVIDER: {AI_PROVIDER}"); return None

    # Filtering
    if not ai_response or "Error:" in ai_response: return None
    if "NONE" in ai_response.upper(): 
        logger.debug("Gemini returned 'NONE'. Staying silent.")
        return None 
    if "I cannot" in ai_response or "I am not" in ai_response or "I'm sorry" in ai_response:
        logger.warning(f"Gemini refused to answer: '{ai_response}'")
        return None
        
    return ai_response

def send_chat_message_to_ai(user_text: str, image: Image.Image, msg_queue: Queue):
    ai_response = "Error: Provider not configured." 
    if AI_PROVIDER == "ollama":
        try:
            encoded_image = _image_to_base64(image); prompt = f"{CHAT_SYSTEM_PROMPT}\n\nUser's Question: {user_text}"
            ai_response = _call_ollama_api(prompt=prompt, images=[encoded_image], num_predict=150, temperature=0.7 )
            if "Error:" in ai_response: msg_queue.put(("System", ai_response)); return 
        except Exception as e: ai_response = f"Error: Ollama chat fail. ({e})"; msg_queue.put(("System", ai_response)); return 

    elif AI_PROVIDER == "google_gemini":
        if GEMINI_CLIENT is None: 
            msg_queue.put(("System", "Error: Gemini client not initialized.")); return
        try:
            prompt = f"{CHAT_SYSTEM_PROMPT}\n\nUser's Question: {user_text}"
            # Use the library client
            ai_response = _call_gemini_api(prompt=prompt, images=[image]) 
            if "Error:" in ai_response: msg_queue.put(("System", ai_response)); return 
        except Exception as e: ai_response = f"Error: Gemini chat fail. ({e})"; msg_queue.put(("System", ai_response)); return 

    else: 
        ai_response = f"Error: Unsupported Provider '{AI_PROVIDER}'"; msg_queue.put(("System", ai_response)); return 

    speak_in_thread(ai_response); msg_queue.put(("AI", ai_response))

