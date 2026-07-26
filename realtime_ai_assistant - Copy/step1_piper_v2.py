"""
STEP 1 — Run this inside your MAIN venv (not rvc_env).

    venv\\Scripts\\activate
    python step1_piper_v2.py

Generates a base speech WAV using Piper, with tunable expressiveness
knobs. Tweak the values below and re-run to A/B test how "alive" the
output sounds BEFORE it goes through RVC.

Once you find settings you like, run step2_rvc.py to hear it in
Maria's voice.
"""

import wave
from piper import PiperVoice
from piper.config import SynthesisConfig

TEXT = "Hey! I'm Maria, your Minecraft companion. Watch out for that creeper behind you!"

MODEL_PATH = "en_US-amy-medium.onnx"
OUTPUT_WAV = "piper_output.wav"

# ----------------------------------------------------------------------
# TUNING KNOBS — change these and re-run to compare
# ----------------------------------------------------------------------
# length_scale: overall speaking speed. 1.0 = normal.
#   < 1.0 = faster/snappier (often sounds less flat/robotic)
#   > 1.0 = slower
LENGTH_SCALE = 1

# noise_scale: controls variation/expressiveness in the voice generator.
#   Higher = more natural variation, but too high can sound unstable.
NOISE_SCALE = 0.85  # Piper default is 0.667

# noise_w: controls variation in phoneme duration/timing (affects rhythm).
#   Higher = less robotic, more "human" timing waver.
NOISE_W = 1  # Piper default is 0.8

# sentence_silence: pause length (seconds) between sentences.
SENTENCE_SILENCE = 0.25
# ----------------------------------------------------------------------


def main():
    print(f"Loading Piper voice model: {MODEL_PATH}")
    voice = PiperVoice.load(MODEL_PATH)

    # Different piper-tts versions name this field differently
    # (noise_w vs noise_scale_w). Build kwargs dynamically so we
    # don't crash on a mismatch.
    import inspect
    valid_fields = set(inspect.signature(SynthesisConfig.__init__).parameters.keys())

    kwargs = {}
    if "length_scale" in valid_fields:
        kwargs["length_scale"] = LENGTH_SCALE
    if "noise_scale" in valid_fields:
        kwargs["noise_scale"] = NOISE_SCALE
    if "noise_w" in valid_fields:
        kwargs["noise_w"] = NOISE_W
    elif "noise_scale_w" in valid_fields:
        kwargs["noise_scale_w"] = NOISE_W
    if "sentence_silence" in valid_fields:
        kwargs["sentence_silence"] = SENTENCE_SILENCE

    print(f"Available SynthesisConfig fields: {sorted(valid_fields)}")
    syn_config = SynthesisConfig(**kwargs)

    print(f"Synthesizing text: \"{TEXT}\"")
    print(f"  length_scale={LENGTH_SCALE}  noise_scale={NOISE_SCALE}  noise_w={NOISE_W}")

    with wave.open(OUTPUT_WAV, "wb") as wav_file:
        voice.synthesize_wav(TEXT, wav_file, syn_config=syn_config)

    print(f"✅ Done. Saved base audio to: {OUTPUT_WAV}")
    print("Listen to it directly first (before RVC) to judge expressiveness.")
    print("Then run step2_rvc.py inside rvc_env to convert it to Maria's voice.")


if __name__ == "__main__":
    main()