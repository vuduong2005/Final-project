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

logger = get_logger()

# Keys the admin config panel is allowed to edit. Must match
# _ADMIN_OVERRIDABLE_KEYS in config.py.
ADMIN_CONFIG_FIELDS = {
    "ROBOFLOW_CONFIDENCE_PERCENT": {"type": "int", "label": "Roboflow Confidence %", "group": "Detection"},
    "MOB_CONFIDENCE_THRESHOLD": {"type": "float", "label": "Mob Confidence Threshold", "group": "Detection"},
    "BLOCK_CONFIDENCE_THRESHOLD": {"type": "float", "label": "Block Confidence Threshold", "group": "Detection"},
    "SCREENSHOT_INTERVAL": {"type": "float", "label": "Screenshot Interval (s)", "group": "Timing"},
    "MIN_ANALYSIS_INTERVAL": {"type": "float", "label": "Min Analysis Interval (s)", "group": "Timing"},
    "STARTUP_DELAY": {"type": "float", "label": "Startup Delay (s)", "group": "Timing"},
    "TTS_SPEED": {"type": "float", "label": "TTS Speed", "group": "TTS"},
    "FRAME_BUFFER_MOVEMENT_THRESHOLD": {"type": "int", "label": "Movement Threshold (px)", "group": "Frame Buffer"},
    "FRAME_BUFFER_CONFIDENCE_THRESHOLD": {"type": "float", "label": "Confidence Trend Threshold", "group": "Frame Buffer"},
}

ADMIN_CONFIG_GROUP_ORDER = ["Detection", "Timing", "TTS", "Frame Buffer"]

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

        self.window.protocol("WM_DELETE_WINDOW", self.toggle)
        self.window.withdraw()  # start hidden

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
        ).pack(anchor="w", padx=10, pady=(8, 0))

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
        ).pack(anchor="w", padx=10, pady=(8, 0))

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
            self.window, height=6,
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

        log_frame = tk.Frame(self.window, bg="#0a0a0a", height=140)
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

        # --- Admin Config Panel ---
        self._create_config_section()

        # --- Footer ---
        self.status_label = tk.Label(
            self.window, text="Ctrl+Alt+S to toggle this panel",
            font=("Arial", 7), fg="#444444", bg="#0a0a0a"
        )
        self.status_label.pack(fill=tk.X, padx=10, pady=(0, 6))

    # ------------------------------------------------------------------
    # Admin Config Panel (restart-to-apply)
    # ------------------------------------------------------------------
    def _create_config_section(self):
        """Builds the admin-only config editor. Writes to config_overrides.json;
        changes take effect on next app restart (config.py reads the file on import)."""

        current_values = self._load_config_field_values()
        self.config_vars = {}  # key -> tk.Variable

        # --- Section header ---
        header_frame = tk.Frame(self.window, bg="#0a0a0a")
        header_frame.pack(fill=tk.X, padx=10, pady=(4, 0))

        tk.Label(
            header_frame, text="CONFIG (ADMIN — RESTART REQUIRED)",
            font=("Consolas", 9, "bold"), fg="#FF9500", bg="#0a0a0a"
        ).pack(anchor="w")

        config_frame = tk.Frame(self.window, bg="#151515", bd=1, relief=tk.FLAT)
        config_frame.pack(fill=tk.X, padx=10, pady=(4, 8))

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
                tk.Entry(
                    row, textvariable=var, font=("Consolas", 9),
                    bg="#0f0f0f", fg="#00FF00", insertbackground="#00FF00",
                    relief=tk.FLAT, bd=0, highlightthickness=1,
                    highlightbackground="#333333", highlightcolor="#00FFAA",
                    width=12
                ).pack(side=tk.LEFT, ipady=2)

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
            with open(CONFIG_OVERRIDES_PATH, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
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
        if self.window.state() == "normal":
            self.window.withdraw()
            logger.info("📊 Status window hidden")
        else:
            self.window.deiconify()
            self.window.lift()
            logger.info("📊 Status window shown")

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
    win.window.deiconify()

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