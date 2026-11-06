"""Object identification for a Jetson motion capture.

Prefers the Caffe MobileNet-SSD model, which loads on the OpenCV build
shipped with JetPack. Falls back to the person and face cascades.
"""

import os

try:
    import cv2
except Exception:
    cv2 = None


CLASSES = (
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus",
    "car", "cat", "chair", "cow", "diningtable", "dog", "horse", "motorbike",
    "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor",
)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DIR = os.path.join(_ROOT, "models", "object_id")
_detector = None


def _min_confidence():
    try:
        return float(os.environ.get("RMLAB_DETECT_MIN", "0.4"))
    except (TypeError, ValueError):
        return 0.4


def _iou(a, b):
    ax, ay, aw, ah = a["x"], a["y"], a["w"], a["h"]
    bx, by, bw, bh = b["x"], b["y"], b["w"], b["h"]
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / float(union)


def nms(objects, iou_thresh=0.45):
    grouped = {}
    for obj in objects:
        grouped.setdefault(obj["label"], []).append(obj)
    kept = []
    for items in grouped.values():
        items.sort(key=lambda obj: -(obj["confidence"] if obj.get("confidence") is not None else 0))
        chosen = []
        for obj in items:
            if all(_iou(obj, prev) < iou_thresh for prev in chosen):
                chosen.append(obj)
        kept.extend(chosen)
    return kept


def draw(frame, objects):
    if cv2 is None:
        return
    for obj in objects:
        x, y, w, h = obj["x"], obj["y"], obj["w"], obj["h"]
        label = obj["label"]
        if obj.get("confidence") is not None:
            label = "%s %.0f%%" % (label, obj["confidence"] * 100)
        cv2.rectangle(frame, (x, y), (x + w, y + h), (80, 180, 90), 2)
        ty = y - 6 if y > 16 else y + 16
        cv2.putText(frame, label, (x, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 180, 90), 1)


class Detector(object):
    def __init__(self):
        self.name = None
        self.error = None
        self.net = None
        self._cascades = []
        if cv2 is None:
            self.error = "opencv not available"
            return
        proto = os.path.join(_DIR, "MobileNetSSD_deploy.prototxt")
        weights = os.path.join(_DIR, "MobileNetSSD_deploy.caffemodel")
        if hasattr(cv2, "dnn") and hasattr(cv2.dnn, "readNetFromCaffe") and os.path.isfile(weights):
            try:
                self.net = cv2.dnn.readNetFromCaffe(proto, weights)
                self.name = "mobilenet-ssd"
            except Exception as exc:
                self.error = str(exc)
        if self.net is None:
            self._load_cascades()
            if self._cascades:
                self.name = "haar"
                if self.error is None and not os.path.isfile(weights):
                    self.error = "MobileNet-SSD model is not on this board"
            elif self.error is None:
                self.error = "no object detector available"

    def _load_cascades(self):
        base = os.environ.get("RMLAB_CASCADE_DIR")
        if not base:
            data = getattr(cv2, "data", None)
            base = getattr(data, "haarcascades", "") if data is not None else ""
        for filename, label in (
            ("haarcascade_fullbody.xml", "person"),
            ("haarcascade_upperbody.xml", "person"),
            ("haarcascade_frontalface_default.xml", "face"),
        ):
            path = os.path.join(base, filename)
            if not os.path.isfile(path):
                continue
            clf = cv2.CascadeClassifier(path)
            if clf.empty():
                continue
            self._cascades.append((clf, label))

    def detect(self, frame):
        if frame is None:
            return []
        if self.net is not None:
            return self._detect_ssd(frame)
        return self._detect_haar(frame)

    def _detect_ssd(self, frame):
        height, width = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(frame, 0.007843, (300, 300), 127.5)
        self.net.setInput(blob)
        det = self.net.forward()
        found = []
        count = det.shape[2] if len(det.shape) > 2 else 0
        min_conf = _min_confidence()
        for i in range(count):
            conf = float(det[0, 0, i, 2])
            if conf < min_conf:
                continue
            label_i = int(det[0, 0, i, 1])
            if label_i <= 0 or label_i >= len(CLASSES):
                continue
            x1 = int(det[0, 0, i, 3] * width)
            y1 = int(det[0, 0, i, 4] * height)
            x2 = int(det[0, 0, i, 5] * width)
            y2 = int(det[0, 0, i, 6] * height)
            x1 = max(0, min(width - 1, x1))
            y1 = max(0, min(height - 1, y1))
            w = max(0, min(width - x1, x2 - x1))
            h = max(0, min(height - y1, y2 - y1))
            if w < 8 or h < 8:
                continue
            found.append(
                {
                    "label": CLASSES[label_i],
                    "confidence": round(conf, 2),
                    "x": x1,
                    "y": y1,
                    "w": w,
                    "h": h,
                }
            )
        return nms(found)

    def _detect_haar(self, frame):
        if not self._cascades:
            return []
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found = []
        for clf, label in self._cascades:
            rects = clf.detectMultiScale(gray, 1.1, 3)
            for rect in rects:
                x, y, w, h = [int(v) for v in rect]
                if w < 8 or h < 8:
                    continue
                found.append({"label": label, "x": x, "y": y, "w": w, "h": h})
        return nms(found)


