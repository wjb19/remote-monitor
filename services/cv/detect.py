"""YOLOX object identification for motion captures on the hub.

OpenCV 5 on this machine loads ONNX. The Jetson and the TK1 use the
detectors their own OpenCV builds can run.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus",
    "train", "truck", "boat", "traffic light", "fire hydrant",
    "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe",
    "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat",
    "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl",
    "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
    "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop",
    "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock",
    "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)

_ROOT = Path(__file__).resolve().parents[2]
_MODEL = _ROOT / "models" / "object_id" / "yolox_2022nov.onnx"
_detector = None


def _min_confidence() -> float:
    try:
        return float(os.environ.get("RMLAB_DETECT_MIN", "0.45"))
    except (TypeError, ValueError):
        return 0.45


def _iou(a: dict, b: dict) -> float:
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


def nms(objects: list[dict], iou_thresh: float = 0.45) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for obj in objects:
        grouped.setdefault(obj["label"], []).append(obj)
    kept: list[dict] = []
    for items in grouped.values():
        items.sort(key=lambda obj: -(obj["confidence"] if obj.get("confidence") is not None else 0))
        chosen: list[dict] = []
        for obj in items:
            if all(_iou(obj, prev) < iou_thresh for prev in chosen):
                chosen.append(obj)
        kept.extend(chosen)
    return kept


def draw(frame, objects: list[dict]) -> None:
    for obj in objects:
        x, y, w, h = obj["x"], obj["y"], obj["w"], obj["h"]
        label = obj["label"]
        if obj.get("confidence") is not None:
            label = "%s %.0f%%" % (label, obj["confidence"] * 100)
        cv2.rectangle(frame, (x, y), (x + w, y + h), (80, 180, 90), 2)
        ty = y - 6 if y > 16 else y + 16
        cv2.putText(frame, label, (x, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 180, 90), 1)


class YoloXDetector:
    name = "yolox"

    def __init__(self) -> None:
        self.error = None
        self.net = None
        self.grids = None
        self.expanded_strides = None
        self.input_size = (640, 640)
        self.strides = (8, 16, 32)
        if not _MODEL.is_file():
            self.error = "missing %s" % _MODEL
            return
        try:
            self.net = cv2.dnn.readNetFromONNX(str(_MODEL))
            self._anchors()
        except Exception as exc:
            self.net = None
            self.error = str(exc)

    def _anchors(self) -> None:
        grids = []
        strides = []
        height, width = self.input_size
        for stride in self.strides:
            hsize = height // stride
            wsize = width // stride
            xv, yv = np.meshgrid(np.arange(wsize), np.arange(hsize))
            grid = np.stack((xv, yv), 2).reshape(1, -1, 2)
            grids.append(grid)
            strides.append(np.full((grid.shape[0], grid.shape[1], 1), stride))
        self.grids = np.concatenate(grids, 1)
        self.expanded_strides = np.concatenate(strides, 1)

    def detect(self, frame) -> list[dict]:
        if self.net is None or frame is None:
            return []
        image_h, image_w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        padded, ratio = _letterbox(rgb, self.input_size)
        blob = np.transpose(padded, (2, 0, 1))[np.newaxis, :, :, :]
        self.net.setInput(blob)
        outs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        output = outs[0]
        if output.ndim == 4:
            output = output[0]
        if output.ndim == 2:
            output = output[np.newaxis, ...]
        boxes = self._decode(output[0], ratio, image_w, image_h)
        return nms(boxes)

    def _decode(self, dets, ratio: float, image_w: int, image_h: int) -> list[dict]:
        if self.grids is None or dets.shape[0] != self.grids.shape[1]:
            raise RuntimeError("unexpected YOLOX output %s" % (dets.shape,))
        grids = self.grids[0]
        strides = self.expanded_strides[0, :, 0]
        cx = (dets[:, 0] + grids[:, 0]) * strides
        cy = (dets[:, 1] + grids[:, 1]) * strides
        bw = np.exp(dets[:, 2]) * strides
        bh = np.exp(dets[:, 3]) * strides
        scores = dets[:, 4:5] * dets[:, 5:]
        class_ids = np.argmax(scores, axis=1)
        class_scores = scores[np.arange(scores.shape[0]), class_ids]
        keep = class_scores >= _min_confidence()
        found = []
        for idx in np.nonzero(keep)[0]:
            label_i = int(class_ids[idx])
            if label_i < 0 or label_i >= len(CLASSES):
                continue
            x = int((cx[idx] - bw[idx] / 2.0) / ratio)
            y = int((cy[idx] - bh[idx] / 2.0) / ratio)
            w = int(bw[idx] / ratio)
            h = int(bh[idx] / ratio)
            x = max(0, min(image_w - 1, x))
            y = max(0, min(image_h - 1, y))
            w = max(0, min(image_w - x, w))
            h = max(0, min(image_h - y, h))
            if w < 8 or h < 8:
                continue
            found.append(
                {
                    "label": CLASSES[label_i],
                    "confidence": round(float(class_scores[idx]), 2),
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                }
            )
        return found


def _letterbox(image, target):
    target_h, target_w = target
    padded = np.ones((target_h, target_w, 3), dtype=np.float32) * 114.0
    ratio = min(target_h / float(image.shape[0]), target_w / float(image.shape[1]))
    resized = cv2.resize(
        image,
        (int(image.shape[1] * ratio), int(image.shape[0] * ratio)),
        interpolation=cv2.INTER_LINEAR,
    ).astype(np.float32)
    padded[: resized.shape[0], : resized.shape[1]] = resized
    return padded, ratio


def get_detector() -> YoloXDetector:
    global _detector
    if _detector is None:
        _detector = YoloXDetector()
    return _detector


def detector_info() -> dict:
    det = get_detector()
    return {"name": det.name if det.net is not None else None, "error": det.error}


STILL_W = 1280
STILL_H = 720


def _rank_box(obj: dict) -> tuple:
    confidence = obj.get("confidence")
    return (confidence if confidence is not None else 0, obj["w"] * obj["h"])


def letterbox_object(frame, obj: dict):
    """Frame one object in a 16:9 still. Pad the edges that leave the image."""
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


def _encode_still(image) -> bytes | None:
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        return None
    return buf.tobytes()


def object_stills(entries, limit: int = 2) -> list[dict]:
    """entries are (frame_index, frame, box). Return up to two 16:9 JPEGs."""
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


def annotate_window(frames, trigger_index: int):
    det = get_detector()
    info = {"name": det.name if det.net is not None else None, "error": det.error, "stills": []}
    if det.net is None or not frames:
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
