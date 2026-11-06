"""Subscribe to ChirpStack MQTT and store node / gateway telemetry."""

from __future__ import annotations

import asyncio
import logging
import struct

from core.lora_ingest import location_from_mqtt, points_from_mqtt

log = logging.getLogger("rmlab.mqtt")

TOPICS = (
    "application/+/device/+/event/up",
    "us915_0/gateway/+/event/up",
    "us915_0/gateway/+/event/stats",
)


def _enc_len(n: int) -> bytes:
    out = bytearray()
    while True:
        digit = n % 128
        n //= 128
        if n:
            digit |= 0x80
        out.append(digit)
        if not n:
            return bytes(out)


def _enc_str(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("!H", len(raw)) + raw


def _connect_packet(client_id: str) -> bytes:
    body = _enc_str("MQTT") + bytes([4, 0x02, 0x00, 60]) + _enc_str(client_id)
    return bytes([0x10]) + _enc_len(len(body)) + body


def _subscribe_packet(topics: tuple[str, ...]) -> bytes:
    body = struct.pack("!H", 1)
    for topic in topics:
        body += _enc_str(topic) + bytes([0])
    return bytes([0x82]) + _enc_len(len(body)) + body


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    first = await reader.readexactly(1)
    value = 0
    mult = 1
    for _ in range(4):
        digit = (await reader.readexactly(1))[0]
        value += (digit & 127) * mult
        if digit & 128 == 0:
            break
        mult *= 128
    else:
        raise RuntimeError("mqtt remaining length too long")
    body = await reader.readexactly(value) if value else b""
    return first[0], body


def _publish_fields(body: bytes) -> tuple[str, bytes]:
    topic_len = struct.unpack("!H", body[:2])[0]
    topic = body[2 : 2 + topic_len].decode("utf-8", "replace")
    return topic, body[2 + topic_len :]


async def run_mqtt_ingest(telemetry, host: str, port: int) -> None:
    while True:
        writer = None
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.write(_connect_packet("rmlab-core"))
            await writer.drain()
            ptype, body = await _read_packet(reader)
            if ptype != 0x20 or len(body) < 2 or body[1] != 0:
                raise RuntimeError("mqtt connect rejected: %s %r" % (ptype, body))
            writer.write(_subscribe_packet(TOPICS))
            await writer.drain()
            log.info("mqtt subscribed %s:%s", host, port)
            while True:
                try:
                    ptype, body = await asyncio.wait_for(_read_packet(reader), timeout=20)
                except asyncio.TimeoutError:
                    writer.write(b"\xc0\x00")
                    await writer.drain()
                    continue
                if ptype & 0xF0 != 0x30 or len(body) < 2:
                    continue
                topic, payload = _publish_fields(body)
                try:
                    fix = location_from_mqtt(topic, payload)
                    if fix:
                        telemetry.record_location(fix)
                    else:
                        fix = telemetry.latest_location()
                    points = points_from_mqtt(topic, payload)
                    if fix:
                        for point in points:
                            metric = str(point.get("metric") or "")
                            if metric.startswith("node.") or metric.startswith("gateway."):
                                tags = dict(point.get("tags") or {})
                                tags["location"] = fix
                                point["tags"] = tags
                    if points:
                        telemetry.insert_points(points)
                        log.info("ingested %s points from %s", len(points), topic)
                except Exception:
                    log.exception("uplink ingest failed for %s", topic)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("mqtt ingest disconnected: %s", exc)
            await asyncio.sleep(5)
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
