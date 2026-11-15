import tkinter as tk
from tkinter import scrolledtext
import threading
import time
import queue
from datetime import datetime

from utils.logger import get_logger
from core import settings
from ui import win_focus

logger = get_logger()


class ChatWindow:
    """Tkinter-based chat window with dual mode support"""
    
    # How long the Stop button stays armed (milliseconds) waiting for the confirming second click
    STOP_CONFIRM_MS = 3000
    STOP_TEXT = "■ Stop Maria"
    STOP_ARMED_TEXT = "Click again to stop"
    VOICE_OFF_TEXT = "🎤 Voice: OFF"
    VOICE_ON_TEXT = "● Voice: ON"
    VOICE_READY_TEXT = "🎤 Hold {key} to talk"
    VOICE_LISTENING_TEXT = "● Listening..."
    VOICE_BUSY_TEXT = "… Understanding"
    TYPE_HINT = "Click the box to type, Enter sends and returns to the game (Ctrl+Alt+G to toggle)"
    OPEN_MIC_HINT = "Voice is ON: just speak, a pause sends it. Talk over her to stop her. Ctrl+Alt+V turns it off"
    PTT_HINT = "Voice is ON: hold {key} and speak, let go to send. Ctrl+Alt+V turns it off"
    PTT_LISTENING_HINT = "Listening... let go of {key} to send"
    
    def __init__(self, message_queue: queue.Queue, app_context: dict, user_input_callback=None,
                 stop_callback=None, interrupt_callback=None):
        """
        Initialize chat window
        
        Args:
            message_queue: Queue for receiving messages from AI
            app_context: Application context dictionary
            user_input_callback: Function to call when user types (optional)
            stop_callback: Function to call when the user confirms the Stop button (optional).
                           Without it the Stop button just ends this window's main loop.
            interrupt_callback: Function to call when the player types or sends something, to cut off what
                                Maria is saying (optional)
        """
        self.message_queue = message_queue
        self.app_context = app_context
        self.user_input_callback = user_input_callback
        self.stop_callback = stop_callback
        self._stop_armed = False
        self._stop_disarm_job = None
        self.voice = None                      # the VoiceInput controller, set by main (set_voice)
        self.interrupt_callback = interrupt_callback
        self._typing_focus = False             # the player is typing in the box (the push-to-talk key must not fire)
        self._last_interrupt = 0.0
        
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
        # Clicking the field takes keyboard focus so the player can type;
        # Enter (after sending) or Escape gives it back to the game.
        win_focus.typing_field(self.input_field)
        # Typing interrupts Maria: she stops talking and listens
        self.input_field.bind("<Key>", self._on_key, add="+")
        self.input_field.bind("<FocusIn>", lambda e: setattr(self, "_typing_focus", True), add="+")
        self.input_field.bind("<FocusOut>", lambda e: setattr(self, "_typing_focus", False), add="+")
        
        # Bottom bar: status text on the left, Stop button on the right
        bottom_bar = tk.Frame(self.window, bg="#0a0a0a")
        bottom_bar.pack(fill=tk.X, padx=5, pady=2)
        
        self.status_label = tk.Label(
            bottom_bar,
            text="Click the box to type, Enter sends and returns to the game (Ctrl+Alt+G to toggle)",
            font=("Arial", 7),
            fg="#555555",
            bg="#0a0a0a",
            anchor="w"
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Two clicks on purpose: the first one arms it, the second one stops Maria.
        self.stop_button = tk.Button(
            bottom_bar,
            text=self.STOP_TEXT,
            font=("Arial", 8),
            fg="#FF6B6B",
            bg="#1a0a0a",
            activeforeground="#FFFFFF",
            activebackground="#7a1f1f",
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=1,
            cursor="hand2",
            command=self._on_stop_click
        )
        self.stop_button.pack(side=tk.RIGHT)

        # One on/off button for talking to her out loud (the controller lives in core/voice_input.py)
        self.voice_button = tk.Button(
            bottom_bar, text=self.VOICE_OFF_TEXT, font=("Arial", 8, "bold"), fg="#888888", bg="#1a1a1a",
            activeforeground="#FFFFFF", activebackground="#2a2a2a", relief=tk.FLAT, bd=0,
            padx=8, pady=1, cursor="hand2", command=self._on_voice_click
        )
        self.voice_button.pack(side=tk.RIGHT, padx=(0, 6))
        self._voice_state = "off"
        
        # Start hidden, and never take keyboard focus away from the game when clicked or shown
        win_focus.start_hidden(self.window)
        
        # Handle window close
        self.window.protocol("WM_DELETE_WINDOW", self.toggle)
    
    def _on_stop_click(self):
        """First click arms the button, a second click within STOP_CONFIRM_MS stops Maria."""
        if not self._stop_armed:
            self._stop_armed = True
            self.stop_button.config(text=self.STOP_ARMED_TEXT, fg="#FFFFFF", bg="#7a1f1f")
            self._stop_disarm_job = self.window.after(self.STOP_CONFIRM_MS, self._disarm_stop)
            return
        
        self._disarm_stop()
        logger.info("⏹️  Stop button confirmed - stopping Maria")
        self.display_message("System", "Stopping Maria... bye!", "system")
        self.stop_button.config(state=tk.DISABLED)   # no double stop
        # Give the farewell line a moment to show, then stop
        self.window.after(300, self._do_stop)
    
    def _disarm_stop(self):
        """Put the Stop button back to its resting look."""
        if self._stop_disarm_job is not None:
            try:
                self.window.after_cancel(self._stop_disarm_job)
            except Exception:
                pass
            self._stop_disarm_job = None
        self._stop_armed = False
        try:
            self.stop_button.config(text=self.STOP_TEXT, fg="#FF6B6B", bg="#1a0a0a")
        except Exception:
            pass  # window already closed
    
    def _do_stop(self):
        if self.stop_callback:
            self.stop_callback()
        else:
            self.window.quit()
    
    def toggle(self):
        """Toggle window visibility (called by hotkey)"""
        # Shown without taking focus: the game keeps the keyboard until the player clicks the text box
        if win_focus.toggle(self.window):
            logger.info("💬 Chat window shown")
        else:
            logger.info("💬 Chat window hidden")
    
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
        except Exception:
            pass  # window already closed
    
    def _on_input_send(self, event=None):
        """Handle user input submission"""
        text = self.input_field.get().strip()
        
        if not text:
            return
        
        self.submit_text(text)
        
        
        # Clear input
        self.input_field.delete(0, tk.END)
        
    
    def submit_text(self, text: str, sender: str = "You"):
        """A message from the player, typed or spoken: shown in the chat and sent on to Maria."""
        self._interrupt()                       # a new message from the player: what she was saying is out of date
        self.display_message(sender, text, "user")
        if self.user_input_callback:
            self.user_input_callback(text)
        logger.info(f"💬 User input ({'voice' if sender != 'You' else 'typed'}): {text}")

    # ---- voice input (the controller lives in core/voice_input.py) ----

    def _later(self, fn):
        """Run fn on the window's own thread (voice callbacks come from other threads)."""
        try:
            self.window.after(0, fn)
        except Exception:
            pass                                   # the window is closing

    def _interrupt(self):
        """Cut off what Maria is saying (danger warnings are not cut)."""
        if self.interrupt_callback:
            try:
                self.interrupt_callback()
            except Exception as e:
                logger.debug(f"Interrupt callback failed: {e}")

    def _on_key(self, event):
        """The player types: she stops talking. Only real typing counts, and not more than twice a second."""
        typed = (event.char and event.char >= " ") or event.keysym in ("BackSpace", "Delete")
        if not typed or time.monotonic() - self._last_interrupt < 0.5:
            return
        self._last_interrupt = time.monotonic()
        self._interrupt()

    def is_typing(self) -> bool:
        """Is the player typing in this window's box? (Then the push-to-talk key is just a letter.)"""
        return self._typing_focus

    # ---- voice input (the controller lives in core/voice_input.py) ----

    def set_voice(self, voice):
        self.voice = voice
        self.on_voice_state(voice.state)

    def _on_voice_click(self):
        """The Voice button: on, or off."""
        if self.voice is None:
            self.on_voice_message("Voice input is not available in this build.")
            return
        self.voice.switch()

    def submit_voice_text(self, text: str):
        """What the player said, as text (called from the voice thread)."""
        self._later(lambda: self.submit_text(text, sender="You (voice)"))

    def on_voice_state(self, state: str):
        """off / ready / listening / transcribing: the Voice button and the hint line follow it."""
        def apply():
            self._voice_state = state
            key = str(settings.get("PUSH_TO_TALK_KEY")).upper()
            push = bool(settings.get("PUSH_TO_TALK"))
            if state == "off":
                self.voice_button.config(text=self.VOICE_OFF_TEXT, fg="#888888", bg="#1a1a1a")
                self.status_label.config(text=self.TYPE_HINT, fg="#555555")
            elif state == "ready":
                self.voice_button.config(text=self.VOICE_READY_TEXT.format(key=key), fg="#00FF88", bg="#0a1a10")
                self.status_label.config(text=self.PTT_HINT.format(key=key), fg="#00AA66")
            elif state == "listening" and push:
                self.voice_button.config(text=self.VOICE_LISTENING_TEXT, fg="#FF6B6B", bg="#2a0f0f")
                self.status_label.config(text=self.PTT_LISTENING_HINT.format(key=key), fg="#FF6B6B")
            elif state == "listening":
                self.voice_button.config(text=self.VOICE_ON_TEXT, fg="#FF6B6B", bg="#2a0f0f")
                self.status_label.config(text=self.OPEN_MIC_HINT, fg="#00AA66")
            else:
                self.voice_button.config(text=self.VOICE_BUSY_TEXT, fg="#FFFF00", bg="#1a1a0a")
                self.status_label.config(text="Understanding what you said...", fg="#FFFF00")
        self._later(apply)

    def on_voice_message(self, message: str):
        """A short problem or hint for the player (no microphone, nothing heard...). Shown in the chat."""
        self._later(lambda: self.display_message("System", message, "system"))

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
        except Exception:
            pass  # window already closed


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
    win_focus.show(chat.window)
    
    # Run
    try:
        chat.window.mainloop()
    except KeyboardInterrupt:
        print("Closing...")
