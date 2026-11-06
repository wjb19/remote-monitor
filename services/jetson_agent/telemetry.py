"""HTTP telemetry client to the main lab host."""

import json
import os

try:
    # Python 3
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    from urllib2 import Request, urlopen


def env(key, default):
    return os.environ.get(key, default)


def post_json(path, payload, timeout=5.0):
    base = env("RMLAB_MAIN_URL", "http://192.168.1.14:8080").rstrip("/")
    url = base + path
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=timeout)
    body = resp.read()
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {"ok": True, "raw": body.decode("utf-8", "replace")[:200]}


def post_multipart_event(jpeg_bytes, meta, timeout=15.0):
    """POST motion event with JPEG attachment (stdlib multipart)."""
    import uuid

    base = env("RMLAB_MAIN_URL", "http://192.168.1.14:8080").rstrip("/")
    url = base + "/api/edge/events"
    boundary = "----rmlab%x" % uuid.uuid4().int
    parts = []
    for key, value in meta.items():
        parts.append(
            ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, key, value)).encode(
                "utf-8"
            )
        )
    parts.append(
        (
            "--%s\r\nContent-Disposition: form-data; name=\"frame\"; filename=\"motion.jpg\"\r\n"
            "Content-Type: image/jpeg\r\n\r\n" % boundary
        ).encode("utf-8")
    )
    parts.append(jpeg_bytes)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    body = b"".join(parts)
    req = Request(
        url,
        data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary},
    )
    resp = urlopen(req, timeout=timeout)
    raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "raw": raw[:200]}
