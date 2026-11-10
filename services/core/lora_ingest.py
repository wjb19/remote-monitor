"""Turn ChirpStack uplinks into the same node metrics the USB LoRa-E5 publishes."""

from __future__ import annotations

import base64
import json
import struct
import time
from typing import Any

from jetson_agent.lora_payload import decode_payload

SENSOR_METRICS = (
    ("temperature_c", "node.temperature_c"),
    ("vdd_v", "node.vdd_v"),
    ("humidity_pct", "node.humidity_pct"),
    ("mq2_mv", "node.mq2_mv"),
    ("mq8_mv", "node.mq8_mv"),
    ("mq135_mv", "node.mq135_mv"),
    ("mq5_mv", "node.mq5_mv"),
)


def _points_for_sensors(sensors: dict, ts: float, tags: dict) -> list[dict]:
    points = []
    for key, metric in SENSOR_METRICS:
        if sensors.get(key) is None:
            continue
        points.append(
            {
                "metric": metric,
                "ts": ts,
                "value": float(sensors[key]),
                "tags": tags,
            }
        )
    return points


def points_from_mqtt(topic: str, payload: bytes, now: float | None = None) -> list[dict]:
    """Decode one MQTT publish. Unknown binary gateway frames still count as RX."""
    ts = now if now is not None else time.time()
    if topic.endswith("/event/up") and "/device/" not in topic:
        return _gateway_up(topic, payload, ts)
    if "/device/" in topic and topic.endswith("/event/up"):
        return _application_up(payload, ts)
    if topic.endswith("/event/stats"):
        return _gateway_stats(topic, payload, ts)
    return []


def _loads(payload: bytes) -> dict | None:
    try:
        msg = json.loads(payload.decode("utf-8"))
    except Exception:
        return None
    return msg if isinstance(msg, dict) else None


def _application_up(payload: bytes, ts: float) -> list[dict]:
    msg = _loads(payload)
    if not msg:
        return []
    info: dict[str, Any] = msg.get("deviceInfo") or {}
    node_id = info.get("deviceName") or info.get("devEui") or "lorawan"
    tags = {
        "node_id": str(node_id),
        "model": "LoRa-E5",
        "profile": "unknown",
        "transport": "lorawan",
        "dev_eui": str(info.get("devEui") or ""),
        "f_port": str(msg.get("fPort") or ""),
    }
    raw = _b64(msg.get("data"))
    decoded = decode_payload(raw) if raw else None
    if not decoded:
        tags["payload_hex"] = raw.hex() if raw else ""
        return [
            {
                "metric": "node.uplink",
                "ts": ts,
                "value": 1.0,
                "tags": tags,
            }
        ]
    tags["profile"] = decoded["profile"]
    points = _points_for_sensors(decoded, ts, tags)
    points.append({"metric": "node.uplink", "ts": ts, "value": 1.0, "tags": tags})
    return points


def _gateway_up(topic: str, payload: bytes, ts: float) -> list[dict]:
    gateway_id = _gateway_id(topic)
    msg = _loads(payload)
    tags = {"gateway_id": gateway_id, "transport": "lorawan"}
    rssi = None
    if msg:
        rx = msg.get("rxInfo") or {}
        if isinstance(rx, list) and rx:
            rx = rx[0]
        if isinstance(rx, dict):
            rssi = rx.get("rssi")
            if rx.get("snr") is not None:
                tags["snr"] = str(rx.get("snr"))
    else:
        tags["encoding"] = "binary"
    points = [{"metric": "gateway.rx", "ts": ts, "value": 1.0, "tags": tags}]
    if rssi is not None:
        points.append(
            {
                "metric": "gateway.rssi",
                "ts": ts,
                "value": float(rssi),
                "tags": tags,
            }
        )
    return points


def location_from_mqtt(topic: str, payload: bytes, now: float | None = None) -> dict | None:
    """Pull a gateway GPS fix out of a ChirpStack uplink or stats event.

    A latitude and longitude of exactly 0 is the packet forwarder's empty
    reference, not a fix, so it is ignored. Gateway Bridge v4 publishes
    protobuf, so a stats payload that is not JSON is read as GatewayStats.
    """
    msg = _loads(payload)
    if msg:
        lat, lon, alt, accuracy, fix_source = _location_fields(msg)
    else:
        lat, lon, alt, accuracy, fix_source = _protobuf_location(topic, payload)
    ts = now if now is not None else time.time()
    if lat is None or lon is None:
        return None
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return None
    if lat_f == 0.0 and lon_f == 0.0:
        return None
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
        return None
    fix = {
        "ts": ts,
        "source": _gateway_id(topic) or "gateway",
        "latitude": lat_f,
        "longitude": lon_f,
        "fix_source": str(fix_source or "GPS"),
    }
    for key, raw in (("altitude_m", alt), ("accuracy_m", accuracy)):
        if raw is None or raw == "":
            continue
        try:
            fix[key] = float(raw)
        except (TypeError, ValueError):
            continue
    return fix


