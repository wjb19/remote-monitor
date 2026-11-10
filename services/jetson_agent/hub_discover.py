"""Find the lab hub when DHCP hands it a new address.

Python 3.6 compatible (Jetson Nano). Accepts a host when GET /health
reports service "core". Rewrites the packet-forwarder server_address when
the hub IP changes.
"""

from __future__ import print_function

import json
import logging
import os
import socket
import subprocess
import threading
import time

try:
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    from urllib2 import Request, urlopen

log = logging.getLogger("rmlab.hub_discover")

_SKIP_IFACE_PREFIXES = ("lo", "docker", "br-", "veth", "tun", "tap", "wg", "virbr")

_lock = threading.RLock()
_state = {
    "url": None,
    "ip": None,
    "hostname": None,
    "reachable": False,
    "scanning": False,
    "checked": 0,
    "last_seen_ts": None,
    "last_scan_ts": None,
    "note": "not searched yet",
    "gateway_server": None,
    "gateway_updated": False,
}


def env(key, default=""):
    return os.environ.get(key, default)


def peer_path():
    configured = env("RMLAB_HUB_PEER_PATH", "").strip()
    if configured:
        return configured
    for candidate in ("/opt/rmlab/data/hub_peer.json", "/home/bill/rmlab/hub_peer.json"):
        parent = os.path.dirname(candidate)
        if os.path.isdir(parent) and os.access(parent, os.W_OK):
            return candidate
        if not os.path.isdir(parent):
            try:
                os.makedirs(parent)
                return candidate
            except OSError:
                continue
    return "/tmp/rmlab_hub_peer.json"


def gateway_conf_path():
    return env("RMLAB_PACKET_FORWARDER_CONF", "/etc/rmlab/global_conf.json")


def hub_port():
    try:
        return int(env("RMLAB_HUB_PORT", "8080"))
    except ValueError:
        return 8080


def hub_hostname():
    return env("RMLAB_HUB_HOSTNAME", "bill-GB-BXBT-1900").strip()


def configured_main_url():
    """Explicit URL from the environment, if any (ignores auto/empty)."""
    raw = env("RMLAB_MAIN_URL", "").strip()
    if not raw or raw.lower() in ("auto", "discover", "dhcp"):
        return None
    return raw.rstrip("/")


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
    host = hub_hostname()
    if host:
        return "http://%s.local:%s" % (host, hub_port())
    return "http://127.0.0.1:%s" % hub_port()


def gateway_server():
    with _lock:
        if _state["ip"]:
            return _state["ip"]
        if _state["gateway_server"] and _is_ip(_state["gateway_server"]):
            return _state["gateway_server"]
    explicit = env("RMLAB_GATEWAY_SERVER", "").strip()
    if explicit and explicit.lower() not in ("auto", "discover", "dhcp"):
        if _is_ip(explicit):
            return explicit
        resolved = _resolve_ip(explicit)
        if resolved:
            return resolved
    url = main_url()
    host = _host_from_url(url)
    resolved = _resolve_ip(host) if host else None
    return resolved or host or "127.0.0.1"


def mqtt_host():
    explicit = env("RMLAB_MQTT_HOST", "").strip()
    if explicit and explicit.lower() not in ("auto", "discover", "dhcp"):
        return explicit
    return gateway_server()


def _host_from_url(url):
    if not url:
        return None
    # http://host:port/path
    rest = url.split("://", 1)[-1]
    hostport = rest.split("/", 1)[0]
    if hostport.startswith("["):
        return hostport[1:].split("]", 1)[0]
    return hostport.split(":", 1)[0]


def _is_ip(host):
    try:
        socket.inet_aton(host)
        return True
    except (socket.error, TypeError):
        return False


def _own_ips():
    ips = set()
    try:
        out = subprocess.check_output(["ip", "-4", "-o", "addr", "show"], stderr=subprocess.STDOUT)
        text = out.decode("utf-8", "replace")
    except Exception:
        return ips
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1].rstrip(":")
        if iface == "lo" or iface.startswith(_SKIP_IFACE_PREFIXES):
            continue
        cidr = parts[3]
        ips.add(cidr.split("/", 1)[0])
    return ips


def _local_networks():
    """Return (iface, network_prefix_str, prefixlen, local_ip) for scanable ifaces."""
    nets = []
    seen = set()
    try:
        out = subprocess.check_output(["ip", "-4", "-o", "addr", "show"], stderr=subprocess.STDOUT)
        text = out.decode("utf-8", "replace")
    except Exception as exc:
        log.warning("could not list local addresses: %s", exc)
        return nets
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1].rstrip(":")
        if iface == "lo" or iface.startswith(_SKIP_IFACE_PREFIXES):
            continue
        cidr = parts[3]
        if "/" not in cidr:
            continue
        local, prefix_s = cidr.split("/", 1)
        try:
            prefix = int(prefix_s)
        except ValueError:
            continue
        if prefix < 24:
            prefix = 24
        # Build /24 (or tighter) covering this host without ipaddress module.
        octets = [int(x) for x in local.split(".")]
        if len(octets) != 4:
            continue
        mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
        ip_int = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
        net_int = ip_int & mask
        key = "%s/%s" % (net_int, prefix)
        if key in seen:
            continue
        seen.add(key)
        nets.append((iface, net_int, prefix, local))
    return nets


