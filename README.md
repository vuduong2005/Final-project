# Maria – Real-Time AI Game Companion

Maria is a desktop AI companion that watches your Minecraft gameplay through screen capture and talks back by voice and chat. It was built solo, starting in January 2026.

> **About this repository.** Maria's personality and reply logic (`prompts`, `ai_handler`, `attention`, `memory` in `core/`) are private modules. This repository holds small stand-in files with the same names, so **the code here does not run on its own**. It is published to show the structure of the project and the parts that are public: capture, voice, windows, settings, tests and the tester build.

## What it does

- **Game Mode (automatic):** looks at the Minecraft window every ~0.5 s, detects mobs, ores, hazards and status bars, and comments on what matters (an approaching creeper, a diamond ore).
- **Fast danger warnings:** a warning about a dangerous mob is built straight from the detections and spoken at once, without waiting for the language model.
- **Chat Mode:** type to Maria in a chat window and she answers as a cheerful friend. She keeps the recent conversation in memory, and remembers the nickname and standing requests you give her ("call me ...", "warn me about creepers").
- **Talk to her:** a Voice button in the chat window turns on speech input. It works as an open microphone or as push to talk (hold a key). You can interrupt her by talking over her, and choose which microphone to use. Speech is turned into text on your PC; the sound is never saved or sent anywhere.
- **Hybrid vision:** a screenshot is attached to a chat message only when the message asks her to look at something ("can you see...", "look at..."). Normal chat stays text-only to keep it fast.
- **Proactive comments:** if you stay on one activity for ~30 s, she may comment unprompted, unless you chatted recently.
- **Short-term visual memory:** a frame buffer keeps the last 3 frames and tracks detection confidence and position to spot threats moving closer.
- **Voice:** local text-to-speech with Piper, in three voice styles, with an optional voice model of her own (not included).
- **A face on screen:** a small pixel-art Maria in a corner of the screen blinks while she waits and moves her mouth while she talks. It never takes keyboard focus away from the game.
- **Live settings:** volume, speed, mute, voice style, microphone, push to talk, capture timing and detection thresholds can be changed while she runs, from the status window.

## How it works

```text
screenshot of the Minecraft window (mss)
   -> 3 Roboflow models (general, mobs, blocks), per-model confidence thresholds
   -> duplicate removal (same class + centers within 50 px in one frame)
   -> frame buffer (last 3 frames, approach / move-away trend)
   -> parser -> short text ("creeper in left middle")
   -> attention filter (decides whether she speaks now)
   -> danger?  spoken at once from the facts
      otherwise  local LLM via Ollama writes the line (Game / Chat / Proactive)
   -> Piper TTS + chat window + on-screen face
```

Threads: vision loop, chat processor, TTS worker, voice input and hotkey listener, connected by thread-safe queues.

## Privacy

- **Only the Minecraft window is captured**, never the rest of your screen (setting `CAPTURE_ONLY_GAME`, on by default). If the game is not found, nothing is captured.
- The picture of the game window is sent to Roboflow's hosted models for detection. Replacing them with a local model is planned.
- Your voice stays on your PC. Chat with the language model runs locally through Ollama.
- Maria's memory is kept on your PC. By default the nickname and requests are cleared at each start (`PROFILE_RESET_EACH_SESSION`).

## Measured performance

From an earlier test run (111 logged Game Mode analysis cycles, before the fast danger lane): average end-to-end latency about 5.6 s, median about 5.4 s, mostly the hosted detection calls and the local LLM. Danger warnings no longer wait for the LLM. This number has not been re-measured.

## Setup (Windows)

