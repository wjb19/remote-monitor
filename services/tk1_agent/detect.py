"""Person and face identification for a TK1 motion capture.

This board's OpenCV 2.4 has no neural-net loader. The cascades and the
HOG people detector are what it can run locally.
"""

from __future__ import division, print_function

import os

try:
    import cv2
except Exception:
    cv2 = None


_detector = None


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
        cv2.rectangle(frame, (x, y), (x + w, y + h), (80, 180, 90), 2)
        ty = y - 6 if y > 16 else y + 16
        cv2.putText(
            frame, obj["label"], (x, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 180, 90), 1
        )


class Detector(object):
    def __init__(self):
        self.name = None
        self.error = None
        self._hog = None
        self._cascades = []
        if cv2 is None:
            self.error = "opencv not available"
            return
        try:
            hog = cv2.HOGDescriptor()
            hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            self._hog = hog
        except Exception as exc:
            self.error = "hog: %s" % exc
        base = os.environ.get("RMLAB_CASCADE_DIR", "/usr/share/OpenCV/haarcascades")
        for filename, label, scale, min_size in (
            ("haarcascade_fullbody.xml", "person", 1.2, (48, 96)),
            ("haarcascade_upperbody.xml", "person", 1.2, (40, 40)),
            ("haarcascade_lowerbody.xml", "person", 1.2, (40, 40)),
            ("haarcascade_frontalface_default.xml", "face", 1.3, (48, 48)),
        ):
            path = os.path.join(base, filename)
            if not os.path.isfile(path):
                continue
            clf = cv2.CascadeClassifier(path)
            try:
                empty = clf.empty()
            except Exception:
                empty = False
            if empty:
                continue
            self._cascades.append((clf, label, scale, min_size))
        if self._hog is not None or self._cascades:
            self.name = "haar-hog"
        elif self.error is None:
            self.error = "no haar cascades found"

    def detect(self, frame):
        if cv2 is None or frame is None:
            return []
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found = []
        if self._hog is not None:
            try:
                rects, _weights = self._hog.detectMultiScale(gray, 0, (8, 8), (8, 8), 1.05)
            except Exception as exc:
                self.error = "hog: %s" % exc
                self._hog = None
                rects = []
            for rect in rects:
                x, y, w, h = [int(v) for v in rect]
                if w < 8 or h < 8:
                    continue
                found.append({"label": "person", "x": x, "y": y, "w": w, "h": h})
        for clf, label, scale, min_size in self._cascades:
            try:
                rects = clf.detectMultiScale(gray, scale, 4, 0, min_size)
            except Exception:
                continue
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
    return buf.tostring()


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
