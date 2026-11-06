"""On-disk circular JPEG ring buffer for a camera."""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path


class RingBuffer:
    def __init__(self, root: Path, buffer_seconds: int = 300, max_gb: float = 2.0):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.buffer_seconds = max(10, int(buffer_seconds))
        self.max_bytes = int(max(0.05, float(max_gb)) * (1024**3))
        self._lock = threading.Lock()
        self._bytes = self._scan_bytes()

    def configure(self, buffer_seconds: int | None = None, max_gb: float | None = None) -> None:
        with self._lock:
            if buffer_seconds is not None:
                self.buffer_seconds = max(10, int(buffer_seconds))
            if max_gb is not None:
                self.max_bytes = int(max(0.05, float(max_gb)) * (1024**3))
            self._prune_unlocked()

    def _scan_bytes(self) -> int:
        total = 0
        for p in self.root.glob("*.jpg"):
            try:
                total += p.stat().st_size
            except OSError:
                pass
        return total

    def write(self, jpeg: bytes, ts: float | None = None) -> Path:
        ts = time.time() if ts is None else ts
        # microsecond resolution keeps names unique at 30fps
        name = f"{ts:.6f}.jpg"
        path = self.root / name
        with self._lock:
            path.write_bytes(jpeg)
            self._bytes += len(jpeg)
            self._prune_unlocked()
        return path

    def _prune_unlocked(self) -> None:
        files = sorted(self.root.glob("*.jpg"), key=lambda p: p.name)
        now = time.time()
        # age prune
        keep_after = now - self.buffer_seconds
        for p in list(files):
            try:
                ts = float(p.stem)
            except ValueError:
                continue
            if ts < keep_after:
                try:
                    size = p.stat().st_size
                    p.unlink(missing_ok=True)
                    self._bytes -= size
                    files.remove(p)
                except OSError:
                    pass
        # size prune (oldest first)
        while files and self._bytes > self.max_bytes:
            p = files.pop(0)
            try:
                size = p.stat().st_size
                p.unlink(missing_ok=True)
                self._bytes -= size
            except OSError:
                break
        if self._bytes < 0:
            self._bytes = self._scan_bytes()

    def stats(self) -> dict:
        with self._lock:
            files = sorted(self.root.glob("*.jpg"), key=lambda p: p.name)
            oldest = newest = None
            if files:
                try:
                    oldest = float(files[0].stem)
                    newest = float(files[-1].stem)
                except ValueError:
                    pass
            return {
                "path": str(self.root),
                "frame_count": len(files),
                "bytes": max(0, self._bytes),
                "max_bytes": self.max_bytes,
                "buffer_seconds": self.buffer_seconds,
                "oldest_ts": oldest,
                "newest_ts": newest,
                "span_s": (None if oldest is None or newest is None else round(newest - oldest, 1)),
            }

    def list_frames(self, limit: int = 200) -> list[dict]:
        with self._lock:
            files = sorted(self.root.glob("*.jpg"), key=lambda p: p.name)
            if limit > 0 and len(files) > limit:
                # evenly sample
                step = max(1, len(files) // limit)
                files = files[::step][:limit]
            out = []
            for p in files:
                try:
                    out.append({"ts": float(p.stem), "name": p.name, "bytes": p.stat().st_size})
                except (ValueError, OSError):
                    continue
            return out

    def frame_near(self, ts: float) -> Path | None:
        with self._lock:
            files = sorted(self.root.glob("*.jpg"), key=lambda p: p.name)
            if not files:
                return None
            best = min(files, key=lambda p: abs(float(p.stem) - ts))
            return best

    def clear(self) -> None:
        with self._lock:
            shutil.rmtree(self.root, ignore_errors=True)
            self.root.mkdir(parents=True, exist_ok=True)
            self._bytes = 0

    def dump_meta(self, path: Path) -> None:
        path.write_text(json.dumps(self.stats(), indent=2))
