"""Read Jetson Nano INA3221 supply rails (POM_5V_IN etc.)."""

import glob
import os
import subprocess

IIO_GLOB = "/sys/bus/i2c/drivers/ina3221x/*/iio:device*"


def _read_text(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        try:
            out = subprocess.check_output(["sudo", "-n", "cat", path], stderr=subprocess.STDOUT)
            return out.decode("utf-8", "replace").strip()
        except Exception:
            return None


def find_iio_device():
    paths = sorted(glob.glob(IIO_GLOB))
    return paths[0] if paths else None


def sample_rails():
    """Return dict of rail metrics (V, A, W)."""
    base = find_iio_device()
    if not base:
        return {"ok": False, "error": "ina3221 iio device not found", "rails": {}}

    rails = {}
    for i in range(3):
        name = _read_text(os.path.join(base, "rail_name_%d" % i)) or ("rail_%d" % i)
        v_raw = _read_text(os.path.join(base, "in_voltage%d_input" % i))
        i_raw = _read_text(os.path.join(base, "in_current%d_input" % i))
        p_raw = _read_text(os.path.join(base, "in_power%d_input" % i))
        if v_raw is None:
            continue
        try:
            voltage_v = float(v_raw) / 1000.0
        except ValueError:
            continue
        current_a = None
        power_w = None
        try:
            if i_raw is not None:
                current_a = float(i_raw) / 1000.0
            if p_raw is not None:
                power_w = float(p_raw) / 1000.0
        except ValueError:
            pass
        rails[name] = {
            "index": i,
            "voltage_v": voltage_v,
            "current_a": current_a,
            "power_w": power_w,
        }

    vin = rails.get("POM_5V_IN")
    if vin is None and rails:
        vin = list(rails.values())[0]
    return {
        "ok": bool(rails),
        "iio_path": base,
        "rails": rails,
        "voltage_in_v": None if vin is None else vin["voltage_v"],
    }
