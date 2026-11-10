"""Board temperature furthest from the CPU on the Jetson Nano.

AO-therm is the Always-On island sensor. It is the usual Tegra proxy for
board/ambient conditions (not a room thermometer, but away from CPU/GPU).
"""

from __future__ import print_function

import os


def read_zone_c(wanted="AO-therm"):
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


def read_board_c():
    return read_zone_c("AO-therm")


def sample():
    board = read_board_c()
    cpu = read_zone_c("CPU-therm")
    return {
        "ok": board is not None,
        "sensor": "AO-therm",
        "tboard_c": board,
        "tcpu_c": cpu,
    }
