"""Read the TK1's supply current from an INA3221 on GEN2 I2C.

The 12 V barrel jack is the channel whose bus voltage sits near 12 V.
Current uses a 10 milliohm shunt, which is the usual value on this board.
Override with RMLAB_SHUNT_OHM if a measured reading disagrees.
"""

from __future__ import print_function

import fcntl
import glob
import os
import struct

I2C_SLAVE = 0x0703
MANUFACTURER_ID = 0x5449
ADDRESSES = (0x40, 0x41, 0x42, 0x43)
# (shunt register, bus register) for the three channels.
CHANNELS = ((0x01, 0x02), (0x03, 0x04), (0x05, 0x06))


def _shunt_ohm():
    try:
        return float(os.environ.get("RMLAB_SHUNT_OHM", "0.01"))
    except (TypeError, ValueError):
        return 0.01


def _signed(raw):
    if raw & 0x8000:
        raw -= 0x10000
    return raw


def _read_reg(fd, reg):
    os.write(fd, chr(reg))
    data = os.read(fd, 2)
    if data is None or len(data) != 2:
        raise IOError("short i2c read")
    return struct.unpack(">H", data)[0]


def _iio_rails():
    paths = sorted(glob.glob("/sys/bus/i2c/drivers/ina3221x/*/iio:device*"))
    rails = []
    for base in paths:
        for index in range(3):
            try:
                name = open(os.path.join(base, "rail_name_%d" % index)).read().strip()
            except Exception:
                name = "rail_%d" % index
            try:
                mv = float(open(os.path.join(base, "in_voltage%d_input" % index)).read())
            except Exception:
                continue
            current = None
            try:
                current = float(open(os.path.join(base, "in_current%d_input" % index)).read()) / 1000.0
            except Exception:
                pass
            rails.append(
                {
                    "name": name or ("rail_%d" % index),
                    "voltage_v": mv / 1000.0,
                    "current_a": current,
                    "source": base,
                }
            )
    return rails


def _bus_number(path):
    tail = path.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else -1


def _addresses_for(path):
    # i2c-4 address 0x40 is the AS3722 PMIC. Leave it alone.
    if _bus_number(path) == 4:
        return (0x41, 0x42, 0x43)
    return ADDRESSES


def _i2c_rails(path):
    shunt = _shunt_ohm()
    rails = []
    fd = os.open(path, os.O_RDWR)
    try:
        for addr in _addresses_for(path):
            try:
                fcntl.ioctl(fd, I2C_SLAVE, addr)
                maker = _read_reg(fd, 0xFE)
            except Exception:
                continue
            if maker != MANUFACTURER_ID:
                continue
            for index, (shunt_reg, bus_reg) in enumerate(CHANNELS):
                try:
                    shunt_raw = _signed(_read_reg(fd, shunt_reg))
                    bus_raw = _signed(_read_reg(fd, bus_reg))
                except Exception:
                    continue
                shunt_v = ((shunt_raw >> 3) * 40) / 1e6
                voltage_v = ((bus_raw >> 3) * 8) / 1000.0
                current_a = None
                if shunt > 0:
                    current_a = shunt_v / shunt
                rails.append(
                    {
                        "name": "i2c%02x_ch%d" % (addr, index),
                        "addr": addr,
                        "channel": index,
                        "voltage_v": voltage_v,
                        "current_a": current_a,
                        "source": path,
                    }
                )
    finally:
        os.close(fd)
    return rails


def _pick_supply(rails):
    near = []
    for rail in rails:
        voltage = rail.get("voltage_v")
        if voltage is None:
            continue
        if 8.0 <= voltage <= 18.0:
            near.append(rail)
    if not near:
        return None
    near.sort(key=lambda rail: abs(rail["voltage_v"] - 12.0))
    return near[0]


def _i2c_devices():
    override = os.environ.get("RMLAB_POWER_I2C")
    if override:
        return [override]
    found = []
    for bus in range(6):
        path = "/dev/i2c-%d" % bus
        if os.path.exists(path):
            found.append(path)
    return found


def sample():
    shunt = _shunt_ohm()
    devices = _i2c_devices()
    error = None
    rails = _iio_rails()
    if not rails:
        readable = [d for d in devices if os.access(d, os.R_OK | os.W_OK)]
        if not readable:
            error = "no I2C bus is readable by this user"
        else:
            for device in readable:
                try:
                    rails.extend(_i2c_rails(device))
                except Exception as exc:
                    error = str(exc)
            if not rails and error is None:
                error = "no INA3221 answered on %s" % ", ".join(readable)
    supply = _pick_supply(rails)
    if supply is None:
        return {
            "ok": False,
            "error": error or "no 12 V supply channel",
            "rails": rails,
            "shunt_ohm": shunt,
        }
    current = supply.get("current_a")
    voltage = supply.get("voltage_v")
    power = None
    if current is not None and voltage is not None:
        power = current * voltage
    tags = {
        "host": os.uname()[1],
        "rail": supply.get("name"),
        "shunt_ohm": shunt,
    }
    if supply.get("addr") is not None:
        tags["addr"] = "0x%02x" % supply["addr"]
        tags["channel"] = supply.get("channel")
    return {
        "ok": current is not None,
        "error": None if current is not None else "supply channel has no current",
        "current_a": current,
        "voltage_v": voltage,
        "power_w": power,
        "rail": supply.get("name"),
        "shunt_ohm": shunt,
        "tags": tags,
        "rails": rails,
    }