Requirements: Python 3.12 or newer (tested on 3.14), [Ollama](https://ollama.com) running locally, a Roboflow API key with access to the three models configured in `config.py`, and the private modules. A graphics card with about 6 GB of VRAM runs the language model fully on the GPU.

The app lives in the `realtime_ai_assistant` folder. From the repo root:

1. Create a virtual environment and install the dependencies (this includes `piper-tts` and `python-dotenv`):

   ```bat
   cd realtime_ai_assistant
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Pull the language model (it can also look at pictures; the name is set in `config.py`):

   ```bat
   ollama pull gemma3:4b
   ```

3. Download a Piper voice. The app expects `en_US-amy-medium.onnx` and `en_US-amy-medium.onnx.json` next to `main.py`. The voice files are not in the repo.
4. Create a `.env` file next to `main.py`, based on `.env.example`, and add your key. Never commit this file.

   ```text
   ROBOFLOW_API_KEY=your_key_here
   ```

5. Optional, to talk to Maria with your voice:

   ```bat
   pip install -r requirements-voice.txt
   ```

   The first time voice is switched on, it downloads a speech model once (about 150 MB).

6. Run it, and switch to Minecraft within the 5-second startup delay:

   ```bat
   python main.py
   ```

Hotkeys: **Ctrl+Alt+G** toggles the chat window, **Ctrl+Alt+S** toggles the status window, **Ctrl+Alt+V** turns voice on or off. With push to talk on, hold the chosen key (V by default) while Minecraft is in front.

### Tester build

To hand Maria to a few testers without a developer setup, `python tester/build_zip.py` builds `dist/maria-tester-<date>.zip` with `setup.bat` and `run.bat`. It never includes `.env`, logs or screenshots. The page that links to it is in `docs/index.html`.

## Live settings

Press **Ctrl+Alt+S** to open the status window:

- **Voice:** volume, speed, mute, voice style, voice model (when installed), and a Test voice button.
- **Talk to Maria:** which microphone, push to talk and its key, and how long a pause ends your phrase.
- **Detection & timing:** capture and analysis intervals, which Roboflow models are on, the confidence thresholds for each model, and the frame-buffer thresholds. At least one model always stays on.
- **Advanced config:** only the startup delay, which needs a restart.

Changes apply from the next frame or spoken line. They are saved to `config_overrides.json` next to `main.py`, which is local to your machine and not tracked by git. Each setting has limits, and out-of-range values are clamped.

## Configuration

Defaults live in `config.py`: detection thresholds, screenshot interval, Ollama model name, proactive-comment timing, vision trigger keywords and voice settings. Anything you change in the status window is saved on top of those defaults in `config_overrides.json`; delete that file to go back to the defaults.

## Project layout

```text
main.py                  entry point, vision loop, chat processor, hotkeys
config.py                defaults and loading of saved overrides
core/settings.py         live settings store (limits, listeners, saving)
core/screenshot.py       captures the Minecraft window only
core/frame_buffer.py     3-frame buffer and trend analysis
core/mob_classes.py      which classes are dangerous, passive, resources
core/tts.py              Piper text-to-speech queue and voice styles
core/speech_state.py     whether she is speaking now, and how loud
core/voice_input.py      microphone, speech to text, open mic and push to talk
core/ai_handler.py       private module (stand-in here): detection, parsing, replies
core/attention.py        private module (stand-in here): when she speaks
core/prompts.py          private module (stand-in here): her personality and wording
core/memory.py           private module (stand-in here): what she remembers
ui/chat_window.py        chat window with the Voice and Stop buttons
ui/status_window.py      status window with the live settings
ui/speaking_overlay.py   her on-screen face
ui/maria_sprite.py       draws that face in code
ui/win_focus.py          keeps her windows from taking focus from the game
utils/logger.py          logging
tests/                   checks that run without the game (the private modules' tests are not included)
tester/                  setup.bat, run.bat and the tester zip builder
```

The tester page is in `docs/` at the repo root.

## Known limitations

- Windows only (uses `winsound` and Windows window handling).
- Works with Minecraft Java in English. The trend math assumes the game view has FOV 100; another FOV only makes the turn and tilt correction less accurate.
- Detection depends on Roboflow's hosted API; replacing it with a custom-trained model is planned.
- Duplicate removal compares class name and center distance, so two real objects of the same type very close together can be merged.
- A custom voice-conversion step (RVC) was prototyped but disabled, because it competed with Ollama for a 6 GB GPU.
- Speech input needs a working microphone; on a very quiet or noisy mic it may miss or invent words.
- The repository alone does not run: the private modules are not included.
