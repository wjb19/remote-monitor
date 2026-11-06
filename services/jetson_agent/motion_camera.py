"""Motion-triggered Jetson camera.

A short in-memory pre-roll plus a configurable post-roll is uploaded when
motion crosses the threshold. Nothing is written continuously to disk.
"""

import json
import os
import time
import threading
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


class MotionCamera(object):
    def __init__(self, device="/dev/video0", on_event=None):
        self.device = device
        self.on_event = on_event
        self.enabled = os.environ.get("RMLAB_MOTION_CAM", "1") != "0"
        self.threshold = env_float("RMLAB_MOTION_THRESHOLD", "12.0")
        self.min_area = env_float("RMLAB_MOTION_MIN_AREA", "0.01")
        self.cooldown_s = env_float("RMLAB_MOTION_COOLDOWN_S", "20")
        self.fps = env_float("RMLAB_MOTION_FPS", "5")
        self.width = env_int("RMLAB_MOTION_WIDTH", "640")
        self.height = env_int("RMLAB_MOTION_HEIGHT", "480")
        self.pre_s = env_float("RMLAB_MOTION_PRE_S", "2")
        self.post_s = env_float("RMLAB_MOTION_POST_S", "4")
        self._stop = threading.Event()
        self._thread = None
        self._cfg_lock = threading.Lock()
        info = _detector_info()
        self.state = {
            "enabled": self.enabled,
            "running": False,
            "last_error": None,
            "last_motion_ts": None,
            "triggers": 0,
            "frames_seen": 0,
            "opencv": cv2 is not None,
            "mode": "motion_window",
            "continuous_buffer": False,
            "device": self.device,
            "device_open": None,
            "frame_w": None,
            "frame_h": None,
            "pre_s": self.pre_s,
            "post_s": self.post_s,
            "threshold": self.threshold,
            "cooldown_s": self.cooldown_s,
            "fps": self.fps,
            "camera_id": "jetson-cam0",
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
                self.fps = _bound(float(kwargs["fps"]), 1.0, 15.0)
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
        if not self.enabled or cv2 is None:
            if cv2 is None:
                self.state["last_error"] = "opencv not available"
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="motion-cam", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self.state["running"] = False

    def _candidates(self):
        found = []
        for dev in (self.device, "/dev/video0", "/dev/video1", "/dev/video2", "/dev/video3"):
            if dev and dev not in found:
                found.append(dev)
        return found

    def _open(self):
        # USB cameras that replace the previous one often expose a metadata
        # node plus a capture node, and many need MJPEG before they return frames.
        for dev in self._candidates():
            cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc("M", "J", "P", "G"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                continue
            self.state["device_open"] = dev
            self.state["frame_w"] = int(frame.shape[1])
            self.state["frame_h"] = int(frame.shape[0])
            self.state["last_error"] = None
            return cap
        return None

    def _run(self):
        while not self._stop.is_set():
            period = 1.0 / max(1.0, self.fps)
            cap = self._open()
            if cap is None:
                self.state["running"] = False
                self.state["device_open"] = None
                self.state["last_error"] = "cannot open %s" % self.device
                self._stop.wait(2.0)
                continue
            self.state["running"] = True
            pre_len = max(1, int(round(self.pre_s * max(1.0, self.fps))))
            buf = deque(maxlen=pre_len)
            prev_gray = None
            last_fire = 0.0
            try:
                while not self._stop.is_set():
                    t0 = time.time()
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        self.state["last_error"] = "frame read failed"
                        break
                    self.state["frames_seen"] += 1
                    want = max(1, int(round(self.pre_s * max(1.0, self.fps))))
                    if buf.maxlen != want:
                        buf = deque(buf, maxlen=want)
                    buf.append((t0, frame))
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    gray = cv2.GaussianBlur(gray, (21, 21), 0)
                    motion = False
                    score = 0.0
                    if prev_gray is not None:
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
        if not window or not self.on_event:
            return
        objects, detector_name, stills = _identify(window, trigger_ts, self.state)
        encoded = []
        for ts, frame in window:
            ok_jpg, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok_jpg:
                encoded.append((ts, buf.tobytes()))
        if not encoded:
            self.state["last_error"] = "jpeg encode failed"
            return
        window_id = "%.6f-%s" % (trigger_ts, "jetson-cam0")
        count = len(encoded)
        errors = 0
        for index, (ts, jpeg) in enumerate(encoded):
            meta = {
                "ts": ts,
                "score": round(score, 2),
                "device": self.state.get("device_open") or self.device,
                "camera_id": "jetson-cam0",
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
                self.on_event(jpeg, meta)
            except Exception as exc:
                errors += 1
                self.state["last_error"] = "event upload failed: %s" % exc
        for n, still in enumerate(stills or []):
            fi = still.get("frame_index") or 0
            still_ts = window[fi][0] if 0 <= fi < len(window) else trigger_ts
            still_meta = {
                "ts": still_ts,
                "score": round(score, 2),
                "device": self.state.get("device_open") or self.device,
                "camera_id": "jetson-cam0",
                "kind": "object_still",
                "window_id": window_id,
                "frame_index": 100 + n,
                "objects": json.dumps(still.get("objects") or []),
                "aspect": still.get("aspect") or "16:9",
            }
            if still.get("label"):
                still_meta["label"] = still.get("label")
            if detector_name:
                still_meta["detector"] = detector_name
            try:
                self.on_event(still["jpeg"], still_meta)
            except Exception as exc:
                errors += 1
                self.state["last_error"] = "still upload failed: %s" % exc
        if errors == 0:
            self.state["last_error"] = None
        self.state["triggers"] += 1
        self.state["last_motion_ts"] = trigger_ts
        self.state["last_window_frames"] = count


def _bound(value, lo, hi):
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


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
