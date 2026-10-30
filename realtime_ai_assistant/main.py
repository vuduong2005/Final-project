import sys
import time
import threading
import queue
import tkinter as tk
import os
import datetime
from pynput.keyboard import GlobalHotKeys

from core.screenshot import capture_screenshot
from core.ai_handler import AIHandler
from core.tts import speak_in_thread
import core.tts
from ui.chat_window import ChatWindow
from ui.status_window import StatusWindow  
from config import (
    SCREENSHOT_INTERVAL,
    STARTUP_DELAY,
    SCREENSHOT_SEQUENCE_LENGTH,
    DEBUG_SCREENSHOT_DIR,
    DEBUG_SCREENSHOT_MAX_FILES,
    MIN_ANALYSIS_INTERVAL
)
from utils.logger import get_logger

logger = get_logger()

# ============================================================================
# SESSION REPORT (readable .txt, one fresh file per run)
# ============================================================================
_SESSION_START = datetime.datetime.now()
REPORT_TXT_DIR = "logs"
REPORT_TXT_PATH = os.path.join(
    REPORT_TXT_DIR,
    f"session_{_SESSION_START.strftime('%Y-%m-%d_%H-%M-%S')}.txt"
)


# Global state
message_queue = queue.Queue()
user_input_queue = queue.Queue()

app_context = {
    'latest_screenshot': None,
    'is_minecraft': False,
    'running': True,
    'recent_game_state': "Player is exploring"
}

hotkey_listener = None
ai_handler = None


# ============================================================================
# CHAT PROCESSOR THREAD
# ============================================================================

def chat_processor_thread(input_queue: queue.Queue, msg_queue: queue.Queue, context: dict):
    """Process user chat input and generate friendly responses"""
    threading.current_thread().name = "ChatProcessorThread"
    logger.info("💬 Chat processor thread started")
    
    while context.get('running', True):
        try:
            try:
                user_message = input_queue.get(timeout=1)
            except queue.Empty:
                continue
            
            if not user_message:
                continue
            
            logger.info(f"👤 User input: '{user_message}'")
            
            try:
                # ✅ Hybrid vision: pass the latest screenshot so Chat Mode can
                # answer questions about things Roboflow doesn't detect
                # (e.g. "do you like the house I built?").
                latest_screenshot = context.get('latest_screenshot')
                response = ai_handler.generate_chat_response(user_message, image=latest_screenshot)
                
                if response:
                    logger.info(f"🤖 Maria responds: '{response}'")
                    
                    msg_queue.put(("AI", response))
                    speak_in_thread(response)
                    
                    timestamp_str = datetime.datetime.now().strftime("%H:%M:%S")
                    _log_chat_to_report(
                        timestamp=timestamp_str,
                        user_message=user_message,
                        ai_response=response,
                        game_context=context.get('recent_game_state', '')
                    )
                else:
                    logger.debug("🤷 No response generated")
            
            except Exception as e:
                logger.error(f"❌ Error processing user chat: {e}", exc_info=True)
        
        except Exception as e:
            logger.error(f"❌ Chat processor error: {e}", exc_info=True)
    
    logger.info("💬 Chat processor thread stopped")


def _prune_debug_captures(max_files: int = DEBUG_SCREENSHOT_MAX_FILES):
    """Delete the oldest debug screenshots so the folder never grows past max_files."""
    try:
        files = [
            os.path.join(DEBUG_SCREENSHOT_DIR, name)
            for name in os.listdir(DEBUG_SCREENSHOT_DIR)
            if name.startswith("img_") and name.endswith(".png")
        ]
        if len(files) <= max_files:
            return
        files.sort(key=os.path.getmtime)
        for old_file in files[:len(files) - max_files]:
            try:
                os.remove(old_file)
            except OSError as e:
                logger.warning(f"Could not remove old debug screenshot {old_file}: {e}")
    except Exception as e:
        logger.error(f"Failed to prune debug screenshots: {e}")


# ============================================================================
# MAIN VISION LOOP
# ============================================================================