def _iter_hosts(net_int, prefix):
    host_bits = 32 - prefix
    if host_bits <= 0 or host_bits > 10:
        return
    count = 1 << host_bits
    for i in range(1, count - 1):
        val = net_int + i
        yield "%d.%d.%d.%d" % (
            (val >> 24) & 255,
            (val >> 16) & 255,
            (val >> 8) & 255,
            val & 255,
        )


def _port_open(ip, port, timeout=0.4):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip, port))
        return True
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _get_json(url, timeout=1.5):
    req = Request(url, headers={"Accept": "application/json"})
    resp = urlopen(req, timeout=timeout)
    raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw)


def _accepts(body):
    if not isinstance(body, dict):
        return False
    if body.get("service") != "core":
        return False
    expected = env("RMLAB_HUB_SERVICE_HOST", "").strip().lower()
    if expected:
        got = str(body.get("hostname") or "").strip().lower()
        if got and got != expected:
            return False
    return True


def probe_url(url, timeout=1.5):
    try:
        body = _get_json(url.rstrip("/") + "/health", timeout=timeout)
    except Exception:
        return None
    if not _accepts(body):
        return None
    return body


def _load():
    path = peer_path()
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r") as fh:
            saved = json.load(fh)
    except Exception:
        return
    with _lock:
        if saved.get("url"):
            _state["url"] = str(saved["url"]).rstrip("/")
        if saved.get("ip"):
            _state["ip"] = str(saved["ip"])
            _state["gateway_server"] = _state["ip"]
        if saved.get("hostname"):
            _state["hostname"] = str(saved["hostname"])
        if saved.get("seen_ts"):
            _state["last_seen_ts"] = float(saved["seen_ts"])
            _state["note"] = "last known hub %s" % (_state["ip"] or _state["url"])


def _save(hostname):
    path = peer_path()
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent)
        except OSError:
            pass
    with _lock:
        payload = {
            "url": _state["url"],
            "ip": _state["ip"],
            "hostname": hostname or _state.get("hostname"),
            "gateway_server": _state.get("gateway_server") or _state.get("ip"),
            "seen_ts": _state["last_seen_ts"],
        }
    try:
        with open(path, "w") as fh:
            json.dump(payload, fh)
    except Exception as exc:
        log.warning("could not save hub peer: %s", exc)


def _resolve_ip(host):
    if not host:
        return None
    if _is_ip(host):
        return host
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET)
    except Exception:
        return None
    if not infos:
        return None
    return infos[0][4][0]


