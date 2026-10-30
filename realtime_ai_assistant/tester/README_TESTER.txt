MARIA - TESTER BUILD
====================
A desktop AI companion that watches your Minecraft screen and talks to you.
This is an early test build for a few people. Thank you for trying it!

WHAT YOU NEED
- Windows 10 or 11 and Minecraft (Java or Bedrock)
- Python 3.12 or newer  (https://www.python.org/downloads/ - tick "Add python.exe to PATH")
- Ollama  (https://ollama.com) with the llava:7b model, about 4.7 GB
- A Roboflow API key. You get it from the person who sent you this build.

SET UP (once)
1. Unzip this folder anywhere.
2. Double-click setup.bat and follow the prompts. It asks you to paste the key.
3. Make sure Ollama is running (the llama icon in the system tray).

RUN
1. Double-click run.bat.
2. Switch to Minecraft within 5 seconds.
3. Ctrl+Alt+G = chat window.   Ctrl+Alt+S = status window with the live settings
   (voice volume and speed, mute, detection thresholds).

PRIVACY - PLEASE READ
- While running, Maria captures your WHOLE primary screen every few seconds.
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
- The newest file in the logs folder (session_....txt). It lists what Maria saw
  and said, plus any messages you typed to her. Read it first if you want to
  remove something.

TO STOP: close the black window, or press Ctrl+C in it.