def vision_loop(msg_queue: queue.Queue, context: dict, status_window: StatusWindow = None):
    """Monitor game state and generate responses"""
    threading.current_thread().name = "VisionLoopThread"
    logger.info("=" * 70)
    logger.info("👁️  VISION LOOP STARTED (3-Model Detection)")
    logger.info("=" * 70)
    
    os.makedirs(DEBUG_SCREENSHOT_DIR, exist_ok=True)
    _initialize_report()
    
    last_analysis_time = 0
    min_interval_between_analyses = MIN_ANALYSIS_INTERVAL  # was hardcoded 2.0 — now respects config.py / admin overrides
    
    logger.info("🎮 Vision loop started - capturing screenshots every 0.5s")
    logger.info("📸 FrameBuffer will auto-analyze when 3 frames are ready")
    logger.info("🔍 Using 3 Roboflow models: General + Mobs + Blocks")
    
    while context.get('running', True):
        try:
            if not core.tts._speech_queue.empty():
                logger.debug("🔊 TTS playing, skipping capture...")
                time.sleep(0.5)
                continue
            
            # Capture screenshot
            screenshot = capture_screenshot()
            if not screenshot:
                logger.warning("⚠️  Failed to capture screenshot")
                time.sleep(SCREENSHOT_INTERVAL)
                continue
            
            context['latest_screenshot'] = screenshot
            logger.debug(f"📸 Captured screenshot")
            
            # Check if enough time has passed since last analysis
            current_time = time.time()
            time_since_last = current_time - last_analysis_time
            
            if time_since_last < min_interval_between_analyses:
                logger.debug(f"⏱️  Waiting {min_interval_between_analyses - time_since_last:.1f}s before next analysis")
                time.sleep(SCREENSHOT_INTERVAL)
                continue
            
            # Analyze immediately (FrameBuffer will internally buffer 3 frames)
            timestamp_str = datetime.datetime.now().strftime("%H-%M-%S")
            # Include the date so captures from different days never overwrite each other
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            debug_filename = f"{DEBUG_SCREENSHOT_DIR}/img_{date_str}_{timestamp_str}.png"

            try:
                screenshot.save(debug_filename)
                logger.debug(f"💾 Debug screenshot saved: {debug_filename}")
                _prune_debug_captures()
            except Exception as e:
                logger.error(f"Failed to save debug image: {e}")
            
            logger.info("🔄 Running 3-stage AI analysis with FrameBuffer...")
            analysis_start = time.time()
            
            try:
                result = ai_handler.analyze_screenshot(screenshot)
                
                analysis_time = time.time() - analysis_start

                # ✅ NEW: Push a status update every analysis cycle, regardless of
                # whether Maria decided to speak. This keeps the dashboard live
                # even during "quiet" moments (no urgent response generated).
                if result and status_window:
                    try:
                        trends = {}
                        if ai_handler.frame_buffer.is_full():
                            trends = ai_handler.frame_buffer.analyze_trends()

                        game_state = result.get("game_state", "")
                        activity = ai_handler.generator.detect_activity(game_state)

                        detections_list = [
                            {
                                "class": d["class"],
                                "confidence": d["confidence"],
                                "location": ai_handler.parser._get_simple_location(d["bbox"])
                            }
                            for d in result.get("detections", {}).get("detections", [])
                        ]

                        status_window.push_update(
                            activity=activity,
                            threat_level=trends.get("threat_level", "none"),
                            detections=detections_list,
                            game_state=game_state
                        )
                    except Exception as e:
                        logger.error(f"⚠️  Failed to push status update: {e}")
                
                if result and result.get("response"):
                    response = result["response"]
                    logger.info(f"📢 Game Response: '{response}'")
                    logger.info(f"⏱️  Analysis took {analysis_time:.2f}s")
                    
                    context['recent_game_state'] = result.get("game_state", "")
                    
                    speak_in_thread(response)
                    msg_queue.put(("AI", response))
                    
                    _log_analysis_to_report(
                        timestamp=timestamp_str,
                        image_file=debug_filename,
                        response=response,
                        game_state=result.get("game_state", ""),
                        timing=result.get("timing", {}),
                        num_detections=len(result.get("detections", {}).get("detections", []))
                    )
                    
                    last_analysis_time = time.time()
                    time.sleep(min_interval_between_analyses)
                else:
                    logger.debug("💤 No urgent game state")
                    if result:
                        context['recent_game_state'] = result.get("game_state", "")

                # ✅ PROACTIVE CHAT COMMENTS: only check when Game Mode itself
                # didn't just speak this cycle, so Maria never says two things
                # back-to-back for the same screenshot.
                if not (result and result.get("response")):
                    proactive = ai_handler.check_proactive_comment()
                    if proactive:
                        logger.info(f"💭 Proactive comment: '{proactive}'")
                        speak_in_thread(proactive)
                        msg_queue.put(("AI", proactive))
            
            except Exception as e:
                logger.error(f"❌ AI analysis failed: {e}", exc_info=True)
        
        except Exception as e:
            logger.error(f"❌ CRITICAL ERROR: {e}", exc_info=True)
        
        time.sleep(SCREENSHOT_INTERVAL)
    
    logger.info("=" * 70)
    logger.info("👁️  VISION LOOP STOPPED")
    logger.info("=" * 70)


