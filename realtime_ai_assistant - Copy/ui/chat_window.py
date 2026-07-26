import tkinter as tk
from tkinter import scrolledtext
import threading
import queue
from datetime import datetime

from utils.logger import get_logger

logger = get_logger()


class ChatWindow:
    """Tkinter-based chat window with dual mode support"""
    
    def __init__(self, message_queue: queue.Queue, app_context: dict, user_input_callback=None):
        """
        Initialize chat window
        
        Args:
            message_queue: Queue for receiving messages from AI
            app_context: Application context dictionary
            user_input_callback: Function to call when user types (optional)
        """
        self.message_queue = message_queue
        self.app_context = app_context
        self.user_input_callback = user_input_callback
        
        logger.info("🖼️  Creating Tkinter chat window...")
        
        # Create window
        self.window = tk.Tk()
        self.window.title("Maria")
        self.window.geometry("600x400")
        self.window.configure(bg="#0a0a0a")
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.70)  # 70% opacity - MORE TRANSPARENT!
        
        # Create UI elements
        self._create_widgets()
        
        # Start message polling thread
        self.polling_thread = threading.Thread(
            target=self._poll_messages,
            daemon=True,
            name="ChatWindowPoller"
        )
        self.polling_thread.start()
        
        logger.info("✅ Chat window created")
    
    def _create_widgets(self):
        """Create all UI elements - Simple & Transparent"""
        
        # Chat display area (read-only) - SIMPLER, NO TITLE
        self.chat_display = scrolledtext.ScrolledText(
            self.window,
            wrap=tk.WORD,
            width=65,
            height=15,
            font=("Courier", 11),
            bg="#0a0a0a",
            fg="#00FF00",
            insertbackground="#00FF00",
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0
        )
        self.chat_display.pack(padx=0, pady=0, fill=tk.BOTH, expand=True)
        self.chat_display.config(state=tk.DISABLED)
        
        # Configure text tags for colors
        self.chat_display.tag_configure("user", foreground="#00FFFF")
        self.chat_display.tag_configure("ai", foreground="#00FF00")
        self.chat_display.tag_configure("system", foreground="#FFFF00")
        self.chat_display.tag_configure("timestamp", foreground="#666666")
        
        # Input field - SIMPLE & CLEAN
        self.input_field = tk.Entry(
            self.window,
            font=("Courier", 10),
            bg="#0f0f0f",
            fg="#00FF00",
            insertbackground="#00FF00",
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0
        )
        self.input_field.pack(fill=tk.X, padx=0, pady=0)
        self.input_field.bind("<Return>", self._on_input_send)
        
        # Status bar - MINIMAL
        self.status_label = tk.Label(
            self.window,
            text="Type here... (Ctrl+Alt+G to toggle)",
            font=("Arial", 7),
            fg="#555555",
            bg="#0a0a0a"
        )
        self.status_label.pack(fill=tk.X, padx=5, pady=2)
        
        # Make window start hidden
        self.window.withdraw()
        
        # Handle window close
        self.window.protocol("WM_DELETE_WINDOW", self.toggle)
    
    def toggle(self):
        """Toggle window visibility (called by hotkey)"""
        if self.window.state() == "normal":
            self.window.withdraw()
            logger.info("💬 Chat window hidden")
        else:
            self.window.deiconify()
            self.window.lift()
            self.input_field.focus()
            logger.info("💬 Chat window shown")
    
    def display_message(self, sender: str, message: str, msg_type: str = "ai"):
        """
        Display message in chat window (thread-safe)
        
        Args:
            sender: Who sent it ("Maria", "You", "System")
            message: Message text
            msg_type: Type for coloring ("ai", "user", "system")
        """
        def _add_message():
            self.chat_display.config(state=tk.NORMAL)
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            # Add to display
            self.chat_display.insert(tk.END, f"[{timestamp}] ", "timestamp")
            self.chat_display.insert(tk.END, f"{sender}: ", msg_type)
            self.chat_display.insert(tk.END, f"{message}\n\n")
            
            # Auto-scroll to bottom
            self.chat_display.see(tk.END)
            self.chat_display.config(state=tk.DISABLED)
        
        # Schedule on main thread
        try:
            self.window.after(0, _add_message)
        except:
            pass
    
    def _on_input_send(self, event=None):
        """Handle user input submission"""
        text = self.input_field.get().strip()
        
        if not text:
            return
        
        # Display user message
        self.display_message("You", text, "user")
        
        # Send to callback if provided
        if self.user_input_callback:
            self.user_input_callback(text)
        
        # Clear input
        self.input_field.delete(0, tk.END)
        
        logger.info(f"💬 User input: {text}")
    
    def _poll_messages(self):
        """Poll message queue for new messages from AI (thread-safe)"""
        while True:
            try:
                # Check queue (non-blocking)
                try:
                    sender, message = self.message_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                
                # Display message
                if sender == "AI":
                    self.display_message("Maria", message, "ai")
                    logger.debug(f"💬 AI message displayed: {message}")
                elif sender == "User":
                    self.display_message("You", message, "user")
                elif sender == "System":
                    self.display_message("System", message, "system")
                else:
                    self.display_message(sender, message, "system")
            
            except Exception as e:
                logger.error(f"Error in message polling: {e}")
                continue
    
    def update_status(self, status: str):
        """Update status bar"""
        try:
            self.status_label.config(text=status)
            self.window.update_idletasks()
        except:
            pass


# Test script
if __name__ == "__main__":
    import time
    
    # Create test queue and context
    test_queue = queue.Queue()
    test_context = {"latest_screenshot": None}
    
    def test_callback(msg):
        print(f"User said: {msg}")
    
    # Create window
    chat = ChatWindow(test_queue, test_context, user_input_callback=test_callback)
    
    # Simulate messages
    def simulate_messages():
        time.sleep(2)
        test_queue.put(("AI", "Hey! Ready to play Minecraft?"))
        time.sleep(2)
        test_queue.put(("AI", "Just spotted some diamonds! 💎"))
        time.sleep(2)
        test_queue.put(("AI", "Watch out for that creeper!"))
    
    # Start simulation in thread
    sim_thread = threading.Thread(target=simulate_messages, daemon=True)
    sim_thread.start()
    
    # Show window
    chat.window.deiconify()
    
    # Run
    try:
        chat.window.mainloop()
    except KeyboardInterrupt:
        print("Closing...")
