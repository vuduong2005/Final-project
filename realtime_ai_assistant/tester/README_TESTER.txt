MARIA - TESTER BUILD
====================
A desktop AI companion that watches your Minecraft screen and talks to you.
This is an early test build for a few people. Thank you for trying it!

WHAT YOU NEED
- Windows 10 or 11 and Minecraft: Java Edition (Bedrock is not supported)
- Python 3.12 or newer  (https://www.python.org/downloads/ - tick "Add python.exe to PATH")
- Ollama  (https://ollama.com) with the gemma3:4b model, about 3.3 GB
- A Roboflow API key. You get it from the person who sent you this build.

SET UP (once)
1. Unzip this folder anywhere.
2. Double-click setup.bat and follow the prompts. It asks you to paste the key.
3. Make sure Ollama is running (the llama icon in the system tray).

RUN
1. Double-click run.bat.
2. Switch to Minecraft within 5 seconds. For the best results play fullscreen with
   the FOV slider set to 100 (Options > Video Settings). Maria uses it to tell whether
   a mob is walking towards you; other values make that guess a little less accurate.
3. Ctrl+Alt+G = chat window.   Ctrl+Alt+S = status window with the live settings
   (voice volume, speed and style, mute, detection thresholds). Click the chat box to type,
   press Enter to send and go back to the game.
   If clicking the chat box pauses Minecraft, press F3+P in the game once (turns off
   "pause on lost focus"), or play in borderless windowed mode.
4. A small pixel portrait of Maria shows in the bottom-right corner while she talks.
5. Optional: talk to her out loud. This needs the extra libraries: run
   venv\Scripts\python.exe -m pip install -r requirements-voice.txt once (the first use also downloads a
   speech model of about 150 MB). Then click the "Voice: OFF" button in the chat window (or press Ctrl+Alt+V)
   to turn voice on, and just speak: a pause sends what you said. Talk over her and she stops. In the Status
   window you can tick "Push to talk" to record only while you hold a key (V by default, only while Minecraft is
   in front and not while Ctrl, Alt or Win is held, e.g. sprinting; if you type a V in Minecraft's chat it records
   too, so pick F8 there). A headset works best:
   with speakers she may hear herself. Voice is always off when the app starts. The microphone is only open
   while voice is on (or the key is held); the sound stays on your PC.

PRIVACY - PLEASE READ
- While running, Maria captures ONLY the Minecraft window (the Java game, whose title looks
  like "Minecraft 1.20.1") every few seconds - even when another window is in front of it.
  Nothing else on your screen is captured. Her own chat and status windows are left out too.
  If Minecraft is closed or minimised she captures nothing.
- Those frames are sent to Roboflow (a hosted service) for detection.
- The last 50 captures are also saved on your PC in the debug_captures folder.
- Close anything private before you start, and stop Maria when you are done.

WHAT TO TRY
- Does she notice mobs, ores and lava? Is anything wrong or missing?
- Chat with her. Ask "can you see my house?" to test the vision step.
- Play with the settings in the status window. Do the changes apply right away?
- How slow is it on your PC? (About 5 seconds per comment is normal for now.)

SEND ME
- What you saw, what broke, and your PC (CPU, GPU, RAM).
- If it would not start: the error text shown in the black window (copy it).
- The newest file in the logs folder, if there is one (session_....txt). It lists what Maria saw
  and said, plus any messages you typed to her. Read it first if you want to
  remove something.

TO STOP: click "Stop Maria" at the bottom of the chat window (click it twice to confirm),
or close the black window, or press Ctrl+C in it.
