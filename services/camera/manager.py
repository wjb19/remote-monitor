"""Multi-camera manager with JSON config persistence."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from .worker import CameraConfig, CameraWorker

log = logging.getLogger("rmlab.camera")


def discover_devices(max_index: int = 8) -> list[str]:
    import cv2

    found: list[str] = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            continue
        ok, _ = cap.read()
        cap.release()
        if ok:
            found.append(f"/dev/video{i}")
    return found


class CameraManager:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_root / "cameras.json"
        self._workers: dict[str, CameraWorker] = {}
        self._lock = threading.Lock()
        self.on_motion = None

    def start(self) -> None:
        configs = self._load_or_init()
        with self._lock:
            for cfg in configs:
                w = CameraWorker(cfg, self.data_root, on_motion=self.on_motion)
                self._workers[cfg.id] = w
                if cfg.enabled:
                    w.start()
        log.info("camera manager started with %d camera(s)", len(self._workers))

    def stop(self) -> None:
        with self._lock:
            for w in self._workers.values():
                w.stop()
            self._workers.clear()

    def list(self) -> list[dict]:
        with self._lock:
            return [w.status() for w in self._workers.values()]

    def get(self, camera_id: str) -> CameraWorker | None:
        with self._lock:
            return self._workers.get(camera_id)

    def update(self, camera_id: str, patch: dict[str, Any]) -> dict:
        with self._lock:
            w = self._workers.get(camera_id)
            if not w:
                raise KeyError(camera_id)
            # device / enabled changes may need restart
            needs_restart = any(k in patch for k in ("device", "enabled", "width", "height", "fps"))
            was_enabled = w.config.enabled
            cfg = w.update_config(**{k: patch[k] for k in patch if hasattr(w.config, k)})
            if needs_restart:
                w.stop()
                if cfg.enabled:
                    w.start()
            elif cfg.enabled and not was_enabled:
                w.start()
            elif not cfg.enabled and was_enabled:
                w.stop()
            self._save_unlocked()
            return w.status()

    def set_illuminator(self, camera_id: str, on: bool) -> dict:
        w = self.get(camera_id)
        if not w:
            raise KeyError(camera_id)
        return w.illuminator.set(on)

    def _load_or_init(self) -> list[CameraConfig]:
        if self.config_path.is_file():
            raw = json.loads(self.config_path.read_text())
            return [CameraConfig(**item) for item in raw.get("cameras", [])]

        devices = discover_devices()
        if not devices:
            devices = ["/dev/video0"]
        configs = []
        for i, dev in enumerate(devices):
            cid = f"cam{i}"
            configs.append(
                CameraConfig(
                    id=cid,
                    device=dev,
                    name=f"Camera {i}",
                    enabled=True,
                    buffer_seconds=300,
                    max_gb=2.0,
                    fps=10,
                )
            )
        self.config_path.write_text(
            json.dumps({"cameras": [c.__dict__ for c in configs]}, indent=2) + "\n"
        )
        return configs

    def _save_unlocked(self) -> None:
        cameras = [w.config.__dict__ for w in self._workers.values()]
        self.config_path.write_text(json.dumps({"cameras": cameras}, indent=2) + "\n")
