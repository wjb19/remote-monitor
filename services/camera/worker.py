"""Per-camera capture worker."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from .illuminator import IlluminatorController
from .ring_buffer import RingBuffer

log = logging.getLogger("rmlab.camera")


@dataclass
class CameraConfig:
    id: str
    device: str = "/dev/video0"
    enabled: bool = True
    name: str = ""
    width: int = 640
    height: int = 480
    fps: int = 10
    jpeg_quality: int = 80
    buffer_seconds: int = 300
    max_gb: float = 2.0
    # write every Nth captured frame into the ring (1 = all)
    ring_stride: int = 1

    def display_name(self) -> str:
        return self.name or self.id


@dataclass
class CameraState:
    config: CameraConfig
    running: bool = False
    last_error: str | None = None
    frames_captured: int = 0
    last_ts: float | None = None
    opened_device: str | None = None


class CameraWorker:
    def __init__(self, config: CameraConfig, data_root: Path, on_motion: Callable | None = None):
        self.config = config
        self.data_root = Path(data_root)
        self.on_motion = on_motion
        self.ring = RingBuffer(
            self.data_root / "cameras" / config.id / "ring",
            buffer_seconds=config.buffer_seconds,
            max_gb=config.max_gb,
        )
        self.illuminator = IlluminatorController(config.id)
        self.state = CameraState(config=config)
        self._latest_jpeg: bytes | None = None
        self._latest_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cfg_lock = threading.Lock()
        self._detector_name: str | None = None
        self._detector_error: str | None = None
        self._last_objects: list = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"cam-{self.config.id}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self.state.running = False

    def update_config(self, **kwargs: Any) -> CameraConfig:
        with self._cfg_lock:
            for k, v in kwargs.items():
                if hasattr(self.config, k) and v is not None:
                    setattr(self.config, k, v)
            self.ring.configure(
                buffer_seconds=self.config.buffer_seconds, max_gb=self.config.max_gb
            )
            self.state.config = self.config
            return self.config

    def latest_jpeg(self) -> bytes | None:
        with self._latest_lock:
            return self._latest_jpeg

    def status(self) -> dict:
        cfg = asdict(self.config)
        return {
            "config": cfg,
            "name": self.config.display_name(),
            "running": self.state.running,
            "last_error": self.state.last_error,
            "frames_captured": self.state.frames_captured,
            "last_ts": self.state.last_ts,
            "opened_device": self.state.opened_device,
            "has_preview": self._latest_jpeg is not None,
            "buffer": self.ring.stats(),
            "illuminator": self.illuminator.status(),
            "detector": self._detector_name,
            "detector_error": self._detector_error,
            "last_objects": self._last_objects,
        }

    def _open_capture(self) -> cv2.VideoCapture | None:
        device = self.config.device
        # OpenCV accepts index or path
        src: Any
        if device.startswith("/dev/video"):
            try:
                src = int(device.replace("/dev/video", ""))
            except ValueError:
                src = device
        else:
            src = device
        cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        cap.set(cv2.CAP_PROP_FPS, self.config.fps)
        return cap

    def _keep_frame(self, frame: np.ndarray, ts: float, write_ring: bool) -> None:
        jpeg = self._encode(frame)
        if jpeg is None:
            self.state.last_error = "jpeg encode failed"
            return
        with self._latest_lock:
            self._latest_jpeg = jpeg
        self.state.frames_captured += 1
        self.state.last_ts = ts
        if write_ring:
            self.ring.write(jpeg, ts=ts)

    def _emit_motion(self, window, trigger_ts: float, score: float, pre_s: float, post_s: float) -> None:
        if not window or self.on_motion is None:
            return
        from cv.detect import annotate_window

        frames = [frame for _ts, frame in window]
        best = 0
        best_dt = None
        for i, (ts, _frame) in enumerate(window):
            dt = abs(ts - trigger_ts)
            if best_dt is None or dt < best_dt:
                best = i
                best_dt = dt
        stills = []
        try:
            objects, info = annotate_window(frames, best)
            self._detector_name = info.get("name")
            self._detector_error = info.get("error")
            self._last_objects = objects
            stills = info.get("stills") or []
        except Exception as exc:
            objects = []
            self.state.last_error = "detect failed: %s" % exc
        camera_id = "hub-%s" % self.config.id
        window_id = "%.6f-%s" % (trigger_ts, camera_id)
        encoded = []
        for ts, frame in window:
            jpeg = self._encode(frame)
            if jpeg:
                encoded.append((ts, jpeg))
        for index, (ts, jpeg) in enumerate(encoded):
            meta = {
                "ts": ts,
                "score": round(score, 2),
                "device": self.state.opened_device or self.config.device,
                "camera_id": camera_id,
                "kind": "motion",
                "window_id": window_id,
                "frame_index": index,
                "frame_count": len(encoded),
                "offset_s": round(ts - trigger_ts, 3),
                "pre_s": pre_s,
                "post_s": post_s,
                "objects": objects,
                "detector": self._detector_name,
            }
            try:
                self.on_motion(jpeg, meta)
            except Exception as exc:
                self.state.last_error = "motion save failed: %s" % exc
        for n, still in enumerate(stills):
            fi = still.get("frame_index") or 0
            still_ts = window[fi][0] if 0 <= fi < len(window) else trigger_ts
            still_meta = {
                "ts": still_ts,
                "score": round(score, 2),
                "device": self.state.opened_device or self.config.device,
                "camera_id": camera_id,
                "kind": "object_still",
                "window_id": window_id,
                "frame_index": 100 + n,
                "objects": still.get("objects") or [],
                "aspect": still.get("aspect") or "16:9",
                "label": still.get("label"),
                "detector": self._detector_name,
            }
            try:
                self.on_motion(still["jpeg"], still_meta)
            except Exception as exc:
                self.state.last_error = "still save failed: %s" % exc

    def _encode(self, frame: np.ndarray) -> bytes | None:
        q = int(self.config.jpeg_quality)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), q])
        if not ok:
            return None
        return buf.tobytes()

    def _run(self) -> None:
        log.info("camera worker starting id=%s device=%s", self.config.id, self.config.device)
        while not self._stop.is_set():
            if not self.config.enabled:
                self.state.running = False
                self.state.last_error = "disabled"
                self._stop.wait(1.0)
                continue

            cap = self._open_capture()
            if cap is None:
                self.state.running = False
                self.state.last_error = f"cannot open {self.config.device}"
                log.warning("%s", self.state.last_error)
                self._stop.wait(2.0)
                continue

            self.state.running = True
            self.state.last_error = None
            self.state.opened_device = self.config.device
            period = 1.0 / max(1, int(self.config.fps))
            stride_i = 0
            pre_s = 2.0
            post_s = 4.0
            threshold = 12.0
            cooldown_s = 20.0
            buf: deque = deque()
            prev_gray = None
            last_fire = 0.0

            try:
                while not self._stop.is_set() and self.config.enabled:
                    t0 = time.time()
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        self.state.last_error = "frame read failed"
                        break
                    stride_i += 1
                    write_ring = stride_i >= max(1, int(self.config.ring_stride))
                    if write_ring:
                        stride_i = 0
                    self._keep_frame(frame, t0, write_ring)
                    want = max(1, int(round(pre_s * max(1, int(self.config.fps)))))
                    buf.append((t0, frame.copy()))
                    while len(buf) > want:
                        buf.popleft()
                    gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (21, 21), 0)
                    motion = False
                    score = 0.0
                    if prev_gray is not None and prev_gray.shape == gray.shape:
                        delta = cv2.absdiff(prev_gray, gray)
                        _, th = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)
                        th = cv2.dilate(th, None, iterations=2)
                        nonzero = cv2.countNonZero(th)
                        area = float(th.shape[0] * th.shape[1]) or 1.0
                        score = 100.0 * nonzero / area
                        motion = score >= threshold and (nonzero / area) >= 0.01
                    prev_gray = gray
                    if self.on_motion is not None and motion and (t0 - last_fire) >= cooldown_s:
                        window = list(buf)
                        deadline = time.time() + post_s
                        while not self._stop.is_set() and time.time() < deadline:
                            ok2, frame2 = cap.read()
                            if not ok2 or frame2 is None:
                                break
                            t2 = time.time()
                            stride_i += 1
                            write_ring = stride_i >= max(1, int(self.config.ring_stride))
                            if write_ring:
                                stride_i = 0
                            self._keep_frame(frame2, t2, write_ring)
                            window.append((t2, frame2.copy()))
                            prev_gray = cv2.GaussianBlur(
                                cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY), (21, 21), 0
                            )
                            self._stop.wait(period)
                        self._emit_motion(window, t0, score, pre_s, post_s)
                        last_fire = t0
                        buf.clear()
                    elapsed = time.time() - t0
                    delay = period - elapsed
                    if delay > 0:
                        self._stop.wait(delay)
            finally:
                cap.release()
                self.state.running = False

            if not self._stop.is_set():
                self._stop.wait(1.0)

        log.info("camera worker stopped id=%s", self.config.id)
