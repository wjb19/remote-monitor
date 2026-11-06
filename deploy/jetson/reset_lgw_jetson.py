#!/usr/bin/env python3
"""Reset Waveshare SX1302 HAT using Jetson.GPIO BOARD pin numbers."""

from __future__ import print_function

import os
import sys
import time

try:
    import Jetson.GPIO as GPIO
except Exception as exc:
    sys.stderr.write("Jetson.GPIO required: %s\n" % exc)
    sys.exit(1)

# Physical 40-pin header positions matching Raspberry Pi BCM wiring on the HAT
POWER_EN = 12  # BCM18 -> sysfs 79
RESET = 16  # BCM23 -> sysfs 232
SX1261 = 15  # BCM22 -> sysfs 194
ADC = 33  # BCM13 -> sysfs 38

# Header SPI is the SPI1 controller at 0x7000d400 (/dev/spidev0.0).
# The boot device tree also claims PC0-PC4 as GPIO inputs, which disconnects
# those pads from the SPI controller. Pinmux and this GPIO config are the
# safe registers; the SPI block itself is clock-gated and must not be mapped.
_PINMUX = (
    (0x50, 0xE000),  # MOSI PC0
    (0x54, 0xE040),  # MISO PC1, input enabled
    (0x58, 0xE000),  # SCK PC2
    (0x5C, 0xE008),  # CS0 PC3, pull-up
)

# L76K RX is header pin 8 (uart2_tx_pg0). Tegra reset pulls that pad low and
# the module latches quiet; POWER_EN does not cut GPS 3V3. Leave the pad
# undriven so a HAT 5 V cycle (or removing R10) can bring NMEA back, and so
# opening ttyTHS1 does not re-drive a break onto Q_RX.
_GPS_RX_OFF = 0xF4
_GPS_RX_RELEASE = 0x10  # tristate, no pull, input buffer off


def release_header_spi():
    import mmap
    import struct

    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    try:
        pin = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x70003000)
        gpio = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x6000D000)
        try:
            cnf = struct.unpack_from("<I", gpio, 0x008)[0]
            struct.pack_into("<I", gpio, 0x008, cnf & ~0x1F)
            for off, val in _PINMUX:
                struct.pack_into("<I", pin, off, val)
        finally:
            pin.close()
            gpio.close()
    finally:
        os.close(fd)


def release_gps_rx():
    """Tristate header pin 8 and drop the port-G GPIO claim on bit 0."""
    import mmap
    import struct

    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    try:
        pin = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x70003000)
        gpio = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0x6000D000)
        try:
            struct.pack_into("<I", pin, _GPS_RX_OFF, _GPS_RX_RELEASE)
            for reg in (0x128, 0x118, 0x108):
                struct.pack_into("<I", gpio, reg + 0x80, 0x100)
        finally:
            pin.close()
            gpio.close()
    finally:
        os.close(fd)


def gps_nmea_bytes(seconds=1.2):
    """Read /dev/ttyTHS1 briefly; GPIO IN cannot see UART-muxed pin 10."""
    import termios

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
        return buf
    finally:
        os.close(tty)


def start():
    release_header_spi()
    release_gps_rx()
    raw = gps_nmea_bytes(1.2)
    nmea = raw.count(b"$G")
    if nmea < 1:
        print(
            "GPS TX quiet after pin-8 release (%s bytes, no NMEA). "
            "POWER_EN does not power-cycle the L76K; remove R10 or cycle HAT 5 V "
            "with pin 8 left undriven." % len(raw)
        )
    else:
        print(
            "GPS TX alive (%s NMEA sentences in 1.2s, %s bytes) with pin 8 released"
            % (nmea, len(raw))
        )
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD)
    for p in (POWER_EN, RESET, SX1261, ADC):
        GPIO.setup(p, GPIO.OUT, initial=GPIO.HIGH)
    GPIO.output(POWER_EN, GPIO.HIGH)
    time.sleep(0.1)
    GPIO.output(RESET, GPIO.HIGH)
    time.sleep(0.1)
    GPIO.output(RESET, GPIO.LOW)
    time.sleep(0.1)
    GPIO.output(SX1261, GPIO.LOW)
    time.sleep(0.1)
    GPIO.output(SX1261, GPIO.HIGH)
    time.sleep(0.1)
    GPIO.output(ADC, GPIO.LOW)
    time.sleep(0.1)
    GPIO.output(ADC, GPIO.HIGH)
    time.sleep(0.1)
    # Re-assert release: Jetson.GPIO must not touch pins 8/10, but keep the
    # pad undriven before lora_pkt_fwd opens ttyTHS1.
    release_gps_rx()
    print(
        "CoreCell reset through BOARD %s (gpio 232); power_en BOARD %s (gpio 79)"
        % (RESET, POWER_EN)
    )


def stop():
    # leave power enabled; just release GPIO ownership
    try:
        GPIO.cleanup()
    except Exception:
        pass
    print("GPIO cleanup done")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    else:
        sys.stderr.write("usage: %s start|stop\n" % sys.argv[0])
        sys.exit(2)
