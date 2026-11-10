"""Read Tboard_tegra, the NCT72 sensor on the TK1 module.

Python 2.7. Furthest from the CPU of the onboard zones and the closest
proxy to room air on this board (still on the module, not a room sensor).
"""

from __future__ import print_function

import os


def read_zone_c(wanted="Tboard_tegra"):
    root = "/sys/class/thermal"
    try:
        names = os.listdir(root)
    except OSError:
        return None
    for name in names:
        zone = os.path.join(root, name)
        try:
            kind = open(os.path.join(zone, "type")).read().strip()
        except IOError:
            continue
        if kind != wanted:
            continue
        try:
            milli = int(open(os.path.join(zone, "temp")).read().strip())
        except (IOError, ValueError):
            return None
        return milli / 1000.0
    return None


def read_tboard_c():
    return read_zone_c("Tboard_tegra")


def sample():
    board = read_tboard_c()
    cpu = read_zone_c("CPU-therm")
    return {
        "ok": board is not None,
        "sensor": "Tboard_tegra",
        "tboard_c": board,
        "tcpu_c": cpu,
    }
