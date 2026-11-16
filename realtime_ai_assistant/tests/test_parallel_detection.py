r"""Checks that the three Roboflow calls run at the same time, retry transient errors,
report failures, and give the same detections as before.

No network and no real API key: requests.post is replaced by a fake that sleeps like a slow API.

Run from the realtime_ai_assistant folder, either way:
    venv\Scripts\python.exe tests\test_parallel_detection.py
    venv\Scripts\python.exe -m pytest tests                       (needs pytest installed)
"""
import os
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ROBOFLOW_API_KEY", "test-key-not-real")   # config refuses to load without one

import requests
from PIL import Image

import core.ai_handler as ai

LATENCY = 0.4   # seconds per fake API call
PREDICTIONS = {
    "general": [{"class": "Cow", "confidence": 0.9, "x": 100, "y": 100, "width": 40, "height": 50}],
    "mobs":    [{"class": "Zombie", "confidence": 0.95, "x": 400, "y": 300, "width": 60, "height": 120}],
    "blocks":  [{"class": "coal", "confidence": 0.8, "x": 700, "y": 500, "width": 30, "height": 30}],
}
IMG = Image.new("RGB", (1280, 720))


class FakeApi:
    """Stands in for requests.post. script[name] is a list of outcomes used one per call
    (the last one repeats): "ok", an int HTTP status, or an exception instance."""

    def __init__(self, detector, script=None, latency=LATENCY):
        self.urls = {detector.general_url: "general", detector.mobs_url: "mobs", detector.blocks_url: "blocks"}
        self.script = script or {}
        self.latency = latency
        self.calls = []          # model names, in call order
        self.uploads = []        # the exact bytes object each call sent

    def __call__(self, url, params=None, files=None, timeout=None):
        time.sleep(self.latency)
        name = self.urls[url]
        self.calls.append(name)
        self.uploads.append(files["file"][1])
        steps = self.script.get(name, ["ok"])
        step = steps[min(self.calls.count(name) - 1, len(steps) - 1)]
        if isinstance(step, Exception):
            raise step
        resp = mock.Mock()
        resp.status_code = 200 if step == "ok" else step
        resp.headers = {"Retry-After": "0"}
        if step == "ok":
            resp.raise_for_status = lambda: None
            resp.json = lambda: {"predictions": PREDICTIONS[name]}
        else:
            def boom():
                raise requests.exceptions.HTTPError(f"HTTP {step}")
            resp.raise_for_status = boom
        return resp


def settings_with(use):
    values = {"USE_GENERAL_MODEL": use.get("general", True), "USE_MOBS_MODEL": use.get("mobs", True),
              "USE_BLOCKS_MODEL": use.get("blocks", True), "ROBOFLOW_CONFIDENCE_PERCENT": 50,
              "MOB_CONFIDENCE_THRESHOLD": 0.6, "BLOCK_CONFIDENCE_THRESHOLD": 0.4}
    return lambda key: values[key]


def run(script=None, use=None, latency=LATENCY):
    det = ai.MultiModelDetector()
    det.RETRY_DELAY = 0          # do not really wait between retries in tests
    api = FakeApi(det, script, latency)
    with mock.patch.object(ai.requests, "post", api), mock.patch.object(ai, "get_setting", settings_with(use or {})):
        t0 = time.time()
        result = det.detect(IMG)
        return result, time.time() - t0, api


def classes(result):
    return {d["class"] for d in result["detections"]}


def test_three_calls_take_about_one_calls_time():
    result, took, api = run()
    assert took < LATENCY * 1.8, f"{took:.2f}s (sequential would be {LATENCY * 3:.2f}s)"
    assert sorted(api.calls) == ["blocks", "general", "mobs"]
    assert result["metadata"]["models_used"] == ["general", "mobs", "blocks"]
    assert result["metadata"]["models_failed"] == []


def test_all_detections_and_box_sizes_are_kept():
    result, _, _ = run()
    assert classes(result) == {"Cow", "Zombie", "coal"}
    zombie = next(d for d in result["detections"] if d["class"] == "Zombie")
    assert zombie["bbox"]["width"] == 60 and zombie["bbox"]["height"] == 120


def test_disabled_models_are_not_called():
    result, _, api = run(use={"mobs": False})
    assert "mobs" not in api.calls and result["metadata"]["models_used"] == ["general", "blocks"]
    result, _, api = run(use={"general": False, "mobs": False, "blocks": False})
    assert result["detections"] == [] and api.calls == []


def test_the_jpeg_is_encoded_once_and_shared():
    det = ai.MultiModelDetector()
    det.RETRY_DELAY = 0
    api = FakeApi(det, latency=0.01)
    real_encode = det._encode_jpeg
    with mock.patch.object(ai.requests, "post", api), mock.patch.object(ai, "get_setting", settings_with({})), \
            mock.patch.object(det, "_encode_jpeg", wraps=real_encode) as encode:
        det.detect(IMG)
    assert encode.call_count == 1
    assert len(api.uploads) == 3 and all(u is api.uploads[0] for u in api.uploads)   # the same bytes object
    assert api.uploads[0][:2] == b"\xff\xd8"                                             # a real JPEG


def test_one_failing_model_is_reported_and_keeps_the_others():
    result, took, api = run({"mobs": [RuntimeError("simulated network error")]})
    assert classes(result) == {"Cow", "coal"}
    assert result["metadata"]["models_failed"] == ["mobs"]
    assert took < LATENCY * 1.8, f"{took:.2f}s"


def test_http_429_is_retried_and_then_succeeds():
    result, _, api = run({"blocks": [429, "ok"]})
    assert api.calls.count("blocks") == 2
    assert classes(result) == {"Cow", "Zombie", "coal"}
    assert result["metadata"]["models_failed"] == []


def test_a_model_that_keeps_returning_429_is_reported_as_failed():
    result, _, api = run({"mobs": [429]})
    assert api.calls.count("mobs") == ai.MultiModelDetector.MAX_ATTEMPTS
    assert classes(result) == {"Cow", "coal"}
    assert result["metadata"]["models_failed"] == ["mobs"]


def test_connection_errors_are_retried_once():
    result, _, api = run({"general": [requests.exceptions.ConnectionError("reset"), "ok"]})
    assert api.calls.count("general") == 2 and result["metadata"]["models_failed"] == []


def test_a_timeout_is_not_retried():
    result, _, api = run({"general": [requests.exceptions.Timeout("slow")]})
    assert api.calls.count("general") == 1
    assert result["metadata"]["models_failed"] == ["general"]


def test_a_client_error_is_not_retried():
    result, _, api = run({"blocks": [401]})            # bad API key: retrying cannot help
    assert api.calls.count("blocks") == 1
    assert result["metadata"]["models_failed"] == ["blocks"]


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
