#!/usr/bin/env python3
"""Leave the L76K receive line undriven.

Header pin 8 is the GPS RX pin. The Tegra UART output rests low, and the boot
pinmux enables that driver, so the module sees a break and stops sending.
Release the pad and drop the GPIO claim. Pin 10 stays the UART input.

Pinmux 0x10 matches the recovery that produced NMEA: tristate, no pull,
input buffer off. Do not drive the pad high across a HAT 5 V cycle — that
back-powers the module through the RX ESD diode and blocks a clean restart.
"""

import mmap
import os
import struct

PINMUX = 0x70003000
GPIO = 0x6000D000
TX_OFF = 0xF4  # uart2_tx_pg0, header pin 8
TX_RELEASE = 0x10


def _w(buf, off, val):
    struct.pack_into("<I", buf, off, val)


def main():
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    try:
        pin = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=PINMUX)
        gpio = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=GPIO)
        try:
            _w(pin, TX_OFF, TX_RELEASE)
            # Masked port-G bit 0: mask set, value clear (OUT, OE, CNF).
            for reg in (0x128, 0x118, 0x108):
                _w(gpio, reg + 0x80, 0x100)
        finally:
            pin.close()
            gpio.close()
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
