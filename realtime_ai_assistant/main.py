import sys
import time
import threading
import queue 
import tkinter as tk 
from pynput.keyboard import GlobalHotKeys, Listener, Key 

# Import necessary core components
from core.screenshot import capture_screenshot
from core.ai_handler import analyze_image 
from core.tts import speak, speak_in_thread 
from ui.chat_window import ChatWindow 
# --- IMPORT NEW CONFIG VARIABLE ---
from config import SCREENSHOT_INTERVAL, STARTUP_DELAY, SCREENSHOT_SEQUENCE_LENGTH 
# ----------------------------------
from utils.logger import get_logger

logger = get_logger()

# Shared resources
message_queue = queue.Queue()
app_context = {'latest_screenshot': None} 
hotkey_listener = None 

# --- UPDATED main_loop for Sequence Analysis ---
def main_loop(msg_queue, context): 
    threading.current_thread().name = "VisionLoop"
    logger.info("Vision loop thread started.")
    
    # List to hold the sequence of screenshots
    screenshot_sequence = [] 

    while True:
        try:
            # 1. Capture screenshot every interval
            screenshot = capture_screenshot()
            if screenshot:
                # Always update context with the single latest screenshot for chat UI
                context['latest_screenshot'] = screenshot 
                # Add the screenshot to our sequence
                screenshot_sequence.append(screenshot)

                # 2. Check if the sequence is full
                if len(screenshot_sequence) >= SCREENSHOT_SEQUENCE_LENGTH:
                    logger.debug(f"Sequence full ({len(screenshot_sequence)} images). Analyzing...")
                    
                    # 3. Analyze the entire sequence of images
                    # We pass the list of images to analyze_image
                    # Note: analyze_image in core/ai_handler.py must be updated to handle a list
                    response = analyze_image(screenshot_sequence) 
                    
                    if response:
                        logger.info(f"AI PROACTIVE: {response}")
                        speak_in_thread(response) 
                        msg_queue.put(("AI", response))
                    
                    # 4. Clear the sequence to start collecting the next batch
                    screenshot_sequence = [] 
                
        except Exception as e:
            logger.error(f"CRITICAL ERROR in vision loop: {e}")
            screenshot_sequence = [] # Clear sequence on error
            
        # Always sleep for the base interval
        time.sleep(SCREENSHOT_INTERVAL)
# ------------------------------------------------

# Hotkey listener functions 
def start_listener(chat_toggle_func):
    global hotkey_listener
    hotkey_map = {'<ctrl>+<alt>+g': chat_toggle_func} 
    try:
        hotkey_listener = GlobalHotKeys(hotkey_map) 
        hotkey_listener.start()
        logger.info("👂 Hotkey listener activated (Ctrl+Alt+G).")
    except Exception as e:
        logger.error(f"Failed to start hotkey listener: {e}")

def stop_listener():
    global hotkey_listener
    if hotkey_listener:
        try:
            if hasattr(hotkey_listener, 'running') and hotkey_listener.running:
                 hotkey_listener.stop()
                 if hasattr(hotkey_listener, 'join'):
                     hotkey_listener.join(timeout=0.5) 
            elif not hasattr(hotkey_listener, 'running'): 
                hotkey_listener.stop()
            logger.info("🛑 Hotkey listener stopped.")
        except Exception as e:
            logger.error(f"Error stopping hotkey listener: {e}")

# Main execution block for Tkinter
if __name__ == "__main__":
    # Initialize the Tkinter UI component
    chat = ChatWindow(message_queue, app_context)

    logger.info(f"⏳ Waiting {STARTUP_DELAY}s. Switch to Minecraft now...")
    
    # Use standard time.sleep for Tkinter, run before mainloop
    time.sleep(STARTUP_DELAY)
    logger.info("🚀 Startup delay finished. Starting AI Vision Loop.")

    # Start screenshot processing thread
    loop_thread = threading.Thread(
        target=main_loop, 
        args=(message_queue, app_context), 
        daemon=True,
        name="VisionLoopThread"
    )
    loop_thread.start()
    logger.info("✅ AI Assistant is now running.")
    
    # Start hotkey listener AFTER chat object exists but BEFORE mainloop
    start_listener(chat.toggle) 

    try:
        # Start the Tkinter event loop
        chat.window.mainloop() 
    except Exception as e:
        logger.error(f"Application crashed: {e}")
    finally:
        stop_listener() # Ensure listener stops on exit
    
    logger.info("Shutting down AI Assistant...")

