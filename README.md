# Maria – Real-Time AI Game Companion

Maria is a desktop AI companion that watches your Minecraft gameplay through screen capture and talks back by voice and chat. It was built solo over about six months (Jan 2026 – Jul 2026).

## What it does

- **Game Mode (automatic):** captures the screen every ~0.5 s, detects mobs, ores, hazards and status bars, and comments on what matters (e.g. an approaching creeper, a diamond ore).
- **Chat Mode (manual):** type to Maria in an overlay window and she replies like a gaming buddy. She keeps the last 10 messages as short-term memory.
- **Hybrid vision:** a screenshot is attached to a chat message only when the message asks her to look at something ("can you see...", "look at..."). Normal chat stays text-only to keep latency low.
- **Proactive comments:** if you stay on one activity for ~30 s, she may comment unprompted, unless you chatted recently.
- **Short-term visual memory:** a frame buffer keeps the last 3 frames and tracks detection confidence and position to spot threats moving closer.
- **Voice:** local text-to-speech with Piper.
- **Live settings:** volume, speed, mute, capture timing and detection thresholds can be changed while she runs, from the status window.

## How it works

```text
screenshot (mss)
   -> 3 Roboflow models (general, mobs, blocks), per-model confidence thresholds
   -> duplicate removal (same class + centers within 50 px in one frame)
   -> frame buffer (last 3 frames, approach / move-away trend)
   -> JSON parser -> short text ("creeper in left middle")
   -> local LLM via Ollama (Game Mode / Chat Mode / Proactive prompts)
   -> Piper TTS + chat window
```

Threads: vision loop, chat processor, TTS worker and hotkey listener, connected by thread-safe queues.

## Measured performance

From an earlier test run (111 logged Game Mode analysis cycles): average end-to-end latency about 5.6 s, median about 5.4 s. Most of that time is spent in the hosted detection calls and the local LLM.

## Setup (Windows)

Requirements: Python 3.12 or newer (tested on 3.14), [Ollama](https://ollama.com) running locally, and a Roboflow API key with access to the three models configured in `config.py`.

The app lives in the `realtime_ai_assistant` folder. From the repo root:

1. Create a virtual environment and install the dependencies (this includes `piper-tts` and `python-dotenv`):

   ```bat
   cd realtime_ai_assistant
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Pull the vision-capable model (the name is set in `config.py`):

   ```bat
   ollama pull llava:7b
   ```

3. Download a Piper voice. The app expects `en_US-amy-medium.onnx` and `en_US-amy-medium.onnx.json` next to `main.py`. The voice files are not in the repo.
4. Create a `.env` file next to `main.py`, based on `.env.example`, and add your key. Never commit this file.

   ```text
   ROBOFLOW_API_KEY=your_key_here
   ```

5. Run it, and switch to Minecraft within the 5-second startup delay:

   ```bat
   python main.py
   ```

Hotkeys: **Ctrl+Alt+G** toggles the chat window, **Ctrl+Alt+S** toggles the status window.

### Tester build

To hand Maria to a few testers without a developer setup, `python tester/build_zip.py` builds `dist/maria-tester-<date>.zip` with `setup.bat` and `run.bat`. It never includes `.env`, logs or screenshots. The page that links to it is in `docs/index.html`.

## Live settings

Press **Ctrl+Alt+S** to open the status window:

- **Voice:** volume, speed, mute, and a Test voice button.
- **Detection & timing:** capture and analysis intervals, which Roboflow models are on, the confidence thresholds for each model, and the frame-buffer thresholds. At least one model always stays on.
- **Advanced config:** only the startup delay, which needs a restart.

Changes apply from the next frame or spoken line. They are saved to `config_overrides.json` next to `main.py`, which is local to your machine and not tracked by git. Each setting has limits, and out-of-range values are clamped.

## Configuration

Defaults live in `config.py`: detection thresholds, screenshot interval, Ollama model name, proactive-comment timing, vision trigger keywords and TTS volume. Anything you change in the status window is saved on top of those defaults in `config_overrides.json`; delete that file to go back to the defaults.

## Project layout

```text
main.py                 entry point, vision loop, chat processor, hotkeys
config.py               defaults and loading of saved overrides
core/settings.py        live settings store (limits, listeners, saving)
core/ai_handler.py      Roboflow detection, parsing, LLM responses, chat memory
core/frame_buffer.py    3-frame buffer and trend analysis
core/screenshot.py      screen capture
core/tts.py             Piper text-to-speech queue
ui/chat_window.py       Tkinter chat overlay
ui/status_window.py     Tkinter status window with the live settings
utils/logger.py         logging
tester/                 setup.bat, run.bat and the tester zip builder
```

The tester page is in `docs/` at the repo root.

## Known limitations

- Windows only (uses `winsound`).
- Detection depends on Roboflow's hosted API; replacing it with a custom-trained model is in progress. While it runs, frames of your whole primary screen are sent to that service.
- A custom voice-conversion step (RVC) was prototyped but disabled, because it competed with Ollama for a 6 GB GPU.
- Duplicate removal compares class name and center distance, so two real objects of the same type very close together can be merged.
- Conversation memory is in RAM only and resets when the app closes.