def get_detector():
    global _detector
    if _detector is None:
        _detector = Detector()
    return _detector


def detector_info():
    det = get_detector()
    return {"name": det.name, "error": det.error}


STILL_W = 1280
STILL_H = 720


def _rank_box(obj):
    confidence = obj.get("confidence")
    return (confidence if confidence is not None else 0, obj["w"] * obj["h"])


def letterbox_object(frame, obj):
    """Frame one object in a 16:9 still. Pad the edges that leave the image."""
    if cv2 is None or frame is None:
        return None
    height, width = frame.shape[:2]
    x, y, bw, bh = obj["x"], obj["y"], obj["w"], obj["h"]
    pad_x = int(round(bw * 0.35))
    pad_y = int(round(bh * 0.35))
    cx = x + bw / 2.0
    cy = y + bh / 2.0
    region_w = max(1, bw + pad_x * 2)
    region_h = max(1, bh + pad_y * 2)
    if region_w / float(region_h) < (16.0 / 9.0):
        region_w = int(round(region_h * 16.0 / 9.0))
    else:
        region_h = int(round(region_w * 9.0 / 16.0))
    x0 = int(round(cx - region_w / 2.0))
    y0 = int(round(cy - region_h / 2.0))
    x1 = x0 + region_w
    y1 = y0 + region_h
    left = max(0, min(width, x0))
    top = max(0, min(height, y0))
    right = max(left, min(width, x1))
    bottom = max(top, min(height, y1))
    if right <= left or bottom <= top:
        return None
    crop = frame[top:bottom, left:right]
    crop = cv2.copyMakeBorder(
        crop,
        top - y0,
        y1 - bottom,
        left - x0,
        x1 - right,
        cv2.BORDER_CONSTANT,
        value=(16, 16, 16),
    )
    shrinking = crop.shape[1] >= STILL_W and crop.shape[0] >= STILL_H
    interpolation = cv2.INTER_AREA if shrinking else cv2.INTER_LINEAR
    return cv2.resize(crop, (STILL_W, STILL_H), interpolation=interpolation)


def _encode_still(image):
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        return None
    return buf.tobytes()


def object_stills(entries, limit=2):
    ranked = sorted(entries, key=lambda item: _rank_box(item[2]), reverse=True)
    chosen = []
    for item in ranked:
        frame_index, _frame, box = item
        if any(frame_index == prev[0] and _iou(box, prev[2]) >= 0.45 for prev in chosen):
            continue
        chosen.append(item)
        if len(chosen) >= limit:
            break
    stills = []
    for frame_index, frame, box in chosen:
        image = letterbox_object(frame, box)
        if image is None:
            continue
        jpeg = _encode_still(image)
        if not jpeg:
            continue
        stills.append(
            {
                "jpeg": jpeg,
                "frame_index": frame_index,
                "label": box.get("label"),
                "objects": [box],
                "aspect": "16:9",
                "width": STILL_W,
                "height": STILL_H,
            }
        )
    return stills


def annotate_window(frames, trigger_index):
    det = get_detector()
    info = {"name": det.name, "error": det.error, "stills": []}
    if not frames or det.name is None:
        return [], info
    count = len(frames)
    trigger_index = max(0, min(count - 1, int(trigger_index)))
    indexes = []
    for i in (trigger_index, count // 2, count - 1):
        if i not in indexes:
            indexes.append(i)
    found = []
    entries = []
    for i in indexes:
        boxes = det.detect(frames[i])
        if not boxes:
            continue
        for box in boxes:
            entries.append((i, frames[i], box))
        draw(frames[i], boxes)
        found.extend(boxes)
    info["stills"] = object_stills(entries)
    return nms(found), info
