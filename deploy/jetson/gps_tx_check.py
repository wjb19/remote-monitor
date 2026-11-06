#!/usr/bin/env python3
"""Report L76K NMEA activity on /dev/ttyTHS1 and pin-8 drive state.

GPIO edge counting on pin 10 is unreliable while the pad is muxed as UART;
read the serial port instead.
"""

from __future__ import print_function

import mmap
import os
import struct
import sys
import termios
import time

PINMUX = 0x70003000
GPIO = 0x6000D000


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 1.5
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    try:
        pin = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ, offset=PINMUX)
        gpio = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ, offset=GPIO)
        try:
            p8 = struct.unpack_from("<I", pin, 0xF4)[0]
            p10 = struct.unpack_from("<I", pin, 0xF8)[0]
            cnf = struct.unpack_from("<I", gpio, 0x108)[0] & 1
            oe = struct.unpack_from("<I", gpio, 0x118)[0] & 1
            out = struct.unpack_from("<I", gpio, 0x128)[0] & 1
        finally:
            pin.close()
            gpio.close()
    finally:
        os.close(fd)

    tty = os.open("/dev/ttyTHS1", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(tty)
        attrs[0] = 0
        attrs[1] = 0
        attrs[3] = 0
        attrs[4] = termios.B9600
        attrs[5] = termios.B9600
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        termios.tcsetattr(tty, termios.TCSANOW, attrs)
        termios.tcflush(tty, termios.TCIOFLUSH)
        buf = b""
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            try:
                chunk = os.read(tty, 512)
                if chunk:
                    buf += chunk
            except OSError:
                time.sleep(0.05)
    finally:
        os.close(tty)

    nmea = buf.count(b"$G")
    sample = ""
    for line in buf.decode("ascii", errors="ignore").splitlines():
        if line.startswith("$G") and ("GGA" in line or "RMC" in line):
            sample = line[:100]
            break
    print(
        "pin8=0x%02x pin10=0x%02x gpio48_cnf=%s oe=%s out=%s bytes=%.0f nmea=%s"
        % (p8, p10, cnf, oe, out, len(buf), nmea)
    )
    if sample:
        print(sample)
    return 0 if nmea >= 1 else 1


if __name__ == "__main__":
    sys.exit(main())
