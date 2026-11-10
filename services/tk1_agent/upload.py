"""Post motion clips to the lab collector. Python 2.7."""

from __future__ import print_function

import json
import os
import uuid

from urllib2 import Request, urlopen

try:
    import hub_from_jetson
except Exception:  # pragma: no cover
    hub_from_jetson = None


def env(key, default):
    return os.environ.get(key, default)


def _base():
    if hub_from_jetson is not None:
        return hub_from_jetson.main_url().rstrip("/")
    return env("RMLAB_MAIN_URL", "http://192.168.8.1:8080").rstrip("/")


def _post_file(path, field, filename, content_type, payload, meta, timeout):
    boundary = "----rmlab%x" % uuid.uuid4().int
    parts = []
    for key, value in meta.items():
        if value is None:
            continue
        parts.append(
            '--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
            % (boundary, key, value)
        )
    parts.append(
        '--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
        "Content-Type: %s\r\n\r\n" % (boundary, field, filename, content_type)
    )
    parts.append(payload)
    parts.append("\r\n--%s--\r\n" % boundary)
    body = "".join(parts)
    req = Request(
        _base() + path,
        data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary},
    )
    resp = urlopen(req, timeout=timeout)
    raw = resp.read()
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw[:200]}


def post_frame(jpeg_bytes, meta, timeout=20.0):
    return _post_file(
        "/api/edge/events",
        "frame",
        "motion.jpg",
        "image/jpeg",
        jpeg_bytes,
        meta,
        timeout,
    )


def post_audio(wav_bytes, meta, timeout=20.0):
    return _post_file(
        "/api/edge/audio",
        "audio",
        "clip.wav",
        "audio/wav",
        wav_bytes,
        meta,
        timeout,
    )


def post_json(path, payload, timeout=5.0):
    data = json.dumps(payload)
    req = Request(
        _base() + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urlopen(req, timeout=timeout)
    raw = resp.read()
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw[:200]}
