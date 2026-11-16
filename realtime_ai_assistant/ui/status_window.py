import tkinter as tk
from tkinter import ttk
import threading
import queue
import time
import json
import os
from datetime import datetime
from collections import deque

from utils.logger import get_logger
import config as config_module
from core import settings, voice_input
from ui import win_focus

logger = get_logger()

# Keys of the restart-required config panel. Each key must also be listed in
# _ADMIN_OVERRIDABLE_KEYS in config.py. Everything else (voice, timing,
# detection, frame buffer) is live and handled by core/settings.py, with its
# own sections in this window.
ADMIN_CONFIG_FIELDS = {
    "STARTUP_DELAY": {"type": "float", "label": "Startup Delay (s)", "group": "Timing"},
}

ADMIN_CONFIG_GROUP_ORDER = ["Timing"]

# Live detection settings shown in the DETECTION & TIMING section
DETECTION_SLIDER_KEYS = (
    "SCREENSHOT_INTERVAL", "MIN_ANALYSIS_INTERVAL",
    "ROBOFLOW_CONFIDENCE_PERCENT", "MOB_CONFIDENCE_THRESHOLD", "THREAT_INSTANT_CONFIDENCE", "BLOCK_CONFIDENCE_THRESHOLD",
    "FRAME_BUFFER_SIZE_CHANGE_PERCENT", "FRAME_BUFFER_CONFIDENCE_THRESHOLD",
)
MODEL_KEYS = ("USE_GENERAL_MODEL", "USE_MOBS_MODEL", "USE_BLOCKS_MODEL")
# Slider knob: one plain green, with no hover colour change, so it stays visible on the dark panel
SLIDER_KNOB = "#2ECC71"
# The first entry of the microphone list: whichever microphone Windows has as its default right now
MIC_DEFAULT_LABEL = "Windows default"
# Voice styles in the Voice section: (value of TTS_VOICE, label)
VOICE_CHOICES = (("now", "1 Normal"), ("livelier", "2 Livelier"), ("bright", "3 Bright"))
# Which voice model: (value of TTS_MODEL, label); shown only when her own voice (maria.onnx) is installed
MODEL_CHOICES = (("amy", "Amy (standard)"), ("maria", "Maria (her own)"))


def _maria_installed() -> bool:
    try:
        from core import tts  # imported here so this window can run on its own
        return tts.maria_available()
    except Exception:
        return False


CONFIG_OVERRIDES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config_overrides.json"
)


