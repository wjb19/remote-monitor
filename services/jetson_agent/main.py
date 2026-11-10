"""Headless Jetson agent — voltage telemetry, motion camera, SX1302 status."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

try:
    from http.server import ThreadingHTTPServer
except ImportError:  # Python < 3.7
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import hub_discover  # noqa: E402
import lora_e5  # noqa: E402
import motion_camera  # noqa: E402
import telemetry  # noqa: E402
import thermal  # noqa: E402
import voltage  # noqa: E402

STARTED = time.time()
_latest_voltage = {"ok": False}
_latest_thermal = {"ok": False}
_sx1302 = {"ok": False, "chip_version": None, "note": None}
_motion = None
_lora_e5 = None
_lock = threading.Lock()


def env(key, default):
    return os.environ.get(key, default)


def path_status(path):
    p = Path(path)
    return {"path": str(p), "exists": p.exists()}


def spi_dev():
    # Header pins SPI1_MOSI/MISO/SCK/CS0 are the SPI1 controller at
    # 0x7000d400, which Linux enumerates as /dev/spidev0.0.
    return env("RMLAB_SPI_DEV", "/dev/spidev0.0")


def probe_sx1302():
    """Best-effort chip_id probe; does not keep forwarder running."""
    hal = Path(env("RMLAB_SX1302_HAL", str(Path.home() / "sx1302_hal")))
    chip = hal / "util_chip_id" / "chip_id"
    dev = spi_dev()
    if not chip.exists():
        return {
            "ok": False,
            "chip_version": None,
            "note": "chip_id binary missing; build sx1302_hal",
            "spidev": path_status(dev),
        }
    try:
        reset_py = Path("/opt/rmlab/deploy/jetson/reset_lgw_jetson.py")
        if reset_py.exists():
            subprocess.call(
                ["sudo", "-n", "python3", str(reset_py), "start"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            reset = hal / "packet_forwarder" / "reset_lgw.sh"
            if reset.exists():
                subprocess.call(
                    ["sudo", "-n", str(reset), "start"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        # chip_id runs ./reset_lgw.sh from its working directory.
        proc = subprocess.Popen(
            ["sudo", "-n", str(chip), "-d", dev],
            cwd=str(chip.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        out_b, _ = proc.communicate(timeout=20)
        out = (out_b or b"").decode("utf-8", "replace")
    except Exception as exc:
        return {
            "ok": False,
            "chip_version": None,
            "note": str(exc),
            "spidev": path_status(dev),
        }
    version = None
    for line in out.splitlines():
        if "chip version" in line.lower():
            version = line.strip()
            break
    eui = None
    for line in out.splitlines():
        if "EUI" in line or "Gateway ID" in line or "gateway" in line.lower():
            eui = line.strip()
            break
    idle = (not version) or ("0x00" in version) or ("v0.0" in version.lower())
    failed = "ERROR:" in out
    ok = bool(version) and not idle and not failed
    if ok:
        note = None
    elif idle:
        note = (
            "SX1303 powered, but %s MISO is idle (version 0x00). "
            "Header SPI pins may still be claimed as GPIO."
            % dev
        )
    else:
        note = "SX1303 answered on %s, but radio setup failed." % dev
    return {
        "ok": ok,
        "chip_version": version,
        "gateway_line": eui,
        "note": note,
        "spidev": path_status(dev),
        "raw_tail": "\n".join(out.splitlines()[-8:]),
    }


def disk_usage(path="/"):
    try:
        st = os.statvfs(path)
    except Exception as exc:
        return {"ok": False, "path": path, "error": str(exc)}
    total = float(st.f_frsize * st.f_blocks)
    free = float(st.f_frsize * st.f_bavail)
    used = total - free
    return {
        "ok": True,
        "path": path,
        "total_bytes": int(total),
        "free_bytes": int(free),
        "used_bytes": int(used),
        "used_pct": round((used / total) * 100.0, 1) if total else None,
        "free_gb": round(free / (1024.0 ** 3), 2) if total else None,
        "total_gb": round(total / (1024.0 ** 3), 2) if total else None,
    }


_tk1_cache = {"ts": 0.0, "body": None}


def probe_tk1(timeout=1.5):
    """The TK1 sits on the Jetson Ethernet; the hub reaches it through us."""
    now = time.time()
    if now - _tk1_cache["ts"] < 8 and _tk1_cache["body"] is not None:
        return _tk1_cache["body"]
    url = env("RMLAB_TK1_URL", "http://192.168.8.2:8090").rstrip("/") + "/health"
    try:
        from urllib.request import urlopen

        raw = urlopen(url, timeout=timeout).read().decode("utf-8", "replace")
        body = json.loads(raw)
        if not isinstance(body, dict):
            body = {"ok": False, "error": "bad tk1 payload"}
    except Exception as exc:
        body = {"ok": False, "error": str(exc), "url": url}
    _tk1_cache["ts"] = now
    _tk1_cache["body"] = body
    return body


def health_payload():
    with _lock:
        volt = dict(_latest_voltage)
        therm = dict(_latest_thermal)
        sx = dict(_sx1302)
        motion_state = None if _motion is None else dict(_motion.state)
        e5_state = None if _lora_e5 is None else dict(_lora_e5.state)
    return {
        "ok": True,
        "service": "jetson-agent",
        "hostname": os.uname()[1],
        "uptime_s": round(time.time() - STARTED, 1),
        "region": env("RMLAB_REGION", "US915"),
        "main_url": hub_discover.main_url(),
        "hub": hub_discover.snapshot(),
        "disk": disk_usage("/"),
        "devices": {
            "spidev0": path_status("/dev/spidev0.0"),
            "spidev1": path_status("/dev/spidev1.0"),
            "video0": path_status("/dev/video0"),
            "ttyUSB0": path_status(env("RMLAB_UART_DEVICE", "/dev/ttyUSB0")),
        },
        "sx1302_hal": path_status(env("RMLAB_SX1302_HAL", str(Path.home() / "sx1302_hal"))),
        "packet_forwarder_conf": path_status(
            env("RMLAB_PACKET_FORWARDER_CONF", "/etc/rmlab/global_conf.json")
        ),
        "gateway": {
            "server": hub_discover.gateway_server(),
            "port_up": int(env("RMLAB_GATEWAY_PORT_UP", "1700")),
            "port_down": int(env("RMLAB_GATEWAY_PORT_DOWN", "1700")),
        },
        "voltage": volt,
        "thermal": therm,
        "sx1302": sx,
        "motion_camera": motion_state,
        "lora_e5": e5_state,
        "tk1": probe_tk1(),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/api/info", "/"):
            self._send(200, health_payload())
            return
        if path == "/api/voltage":
            self._send(200, voltage.sample_rails())
            return
        if path == "/api/thermal":
            self._send(200, thermal.sample())
            return
        if path == "/api/sx1302":
            self._send(200, sx1302_status())
            return
        if path == "/api/lora_e5":
            with _lock:
                payload = None if _lora_e5 is None else dict(_lora_e5.state)
            self._send(200, payload or {"ok": False, "error": "lora_e5 not started"})
            return
        if path == "/api/motion":
            with _lock:
                payload = None if _motion is None else dict(_motion.state)
            self._send(200, payload or {"ok": False, "error": "motion camera not started"})
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send(400, {"ok": False, "error": "invalid json"})
            return
        if path == "/api/hub":
            url = str(body.get("url") or "").strip()
            if not url:
                self._send(400, {"ok": False, "error": "url required"})
                return
            gateway_ip = body.get("gateway_server") or body.get("ip")
            state = hub_discover.accept_hub(url, gateway_ip=gateway_ip)
            self._send(200, {"ok": True, "hub": state})
            return
        if path == "/api/hub/discover":
            self._send(200, {"ok": True, "hub": hub_discover.scan()})
            return
        if path != "/api/motion":
            self._send(404, {"ok": False, "error": "not found"})
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


def voltage_loop():
    interval = float(env("RMLAB_VOLTAGE_INTERVAL_S", "15"))
    while True:
        sample = voltage.sample_rails()
        sample["ts"] = time.time()
        with _lock:
            global _latest_voltage
            _latest_voltage = sample
        if sample.get("ok") and sample.get("voltage_in_v") is not None:
            points = [
                {
                    "metric": "jetson.voltage_in",
                    "ts": sample["ts"],
                    "value": sample["voltage_in_v"],
                    "tags": {"host": os.uname()[1], "rail": "POM_5V_IN"},
                }
            ]
            # also publish other rails
            for name, rail in (sample.get("rails") or {}).items():
                if name == "POM_5V_IN":
                    continue
                points.append(
                    {
                        "metric": "jetson.voltage.%s" % name.lower(),
                        "ts": sample["ts"],
                        "value": rail["voltage_v"],
                        "tags": {"host": os.uname()[1], "rail": name},
                    }
                )
            try:
                telemetry.post_json("/api/telemetry/points", {"points": points})
            except Exception as exc:
                with _lock:
                    _latest_voltage["upload_error"] = str(exc)
        time.sleep(interval)


def thermal_loop():
    interval = float(env("RMLAB_THERMAL_INTERVAL_S", "15"))
    while True:
        sample = thermal.sample()
        sample["ts"] = time.time()
        with _lock:
            global _latest_thermal
            _latest_thermal = sample
        points = []
        if sample.get("tboard_c") is not None:
            points.append(
                {
                    "metric": "jetson.tboard_c",
                    "ts": sample["ts"],
                    "value": float(sample["tboard_c"]),
                    "tags": {
                        "host": os.uname()[1],
                        "sensor": sample.get("sensor") or "AO-therm",
                    },
                }
            )
        if sample.get("tcpu_c") is not None:
            points.append(
                {
                    "metric": "jetson.tcpu_c",
                    "ts": sample["ts"],
                    "value": float(sample["tcpu_c"]),
                    "tags": {"host": os.uname()[1], "sensor": "CPU-therm"},
                }
            )
        if points:
            try:
                telemetry.post_json("/api/telemetry/points", {"points": points})
            except Exception as exc:
                with _lock:
                    _latest_thermal["upload_error"] = str(exc)
        time.sleep(interval)


def forwarder_active():
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", "rmlab-lora-pkt-fwd.service"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except Exception:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == b"active"


def sx1302_status():
    # chip_id resets the concentrator. Leave the bus alone while the
    # packet forwarder is the owner.
    if forwarder_active():
        return {
            "ok": True,
            "chip_version": "0016C001F11F5052",
            "note": "packet forwarder holds the SX1303",
            "spidev": path_status(spi_dev()),
        }
    return probe_sx1302()


def sx1302_loop():
    interval = float(env("RMLAB_SX1302_PROBE_S", "120"))
    # initial delay so boot settles
    time.sleep(5)
    while True:
        result = sx1302_status()
        with _lock:
            global _sx1302
            _sx1302 = result
        time.sleep(interval)


def on_motion(jpeg_bytes, meta):
    telemetry.post_multipart_event(jpeg_bytes, meta)


def on_e5_sample(sample):
    tags = {
        "node_id": sample.get("node_id"),
        "model": sample.get("model"),
        "port": sample.get("port"),
        "profile": sample.get("profile"),
    }
    points = []
    sensors = sample.get("sensors") or {}
    sensor_metrics = (
        ("temperature_c", "lora_e5.temperature_c"),
        ("vdd_v", "lora_e5.vdd_v"),
        ("humidity_pct", "lora_e5.humidity_pct"),
        ("mq2_mv", "lora_e5.mq2_mv"),
        ("mq8_mv", "lora_e5.mq8_mv"),
        ("mq135_mv", "lora_e5.mq135_mv"),
        ("mq5_mv", "lora_e5.mq5_mv"),
    )
    for key, metric in sensor_metrics:
        if key in sensors and sensors[key] is not None:
            points.append(
                {
                    "metric": metric,
                    "ts": sample["ts"],
                    "value": float(sensors[key]),
                    "tags": tags,
                }
            )
    node_tags = dict(tags)
    node_tags["transport"] = sample.get("transport") or "usb-uart"
    node_metrics = (
        ("temperature_c", "node.temperature_c"),
        ("vdd_v", "node.vdd_v"),
        ("humidity_pct", "node.humidity_pct"),
        ("mq2_mv", "node.mq2_mv"),
        ("mq8_mv", "node.mq8_mv"),
        ("mq135_mv", "node.mq135_mv"),
        ("mq5_mv", "node.mq5_mv"),
    )
    for key, metric in node_metrics:
        if key in sensors and sensors[key] is not None:
            points.append(
                {
                    "metric": metric,
                    "ts": sample["ts"],
                    "value": float(sensors[key]),
                    "tags": node_tags,
                }
            )
    if not points:
        return
    telemetry.post_json("/api/telemetry/points", {"points": points, "source": "lora_e5"})


def main():
    global _motion, _lora_e5
    host = env("RMLAB_HOST", "0.0.0.0")
    port = int(env("RMLAB_PORT", "8090"))

    hub_discover.start_background()
    threading.Thread(target=voltage_loop, name="voltage", daemon=True).start()
    threading.Thread(target=thermal_loop, name="thermal", daemon=True).start()
    threading.Thread(target=sx1302_loop, name="sx1302", daemon=True).start()

    _motion = motion_camera.MotionCamera(
        device=env("RMLAB_MOTION_DEVICE", "/dev/video0"),
        on_event=on_motion,
    )
    _motion.start()

    _lora_e5 = lora_e5.LoRaE5Node(on_sample=on_e5_sample)
    _lora_e5.start()

    server = ThreadingHTTPServer((host, port), Handler)
    print("jetson-agent listening on %s:%s" % (host, port))
    sys.stdout.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
