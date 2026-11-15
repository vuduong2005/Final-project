"""Whether Maria is speaking right now, and how loud her voice is at this moment.

core/tts.py reports when a line starts and ends; the on-screen face (ui/speaking_overlay.py)
reads the loudness to move its mouth in time with the sound.
"""
import math
import sys
import time
import wave
from array import array
from typing import List, Optional

FRAME_SECONDS = 1 / 30      # the loudness is measured once per this much sound

_current = None             # (start time, loudness list, seconds per entry) while she speaks


def envelope_from_wav(path: str, step: float = FRAME_SECONDS) -> List[float]:
    """Loudness of a 16-bit wav over time: one value from 0 to 1 per `step` seconds.
    Returns an empty list when the file cannot be read (the face then just wobbles)."""
    try:
        with wave.open(path, "rb") as wav:
            if wav.getsampwidth() != 2:
                return []
            rate, channels = wav.getframerate(), wav.getnchannels()
            samples = array("h")
            samples.frombytes(wav.readframes(wav.getnframes()))
        if sys.byteorder == "big":
            samples.byteswap()
        chunk = max(1, int(rate * step)) * channels
        peaks = [max(max(part), -min(part))
                 for part in (samples[i:i + chunk] for i in range(0, len(samples), chunk))]
        loudest = max(peaks, default=0)
        if loudest <= 0:
            return [0.0] * len(peaks)
        # the power lifts the quiet parts a little, so the mouth does not sit closed in soft syllables
        return [min(1.0, peak / loudest) ** 0.6 for peak in peaks]
    except Exception:
        return []


def begin(envelope: Optional[List[float]] = None, step: float = FRAME_SECONDS, now: Optional[float] = None,
          mood: str = "talk"):
    """A line starts playing now. `mood` is "talk" for ordinary lines, "alarm" for danger warnings."""
    global _current
    _current = (time.monotonic() if now is None else now, envelope or [], step, mood)


def end():
    """The line has finished (or failed)."""
    global _current
    _current = None


def mood() -> Optional[str]:
    """None while she is silent, otherwise the mood of the line being spoken."""
    current = _current
    return current[3] if current else None


def level(now: Optional[float] = None) -> Optional[float]:
    """None while she is silent, otherwise her loudness right now from 0 to 1."""
    current = _current
    if current is None:
        return None
    started, envelope, step, _ = current
    elapsed = max(0.0, (time.monotonic() if now is None else now) - started)
    if not envelope:
        return abs(math.sin(elapsed * 9.0))        # loudness unknown: keep the mouth moving anyway
    index = int(elapsed / step)
    return envelope[index] if index < len(envelope) else 0.0