class StatusWindow:
    """
    Tkinter-based activity/status dashboard for Maria.

    Separate from ChatWindow. Shows:
      - Current activity (mining / combat / exploring / etc.)
      - Threat level (color-coded)
      - Live detections (what's on screen right now)
      - Recent activity history log

    Has its own toggle() method — bind this to a different hotkey
    than the chat window's toggle.
    """

    THREAT_COLORS = {
        "critical": "#FF3B30",
        "high":     "#FF9500",
        "medium":   "#FFCC00",
        "low":      "#34C759",
        "none":     "#555555",
    }

    ACTIVITY_ICONS = {
        "mining/chopping wood": "🪓",
        "mining": "⛏️",
        "combat": "⚔️",
        "animal nearby": "🐄",
        "exploring": "🧭",
    }

    def __init__(self, app_context: dict, status_queue: queue.Queue = None):
        """
        Args:
            app_context: shared app context dict (reads 'recent_game_state' etc.)
            status_queue: optional queue of dicts pushed from the vision loop /
                          ai_handler, e.g.:
                          {
                              "activity": "mining",
                              "threat_level": "high",
                              "detections": [{"class": "creeper", "confidence": 0.87, "location": "left middle"}],
                              "game_state": "creeper in left middle"
                          }
        """
        self.app_context = app_context
        self.status_queue = status_queue or queue.Queue()
        self.history = deque(maxlen=25)

        logger.info("🖼️  Creating Status dashboard window...")

        self.window = tk.Tk() if not tk._default_root else tk.Toplevel()
        self.window.title("Maria - Status")
        self.window.geometry("520x900+20+20")
        self.window.configure(bg="#0a0a0a")
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.85)

        self._create_widgets()
        self._fit_height()

        self.window.protocol("WM_DELETE_WINDOW", self.toggle)
        # Start hidden, and never take keyboard focus away from the game when clicked or shown
        win_focus.start_hidden(self.window)

        self.polling_thread = threading.Thread(
            target=self._poll_status,
            daemon=True,
            name="StatusWindowPoller"
        )
        self.polling_thread.start()

        logger.info("✅ Status window created")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _create_widgets(self):
        pad = {"padx": 10, "pady": 6}
        self._sections = {}  # collapsible sections, see _create_collapsible_section

        # --- Header ---
        header = tk.Label(
            self.window, text="MARIA — STATUS",
            font=("Consolas", 12, "bold"),
            fg="#00FFAA", bg="#0a0a0a"
        )
        header.pack(fill=tk.X, **pad)

        # --- Current Activity Card ---
        activity_frame = tk.Frame(self.window, bg="#151515", bd=1, relief=tk.FLAT)
        activity_frame.pack(fill=tk.X, padx=10, pady=(0, 8))

        tk.Label(
            activity_frame, text="CURRENT ACTIVITY",
            font=("Consolas", 8), fg="#888888", bg="#151515"
        ).pack(anchor="w", padx=10, pady=(4, 0))

        self.activity_label = tk.Label(
            activity_frame, text="🧭  Exploring",
            font=("Consolas", 16, "bold"),
            fg="#00FF00", bg="#151515"
        )
        self.activity_label.pack(anchor="w", padx=10, pady=(0, 10))

        # --- Threat Level Card ---
        threat_frame = tk.Frame(self.window, bg="#151515", bd=1, relief=tk.FLAT)
        threat_frame.pack(fill=tk.X, padx=10, pady=(0, 8))

        tk.Label(
            threat_frame, text="THREAT LEVEL",
            font=("Consolas", 8), fg="#888888", bg="#151515"
        ).pack(anchor="w", padx=10, pady=(4, 0))

        threat_row = tk.Frame(threat_frame, bg="#151515")
        threat_row.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.threat_dot = tk.Canvas(threat_row, width=14, height=14, bg="#151515", highlightthickness=0)
        self.threat_dot.pack(side=tk.LEFT, padx=(0, 8))
        self._threat_circle = self.threat_dot.create_oval(2, 2, 12, 12, fill="#555555", outline="")

        self.threat_label = tk.Label(
            threat_row, text="NONE",
            font=("Consolas", 13, "bold"),
            fg="#555555", bg="#151515"
        )
        self.threat_label.pack(side=tk.LEFT)

        # --- Live Detections List ---
        tk.Label(
            self.window, text="LIVE DETECTIONS",
            font=("Consolas", 8), fg="#888888", bg="#0a0a0a"
        ).pack(anchor="w", padx=14)

        self.detections_box = tk.Listbox(
            self.window, height=4,
            font=("Consolas", 10),
            bg="#0f0f0f", fg="#00FFAA",
            relief=tk.FLAT, bd=0, highlightthickness=0,
            selectbackground="#222222"
        )
        self.detections_box.pack(fill=tk.X, padx=10, pady=(2, 8))

        # --- History Log ---
        tk.Label(
            self.window, text="ACTIVITY LOG",
            font=("Consolas", 8), fg="#888888", bg="#0a0a0a"
        ).pack(anchor="w", padx=14)

        log_frame = tk.Frame(self.window, bg="#0a0a0a", height=80)
        log_frame.pack(fill=tk.X, padx=10, pady=(2, 10))
        log_frame.pack_propagate(False)

        scrollbar = tk.Scrollbar(log_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_box = tk.Listbox(
            log_frame, font=("Consolas", 9),
            bg="#0f0f0f", fg="#666666",
            relief=tk.FLAT, bd=0, highlightthickness=0,
            yscrollcommand=scrollbar.set
        )
        self.log_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.log_box.yview)

        # --- Live voice settings (apply immediately) ---
        self._create_live_section()

        # --- Live detection / timing settings (collapsed by default) ---
        self._create_detection_section()

        # --- Admin Config Panel (restart required, collapsed by default) ---
        self._create_config_section()

        # --- Footer ---
        self.status_label = tk.Label(
            self.window, text="Ctrl+Alt+S to toggle this panel",
            font=("Arial", 7), fg="#444444", bg="#0a0a0a"
        )
        self.status_label.pack(fill=tk.X, padx=10, pady=(0, 6))

    # ------------------------------------------------------------------
    # Live settings (apply immediately, no restart)
    # ------------------------------------------------------------------
    def _create_live_section(self):
        """Volume / speed sliders, mute and test voice. Every change goes through
        core/settings.py and applies to the next line Maria speaks."""
        tk.Label(
            self.window, text="VOICE (LIVE — APPLIES IMMEDIATELY)",
            font=("Consolas", 9, "bold"), fg="#00FFAA", bg="#0a0a0a"
        ).pack(anchor="w", padx=10, pady=(4, 0))

        frame = tk.Frame(self.window, bg="#151515", bd=1, relief=tk.FLAT)
        frame.pack(fill=tk.X, padx=10, pady=(4, 8))

        self.live_sliders = {}  # key -> (tk.DoubleVar, format function, value label)
        self._add_live_slider(frame, "TTS_VOLUME", "Volume", lambda v: f"{int(round(v * 100))}%")
        self._add_live_slider(frame, "TTS_SPEED", "Speed", lambda v: f"{v:.2f}x")

        # Which voice she uses (applies to the very next line)
        self.model_var = tk.StringVar(value=settings.get("TTS_MODEL"))
        if _maria_installed():
            tk.Label(frame, text="VOICE", font=("Consolas", 8, "bold"), fg="#888888",
                     bg="#151515").pack(anchor="w", padx=10, pady=(6, 0))
            model_row = tk.Frame(frame, bg="#151515")
            model_row.pack(fill=tk.X, padx=10, pady=(2, 0))
            for value, text in MODEL_CHOICES:
                tk.Radiobutton(
                    model_row, text=text, value=value, variable=self.model_var, command=self._on_model_change,
                    font=("Consolas", 9), fg="#CCCCCC", bg="#151515", activebackground="#151515",
                    activeforeground="#CCCCCC", selectcolor="#0f0f0f", highlightthickness=0, bd=0
                ).pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(frame, text="VOICE STYLE", font=("Consolas", 8, "bold"), fg="#888888",
                 bg="#151515").pack(anchor="w", padx=10, pady=(6, 0))
        voice_row = tk.Frame(frame, bg="#151515")
        voice_row.pack(fill=tk.X, padx=10, pady=(2, 0))
        self.voice_var = tk.StringVar(value=settings.get("TTS_VOICE"))
        for value, text in VOICE_CHOICES:
            tk.Radiobutton(
                voice_row, text=text, value=value, variable=self.voice_var, command=self._on_voice_change,
                font=("Consolas", 9), fg="#CCCCCC", bg="#151515", activebackground="#151515",
                activeforeground="#CCCCCC", selectcolor="#0f0f0f", highlightthickness=0, bd=0
            ).pack(side=tk.LEFT, padx=(0, 10))

        self.mute_var = tk.BooleanVar(value=settings.get("TTS_MUTED"))
        tk.Checkbutton(
            frame, text="Mute Maria's voice", variable=self.mute_var,
            command=self._on_mute_toggle, font=("Consolas", 9),
            fg="#CCCCCC", bg="#151515", activebackground="#151515",
            activeforeground="#CCCCCC", selectcolor="#0f0f0f",
            highlightthickness=0, bd=0
        ).pack(anchor="w", padx=10, pady=(4, 2))

        button_row = tk.Frame(frame, bg="#151515")
        button_row.pack(fill=tk.X, padx=10, pady=(4, 8))

        for text, command in (("Test voice", self._on_test_voice), ("Reset voice", self._on_reset_voice)):
            tk.Button(
                button_row, text=text, font=("Consolas", 9, "bold"),
                bg="#00332A", fg="#00FFAA", activebackground="#004D3D",
                activeforeground="#00FFAA", relief=tk.FLAT, bd=0,
                padx=8, command=command
            ).pack(side=tk.LEFT, padx=(0, 6))

        self.live_status = tk.Label(
            button_row, text="", font=("Consolas", 8), fg="#00FF00", bg="#151515"
        )
        self.live_status.pack(side=tk.LEFT, padx=6)

        # How the player talks to her
        tk.Label(
            self.window, text="TALK TO MARIA", font=("Consolas", 9, "bold"), fg="#00FFAA", bg="#0a0a0a"
        ).pack(anchor="w", padx=10, pady=(4, 0))
        talk_frame = tk.Frame(self.window, bg="#151515", bd=1, relief=tk.FLAT)
        talk_frame.pack(fill=tk.X, padx=10, pady=(4, 8))
        mic_row = tk.Frame(talk_frame, bg="#151515")
        mic_row.pack(fill=tk.X, padx=10, pady=(6, 0))
        tk.Label(mic_row, text="Microphone", font=("Consolas", 9), fg="#CCCCCC", bg="#151515",
                 width=11, anchor="w").pack(side=tk.LEFT)
        self.mic_var = tk.StringVar()
        self._mic_options = {}                                   # what the list shows -> the setting value
        self.mic_menu = tk.OptionMenu(mic_row, self.mic_var, "")
        self.mic_menu.config(font=("Consolas", 9), fg="#00FF00", bg="#0f0f0f", activebackground="#1a1a1a",
                             activeforeground="#00FF00", highlightthickness=0, bd=0, width=30, anchor="w")
        self.mic_menu.pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(mic_row, text="Refresh", command=self._refresh_mics, font=("Consolas", 8), fg="#CCCCCC",
                  bg="#222222", activebackground="#333333", activeforeground="#FFFFFF", bd=0,
                  highlightthickness=0, padx=6).pack(side=tk.LEFT)
        self._refresh_mics()
        ptt_row = tk.Frame(talk_frame, bg="#151515")
        ptt_row.pack(fill=tk.X, padx=10, pady=(6, 0))
        self.ptt_var = tk.BooleanVar(value=settings.get("PUSH_TO_TALK"))
        tk.Checkbutton(
            ptt_row, text="Push to talk (hold the key to record)", variable=self.ptt_var, command=self._on_ptt_change,
            font=("Consolas", 9), fg="#CCCCCC", bg="#151515", activebackground="#151515",
            activeforeground="#CCCCCC", selectcolor="#0f0f0f", highlightthickness=0, bd=0
        ).pack(side=tk.LEFT)
        self.ptt_key_var = tk.StringVar(value=str(settings.get("PUSH_TO_TALK_KEY")).upper())
        keys = tuple(k.upper() for k in settings.SPECS["PUSH_TO_TALK_KEY"]["choices"])
        key_menu = tk.OptionMenu(ptt_row, self.ptt_key_var, *keys, command=lambda value: self._on_ptt_key_change())
        key_menu.config(font=("Consolas", 9), fg="#00FF00", bg="#0f0f0f", activebackground="#1a1a1a",
                        activeforeground="#00FF00", highlightthickness=0, bd=0)
        key_menu.pack(side=tk.LEFT, padx=6)
        self._add_live_slider(talk_frame, "VOICE_SILENCE_SECONDS", "Pause ends", lambda v: f"{v:.1f}s",
                              resolution=0.1, label_width=11)
        tk.Label(
            talk_frame, text="Pick the microphone you talk into (press Refresh after plugging earphones in). Turn voice on with the Voice button in the chat window (or Ctrl+Alt+V). Talk over her "
                             "to stop her. Push to talk works while Minecraft is in front (typing the key in Minecraft's own "
                             "chat records too: pick F8 then). Sound stays on this PC (first use downloads a ~150 MB model once).",
            font=("Consolas", 8), fg="#888888", bg="#151515", anchor="w", justify=tk.LEFT, wraplength=420
        ).pack(fill=tk.X, padx=10, pady=(0, 6))

    def _add_live_slider(self, parent, key: str, label: str, fmt,
                         resolution: float = 0.05, label_width: int = 7):
        """One labelled slider bound to a setting. Limits come from settings.SPECS."""
        spec = settings.SPECS[key]

        row = tk.Frame(parent, bg="#151515")
        row.pack(fill=tk.X, padx=10, pady=2)

        tk.Label(
            row, text=label, font=("Consolas", 9), fg="#CCCCCC",
            bg="#151515", width=label_width, anchor="w"
        ).pack(side=tk.LEFT)

        var = tk.DoubleVar(value=settings.get(key))
        value_label = tk.Label(
            row, text=fmt(var.get()), font=("Consolas", 9, "bold"),
            fg="#00FF00", bg="#151515", width=6, anchor="e"
        )

        scale = tk.Scale(
            row, variable=var, from_=spec["min"], to=spec["max"], resolution=resolution,
            orient=tk.HORIZONTAL, showvalue=False, width=10, fg="#CCCCCC",
            # The knob is drawn in bg; activebackground (hover and drag) is the same, so it never changes
            bg=SLIDER_KNOB, activebackground=SLIDER_KNOB,
            troughcolor="#0f0f0f", highlightthickness=0, bd=0, sliderrelief=tk.FLAT
        )
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        value_label.pack(side=tk.LEFT)

        # Attach the callback last so building the widget does not trigger it
        scale.config(command=lambda raw, k=key: self._on_live_slider(k, raw))
        self.live_sliders[key] = (var, fmt, value_label)

    def _on_live_slider(self, key: str, raw):
        """Slider moved: apply the new value straight away."""
        try:
            applied = settings.update(key, round(float(raw), 2))
        except ValueError as e:
            logger.error(f"Invalid value for {key}: {e}")
            return
        _, fmt, value_label = self.live_sliders[key]
        value_label.config(text=fmt(applied))

    def _refresh_mics(self):
        """Fill the microphone list with what is plugged in now (also after plugging in earphones: press Refresh).
        A saved microphone that is not connected stays in the list, marked, and Maria uses the default one meanwhile."""
        names = voice_input.input_device_names()
        saved = settings.get("MIC_DEVICE")
        options = {MIC_DEFAULT_LABEL: ""}
        options.update({name: name for name in names})
        if saved and saved not in names:
            options[f"{saved} (not connected)"] = saved
        self._mic_options = options
        menu = self.mic_menu["menu"]
        menu.delete(0, "end")
        for label in options:
            menu.add_command(label=label, command=lambda chosen=label: self._on_mic_pick(chosen))
        self.mic_var.set(next(label for label, value in options.items() if value == saved))

    def _on_mic_pick(self, label: str):
        """A microphone was picked: applies at once (an open microphone is reopened on the new one)."""
        self.mic_var.set(label)
        try:
            settings.update("MIC_DEVICE", self._mic_options.get(label, ""))
        except ValueError as e:
            logger.error(f"Invalid microphone: {e}")

    def _on_ptt_change(self):
        """Push to talk was ticked or unticked: applies at once."""
        try:
            settings.update("PUSH_TO_TALK", bool(self.ptt_var.get()))
        except ValueError as e:
            logger.error(f"Invalid push-to-talk setting: {e}")

    def _on_ptt_key_change(self):
        try:
            settings.update("PUSH_TO_TALK_KEY", self.ptt_key_var.get().lower())
        except ValueError as e:
            logger.error(f"Invalid push-to-talk key: {e}")

    def _on_model_change(self):
        """Amy or Maria's own voice was picked: apply it straight away and let her say a sample."""
        try:
            settings.update("TTS_MODEL", self.model_var.get())
        except ValueError as e:
            logger.error(f"Invalid voice model: {e}")
            return
        self._on_test_voice()

    def _on_voice_change(self):
        """A voice style was picked: apply it straight away and let her say a sample."""
        try:
            settings.update("TTS_VOICE", self.voice_var.get())
        except ValueError as e:
            logger.error(f"Invalid voice style: {e}")
            return
        self._on_test_voice()

    def _on_mute_toggle(self):
        settings.update("TTS_MUTED", bool(self.mute_var.get()))

    def _set_live_status(self, text: str, color: str = "#00FF00"):
        self.live_status.config(text=text, fg=color)

    def _on_test_voice(self):
        """Speak a sample line with the current volume / speed."""
        if settings.get("TTS_MUTED"):
            self._set_live_status("Muted - untick Mute to hear it", "#FF9500")
            return
        try:
            from core.tts import speak_test  # imported here so this window can run on its own
            speak_test()
            self._set_live_status("Playing test line...")
        except Exception as e:
            logger.error(f"Test voice failed: {e}", exc_info=True)
            self._set_live_status("Test voice failed (see log)", "#FF3B30")

    def _on_reset_voice(self):
        """Put volume, speed and mute back to the defaults from config.py."""
        for key in ("TTS_VOLUME", "TTS_SPEED"):
            var, fmt, value_label = self.live_sliders[key]
            value = settings.reset(key)
            var.set(value)
            value_label.config(text=fmt(value))
        self.mute_var.set(settings.reset("TTS_MUTED"))
        self.voice_var.set(settings.reset("TTS_VOICE"))
        self.model_var.set(settings.reset("TTS_MODEL"))
        self._set_live_status("Voice settings reset")

    # ------------------------------------------------------------------
    # Live detection / timing settings (apply immediately, no restart)
    # ------------------------------------------------------------------
    def _create_detection_section(self):
        """Capture speed, which Roboflow models run, confidence thresholds and
        frame-buffer tuning. Every change goes through core/settings.py and is
        picked up on the next capture / detection cycle."""
        frame = self._create_collapsible_section(
            "detection", "DETECTION & TIMING (LIVE)", "#00FFAA"
        )

        # --- Timing ---
        tk.Label(
            frame, text="TIMING", font=("Consolas", 8, "bold"), fg="#888888", bg="#151515"
        ).pack(anchor="w", padx=10, pady=(4, 0))
        self._add_live_slider(frame, "SCREENSHOT_INTERVAL", "Capture", lambda v: f"{v:.1f}s",
                              resolution=0.1, label_width=9)
        self._add_live_slider(frame, "MIN_ANALYSIS_INTERVAL", "Analyze", lambda v: f"{v:.1f}s",
                              resolution=0.5, label_width=9)

        # --- Which Roboflow models run (turning one off also saves API calls) ---
        header_row = tk.Frame(frame, bg="#151515")
        header_row.pack(fill=tk.X, padx=10, pady=(4, 0))
        tk.Label(
            header_row, text="MODELS", font=("Consolas", 8, "bold"), fg="#888888", bg="#151515"
        ).pack(side=tk.LEFT)
        self.detect_status = tk.Label(
            header_row, text="", font=("Consolas", 8), fg="#00FF00", bg="#151515"
        )
        self.detect_status.pack(side=tk.RIGHT)

        model_row = tk.Frame(frame, bg="#151515")
        model_row.pack(fill=tk.X, padx=10, pady=2)
        self.model_vars = {}
        for key, text in zip(MODEL_KEYS, ("General", "Mobs", "Blocks")):
            var = tk.BooleanVar(value=settings.get(key))
            tk.Checkbutton(
                model_row, text=text, variable=var,
                command=lambda k=key: self._on_model_toggle(k),
                font=("Consolas", 9), fg="#CCCCCC", bg="#151515",
                activebackground="#151515", activeforeground="#CCCCCC",
                selectcolor="#0f0f0f", highlightthickness=0, bd=0
            ).pack(side=tk.LEFT, padx=(0, 12))
            self.model_vars[key] = var

        tk.Button(
            model_row, text="Reset detection", font=("Consolas", 9, "bold"),
            bg="#00332A", fg="#00FFAA", activebackground="#004D3D",
            activeforeground="#00FFAA", relief=tk.FLAT, bd=0,
            padx=8, command=self._on_reset_detection
        ).pack(side=tk.RIGHT)

        # --- Confidence thresholds ---
        tk.Label(
            frame, text="CONFIDENCE (HOW SURE A DETECTION MUST BE)",
            font=("Consolas", 8, "bold"), fg="#888888", bg="#151515"
        ).pack(anchor="w", padx=10, pady=(4, 0))
        self._add_live_slider(frame, "ROBOFLOW_CONFIDENCE_PERCENT", "General", lambda v: f"{int(round(v))}%",
                              resolution=1, label_width=9)
        self._add_live_slider(frame, "MOB_CONFIDENCE_THRESHOLD", "Mobs", lambda v: f"{v:.2f}",
                              resolution=0.05, label_width=9)
        self._add_live_slider(frame, "THREAT_INSTANT_CONFIDENCE", "Danger now", lambda v: f"{v:.2f}",
                              resolution=0.05, label_width=11)
        self._add_live_slider(frame, "BLOCK_CONFIDENCE_THRESHOLD", "Blocks", lambda v: f"{v:.2f}",
                              resolution=0.05, label_width=9)

        # --- Frame buffer tuning ---
        tk.Label(
            frame, text="FRAME BUFFER (ADVANCED)", font=("Consolas", 8, "bold"),
            fg="#888888", bg="#151515"
        ).pack(anchor="w", padx=10, pady=(4, 0))
        self._add_live_slider(frame, "FRAME_BUFFER_SIZE_CHANGE_PERCENT", "Size chg", lambda v: f"{int(round(v))}%",
                              resolution=5, label_width=9)
        self._add_live_slider(frame, "FRAME_BUFFER_CONFIDENCE_THRESHOLD", "Trend", lambda v: f"{v:.2f}",
                              resolution=0.01, label_width=9)

        tk.Frame(frame, height=6, bg="#151515").pack(fill=tk.X)  # a little breathing room at the bottom

    def _on_model_toggle(self, key: str):
        """Turn a Roboflow model on / off. At least one has to stay on, otherwise
        nothing would ever be detected."""
        var = self.model_vars[key]
        if not any(v.get() for v in self.model_vars.values()):
            var.set(True)
            self.detect_status.config(text="Keep at least one model on", fg="#FF9500")
            return
        settings.update(key, bool(var.get()))
        self.detect_status.config(text="")

    def _on_reset_detection(self):
        """Put every detection / timing / frame-buffer setting back to its default."""
        for key in DETECTION_SLIDER_KEYS:
            var, fmt, value_label = self.live_sliders[key]
            value = settings.reset(key)
            var.set(value)
            value_label.config(text=fmt(value))
        for key, var in self.model_vars.items():
            var.set(settings.reset(key))
        self.detect_status.config(text="Detection settings reset", fg="#00FF00")

    # ------------------------------------------------------------------
    # Collapsible sections (only one open at a time)
    # ------------------------------------------------------------------
    def _create_collapsible_section(self, key: str, title: str, color: str):
        """Clickable header plus a body frame that starts hidden. Opening one
        section closes the others, so the window stays short enough for the
        screen. Returns the body frame; put the section's widgets inside it."""
        header = tk.Label(
            self.window, text=f"▶ {title}", font=("Consolas", 9, "bold"),
            fg=color, bg="#0a0a0a", cursor="hand2", anchor="w"
        )
        header.pack(fill=tk.X, padx=10, pady=(4, 0))
        header.bind("<Button-1>", lambda event, k=key: self._toggle_section(k))

        # The container keeps its place in the window; only the body inside is
        # shown or hidden, so the footer stays at the bottom.
        container = tk.Frame(self.window, bg="#0a0a0a")
        container.pack(fill=tk.X)
        body = tk.Frame(container, bg="#151515", bd=1, relief=tk.FLAT)

        self._sections[key] = {
            "title": title, "header": header, "container": container,
            "body": body, "visible": False,
        }
        return body

    def _toggle_section(self, key: str):
        """Open the clicked section (or close it if open) and close all others."""
        for other_key, section in self._sections.items():
            should_show = (other_key == key) and not section["visible"]
            if should_show == section["visible"]:
                continue
            if should_show:
                section["body"].pack(fill=tk.X, padx=10, pady=(4, 8))
                arrow = "▼"
            else:
                section["body"].pack_forget()
                # A frame with no children keeps its old size in Tk, so shrink it by hand
                section["container"].config(height=1)
                arrow = "▶"
            section["visible"] = should_show
            section["header"].config(text=f"{arrow} {section['title']}")
        self._fit_height()

    # ------------------------------------------------------------------
    # Admin Config Panel (restart-to-apply)
    # ------------------------------------------------------------------
    def _create_config_section(self):
        """Builds the admin-only config editor. Writes to config_overrides.json;
        changes take effect on next app restart (config.py reads the file on import)."""

        current_values = self._load_config_field_values()
        self.config_vars = {}  # key -> tk.Variable

        # --- Collapsible header + body (collapsed by default) ---
        config_frame = self._create_collapsible_section(
            "advanced", "ADVANCED CONFIG (RESTART REQUIRED)", "#FF9500"
        )

        # Group fields in the order defined in ADMIN_CONFIG_GROUP_ORDER
        for group_name in ADMIN_CONFIG_GROUP_ORDER:
            group_keys = [k for k, v in ADMIN_CONFIG_FIELDS.items() if v["group"] == group_name]
            if not group_keys:
                continue

            tk.Label(
                config_frame, text=group_name.upper(),
                font=("Consolas", 8, "bold"), fg="#888888", bg="#151515"
            ).pack(anchor="w", padx=10, pady=(8, 2))

            for key in group_keys:
                field = ADMIN_CONFIG_FIELDS[key]
                row = tk.Frame(config_frame, bg="#151515")
                row.pack(fill=tk.X, padx=10, pady=2)

                tk.Label(
                    row, text=field["label"], font=("Consolas", 9),
                    fg="#CCCCCC", bg="#151515", width=26, anchor="w"
                ).pack(side=tk.LEFT)

                value = current_values.get(key)

                var = tk.StringVar(value=str(value))
                entry = tk.Entry(
                    row, textvariable=var, font=("Consolas", 9),
                    bg="#0f0f0f", fg="#00FF00", insertbackground="#00FF00",
                    relief=tk.FLAT, bd=0, highlightthickness=1,
                    highlightbackground="#333333", highlightcolor="#00FFAA",
                    width=12
                )
                entry.pack(side=tk.LEFT, ipady=2)
                win_focus.typing_field(entry)   # typing needs focus: a click takes it, Enter / Escape returns it

                self.config_vars[key] = var

        # --- Save button + status ---
        button_row = tk.Frame(config_frame, bg="#151515")
        button_row.pack(fill=tk.X, padx=10, pady=(8, 10))

        save_btn = tk.Button(
            button_row, text="Save Config (restart to apply)",
            font=("Consolas", 9, "bold"),
            bg="#00332A", fg="#00FFAA", activebackground="#004D3D",
            activeforeground="#00FFAA", relief=tk.FLAT, bd=0,
            command=self._save_admin_config
        )
        save_btn.pack(side=tk.LEFT)

        self.config_save_status = tk.Label(
            button_row, text="", font=("Consolas", 8),
            fg="#00FF00", bg="#151515"
        )
        self.config_save_status.pack(side=tk.LEFT, padx=10)

    def _fit_height(self):
        """Make the window exactly as tall as its content (but never taller than
        the screen), so collapsing / expanding the config panel leaves no gap
        and nothing is cut off. Only the size changes, not the position."""
        self.window.update_idletasks()
        height = min(self.window.winfo_reqheight(), self.window.winfo_screenheight() - 40)
        self.window.geometry(f"520x{height}")

    def _load_config_field_values(self) -> dict:
        """Start from config.py's hardcoded defaults, then layer any values
        already saved in config_overrides.json on top (so the panel reflects
        what's actually active, not just the shipped defaults)."""
        values = {}
        for key in ADMIN_CONFIG_FIELDS:
            values[key] = getattr(config_module, key, None)

        if os.path.isfile(CONFIG_OVERRIDES_PATH):
            try:
                with open(CONFIG_OVERRIDES_PATH, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                for key, value in saved.items():
                    if key in ADMIN_CONFIG_FIELDS:
                        values[key] = value
            except Exception as e:
                logger.error(f"Failed to read existing config_overrides.json: {e}")

        return values

    def _save_admin_config(self):
        """Validate + convert the panel's fields and write config_overrides.json."""
        result = {}
        errors = []

        for key, var in self.config_vars.items():
            field = ADMIN_CONFIG_FIELDS[key]
            raw = var.get()
            try:
                if field["type"] == "int":
                    result[key] = int(float(raw))
                elif field["type"] == "float":
                    result[key] = float(raw)
                else:
                    result[key] = raw
            except (ValueError, TypeError):
                errors.append(field["label"])

        if errors:
            self.config_save_status.config(
                fg="#FF3B30",
                text=f"⚠ Invalid value(s): {', '.join(errors)}"
            )
            return

        try:
            # Merge into the existing file so the live settings saved by
            # core/settings.py (volume, speed, mute) are not wiped out.
            merged = {}
            if os.path.isfile(CONFIG_OVERRIDES_PATH):
                try:
                    with open(CONFIG_OVERRIDES_PATH, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                    if isinstance(existing, dict):
                        merged = existing
                except Exception as e:
                    logger.warning(f"Could not read existing config_overrides.json, starting fresh: {e}")
            merged.update(result)

            with open(CONFIG_OVERRIDES_PATH, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2)
            self.config_save_status.config(
                fg="#00FF00",
                text="✅ Saved. Restart the app to apply."
            )
            logger.info(f"💾 Admin config saved to {CONFIG_OVERRIDES_PATH}")
        except Exception as e:
            self.config_save_status.config(fg="#FF3B30", text=f"⚠ Save failed: {e}")
            logger.error(f"Failed to save config_overrides.json: {e}")

    # ------------------------------------------------------------------
    # Public API - call these from main.py / vision_loop / ai_handler
    # ------------------------------------------------------------------
    def push_update(self, activity: str = None, threat_level: str = None,
                     detections: list = None, game_state: str = None):
        """
        Thread-safe: push a new status snapshot. Safe to call from
        the VisionLoopThread or AIHandler directly.
        """
        payload = {
            "activity": activity,
            "threat_level": threat_level,
            "detections": detections or [],
            "game_state": game_state,
            "timestamp": time.time(),
        }
        self.status_queue.put(payload)

    def toggle(self):
        """Show/hide the status panel (bind to its own hotkey)."""
        if win_focus.toggle(self.window):
            logger.info("📊 Status window shown")
        else:
            logger.info("📊 Status window hidden")

    # ------------------------------------------------------------------
    # Internal: polling + rendering
    # ------------------------------------------------------------------
    def _poll_status(self):
        while True:
            try:
                try:
                    update = self.status_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                self.window.after(0, self._apply_update, update)
            except Exception as e:
                logger.error(f"Error in status polling: {e}")
                continue

    def _apply_update(self, update: dict):
        try:
            activity = update.get("activity")
            threat_level = update.get("threat_level")
            detections = update.get("detections") or []
            game_state = update.get("game_state")

            if activity:
                icon = self.ACTIVITY_ICONS.get(activity, "❔")
                self.activity_label.config(text=f"{icon}  {activity.title()}")

            if threat_level:
                color = self.THREAT_COLORS.get(threat_level, "#555555")
                self.threat_label.config(text=threat_level.upper(), fg=color)
                self.threat_dot.itemconfig(self._threat_circle, fill=color)

            self.detections_box.delete(0, tk.END)
            for det in detections:
                cls = det.get("class", "?")
                conf = det.get("confidence", 0)
                loc = det.get("location", "")
                self.detections_box.insert(tk.END, f"{cls}  {conf:.0%}  ({loc})")

            if activity or game_state:
                timestamp = datetime.now().strftime("%H:%M:%S")
                entry = f"[{timestamp}] {activity or ''} — {game_state or ''}".strip()
                self.history.append(entry)
                self.log_box.insert(0, entry)
                # Cap visible rows
                if self.log_box.size() > 25:
                    self.log_box.delete(25, tk.END)

        except Exception as e:
            logger.error(f"Error applying status update: {e}")


# ============================================================================
# TEST
# ============================================================================
if __name__ == "__main__":
    import random

    ctx = {}
    win = StatusWindow(ctx)
    win_focus.show(win.window)

    def simulate():
        activities = ["mining", "combat", "exploring", "animal nearby"]
        threats = ["none", "low", "medium", "high", "critical"]
        while True:
            time.sleep(2)
            win.push_update(
                activity=random.choice(activities),
                threat_level=random.choice(threats),
                detections=[
                    {"class": "creeper", "confidence": 0.87, "location": "left middle"},
                    {"class": "diamond_ore", "confidence": 0.93, "location": "center bottom"},
                ],
                game_state="simulated frame"
            )

    threading.Thread(target=simulate, daemon=True).start()
    win.window.mainloop()