def apply_gateway_server(ip):
    """Write server_address into the packet-forwarder conf and restart if needed."""
    if not ip:
        return False
    conf_path = gateway_conf_path()
    changed = False
    try:
        with open(conf_path, "r") as fh:
            conf = json.load(fh)
        current = conf.get("gateway_conf", {}).get("server_address")
        if current == ip:
            with _lock:
                _state["gateway_server"] = ip
                _state["gateway_updated"] = False
            return False
        conf.setdefault("gateway_conf", {})["server_address"] = ip
        tmp = conf_path + ".tmp"
        text = json.dumps(conf, indent=4) + "\n"
        # Prefer writing via sudo when the file is root-owned.
        try:
            with open(tmp, "w") as fh:
                fh.write(text)
            os.rename(tmp, conf_path)
        except (IOError, OSError):
            proc = subprocess.Popen(
                ["sudo", "-n", "tee", conf_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            _, err = proc.communicate(text.encode("utf-8"))
            if proc.returncode != 0:
                raise RuntimeError(err.decode("utf-8", "replace")[:200])
        changed = True
    except Exception as exc:
        log.warning("gateway conf update failed: %s", exc)
        with _lock:
            _state["note"] = "hub found but gateway conf update failed: %s" % exc
        return False

    with _lock:
        _state["gateway_server"] = ip
        _state["gateway_updated"] = changed

    if changed:
        try:
            subprocess.call(
                ["sudo", "-n", "systemctl", "try-restart", "rmlab-lora-pkt-fwd.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("gateway server_address -> %s (pkt-fwd restarted)", ip)
        except Exception as exc:
            log.warning("pkt-fwd restart failed: %s", exc)
    return changed


def remember(url, body=None):
    body = body or {}
    host = _host_from_url(url)
    ip = _resolve_ip(host) or (host if host and _is_ip(host) else None)
    hostname = str(body.get("hostname") or "").strip() or hub_hostname()
    with _lock:
        _state["url"] = url.rstrip("/")
        _state["ip"] = ip
        _state["hostname"] = hostname
        _state["reachable"] = True
        _state["last_seen_ts"] = time.time()
        where = host if not ip or ip == host else "%s (%s)" % (host, ip)
        _state["note"] = "found hub at %s" % where
    _save(hostname)
    if ip:
        apply_gateway_server(ip)
    return snapshot()


def accept_hub(url, gateway_ip=None):
    """Called when the hub pushes its address, or after a successful probe."""
    body = probe_url(url) or {"hostname": hub_hostname()}
    if gateway_ip and _is_ip(gateway_ip):
        # Prefer the concrete IP the hub told us about.
        host = _host_from_url(url)
        if host and not _is_ip(host):
            url = "http://%s:%s" % (gateway_ip, hub_port())
        remember(url, body)
        apply_gateway_server(gateway_ip)
        return snapshot()
    return remember(url, body)


def _candidate_urls():
    urls = []
    with _lock:
        if _state["url"]:
            urls.append(_state["url"])
    configured = configured_main_url()
    if configured:
        urls.append(configured)
    host = hub_hostname()
    if host:
        urls.append("http://%s.local:%s" % (host, hub_port()))
        urls.append("http://%s:%s" % (host, hub_port()))
    found = []
    for url in urls:
        if url and url not in found:
            found.append(url)
    return found


def _remember_if_alive(url):
    body = probe_url(url)
    if not body:
        return False
    remember(url, body)
    return True


def scan():
    with _lock:
        _state["scanning"] = True
        _state["checked"] = 0
        _state["note"] = "searching"
    try:
        _scan()
    finally:
        with _lock:
            _state["scanning"] = False
            _state["last_scan_ts"] = time.time()
    return snapshot()


def _scan():
    for url in _candidate_urls():
        with _lock:
            _state["checked"] += 1
        if _remember_if_alive(url):
            log.info("%s", snapshot()["note"])
            return snapshot()

    mine = _own_ips()
    port = hub_port()
    hosts = []
    for _iface, net_int, prefix, _local in _local_networks():
        for ip in _iter_hosts(net_int, prefix):
            if ip not in mine:
                hosts.append(ip)

    open_hosts = []
    # Modest parallelism for the Nano.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def check(ip):
        with _lock:
            _state["checked"] += 1
        if _port_open(ip, port):
            return ip
        return None

    workers = min(40, max(4, len(hosts) // 8 or 4))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(check, ip) for ip in hosts]
        for fut in as_completed(futures):
            ip = fut.result()
            if ip:
                open_hosts.append(ip)

    matches = []
    for ip in open_hosts:
        url = "http://%s:%s" % (ip, port)
        body = probe_url(url)
        if body:
            matches.append({"url": url, "body": body})
    if not matches:
        with _lock:
            _state["reachable"] = False
            _state["note"] = "no core hub on the local network"
        log.info("%s (%s hosts checked)", _state["note"], _state["checked"])
        return snapshot()

    chosen = matches[0]
    expected = env("RMLAB_HUB_SERVICE_HOST", hub_hostname()).strip().lower()
    if expected:
        exact = [
            m
            for m in matches
            if str(m["body"].get("hostname") or "").strip().lower() == expected
        ]
        if exact:
            chosen = exact[0]
    remember(chosen["url"], chosen["body"])
    if len(matches) > 1:
        with _lock:
            _state["note"] += " (%s cores answered)" % len(matches)
    log.info("%s", snapshot()["note"])
    return snapshot()


def watch_loop(stop_event=None):
    """Background discovery loop."""
    stop_event = stop_event or threading.Event()
    try:
        scan()
    except Exception:
        log.exception("initial hub search failed")
    while not stop_event.wait(20):
        try:
            url = main_url()
            if _remember_if_alive(url):
                continue
            with _lock:
                _state["reachable"] = False
                last_scan = _state.get("last_scan_ts") or 0
            if time.time() - last_scan < 60:
                with _lock:
                    _state["note"] = "saved hub %s is not answering" % (
                        _state.get("ip") or url
                    )
                continue
            scan()
        except Exception:
            log.exception("hub search failed")


def start_background():
    _load()
    # Apply any saved gateway IP early so pkt-fwd is correct even before
    # the first successful HTTP probe completes.
    with _lock:
        saved_ip = _state.get("ip")
    if saved_ip:
        apply_gateway_server(saved_ip)
    thread = threading.Thread(target=watch_loop, name="hub-discover", daemon=True)
    thread.start()
    return thread
