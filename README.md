# Maria – Real-Time AI Game Companion

Maria is a desktop AI companion that watches your Minecraft gameplay through screen capture and talks back by voice and chat. It was built solo over about six months (Jan 2026 – Jul 2026).



## What it does

- **Game Mode (automatic):** captures the screen every ~0.5 s, detects mobs, ores, hazards and status bars, and comments on what matters (e.g. an approaching creeper, a diamond ore).
- **Chat Mode (manual):** type to Maria in an overlay window and she replies like a gaming buddy. She keeps the last 10 messages as short-term memory.
- **Hybrid vision:** a screenshot is attached to a chat message only when the message asks her to look at something ("can you see...", "look at..."). Normal chat stays text-only to keep latency low.
- **Proactive comments:** if you stay on one activity for ~30 s, she may comment unprompted, unless you chatted recently.
- **Short-term visual memory:** a frame buffer keeps the last 3 frames and tracks detection confidence and position to spot threats moving closer.
- **Voice:** local text-to-speech with Piper.

## How it works


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

From `test_report.csv` (111 logged Game Mode analysis cycles): average end-to-end latency about 5.6 s, median about 5.4 s. Most of that time is spent in the hosted detection calls and the local LLM.

## Setup (Windows)

Requirements: Python 3.12, [Ollama](https://ollama.com) running locally, a Roboflow account with API access.

1. Clone the repo and create a virtual environment.
2. Install dependencies:
   
   pip install -r requirements.txt
   pip install python-dotenv piper-tts
   
   (`python-dotenv` and the Piper package may be missing from `requirements.txt`; install them if you see import errors.)
3. Pull the vision-capable model: `ollama pull llava:7b` (configurable in `config.py`).
4. Download a Piper voice (this project expects `en_US-amy-medium.onnx`) and place it where `core/tts.py` looks for it.
5. Create a `.env` file next to `main.py`, based on `.env.example`:
   
   ROBOFLOW_API_KEY=your_key_here
   

6. Run `python main.py` and switch to Minecraft within the 5-second startup delay.

Hotkeys: **Ctrl+Alt+G** toggles the chat window, **Ctrl+Alt+S** toggles the status dashboard.

## Configuration

Most settings live in `config.py`: detection thresholds, screenshot interval, Ollama model name, proactive-comment timing, vision trigger keywords and TTS volume.

## Project layout


main.py                 entry point, vision loop, chat processor, hotkeys
config.py               settings
core/ai_handler.py      Roboflow detection, parsing, LLM responses, chat memory
core/frame_buffer.py    3-frame buffer and trend analysis
core/screenshot.py      screen capture
core/tts.py             Piper text-to-speech queue
ui/chat_window.py       Tkinter chat overlay
ui/status_window.py     Tkinter status dashboard
utils/logger.py         logging


## Known limitations

- Windows only (uses `winsound`).
- Detection depends on Roboflow's hosted API; replacing it with a custom-trained model is in progress.
- A custom voice-conversion step (RVC) was prototyped but disabled, because it competed with Ollama for a 6 GB GPU.
- Duplicate removal compares class name and center distance, so two real objects of the same type very close together can be merged.
- Conversation memory is in RAM only and resets when the app closes.