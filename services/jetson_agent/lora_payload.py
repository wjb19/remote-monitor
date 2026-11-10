"""Compact sensor payload shared by USB LoRa-E5 nodes and LoRaWAN uplinks.

temp_v1 (profile 1), 5 bytes:
    u8  1
    i16 temperature_c * 10, little-endian
    u16 vdd_mV, little-endian

temp_humi_v1 (profile 2), 7 bytes: temp_v1 plus
    u16 humidity_pct * 10, little-endian

gas_ads1115_v1 (profile 3), 13 bytes:
    u8  3
    i16 temperature_c * 10, little-endian (0 if unused)
    u16 vdd_mV, little-endian
    u16 mq2_mV, mq8_mV, mq135_mV, mq5_mV  (ADS1115 A0..A3 single-ended)
"""

import binascii
import struct

PROFILE_TEMP_V1 = 1
PROFILE_TEMP_HUMI_V1 = 2
PROFILE_GAS_ADS1115_V1 = 3

# ADS1115 single-ended channel map on the LoRa-E5 I2C bus.
GAS_CHANNELS = (
    ("mq2_mv", "MQ-2", 0),
    ("mq8_mv", "MQ-8", 1),
    ("mq135_mv", "MQ-135", 2),
    ("mq5_mv", "MQ-5", 3),
)


def _clamp(value, lo, hi):
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _mv(value):
    if value is None:
        return 0
    return _clamp(int(round(float(value))), 0, 65535)


def encode_sensors(temp_c, vdd_v=None, humidity_pct=None, gas_mv=None):
    """Encode sensors. gas_mv is an optional dict with mq2_mv..mq5_mv."""
    temp_x10 = _clamp(int(round(float(temp_c) * 10.0)), -32768, 32767)
    vdd_mv = 0 if vdd_v is None else _clamp(int(round(float(vdd_v) * 1000.0)), 0, 65535)
    if gas_mv:
        raw = struct.pack(
            "<BhHHHHH",
            PROFILE_GAS_ADS1115_V1,
            temp_x10,
            vdd_mv,
            _mv(gas_mv.get("mq2_mv")),
            _mv(gas_mv.get("mq8_mv")),
            _mv(gas_mv.get("mq135_mv")),
            _mv(gas_mv.get("mq5_mv")),
        )
        return raw
    if humidity_pct is None:
        return struct.pack("<BhH", PROFILE_TEMP_V1, temp_x10, vdd_mv)
    hum = _clamp(int(round(float(humidity_pct) * 10.0)), 0, 65535)
    return struct.pack("<BhHH", PROFILE_TEMP_HUMI_V1, temp_x10, vdd_mv, hum)


def encode_hex(temp_c, vdd_v=None, humidity_pct=None, gas_mv=None):
    return binascii.hexlify(encode_sensors(temp_c, vdd_v, humidity_pct, gas_mv)).decode("ascii")


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
    if profile == PROFILE_GAS_ADS1115_V1 and len(buf) >= 13:
        _profile, temp_x10, vdd_mv, mq2, mq8, mq135, mq5 = struct.unpack_from("<BhHHHHH", buf)
        return {
            "profile": "gas_ads1115_v1",
            "temperature_c": temp_x10 / 10.0,
            "vdd_v": vdd_mv / 1000.0,
            "mq2_mv": float(mq2),
            "mq8_mv": float(mq8),
            "mq135_mv": float(mq135),
            "mq5_mv": float(mq5),
        }
    return None