# ============================================================================
# SESSION REPORT LOGGING (readable .txt)
# ============================================================================

def _initialize_report():
    """Create a fresh, human-readable session report for this run."""
    try:
        os.makedirs(REPORT_TXT_DIR, exist_ok=True)
        with open(REPORT_TXT_PATH, mode='w', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write("MINECRAFT AI COMPANION - SESSION REPORT\n")
            f.write(f"Session started: {_SESSION_START.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 70 + "\n\n")
        logger.info(f"📝 Session report initialized: {REPORT_TXT_PATH}")
    except Exception as e:
        logger.error(f"Failed to initialize session report: {e}")


def _log_analysis_to_report(timestamp, image_file, response, game_state, timing, num_detections):
    """Append a readable Game Mode entry to the session report."""
    try:
        with open(REPORT_TXT_PATH, mode='a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] GAME MODE\n")
            f.write(f"  Detected:   {game_state}\n")
            f.write(f"  Maria said: \"{response}\"\n")
            f.write(f"  Objects seen: {num_detections}\n")
            f.write(f"  Time taken:   {round(timing.get('total', 0), 2)}s\n")
            f.write(f"  Screenshot:   {image_file}\n")
            f.write("-" * 70 + "\n\n")
        logger.debug("✅ Logged game event to session report")
    except Exception as e:
        logger.error(f"Failed to log to session report: {e}")


def _log_chat_to_report(timestamp, user_message, ai_response, game_context):
    """Append a readable Chat Mode entry to the session report."""
    try:
        with open(REPORT_TXT_PATH, mode='a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] CHAT MODE\n")
            f.write(f"  You said:   \"{user_message}\"\n")
            f.write(f"  Maria said: \"{ai_response}\"\n")
            if game_context:
                f.write(f"  Game context at the time: {game_context}\n")
            f.write("-" * 70 + "\n\n")
        logger.debug("✅ Logged chat to session report")
    except Exception as e:
        logger.error(f"Failed to log chat to session report: {e}")


def _finalize_report():
    """Write a closing footer to the session report when the app exits."""
    try:
        ended = datetime.datetime.now()
        duration = ended - _SESSION_START
        with open(REPORT_TXT_PATH, mode='a', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write(f"Session ended: {ended.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Duration: {str(duration).split('.')[0]}\n")
            f.write("=" * 70 + "\n")
        logger.info(f"📝 Session report finalized: {REPORT_TXT_PATH}")
    except Exception as e:
        logger.error(f"Failed to finalize session report: {e}")


# ============================================================================
# HOTKEY MANAGEMENT
# ============================================================================

def start_hotkey_listener(chat_toggle_func, status_toggle_func):
    global hotkey_listener
    hotkey_map = {
        '<ctrl>+<alt>+g': chat_toggle_func,
        '<ctrl>+<alt>+s': status_toggle_func
    }
    
    try:
        hotkey_listener = GlobalHotKeys(hotkey_map)
        hotkey_listener.start()
        logger.info("👂 Hotkey listener started (Ctrl+Alt+G for Chat, Ctrl+Alt+S for Status)")
    except Exception as e:
        logger.error(f"❌ Failed to start hotkey listener: {e}")


def stop_hotkey_listener():
    global hotkey_listener
    if hotkey_listener:
        try:
            if hasattr(hotkey_listener, 'running') and hotkey_listener.running:
                hotkey_listener.stop()
                if hasattr(hotkey_listener, 'join'):
                    hotkey_listener.join(timeout=0.5)
            else:
                hotkey_listener.stop()
            logger.info("👂 Hotkey listener stopped")
        except Exception as e:
            logger.error(f"Error stopping hotkey listener: {e}")


# ============================================================================
# APPLICATION STARTUP
# ============================================================================

def main():
    global ai_handler
    
    logger.info("=" * 70)
    logger.info("🎮 MINECRAFT AI COMPANION - STARTING (Dual Mode + 3 Models)")
    logger.info("=" * 70)
    logger.info("   Mode 1: Game responses (threats/resources)")
    logger.info("   Mode 2: Chat interaction (friend companion)")
    logger.info("   Detection: 3 Roboflow models (General + Mobs + Blocks)")
    logger.info("=" * 70)
    
    logger.info("🤖 Initializing AI Handler with MultiModelDetector...")
    try:
        ai_handler = AIHandler()
        logger.info("✅ AI Handler initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize AI Handler: {e}")
        sys.exit(1)
    
    logger.info("🖼️  Creating UI windows...")
    
    def handle_user_input(message: str):
        user_input_queue.put(message)
    
    # Initialize Chat UI
    chat = ChatWindow(message_queue, app_context, user_input_callback=handle_user_input)
    
    # Initialize Status Window UI
    status_window = StatusWindow(app_context)
    
    logger.info(f"⏳ Waiting {STARTUP_DELAY}s before starting vision loop...")
    logger.info("   (Switch to Minecraft window now)")
    time.sleep(STARTUP_DELAY)
    
    logger.info("🚀 Starting vision loop thread...")
    loop_thread = threading.Thread(
        target=vision_loop,
        args=(message_queue, app_context, status_window),  # ✅ status_window now passed in
        daemon=True,
        name="VisionLoopThread"
    )
    loop_thread.start()
    
    logger.info("🚀 Starting chat processor thread...")
    chat_thread = threading.Thread(
        target=chat_processor_thread,
        args=(user_input_queue, message_queue, app_context),
        daemon=True,
        name="ChatProcessorThread"
    )
    chat_thread.start()
    
    logger.info("🎹 Starting hotkey listener...")
    start_hotkey_listener(chat.toggle, status_window.toggle)
    
    logger.info("=" * 70)
    logger.info("✅ APPLICATION READY")
    logger.info("   Game Mode: AI watches Minecraft and responds to events")
    logger.info("   Chat Mode: Type messages and AI responds as your friend!")
    logger.info("   Detection: Using 3 specialized Roboflow models")
    logger.info("   Press Ctrl+Alt+G to toggle chat window")
    logger.info("   Press Ctrl+Alt+S to toggle status window")
    logger.info("=" * 70)
    
    try:
        chat.window.mainloop()
    except KeyboardInterrupt:
        logger.info("⏹️  User interrupted (Ctrl+C)")
    except Exception as e:
        logger.error(f"❌ Application crashed: {e}", exc_info=True)
    finally:
        logger.info("🧹 Cleaning up...")
        app_context['running'] = False
        stop_hotkey_listener()
        _finalize_report()
        logger.info("=" * 70)
        logger.info("👋 MINECRAFT AI COMPANION - STOPPED")
        logger.info("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"❌ FATAL ERROR: {e}", exc_info=True)
        sys.exit(1)