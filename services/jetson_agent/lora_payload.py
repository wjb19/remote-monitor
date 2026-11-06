"""Compact sensor payload shared by USB LoRa-E5 nodes and LoRaWAN uplinks.

temp_v1 (profile 1), 5 bytes:
    u8  1
    i16 temperature_c * 10, little-endian
    u16 vdd_mV, little-endian

temp_humi_v1 (profile 2), 7 bytes: temp_v1 plus
    u16 humidity_pct * 10, little-endian
"""

import binascii
import struct

PROFILE_TEMP_V1 = 1
PROFILE_TEMP_HUMI_V1 = 2


def _clamp(value, lo, hi):
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def encode_sensors(temp_c, vdd_v=None, humidity_pct=None):
    temp_x10 = _clamp(int(round(float(temp_c) * 10.0)), -32768, 32767)
    vdd_mv = 0 if vdd_v is None else _clamp(int(round(float(vdd_v) * 1000.0)), 0, 65535)
    if humidity_pct is None:
        raw = struct.pack("<BhH", PROFILE_TEMP_V1, temp_x10, vdd_mv)
    else:
        hum = _clamp(int(round(float(humidity_pct) * 10.0)), 0, 65535)
        raw = struct.pack("<BhHH", PROFILE_TEMP_HUMI_V1, temp_x10, vdd_mv, hum)
    return raw


def encode_hex(temp_c, vdd_v=None, humidity_pct=None):
    return binascii.hexlify(encode_sensors(temp_c, vdd_v, humidity_pct)).decode("ascii")


def decode_payload(buf):
    if not buf:
        return None
    profile = buf[0]
    if profile == PROFILE_TEMP_V1 and len(buf) >= 5:
        _profile, temp_x10, vdd_mv = struct.unpack_from("<BhH", buf)
        return {
            "profile": "temp_v1",
            "temperature_c": temp_x10 / 10.0,
            "vdd_v": vdd_mv / 1000.0,
        }
    if profile == PROFILE_TEMP_HUMI_V1 and len(buf) >= 7:
        _profile, temp_x10, vdd_mv, hum = struct.unpack_from("<BhHH", buf)
        return {
            "profile": "temp_humi_v1",
            "temperature_c": temp_x10 / 10.0,
            "vdd_v": vdd_mv / 1000.0,
            "humidity_pct": hum / 10.0,
        }
    return None
