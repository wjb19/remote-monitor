"""Motion-triggered video and audio for the Tegra K1.

Video matches the Jetson window: an in-memory pre-roll plus a post-roll,
uploaded as JPEGs. The camera microphone is captured the whole time into a
short PCM ring and uploaded as one WAV for the same window.
"""

from __future__ import print_function

import json
import os
import subprocess
import threading
import time
import wave
from collections import deque

try:
    import cv2
except Exception:
    cv2 = None

try:
    import detect
except Exception:
    detect = None


def env_float(key, default):
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def env_int(key, default):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return int(default)


def _bound(value, lo, hi):
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def camera_audio_device():
    override = os.environ.get("RMLAB_AUDIO_DEVICE")
    if override:
        return override
    try:
        text = open("/proc/asound/cards").read()
    except Exception:
        return "plughw:2,0"
    for line in text.splitlines():
        if "USB Camera" in line or "[Camera" in line:
            parts = line.strip().split()
            if parts and parts[0].isdigit():
                return "plughw:%s,0" % parts[0]
    return "plughw:2,0"


class MotionAV(object):
    def __init__(self, on_frame=None, on_audio=None):
        self.on_frame = on_frame
        self.on_audio = on_audio
        self.camera_id = os.environ.get("RMLAB_CAMERA_ID", "tk1-cam0")
        self.device = os.environ.get("RMLAB_MOTION_DEVICE", "/dev/video0")
        self.audio_device = camera_audio_device()
        self.enabled = os.environ.get("RMLAB_MOTION_CAM", "1") != "0"
        self.threshold = env_float("RMLAB_MOTION_THRESHOLD", "12.0")
        self.min_area = env_float("RMLAB_MOTION_MIN_AREA", "0.01")
        self.cooldown_s = env_float("RMLAB_MOTION_COOLDOWN_S", "20")
        self.fps = env_float("RMLAB_MOTION_FPS", "5")
        self.width = env_int("RMLAB_MOTION_WIDTH", "640")
        self.height = env_int("RMLAB_MOTION_HEIGHT", "480")
        self.pre_s = env_float("RMLAB_MOTION_PRE_S", "2")
        self.post_s = env_float("RMLAB_MOTION_POST_S", "4")
        self.rate = env_int("RMLAB_AUDIO_RATE", "16000")
        self._stop = threading.Event()
        self._video_thread = None
        self._audio_thread = None
        self._cfg_lock = threading.Lock()
        self._audio_lock = threading.Lock()
        self._chunks = deque()
        self._audio_proc = None
        info = _detector_info()
        self.state = {
            "enabled": self.enabled,
            "running": False,
            "last_error": None,
            "last_motion_ts": None,
            "triggers": 0,
            "frames_seen": 0,
            "audio_bytes": 0,
            "opencv": cv2 is not None,
            "mode": "motion_window",
            "continuous_buffer": False,
            "device": self.device,
            "audio_device": self.audio_device,
            "device_open": None,
            "frame_w": None,
            "frame_h": None,
            "pre_s": self.pre_s,
            "post_s": self.post_s,
            "threshold": self.threshold,
            "cooldown_s": self.cooldown_s,
            "fps": self.fps,
            "camera_id": self.camera_id,
            "detector": info.get("name"),
            "detector_error": info.get("error"),
            "last_objects": [],
        }

    def configure(self, **kwargs):
        with self._cfg_lock:
            if kwargs.get("pre_s") is not None:
                self.pre_s = _bound(float(kwargs["pre_s"]), 0.0, 15.0)
            if kwargs.get("post_s") is not None:
                self.post_s = _bound(float(kwargs["post_s"]), 0.5, 60.0)
            if kwargs.get("threshold") is not None:
                self.threshold = _bound(float(kwargs["threshold"]), 0.5, 100.0)
            if kwargs.get("cooldown_s") is not None:
                self.cooldown_s = _bound(float(kwargs["cooldown_s"]), 1.0, 600.0)
            if kwargs.get("fps") is not None:
                self.fps = _bound(float(kwargs["fps"]), 1.0, 10.0)
            if kwargs.get("device"):
                self.device = str(kwargs["device"])
            self.state["pre_s"] = self.pre_s
            self.state["post_s"] = self.post_s
            self.state["threshold"] = self.threshold
            self.state["cooldown_s"] = self.cooldown_s
            self.state["fps"] = self.fps
            self.state["device"] = self.device
        return dict(self.state)

    def start(self):
        if not self.enabled:
            return
        if cv2 is None:
            self.state["last_error"] = "opencv not available"
            return
        if self._video_thread and self._video_thread.is_alive():
            return
        self._stop.clear()
        self._audio_thread = threading.Thread(target=self._audio_run)
        self._audio_thread.setDaemon(True)
        self._audio_thread.start()
        self._video_thread = threading.Thread(target=self._video_run)
        self._video_thread.setDaemon(True)
        self._video_thread.start()

    def stop(self):
        self._stop.set()
        proc = self._audio_proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        if self._video_thread:
            self._video_thread.join(3.0)
        self.state["running"] = False

    def _sources(self):
        # OpenCV 2.4 on this board opens /dev/videoN by index, not by path.
        sources = []
        dev = self.device
        if isinstance(dev, basestring) and dev.startswith("/dev/video"):
            tail = dev[len("/dev/video"):]
            if tail.isdigit():
                sources.append(int(tail))
        sources.append(dev)
        return sources

    def _open(self):
        fourcc = None
        legacy = getattr(cv2, "cv", None)
        if legacy is not None and hasattr(legacy, "CV_FOURCC"):
            fourcc = legacy.CV_FOURCC("M", "J", "P", "G")
        elif hasattr(cv2, "VideoWriter_fourcc"):
            fourcc = cv2.VideoWriter_fourcc("M", "J", "P", "G")
        for src in self._sources():
            cap = cv2.VideoCapture(src)
            if not cap.isOpened():
                cap.release()
                continue
            if fourcc is not None:
                cap.set(6, fourcc)
            cap.set(3, self.width)
            cap.set(4, self.height)
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                continue
            self.state["device_open"] = self.device
            self.state["frame_w"] = int(frame.shape[1])
            self.state["frame_h"] = int(frame.shape[0])
            self.state["last_error"] = None
            return cap, frame
        return None

    def _audio_run(self):
        while not self._stop.is_set():
            log_dir = os.path.expanduser("~/rmlab/log")
            try:
                os.makedirs(log_dir)
            except OSError:
                pass
            err_f = open(os.path.join(log_dir, "arecord.log"), "ab")
            proc = subprocess.Popen(
                [
                    "arecord",
                    "-D",
                    self.audio_device,
                    "-f",
                    "S16_LE",
                    "-r",
                    str(self.rate),
                    "-c",
                    "1",
                    "-t",
                    "raw",
                    "-q",
                ],
                stdout=subprocess.PIPE,
                stderr=err_f,
            )
            self._audio_proc = proc
            try:
                while not self._stop.is_set():
                    chunk = proc.stdout.read(self.rate * 2 // 5)
                    if not chunk:
                        break
                    now = time.time()
                    with self._audio_lock:
                        self._chunks.append((now, chunk))
                        self.state["audio_bytes"] += len(chunk)
                        keep = self.pre_s + self.post_s + 3.0
                        while self._chunks and (now - self._chunks[0][0]) > keep:
                            self._chunks.popleft()
            finally:
                if proc.poll() is None:
                    proc.terminate()
                err_f.close()
            if not self._stop.is_set():
                self._stop.wait(2.0)

    def _pcm_between(self, t0, t1):
        with self._audio_lock:
            parts = []
            bytes_per_sec = float(self.rate * 2)
            for t_end, data in self._chunks:
                dur = len(data) / bytes_per_sec
                t_start = t_end - dur
                if t_end < t0 or t_start > t1:
                    continue
                parts.append(data)
            return "".join(parts)

    def _wav_bytes(self, pcm):
        # wave wants a file; a temp path under /tmp is fine on this board.
        path = "/tmp/rmlab-tk1-%d.wav" % os.getpid()
        handle = wave.open(path, "wb")
        try:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.rate)
            handle.writeframes(pcm)
        finally:
            handle.close()
        try:
            data = open(path, "rb").read()
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
        return data

    def _video_run(self):
        while not self._stop.is_set():
            period = 1.0 / max(1.0, self.fps)
            opened = self._open()
            if opened is None:
                self.state["running"] = False
                self.state["device_open"] = None
                self.state["last_error"] = "cannot open %s" % self.device
                self._stop.wait(2.0)
                continue
            cap, first = opened
            self.state["running"] = True
            buf = deque()
            prev_gray = None
            last_fire = 0.0
            pending = [(time.time(), first)]
            try:
                while not self._stop.is_set():
                    if pending:
                        t0, frame = pending.pop(0)
                    else:
                        t0 = time.time()
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            self.state["last_error"] = "frame read failed"
                            break
                    self.state["frames_seen"] += 1
                    want = max(1, int(round(self.pre_s * max(1.0, self.fps))))
                    buf.append((t0, frame))
                    while len(buf) > want:
                        buf.popleft()
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    gray = cv2.GaussianBlur(gray, (21, 21), 0)
                    motion = False
                    score = 0.0
                    if prev_gray is not None and prev_gray.shape == gray.shape:
                        delta = cv2.absdiff(prev_gray, gray)
                        _, th = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)
                        th = cv2.dilate(th, None, iterations=2)
                        nonzero = cv2.countNonZero(th)
                        area = float(th.shape[0] * th.shape[1]) or 1.0
                        score = 100.0 * nonzero / area
                        motion = score >= self.threshold and (nonzero / area) >= self.min_area
                    prev_gray = gray

                    if motion and (t0 - last_fire) >= self.cooldown_s:
                        window = list(buf)
                        deadline = time.time() + self.post_s
                        while not self._stop.is_set() and time.time() < deadline:
                            ok2, frame2 = cap.read()
                            if not ok2 or frame2 is None:
                                break
                            self.state["frames_seen"] += 1
                            window.append((time.time(), frame2))
                            prev_gray = cv2.GaussianBlur(
                                cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY), (21, 21), 0
                            )
                            self._stop.wait(period)
                        self._emit(window, t0, score)
                        last_fire = t0
                        buf.clear()

                    elapsed = time.time() - t0
                    delay = period - elapsed
                    if delay > 0:
                        self._stop.wait(delay)
            finally:
                cap.release()
                self.state["running"] = False
            if not self._stop.is_set():
                self._stop.wait(1.0)

    def _emit(self, window, trigger_ts, score):
        if not window:
            return
        objects, detector_name, stills = _identify(window, trigger_ts, self.state)
        encoded = []
        for ts, frame in window:
            ok_jpg, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok_jpg:
                encoded.append((ts, buf.tostring()))
        if not encoded:
            self.state["last_error"] = "jpeg encode failed"
            return
        window_id = "%.6f-%s" % (trigger_ts, self.camera_id)
        count = len(encoded)
        errors = 0
        for index, (ts, jpeg) in enumerate(encoded):
            meta = {
                "ts": ts,
                "score": round(score, 2),
                "device": self.state.get("device_open") or self.device,
                "camera_id": self.camera_id,
                "kind": "motion",
                "window_id": window_id,
                "frame_index": index,
                "frame_count": count,
                "offset_s": round(ts - trigger_ts, 3),
                "pre_s": self.pre_s,
                "post_s": self.post_s,
                "objects": json.dumps(objects),
            }
            if detector_name:
                meta["detector"] = detector_name
            try:
                if self.on_frame:
                    self.on_frame(jpeg, meta)
            except Exception as exc:
                errors += 1
                self.state["last_error"] = "frame upload failed: %s" % exc
        for n, still in enumerate(stills or []):
            fi = still.get("frame_index") or 0
            still_ts = window[fi][0] if 0 <= fi < len(window) else trigger_ts
            still_meta = {
                "ts": still_ts,
                "score": round(score, 2),
                "device": self.state.get("device_open") or self.device,
                "camera_id": self.camera_id,
                "kind": "object_still",
                "window_id": window_id,
                "frame_index": 100 + n,
                "objects": json.dumps(still.get("objects") or []),
                "aspect": still.get("aspect") or "16:9",
                "label": still.get("label"),
            }
            if detector_name:
                still_meta["detector"] = detector_name
            try:
                if self.on_frame:
                    self.on_frame(still["jpeg"], still_meta)
            except Exception as exc:
                errors += 1
                self.state["last_error"] = "still upload failed: %s" % exc
        audio_meta = {
            "ts": trigger_ts,
            "score": round(score, 2),
            "device": self.audio_device,
            "camera_id": self.camera_id,
            "kind": "audio",
            "window_id": window_id,
            "pre_s": self.pre_s,
            "post_s": self.post_s,
        }
        pcm = self._pcm_between(trigger_ts - self.pre_s, trigger_ts + self.post_s)
        if pcm and self.on_audio:
            try:
                wav = self._wav_bytes(pcm)
                audio_meta["duration_s"] = round(len(pcm) / float(self.rate * 2), 3)
                self.on_audio(wav, audio_meta)
            except Exception as exc:
                errors += 1
                self.state["last_error"] = "audio upload failed: %s" % exc
        elif not pcm:
            self.state["last_error"] = "audio ring was empty"
        if errors == 0 and pcm:
            self.state["last_error"] = None
        self.state["triggers"] += 1
        self.state["last_motion_ts"] = trigger_ts
        self.state["last_window_frames"] = count
        self.state["last_audio_bytes"] = len(pcm)


def _detector_info():
    if detect is None:
        return {"name": None, "error": "detector unavailable"}
    try:
        return detect.detector_info()
    except Exception as exc:
        return {"name": None, "error": str(exc)}


def _closest_index(window, trigger_ts):
    best = 0
    best_dt = None
    for i, (ts, _frame) in enumerate(window):
        dt = abs(ts - trigger_ts)
        if best_dt is None or dt < best_dt:
            best = i
            best_dt = dt
    return best


def _identify(window, trigger_ts, state):
    if detect is None:
        return [], None, []
    frames = [frame for _ts, frame in window]
    try:
        objects, info = detect.annotate_window(frames, _closest_index(window, trigger_ts))
    except Exception as exc:
        state["last_error"] = "detect failed: %s" % exc
        return [], state.get("detector"), []
    state["detector"] = info.get("name")
    state["detector_error"] = info.get("error")
    state["last_objects"] = objects
    return objects, info.get("name"), info.get("stills") or []
