"""Learn the hub URL from the Jetson agent on the direct Ethernet link.

Python 2.7. The TK1 does not scan the house Wi-Fi; it asks the Jetson at
192.168.8.1 (with the old .17 address kept as a cutover fallback).
"""

from __future__ import print_function

import json
import os
import threading
import time

from urllib2 import urlopen

_lock = threading.Lock()
_state = {
    "url": None,
    "ip": None,
    "jetson_url": None,
    "reachable": False,
    "last_seen_ts": None,
    "note": "not searched yet",
}


def env(key, default=""):
    return os.environ.get(key, default)


def configured_main_url():
    raw = env("RMLAB_MAIN_URL", "").strip()
    if not raw or raw.lower() in ("auto", "discover", "dhcp"):
        return None
    return raw.rstrip("/")


def jetson_candidates():
    raw = env("RMLAB_JETSON_URL", "").strip()
    urls = []
    if raw:
        urls.append(raw.rstrip("/"))
    urls.extend(
        [
            "http://192.168.8.1:8090",
            "http://192.168.1.17:8090",
        ]
    )
    found = []
    for url in urls:
        if url and url not in found:
            found.append(url)
    return found


def snapshot():
    with _lock:
        return dict(_state)


def main_url():
    with _lock:
        if _state["url"]:
            return _state["url"]
    configured = configured_main_url()
    if configured:
        return configured
    return "http://192.168.8.1:8080"


def _probe_jetson(url, timeout=2.0):
    try:
        raw = urlopen(url.rstrip("/") + "/health", timeout=timeout).read()
        body = json.loads(raw)
    except Exception:
        return None
    if not isinstance(body, dict) or body.get("service") != "jetson-agent":
        return None
    return body


def _hub_url_from_body(body):
    if not body:
        return None, None
    hub = body.get("hub") or {}
    ip = hub.get("ip")
    if ip:
        return "http://%s:8080" % ip, ip
    main = (body.get("main_url") or "").strip().rstrip("/")
    if main and main.lower() not in ("auto", "discover", "dhcp"):
        # Prefer a concrete IP if the Jetson already resolved one.
        if "://" in main:
            host = main.split("://", 1)[1].split("/", 1)[0].split(":")[0]
            if host and all(c.isdigit() or c == "." for c in host):
                return main, host
        return main, ip
    return None, ip


def refresh():
    for jetson_url in jetson_candidates():
        body = _probe_jetson(jetson_url)
        if not body:
            continue
        url, ip = _hub_url_from_body(body)
        if not url:
            continue
        with _lock:
            _state["url"] = url
            _state["ip"] = ip
            _state["jetson_url"] = jetson_url
            _state["reachable"] = True
            _state["last_seen_ts"] = time.time()
            _state["note"] = "hub %s via %s" % (url, jetson_url)
        return snapshot()
    with _lock:
        _state["reachable"] = False
        if not _state["url"]:
            _state["note"] = "jetson agent not reachable on the direct link"
    return snapshot()


def watch_loop(stop_event=None):
    stop_event = stop_event or threading.Event()
    try:
        refresh()
    except Exception as exc:
        with _lock:
            _state["note"] = "hub refresh failed: %s" % exc
    while not stop_event.wait(20):
        try:
            refresh()
        except Exception as exc:
            with _lock:
                _state["note"] = "hub refresh failed: %s" % exc


def start_background():
    # Seed from an explicit URL so the first upload has somewhere to go.
    configured = configured_main_url()
    if configured:
        with _lock:
            _state["url"] = configured
            _state["note"] = "using configured RMLAB_MAIN_URL"
    thread = threading.Thread(target=watch_loop)
    thread.setDaemon(True)
    thread.start()
    return thread
