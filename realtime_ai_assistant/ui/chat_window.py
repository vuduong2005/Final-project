import tkinter as tk
from tkinter import scrolledtext
import threading
import queue 
from utils.logger import get_logger
from core.ai_handler import send_chat_message_to_ai 
import html # Import html for escaping

logger = get_logger()

# --- NEW: Define font settings ---
FONT_FAMILY = "Arial"
FONT_SIZE_MAIN = 18
FONT_SIZE_SYSTEM = 12
# ---------------------------------

class ChatWindow:
    def __init__(self, message_queue, app_context):
        self.message_queue = message_queue 
        self.app_context = app_context 
        self.visible = False
        self.logger_message = "✅ AI Assistant running. Press Ctrl+Alt+G to toggle." 

        # Initialize the Tkinter root
        self.window = tk.Tk()
        self.window.withdraw() # Start hidden
        
        # Force window registration for hotkey
        self.window.update_idletasks() 
        
        # --- UI Overlay ---
        self.window.attributes("-transparentcolor", "pink") 
        self.window.attributes("-topmost", True) 
        
        # --- UI Setup ---
        self.window.title("Minecraft AI Companion")
        # --- FIX: Make window bigger ---
        self.window.geometry("500x400+0+0") # Was 400x300
        # -------------------------------
        self.window.resizable(False, False)
        self.window.config(bg="pink") 

        # 1. Input field (Packed FIRST)
        self.entry = tk.Entry(
            self.window, 
            # --- FIX: Increase font size ---
            font=(FONT_FAMILY, FONT_SIZE_MAIN), 
            # -------------------------------
            bg="#1e1e1e", 
            fg="#ffffff", 
            insertbackground="#ffffff", 
            bd=1, 
            relief="solid" 
        )
        self.entry.pack(fill="x", padx=5, pady=(0, 5), side=tk.BOTTOM) # Pack at the bottom
        self.entry.bind("<Return>", self._handle_user_input)

        # 2. Chat display area (Packed SECOND, fills remaining space)
        self.chat_area = scrolledtext.ScrolledText(
            self.window, 
            wrap="word", 
            state="disabled", 
            # --- FIX: Increase font size ---
            font=(FONT_FAMILY, FONT_SIZE_MAIN), 
            # -------------------------------
            bg="pink", 
            fg="#ffffff", 
            bd=0, 
            relief="flat",
            highlightthickness=0 
        )
        self.chat_area.pack(expand=True, fill="both", padx=5, pady=5, side=tk.TOP) 
        
        # Define tags for coloring messages
        self.chat_area.tag_config("user", foreground="#8be9fd", font=(FONT_FAMILY, FONT_SIZE_MAIN, "bold"))
        self.chat_area.tag_config("ai", foreground="#50fa7b", font=(FONT_FAMILY, FONT_SIZE_MAIN, "bold"))
        self.chat_area.tag_config("System", foreground="#ffaa00", font=(FONT_FAMILY, FONT_SIZE_SYSTEM))
        
        # Log initial message
        self.display_message("System", self.logger_message, "System")

        # --- Thread-Safe Polling ---
        self.window.after(100, self._poll_queue) 
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        
    def toggle(self):
        """Toggles the visibility of the chat window."""
        if self.visible:
            self.hide()
        else:
            self.show()

    def show(self):
        """Shows the hidden window and sets focus."""
        self.window.deiconify()
        self.visible = True
        self.entry.focus_set() 
        logger.debug("Chat window shown, focus set.")

    def hide(self):
        """Hides the chat window."""
        if self.window:
            self.window.withdraw()
            self.visible = False
            logger.debug("Chat window hidden.")

    def _handle_user_input(self, event=None):
        """Handles user message from the entry box."""
        user_text = self.entry.get()
        if not user_text.strip():
            return

        self.display_message("You", user_text, "user")
        self.entry.delete(0, "end")
        
        current_screenshot = self.app_context.get('latest_screenshot', None)
        
        if current_screenshot is None:
            self.display_message("System", "Error: No screenshot context. Wait.", "System")
            return
            
        self.display_message("System", "Sending message to AI...", "System")

        threading.Thread(
            target=send_chat_message_to_ai, 
            args=(user_text, current_screenshot, self.message_queue), 
            daemon=True,
            name="ChatThread"
        ).start() 

    def display_message(self, sender, text, tag_name="ai"):
        """Displays a message in the chat area in a thread-safe manner."""
        if self.window is None or not self.window.winfo_exists():
            return
            
        try:
            is_disabled = self.chat_area.cget("state") == "disabled"
            if is_disabled:
                self.chat_area.config(state="normal")
            
            safe_text = html.escape(text) 

            self.chat_area.insert("end", f"{sender}: ", tag_name)
            self.chat_area.insert("end", f"{safe_text}\n")
            
            self.chat_area.see("end")
            
            if is_disabled:
                 self.chat_area.config(state="disabled")
        except tk.TclError as e:
            logger.error(f"Tkinter error displaying message: {e}")
        except Exception as e:
            logger.error(f"Unexpected error displaying message: {e}")


    def _poll_queue(self):
        """Checks the message queue for new messages from background threads."""
        if self.window is None: 
             return
        try:
            while True:
                sender, text = self.message_queue.get_nowait()
                tag_name = "user" if sender == "You" else ("ai" if sender == "AI" else "System")
                self.display_message(sender, text, tag_name)
        except queue.Empty:
            pass 
        except Exception as e:
            logger.error(f"Error polling queue: {e}")
        finally:
            if self.window:
                 self.window.after(100, self._poll_queue)

