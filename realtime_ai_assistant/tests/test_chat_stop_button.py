r"""Checks the Stop button in the chat window (headless: the window is created but never shown).

Run from the realtime_ai_assistant folder, either way:
    venv\Scripts\python.exe tests\test_chat_stop_button.py
    venv\Scripts\python.exe -m pytest tests                       (needs pytest installed)
"""
import queue
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.chat_window import ChatWindow


def make_chat(stop_callback=None, confirm_ms=2000):
    chat = ChatWindow(queue.Queue(), {}, stop_callback=stop_callback)
    chat.STOP_CONFIRM_MS = confirm_ms
    return chat


def pump(chat, seconds):
    """Let Tk run its scheduled events for a while."""
    end = time.time() + seconds
    while time.time() < end:
        chat.window.update()
        time.sleep(0.01)


def chat_text(chat):
    return chat.chat_display.get("1.0", tk.END)


def test_button_starts_at_rest():
    chat = make_chat()
    try:
        assert chat.stop_button.cget("text") == ChatWindow.STOP_TEXT
        assert str(chat.stop_button.cget("state")) == "normal"
        assert chat.status_label.cget("text").startswith("Type here")    # the old status text is still there
    finally:
        chat.window.destroy()


def test_first_click_only_arms_it():
    calls = []
    chat = make_chat(stop_callback=lambda: calls.append(1))
    try:
        chat.stop_button.invoke()
        assert chat.stop_button.cget("text") == ChatWindow.STOP_ARMED_TEXT
        pump(chat, 0.5)
        assert calls == [], "one click must not stop Maria"
    finally:
        chat.window.destroy()


def test_second_click_stops_once():
    calls = []
    chat = make_chat(stop_callback=lambda: calls.append(1))
    try:
        chat.stop_button.invoke()
        chat.stop_button.invoke()
        pump(chat, 0.1)                                       # the farewell line goes through Tk's event queue
        assert "Stopping Maria" in chat_text(chat)
        assert str(chat.stop_button.cget("state")) == "disabled"
        assert calls == [], "stopping waits a moment so the farewell is visible"
        pump(chat, 0.8)
        assert calls == [1]
    finally:
        chat.window.destroy()


def test_arming_times_out_and_needs_two_clicks_again():
    calls = []
    chat = make_chat(stop_callback=lambda: calls.append(1), confirm_ms=100)
    try:
        chat.stop_button.invoke()
        pump(chat, 0.4)
        assert chat.stop_button.cget("text") == ChatWindow.STOP_TEXT     # back to rest
        chat.stop_button.invoke()                                         # this click only arms again
        pump(chat, 0.05)
        assert calls == []
        assert chat.stop_button.cget("text") == ChatWindow.STOP_ARMED_TEXT
    finally:
        chat.window.destroy()


def test_without_a_callback_the_main_loop_ends():
    chat = make_chat()
    fired = []
    try:
        chat.stop_button.invoke()
        chat.stop_button.invoke()
        chat.window.after(3000, lambda: (fired.append(1), chat.window.quit()))   # failsafe so a bug cannot hang the test
        start = time.time()
        chat.window.mainloop()
        assert fired == [], "mainloop only ended because of the failsafe"
        assert time.time() - start < 2
    finally:
        chat.window.destroy()


def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{'ALL OK' if not failed else 'SOME CHECKS FAILED'} ({len(tests) - failed} of {len(tests)} tests passed)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
