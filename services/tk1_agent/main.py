"""Headless Tegra K1 agent: motion video plus the camera microphone.

Python 2.7. The collector URL is RMLAB_MAIN_URL (the hub on this LAN).
When the K1 moves onto the Jetson's network, point that variable at the
host that should receive the clips.
"""

from __future__ import print_function

import json
import os
import sys
import threading
import time

from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from SocketServer import ThreadingMixIn

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import motion_av  # noqa: E402
import power  # noqa: E402
import thermal  # noqa: E402
import upload  # noqa: E402

STARTED = time.time()
_motion = None
_power = {"ok": False}
_lock = threading.Lock()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def env(key, default):
    return os.environ.get(key, default)


def health_payload():
    with _lock:
        motion_state = None if _motion is None else dict(_motion.state)
        supply = dict(_power)
    supply.pop("rails", None)
    return {
        "ok": True,
        "service": "tk1-agent",
        "hostname": os.uname()[1],
        "uptime_s": round(time.time() - STARTED, 1),
        "main_url": env("RMLAB_MAIN_URL", "http://192.168.1.14:8080"),
        "motion_camera": motion_state,
        "supply": supply,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, payload):
        body = json.dumps(payload)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/api/info", "/"):
            self._send(200, health_payload())
            return
        if path == "/api/motion":
            with _lock:
                payload = None if _motion is None else dict(_motion.state)
            self._send(200, payload or {"ok": False, "error": "motion camera not started"})
            return
        if path == "/api/supply":
            with _lock:
                payload = dict(_power)
            self._send(200, payload)
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else ""
        if path != "/api/motion":
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            body = json.loads(raw or "{}")
        except Exception:
            self._send(400, {"ok": False, "error": "invalid json"})
            return
        if _motion is None:
            self._send(503, {"ok": False, "error": "motion camera not started"})
            return
        allowed = {}
        for key in ("pre_s", "post_s", "threshold", "cooldown_s", "fps", "device"):
            if key in body and body[key] is not None:
                allowed[key] = body[key]
        state = _motion.configure(**allowed)
        self._send(200, {"ok": True, "motion_camera": state})


def on_frame(jpeg_bytes, meta):
    upload.post_frame(jpeg_bytes, meta)


def on_audio(wav_bytes, meta):
    upload.post_audio(wav_bytes, meta)


def power_loop():
    global _power
    interval = float(env("RMLAB_POWER_INTERVAL_S", "15"))
    while True:
        sample = power.sample()
        sample["ts"] = time.time()
        sample["tboard_c"] = thermal.read_tboard_c()
        with _lock:
            _power = sample
        points = []
        if sample.get("ok") and sample.get("current_a") is not None:
            tags = sample.get("tags") or {}
            points.append(
                {
                    "metric": "tk1.supply_current_a",
                    "ts": sample["ts"],
                    "value": sample["current_a"],
                    "tags": tags,
                }
            )
            points.append(
                {
                    "metric": "tk1.supply_voltage_v",
                    "ts": sample["ts"],
                    "value": sample["voltage_v"],
                    "tags": tags,
                }
            )
            if sample.get("power_w") is not None:
                points.append(
                    {
                        "metric": "tk1.supply_power_w",
                        "ts": sample["ts"],
                        "value": sample["power_w"],
                        "tags": tags,
                    }
                )
        if sample.get("tboard_c") is not None:
            points.append(
                {
                    "metric": "tk1.tboard_c",
                    "ts": sample["ts"],
                    "value": sample["tboard_c"],
                    "tags": {"sensor": "Tboard_tegra"},
                }
            )
        if points:
            try:
                upload.post_json("/api/telemetry/points", {"points": points})
            except Exception as exc:
                with _lock:
                    _power["upload_error"] = str(exc)
        time.sleep(interval)


def main():
    global _motion
    host = env("RMLAB_HOST", "0.0.0.0")
    port = int(env("RMLAB_PORT", "8090"))
    power_thread = threading.Thread(target=power_loop)
    power_thread.setDaemon(True)
    power_thread.start()
    _motion = motion_av.MotionAV(on_frame=on_frame, on_audio=on_audio)
    _motion.start()
    server = ThreadingHTTPServer((host, port), Handler)
    print("tk1-agent listening on %s:%s" % (host, port))
    sys.stdout.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
