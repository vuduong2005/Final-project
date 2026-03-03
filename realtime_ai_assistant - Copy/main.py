import sys
import time
import threading
import queue 
import tkinter as tk 
import os 
import datetime 
import csv  
from pynput.keyboard import GlobalHotKeys, Listener, Key 

# Import necessary core components
from core.screenshot import capture_screenshot
from core.ai_handler import analyze_image 
from core.tts import speak, speak_in_thread 
import core.tts 
from ui.chat_window import ChatWindow 
# --- IMPORT CONFIG ---
from config import SCREENSHOT_INTERVAL, STARTUP_DELAY, SCREENSHOT_SEQUENCE_LENGTH, AI_PROVIDER
# ---------------------
from utils.logger import get_logger

logger = get_logger()

# Shared resources
message_queue = queue.Queue()
app_context = {'latest_screenshot': None} 
hotkey_listener = None 

# --- UPDATED main_loop with CSV Reporting ---
def main_loop(msg_queue, context): 
    threading.current_thread().name = "VisionLoop"
    logger.info("Vision loop thread started.")
    
    # 1. Setup Folders & Files
    if not os.path.exists("debug_captures"):
        os.makedirs("debug_captures")

    # 2. Create/Append to Report File
    report_file = "test_report.csv"
    file_exists = os.path.isfile(report_file)
    
    # Write Header if new file
    if not file_exists:
        with open(report_file, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # These are the columns for your report
            writer.writerow(["Timestamp", "Image_Filename", "AI_Response", "Latency_Seconds", "Model_Provider"])

    screenshot_sequence = [] 

    while True:
        try:
            # SPEECH LOCK
            if not core.tts._speech_queue.empty(): 
                time.sleep(0.5) 
                screenshot_sequence = [] 
                continue 

            # CAPTURE
            screenshot = capture_screenshot()
            if screenshot:
                context['latest_screenshot'] = screenshot 
                screenshot_sequence.append(screenshot)

                # CHECK IF FULL
                if len(screenshot_sequence) >= SCREENSHOT_SEQUENCE_LENGTH:
                    logger.debug(f"Sequence full. Analyzing...")
                    
                    # A. Save Debug Image
                    timestamp_str = datetime.datetime.now().strftime("%H-%M-%S")
                    filename = f"debug_captures/img_{timestamp_str}.png"
                    try:
                        screenshot.save(filename)
                    except Exception as e:
                        logger.error(f"Save failed: {e}")

                    # B. Measure Latency (Speed Test)
                    start_time = time.time() # <--- Start Timer
                    
                    # C. Analyze
                    response = analyze_image(screenshot_sequence) 
                    
                    end_time = time.time() # <--- Stop Timer
                    latency = round(end_time - start_time, 2) # Calculate duration
                    
                    if response:
                        logger.info(f"AI: {response} (Took {latency}s)")
                        
                        # D. Log to CSV Report
                        try:
                            with open(report_file, mode='a', newline='', encoding='utf-8') as f:
                                writer = csv.writer(f)
                                writer.writerow([
                                    timestamp_str,    # When
                                    filename,         # Proof (Image)
                                    response,         # Output
                                    latency,          # Performance metric
                                    AI_PROVIDER       # Which model used
                                ])
                                logger.info(f"📝 Logged to {report_file}")
                        except Exception as e:
                            logger.error(f"CSV Write Failed: {e}")

                        # E. Output to User
                        speak_in_thread(response) 
                        msg_queue.put(("AI", response))
                        
                        screenshot_sequence = []
                        time.sleep(2.0) 
                    
                    screenshot_sequence = [] 
                
        except Exception as e:
            logger.error(f"CRITICAL ERROR in vision loop: {e}")
            screenshot_sequence = []
            
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
        except Exception as e:
            logger.error(f"Error stopping: {e}")

# Main execution
if __name__ == "__main__":
    chat = ChatWindow(message_queue, app_context)

    logger.info(f"⏳ Waiting {STARTUP_DELAY}s...")
    time.sleep(STARTUP_DELAY)
    logger.info("🚀 Starting AI Vision Loop.")

    loop_thread = threading.Thread(
        target=main_loop, 
        args=(message_queue, app_context), 
        daemon=True,
        name="VisionLoopThread"
    )
    loop_thread.start()
    
    start_listener(chat.toggle) 

    try:
        chat.window.mainloop() 
    except Exception as e:
        logger.error(f"Crashed: {e}")
    finally:
        stop_listener() 
    
    logger.info("Shutting down...")