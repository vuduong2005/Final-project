import json
import os
import tempfile
import threading

import config
from utils.logger import get_logger

logger = get_logger()

# ============================================================================
# LIVE SETTINGS
# ============================================================================
# One shared place for values that can change while the app is running.
# Code that wants a change to apply immediately must call get("KEY") every time
# it needs the value, instead of importing the constant from config.py (that
# import copies the value once at startup).
#
# Only settings listed in SPECS are managed here. Add a key to SPECS (and to
# config._ADMIN_OVERRIDABLE_KEYS) when its consumer is switched over to get().

SPECS = {
    # Voice
    "TTS_VOLUME": {"type": float, "min": 0.0, "max": 2.0},
    "TTS_SPEED": {"type": float, "min": 0.5, "max": 2.0},
    "TTS_MUTED": {"type": bool},
    # Timing (seconds)
    "SCREENSHOT_INTERVAL": {"type": float, "min": 0.1, "max": 5.0},
    "MIN_ANALYSIS_INTERVAL": {"type": float, "min": 0.5, "max": 30.0},
    # Detection: which Roboflow models run, and how sure a detection must be
    "USE_GENERAL_MODEL": {"type": bool},
    "USE_MOBS_MODEL": {"type": bool},
    "USE_BLOCKS_MODEL": {"type": bool},
    "ROBOFLOW_CONFIDENCE_PERCENT": {"type": int, "min": 5, "max": 100},  # general model, 0-100
    "MOB_CONFIDENCE_THRESHOLD": {"type": float, "min": 0.05, "max": 1.0},
    "BLOCK_CONFIDENCE_THRESHOLD": {"type": float, "min": 0.05, "max": 1.0},
    # Frame buffer (short-term memory trends)
    "FRAME_BUFFER_MOVEMENT_THRESHOLD": {"type": int, "min": 5, "max": 300},  # pixels
    "FRAME_BUFFER_CONFIDENCE_THRESHOLD": {"type": float, "min": 0.01, "max": 0.5},
}

OVERRIDES_PATH = config.CONFIG_OVERRIDES_PATH
SAVE_DELAY_SECONDS = 0.5  # wait this long after the last change before writing the file

_lock = threading.RLock()       # guards _values, _listeners, _save_timer
_file_lock = threading.Lock()   # one file write at a time
_values = {key: getattr(config, key) for key in SPECS}  # config already applied the saved overrides
_listeners = {}                 # key -> [callback(new_value), ...]
_save_timer = None


def _check_key(key: str):
    if key not in SPECS:
        raise KeyError(f"Unknown setting: {key}")


def _coerce(key: str, value):
    """Convert to the setting's type and clamp numbers into the allowed range.
    Raises ValueError for values that cannot be used at all (e.g. 'abc', NaN)."""
    spec = SPECS[key]

    if spec["type"] is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be True or False, got {value!r}")
        return value

    if isinstance(value, bool):  # bool is a subclass of int; do not accept it as a number
        raise ValueError(f"{key} must be a number, got {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number, got {value!r}")
    if number != number:  # NaN
        raise ValueError(f"{key} must be a number, got NaN")
    if number in (float("inf"), float("-inf")):
        raise ValueError(f"{key} must be a finite number, got {value!r}")

    if spec["type"] is int:
        number = int(round(number))

    return max(spec["min"], min(spec["max"], number))


def get(key: str):
    """Current value of a setting (thread-safe)."""
    _check_key(key)
    with _lock:
        return _values[key]


def get_all() -> dict:
    """Copy of all managed settings, e.g. for filling the admin panel."""
    with _lock:
        return dict(_values)


def update(key: str, value):
    """Validate, apply, notify listeners and schedule a save.
    Out-of-range numbers are clamped; unusable values raise ValueError.
    Returns the value that was actually applied."""
    _check_key(key)
    new_value = _coerce(key, value)

    with _lock:
        old_value = _values[key]
        _values[key] = new_value
        callbacks = list(_listeners.get(key, ()))

    if new_value != old_value:
        logger.info(f"⚙️  Setting {key}: {old_value} -> {new_value}")
        for callback in callbacks:  # called outside the lock so a callback can use get()/update()
            try:
                callback(new_value)
            except Exception as e:
                logger.error(f"Listener for {key} failed: {e}", exc_info=True)
        _schedule_save()

    return new_value


def reset(key: str):
    """Go back to the default from config.py. Returns the applied value."""
    _check_key(key)
    return update(key, config.ADMIN_DEFAULTS[key])


def on_change(key: str, callback):
    """Register callback(new_value), called (on the thread that changed the
    setting) whenever the value actually changes."""
    _check_key(key)
    with _lock:
        _listeners.setdefault(key, []).append(callback)


# ============================================================================
# SAVING (config_overrides.json)
# ============================================================================

def _schedule_save():
    """Debounce: dragging a slider changes the value many times a second, so
    wait until it has been quiet for SAVE_DELAY_SECONDS before writing."""
    global _save_timer
    with _lock:
        if _save_timer is not None:
            _save_timer.cancel()
        _save_timer = threading.Timer(SAVE_DELAY_SECONDS, _save_now)
        _save_timer.daemon = True
        _save_timer.start()


def flush():
    """Write any pending change right now. Call this when the app shuts down."""
    global _save_timer
    with _lock:
        pending = _save_timer is not None
        if pending:
            _save_timer.cancel()
            _save_timer = None
    if pending:
        _save_now()


def _save_now():
    """Write only the values that differ from the defaults into
    config_overrides.json, keeping every other key already in the file."""
    global _save_timer
    with _lock:
        _save_timer = None
        changed = {k: v for k, v in _values.items() if v != config.ADMIN_DEFAULTS[k]}

    with _file_lock:
        tmp_path = None
        try:
            data = {}
            if os.path.isfile(OVERRIDES_PATH):
                try:
                    with open(OVERRIDES_PATH, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                    if isinstance(existing, dict):
                        data = existing
                except Exception as e:
                    logger.warning(f"Could not read existing config_overrides.json, starting fresh: {e}")

            for key in SPECS:  # drop our keys, then add back the ones that differ from default
                data.pop(key, None)
            data.update(changed)

            # Write to a temp file, then swap it in: a crash mid-write can never
            # leave a half-written config_overrides.json behind.
            fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(OVERRIDES_PATH), prefix="config_overrides_", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, OVERRIDES_PATH)
            tmp_path = None
            logger.debug(f"💾 Settings saved to {OVERRIDES_PATH}")
        except Exception as e:
            logger.error(f"Failed to save settings: {e}", exc_info=True)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
