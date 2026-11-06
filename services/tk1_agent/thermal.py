"""Read Tboard_tegra, the NCT72 sensor on the TK1 module.

Python 2.7. This is the onboard reading nearest room air. It is still on
the module, not a room sensor.
"""

from __future__ import print_function

import os


def read_tboard_c():
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
        if kind != "Tboard_tegra":
            continue
        try:
            milli = int(open(os.path.join(zone, "temp")).read().strip())
        except (IOError, ValueError):
            return None
        return milli / 1000.0
    return None