def _location_fields(msg: dict) -> tuple[Any, Any, Any, Any, Any]:
    loc = msg.get("location")
    if isinstance(loc, dict) and loc.get("latitude") is not None:
        return (
            loc.get("latitude"),
            loc.get("longitude"),
            loc.get("altitude"),
            loc.get("accuracy"),
            loc.get("source"),
        )
    rx = msg.get("rxInfo")
    if isinstance(rx, list):
        for item in rx:
            if not isinstance(item, dict):
                continue
            nested = item.get("location")
            if isinstance(nested, dict) and nested.get("latitude") is not None:
                return (
                    nested.get("latitude"),
                    nested.get("longitude"),
                    nested.get("altitude"),
                    nested.get("accuracy"),
                    nested.get("source"),
                )
    if msg.get("latitude") is not None or msg.get("lati") is not None:
        return (
            msg.get("latitude", msg.get("lati")),
            msg.get("longitude", msg.get("long")),
            msg.get("altitude", msg.get("alti")),
            None,
            "GPS",
        )
    return None, None, None, None, None


# common.Location.source in the ChirpStack gateway protobuf.
_LOCATION_SOURCE = {
    0: "UNKNOWN",
    1: "GPS",
    2: "CONFIG",
    3: "GEO_RESOLVER_TDOA",
    4: "GEO_RESOLVER_RSSI",
    5: "GEO_RESOLVER_GNSS",
    6: "GEO_RESOLVER_WIFI",
}


def _protobuf_location(topic: str, payload: bytes) -> tuple[Any, Any, Any, Any, Any]:
    """Location from a ChirpStack v4 gateway protobuf event.

    Stats carry common.Location at GatewayStats.location (field 3).
    An uplink carries it at UplinkFrame.rx_info.location (fields 5, then 12).
    """
    try:
        if topic.endswith("/event/stats"):
            for field, val in _pb_fields(payload):
                if field == 3 and isinstance(val, (bytes, bytearray)):
                    return _location_message(val)
        elif topic.endswith("/event/up") and "/device/" not in topic:
            for field, val in _pb_fields(payload):
                if field == 5 and isinstance(val, (bytes, bytearray)):
                    for inner, nested in _pb_fields(val):
                        if inner == 12 and isinstance(nested, (bytes, bytearray)):
                            return _location_message(nested)
    except (ValueError, struct.error):
        return None, None, None, None, None
    return None, None, None, None, None


def _location_message(raw: bytes) -> tuple[Any, Any, Any, Any, Any]:
    lat = lon = alt = accuracy = None
    source = None
    for field, val in _pb_fields(raw):
        if field == 1 and isinstance(val, float):
            lat = val
        elif field == 2 and isinstance(val, float):
            lon = val
        elif field == 3 and isinstance(val, float):
            alt = val
        elif field == 4 and isinstance(val, int):
            source = _LOCATION_SOURCE.get(val, "GPS")
        elif field == 5 and isinstance(val, float):
            accuracy = val
    return lat, lon, alt, accuracy, source


def _pb_fields(buf: bytes):
    i = 0
    view = bytes(buf)
    while i < len(view):
        key, i = _pb_varint(view, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _pb_varint(view, i)
            yield field, val
        elif wire == 1:
            if i + 8 > len(view):
                return
            yield field, struct.unpack_from("<d", view, i)[0]
            i += 8
        elif wire == 5:
            if i + 4 > len(view):
                return
            yield field, struct.unpack_from("<f", view, i)[0]
            i += 4
        elif wire == 2:
            length, i = _pb_varint(view, i)
            if length < 0 or i + length > len(view):
                return
            yield field, view[i : i + length]
            i += length
        else:
            return


def _pb_varint(buf: bytes, i: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while i < len(buf) and shift <= 63:
        byte = buf[i]
        i += 1
        val |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return val, i
        shift += 7
    raise ValueError("truncated protobuf varint")


def _gateway_stats(topic: str, payload: bytes, ts: float) -> list[dict]:
    msg = _loads(payload)
    if not msg:
        return []
    tags = {"gateway_id": _gateway_id(topic)}
    rx = msg.get("rxPacketsReceived")
    if rx is None:
        stats = msg.get("stats") or {}
        rx = stats.get("rxPacketsReceived") if isinstance(stats, dict) else None
    if rx is None:
        return []
    return [{"metric": "gateway.rx_total", "ts": ts, "value": float(rx), "tags": tags}]


def _gateway_id(topic: str) -> str:
    parts = topic.split("/")
    if "gateway" in parts:
        i = parts.index("gateway")
        if i + 1 < len(parts):
            return parts[i + 1]
    return ""


def _b64(value: Any) -> bytes | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return base64.b64decode(value, validate=False)
    except Exception:
        return None
