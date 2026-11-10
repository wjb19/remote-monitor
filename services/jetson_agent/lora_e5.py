"""Seeed LoRa-E5 (Wio-E5) USB-UART node helper.

Talks AT over the CP2102N USB serial link. Stock firmware only has TEMP/VDD.
Custom rmlab-gas firmware adds AT+GAS for ADS1115 channels:
  A0 MQ-2, A1 MQ-8, A2 MQ-135, A3 MQ-5
"""

import os
import re
import threading
import time

import lora_payload
import serial_at


def env(key, default):
    return os.environ.get(key, default)


class LoRaE5Node(object):
    def __init__(self, port=None, baud=None, node_id=None, on_sample=None):
        self.port = port or env("RMLAB_LORA_E5_PORT", "/dev/ttyUSB0")
        self.baud = int(baud or env("RMLAB_LORA_E5_BAUD", "9600"))
        self.node_id = node_id or env("RMLAB_LORA_E5_NODE_ID", "e5-usb0")
        self.interval_s = float(env("RMLAB_LORA_E5_INTERVAL_S", "30"))
        self.on_sample = on_sample
        self._stop = threading.Event()
        self._thread = None
        self.state = {
            "enabled": env("RMLAB_LORA_E5", "1") != "0",
            "running": False,
            "port": self.port,
            "baud": self.baud,
            "node_id": self.node_id,
            "model": "LoRa-E5",
            "last_error": None,
            "last_ok_ts": None,
            "identity": {},
            "last_sample": None,
            "samples": 0,
            "profile": "temp_v1",
            "gas_available": False,
            "tx_enabled": env("RMLAB_LORA_E5_TX", "0") != "0",
            "last_tx": None,
            "last_tx_error": None,
        }

    def start(self):
        if not self.state["enabled"]:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lora-e5", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self.state["running"] = False

    def _open(self):
        ser = serial_at.SerialAT(self.port, baud=self.baud, timeout=1.0)
        ser.open()
        return ser

    def _probe_identity(self, ser):
        identity = {}
        for cmd, key in (
            ("AT", "at"),
            ("AT+VER", "ver"),
            ("AT+ID", "id"),
            ("AT+DR=SCHEME", "dr"),
            ("AT+MODE", "mode"),
            ("AT+VDD", "vdd"),
            ("AT+GAS", "gas"),
        ):
            try:
                resp = ser.command(cmd, wait=1.2)
                identity[key] = resp.strip()
            except Exception as exc:
                identity[key] = "error: %s" % exc
        self.state["identity"] = identity
        gas = identity.get("gas") or ""
        self.state["gas_available"] = "+GAS:" in gas and "ERROR" not in gas.upper()
        if self.state["gas_available"]:
            self.state["profile"] = "gas_ads1115_v1"
        elif "rmlab-gas" in (identity.get("ver") or "").lower():
            self.state["profile"] = "gas_ads1115_v1"
        else:
            self.state["profile"] = "temp_v1"
        # basic liveness: AT should contain OK
        at = identity.get("at") or ""
        if "OK" not in at and "+AT" not in at.upper():
            raise RuntimeError("LoRa-E5 not responding on %s @ %s" % (self.port, self.baud))
        return identity

    def read_temperature(self, ser):
        resp = ser.command("AT+TEMP", wait=1.0)
        # +TEMP: 20.5
        m = re.search(r"\+TEMP:\s*([-+]?\d+(?:\.\d+)?)", resp)
        if not m:
            raise RuntimeError("TEMP parse failed: %r" % resp.strip())
        return float(m.group(1)), resp.strip()

    def read_vdd(self, ser):
        resp = ser.command("AT+VDD", wait=1.0)
        m = re.search(r"\+VDD:\s*([-+]?\d+(?:\.\d+)?)", resp)
        if not m:
            return None, resp.strip()
        return float(m.group(1)), resp.strip()

    def read_gas(self, ser):
        """Parse +GAS: mq2_mv=..,mq8_mv=..,mq135_mv=..,mq5_mv=.."""
        resp = ser.command("AT+GAS", wait=2.0)
        text = resp.strip()
        if "+GAS:" not in text or "ERROR" in text.upper():
            return None, text
        body = text.split("+GAS:", 1)[1].strip()
        out = {}
        for part in body.split(","):
            if "=" not in part:
                continue
            key, raw = part.split("=", 1)
            key = key.strip().lower()
            try:
                out[key] = float(raw.strip())
            except ValueError:
                continue
        needed = ("mq2_mv", "mq8_mv", "mq135_mv", "mq5_mv")
        if not all(k in out for k in needed):
            return None, text
        return out, text

    def collect_sample(self, ser):
        """Collect sensors for this node profile."""
        temp_c, temp_raw = self.read_temperature(ser)
        vdd_v, vdd_raw = self.read_vdd(ser)
        gas, gas_raw = (None, None)
        if self.state.get("gas_available") or self.state.get("profile") == "gas_ads1115_v1":
            gas, gas_raw = self.read_gas(ser)
            if gas:
                self.state["gas_available"] = True
                self.state["profile"] = "gas_ads1115_v1"
        sample = {
            "ts": time.time(),
            "node_id": self.node_id,
            "model": "LoRa-E5",
            "profile": self.state["profile"],
            "transport": "usb-uart",
            "port": self.port,
            "sensors": {
                "temperature_c": temp_c,
            },
            "raw": {"temp": temp_raw, "vdd": vdd_raw},
        }
        if vdd_v is not None:
            sample["sensors"]["vdd_v"] = vdd_v
        if gas_raw:
            sample["raw"]["gas"] = gas_raw
        if gas:
            sample["sensors"].update(gas)
            sample["payload_hex"] = lora_payload.encode_hex(temp_c, vdd_v, gas_mv=gas)
        else:
            sample["payload_hex"] = lora_payload.encode_hex(
                temp_c, vdd_v, sample["sensors"].get("humidity_pct")
            )
        return sample

    def maybe_transmit(self, ser, sample):
        """Send the same payload a field node would uplink, once the module is joined.

        Left off unless RMLAB_LORA_E5_TX=1 so the USB poll path keeps working.
        """
        if not self.state["tx_enabled"]:
            return
        hx = sample.get("payload_hex")
        if not hx:
            return
        try:
            resp = ser.command("AT+CMSGHEX=%s" % hx, wait=8.0)
            self.state["last_tx"] = resp.strip()
            self.state["last_tx_error"] = None
        except Exception as exc:
            self.state["last_tx_error"] = str(exc)

    def _run(self):
        while not self._stop.is_set():
            ser = None
            try:
                ser = self._open()
                self.state["running"] = True
                self.state["last_error"] = None
                self._probe_identity(ser)
                while not self._stop.is_set():
                    sample = self.collect_sample(ser)
                    self.maybe_transmit(ser, sample)
                    self.state["last_sample"] = sample
                    self.state["last_ok_ts"] = sample["ts"]
                    self.state["samples"] += 1
                    if self.on_sample:
                        self.on_sample(sample)
                    self._stop.wait(self.interval_s)
            except Exception as exc:
                self.state["running"] = False
                self.state["last_error"] = str(exc)
                self._stop.wait(5.0)
            finally:
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass
