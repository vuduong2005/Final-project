"""Build the tester zip: dist/maria-tester-<date>.zip

Run from anywhere:  python tester/build_zip.py

The zip is assembled from an allow-list taken from the working tree, so it
contains the app exactly as it is on disk right now. Nothing else is ever
added: no .env, no logs, no screenshots, no virtual environments.
"""
import datetime
import os
import sys
import zipfile

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # realtime_ai_assistant
OUT_DIR = os.path.join(APP_DIR, "dist")  # gitignored
ROOT_NAME = "maria-tester"

# Files copied as they are, relative to APP_DIR
APP_FILES = [
    "main.py",
    "config.py",
    ".env.example",
    "en_US-amy-medium.onnx",        # Piper voice (about 63 MB)
    "en_US-amy-medium.onnx.json",
]
APP_FOLDERS = ["core", "ui", "utils"]  # only *.py files are taken from these

# Files that live in tester/ and go to the zip root. Text files are written
# with Windows line endings so the .bat files always work.
TESTER_FILES = ["setup.bat", "run.bat", "README_TESTER.txt"]

# Versions this build was tested with (Python 3.14). Direct dependencies only.
REQUIREMENTS = """Pillow==12.2.0
pynput==1.8.1
requests==2.33.1
mss==10.2.0
python-dotenv==1.2.2
piper-tts==1.4.2
"""


def to_crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def main():
    missing = [f for f in APP_FILES if not os.path.isfile(os.path.join(APP_DIR, f))]
    missing += [f for f in TESTER_FILES if not os.path.isfile(os.path.join(APP_DIR, "tester", f))]
    if missing:
        sys.exit("Missing files, cannot build: " + ", ".join(missing))

    # Safety check: the example env file must not contain a real key
    with open(os.path.join(APP_DIR, ".env.example"), encoding="utf-8") as f:
        for line in f:
            if line.strip().startswith("ROBOFLOW_API_KEY=") and line.split("=", 1)[1].strip():
                sys.exit(".env.example contains a key value. Remove it before building.")

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.date.today().strftime("%Y-%m-%d")
    zip_path = os.path.join(OUT_DIR, f"maria-tester-{stamp}.zip")

    entries = []  # (path inside zip, source file or None, text content or None)
    for name in APP_FILES:
        entries.append((name, os.path.join(APP_DIR, name), None))
    for folder in APP_FOLDERS:
        for fname in sorted(os.listdir(os.path.join(APP_DIR, folder))):
            if fname.endswith(".py"):
                entries.append((f"{folder}/{fname}", os.path.join(APP_DIR, folder, fname), None))
    for name in TESTER_FILES:
        with open(os.path.join(APP_DIR, "tester", name), encoding="utf-8") as f:
            entries.append((name, None, to_crlf(f.read())))
    entries.append(("requirements.txt", None, to_crlf(REQUIREMENTS)))

    # Hard stop if anything secret-looking slipped into the list
    for arcname, _, _ in entries:
        base = os.path.basename(arcname).lower()
        if base == ".env" or base.endswith((".pem", ".key")):
            sys.exit(f"Refusing to add {arcname} to the zip.")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, src, text in entries:
            target = f"{ROOT_NAME}/{arcname}"
            if src:
                zf.write(src, target)
            else:
                zf.writestr(target, text.encode("utf-8"))

    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"Built {zip_path}  ({size_mb:.1f} MB, {len(entries)} files)")
    for arcname, _, _ in entries:
        print("  ", arcname)


if __name__ == "__main__":
    main()
