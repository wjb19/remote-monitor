"""USB illuminator / IR LED control stub.

Replace the body of IlluminatorController.set() when real USB circuitry arrives
(serial, HID, or GPIO bridge).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("rmlab.illuminator")


@dataclass
class IlluminatorController:
    camera_id: str
    on: bool = False
    backend: str = "stub"
    last_error: str | None = None
    notes: str = field(
        default="Stub only — wire USB IR / LED control here when hardware is ready."
    )

    def set(self, on: bool) -> dict:
        self.on = bool(on)
        self.last_error = None
        log.info("illuminator %s -> %s (backend=%s)", self.camera_id, self.on, self.backend)
        # Future: open serial/HID device and toggle IR LEDs.
        return self.status()

    def status(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "on": self.on,
            "backend": self.backend,
            "last_error": self.last_error,
            "notes": self.notes,
        }
