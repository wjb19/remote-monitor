"""Minimal UART AT helper using only the Python stdlib (no pyserial)."""

import os
import select
import termios
import time


class SerialAT(object):
    def __init__(self, port, baud=9600, timeout=1.0):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.fd = None

    def open(self):
        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        # iflag, oflag, cflag, lflag, ispeed, ospeed, cc
        attrs[0] = 0
        attrs[1] = 0
        attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
        # baud
        speed = {
            9600: termios.B9600,
            19200: termios.B19200,
            38400: termios.B38400,
            57600: termios.B57600,
            115200: termios.B115200,
        }.get(self.baud, termios.B9600)
        attrs[4] = speed
        attrs[5] = speed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        os.write(self.fd, data)

    def read_until(self, deadline):
        chunks = []
        while time.time() < deadline:
            r, _, _ = select.select([self.fd], [], [], max(0.0, deadline - time.time()))
            if not r:
                break
            try:
                data = os.read(self.fd, 512)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
            # if we got a newline-terminated response, keep draining briefly
            joined = b"".join(chunks)
            if b"\n" in joined and (b"OK" in joined or b"ERROR" in joined or b"+" in joined):
                # short settle for multi-line replies
                time.sleep(0.05)
                while True:
                    r2, _, _ = select.select([self.fd], [], [], 0.05)
                    if not r2:
                        break
                    try:
                        more = os.read(self.fd, 512)
                    except OSError:
                        break
                    if not more:
                        break
                    chunks.append(more)
                break
        return b"".join(chunks)

    def command(self, cmd, wait=1.0):
        if not cmd.endswith("\n"):
            cmd = cmd + "\r\n"
        termios.tcflush(self.fd, termios.TCIFLUSH)
        self.write(cmd)
        raw = self.read_until(time.time() + wait)
        text = raw.decode("utf-8", "replace")
        return text
