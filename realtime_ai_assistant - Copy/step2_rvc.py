"""
STEP 2 — Run this inside rvc_env (after step1_piper.py has produced
piper_output.wav in the same folder).

    rvc_env\\Scripts\\activate
    python step2_rvc.py

Converts piper_output.wav into Maria's voice using the trained
RVC model + index from Applio, and saves the result as maria_output.wav.
"""

from rvc_python.infer import RVCInference

INPUT_WAV = "piper_output.wav"
OUTPUT_WAV = "maria_output.wav"

MODEL_PATH = r"D:\Applio\logs\Maria\Maria_200e_11000s.pth"
INDEX_PATH = r"D:\Applio\logs\Maria\Maria.index"

# 0.0 = no pitch shift. If Maria's voice sounds off-pitch, try +/- 12 (one octave).
PITCH_SHIFT = 7

# How strongly RVC leans on the index file for tone matching (0.0 - 1.0)
INDEX_RATE = 1

def main():
    print("Loading RVC inference engine...")
    rvc = RVCInference(device="cuda:0")  # change to "cpu" if you don't have a CUDA GPU

    print(f"Loading Maria model: {MODEL_PATH}")
    rvc.load_model(MODEL_PATH, index_path=INDEX_PATH)

    # Some rvc-python versions expose these as attributes instead of load_model args
    try:
        rvc.f0up_key = PITCH_SHIFT
        rvc.index_rate = INDEX_RATE
    except Exception:
        pass

    print(f"Converting {INPUT_WAV} -> {OUTPUT_WAV} ...")
    rvc.infer_file(INPUT_WAV, OUTPUT_WAV)

    print(f"✅ Done! Listen to {OUTPUT_WAV} — that's Maria's voice.")

if __name__ == "__main__":
    main()