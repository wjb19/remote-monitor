"""Find the Jetson agent when DHCP hands it a new address.

The hub scans only IPv4 networks on its own interfaces and accepts a host
when GET /health reports service "jetson-agent" and the expected hostname.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger("rmlab.discover")

_SKIP_IFACE_PREFIXES = ("lo", "docker", "br-", "veth", "tun", "tap", "wg", "virbr")


def _lan_addrs() -> list[tuple[str, str, int]]:
    """Return (iface, ipv4, prefixlen) for non-virtual interfaces."""
    try:
        raw = subprocess.check_output(["ip", "-4", "-j", "addr"], text=True)
        ifaces = json.loads(raw)
    except Exception as exc:
        log.warning("could not list local addresses: %s", exc)
        return []
    found: list[tuple[str, str, int]] = []
    for iface in ifaces:
        name = str(iface.get("ifname") or "")
        if name == "lo" or name.startswith(_SKIP_IFACE_PREFIXES):
            continue
        for addr in iface.get("addr_info") or []:
            if addr.get("family") != "inet":
                continue
            local = addr.get("local")
            prefix = addr.get("prefixlen")
            if not local or prefix is None:
                continue
            ip = ipaddress.ip_address(local)
            if ip.is_loopback or ip.is_link_local or ip.is_multicast:
                continue
            found.append((name, str(local), int(prefix)))
    return found


def primary_lan_ipv4(prefer_iface: str | None = None) -> str | None:
    """Best IPv4 to advertise to edge nodes (Wi-Fi/LAN, not docker)."""
    addrs = _lan_addrs()
    if not addrs:
        return None
    if prefer_iface:
        for name, local, _prefix in addrs:
            if name == prefer_iface:
                return local
    # Prefer wireless, then anything else.
    for name, local, _prefix in addrs:
        if name.startswith(("wl", "wlan")):
            return local
    return addrs[0][1]


def local_scan_networks() -> list[ipaddress.IPv4Network]:
    """IPv4 subnets attached to this host, narrowed so a scan stays local."""
    nets: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()
    for _name, local, prefix in _lan_addrs():
        if prefix < 24:
            prefix = 24
        # A field router is typically a /24. Larger masks are cut down to
        # the /24 that contains this host so we do not sweep a whole /16.
        net = ipaddress.ip_network(f"{local}/{prefix}", strict=False)
        if str(net) in seen or net.num_addresses > 1024:
            continue
        seen.add(str(net))
        nets.append(net)
    return nets


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _own_ips() -> set[str]:
    ips: set[str] = set()
    try:
        raw = subprocess.check_output(["ip", "-4", "-j", "addr"], text=True)
        ifaces = json.loads(raw)
    except Exception:
        return ips
    for iface in ifaces:
        for addr in iface.get("addr_info") or []:
            local = addr.get("local")
            if local:
                ips.add(str(local))
    return ips


def _resolve_ip(host: str) -> str | None:
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
    return str(infos[0][4][0])


def _host_from_url(url: str) -> str | None:
    try:
        return urlparse(url).hostname
    except Exception:
        return None


def _trust_host_key_if_known(host: str) -> None:
    """Add host to known_hosts only if its key fingerprint is already trusted."""
    if not host:
        return
    try:
        scan = subprocess.check_output(
            ["ssh-keyscan", "-T", "5", "-t", "ed25519,ecdsa", host],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return
    known_path = Path.home() / ".ssh" / "known_hosts"
    if not known_path.is_file():
        return
    try:
        known_fps = subprocess.check_output(
            ["ssh-keygen", "-l", "-f", str(known_path)],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return
    fps = {line.split()[1] for line in known_fps.splitlines() if len(line.split()) >= 2}
    # Already present?
    try:
        subprocess.check_call(
            ["ssh-keygen", "-F", host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    except Exception:
        pass
    for line in scan.splitlines():
        if not line or line.startswith("#"):
            continue
        try:
            fp = subprocess.check_output(
                ["ssh-keygen", "-lf", "-"],
                input=line + "\n",
                text=True,
            ).split()[1]
        except Exception:
            continue
        if fp in fps:
            with known_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            log.info("trusted SSH host key for %s", host)
            return


def update_ssh_hostname(alias: str, hostname: str, user: str | None = None) -> None:
    """Rewrite HostName for an OpenSSH config alias (creates the block if missing)."""
    if not alias or not hostname:
        return
    path = Path.home() / ".ssh" / "config"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        line = lines[i]
        if line.strip().lower() == f"host {alias.lower()}" or line.strip().lower().startswith(
            f"host {alias.lower()} "
        ):
            out.append(line)
            i += 1
            saw_hostname = False
            while i < len(lines) and not lines[i].lower().startswith("host "):
                raw = lines[i]
                if raw.strip().lower().startswith("hostname "):
                    out.append(f"  HostName {hostname}")
                    saw_hostname = True
                    replaced = True
                else:
                    out.append(raw)
                i += 1
            if not saw_hostname:
                out.append(f"  HostName {hostname}")
                replaced = True
            continue
        out.append(line)
        i += 1
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"Host {alias}")
        out.append(f"  HostName {hostname}")
        if user:
            out.append(f"  User {user}")
        replaced = True
    if replaced:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass


class JetsonLocator:
    def __init__(self, settings: Any, data_root: Path):
        ssh = str(settings.jetson_ssh)
        self.user = ssh.split("@", 1)[0] if "@" in ssh else "bill"
        self.expected_hostname = str(settings.jetson_hostname).strip().lower()
        self.port = int(urlparse(settings.jetson_url).port or 8090)
        self.configured_url = str(settings.jetson_url).rstrip("/")
        self.url = self.configured_url
        self.ssh = ssh
        self.path = Path(data_root) / "jetson_peer.json"
        self.ssh_alias = str(getattr(settings, "jetson_ssh_alias", "jetson") or "jetson")
        self.hub_port = int(getattr(settings, "port", 8080) or 8080)
        self.last_seen_ts: float | None = None
        self.last_scan_ts: float | None = None
        self.scanning = False
        self.reachable = False
        self.checked = 0
        self.note = "not searched yet"
        self._lock = asyncio.Lock()
        self._last_notified: str | None = None
        self._load()
        # Seed SSH from the last saved peer IP when available (mDNS is flaky).
        saved_host = _host_from_url(self.url) if self.url else None
        seed = _resolve_ip(saved_host) if saved_host else None
        if seed:
            try:
                update_ssh_hostname(self.ssh_alias, seed, user=self.user)
            except Exception:
                log.exception("could not seed SSH HostName for %s", self.ssh_alias)

    def snapshot(self) -> dict:
        return {
            "ok": self.reachable,
            "url": self.url,
            "ssh": self.ssh,
            "hostname": self.expected_hostname,
            "configured_url": self.configured_url,
            "last_seen_ts": self.last_seen_ts,
            "last_scan_ts": self.last_scan_ts,
            "scanning": self.scanning,
            "checked": self.checked,
            "note": self.note,
        }

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if saved.get("url"):
            self.url = str(saved["url"]).rstrip("/")
        if saved.get("ssh"):
            self.ssh = str(saved["ssh"])
        if saved.get("seen_ts"):
            self.last_seen_ts = float(saved["seen_ts"])
            self.note = "last known address %s" % _host_from_url(self.url)

    def _save(self, hostname: str) -> None:
        host = _host_from_url(self.url) or ""
        ip = _resolve_ip(host)
        # Peer file + SSH alias track the concrete IP so DHCP moves stay usable
        # even when mDNS is down.
        self.ssh = "%s@%s" % (self.user, ip or host)
        self.last_seen_ts = time.time()
        where = host if not ip or ip == host else "%s (%s)" % (host, ip)
        self.note = "found %s at %s" % (hostname or self.expected_hostname, where)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "url": self.url,
                    "ssh": self.ssh,
                    "hostname": hostname,
                    "ip": ip,
                    "seen_ts": self.last_seen_ts,
                }
            ),
            encoding="utf-8",
        )
        try:
            update_ssh_hostname(self.ssh_alias, ip or host, user=self.user)
        except Exception:
            log.exception("could not update SSH HostName for %s", self.ssh_alias)

        # Also trust this IP in known_hosts when the key matches a previously
        # accepted Jetson fingerprint (DHCP rename of the same board).
        if ip:
            try:
                _trust_host_key_if_known(ip)
            except Exception:
                log.debug("known_hosts update skipped for %s", ip, exc_info=True)

    async def notify_hub_address(self) -> None:
        """Push this hub's current LAN URL to the Jetson so it can rebind."""
        hub_ip = primary_lan_ipv4()
        if not hub_ip or not self.url:
            return
        payload = {
            "url": f"http://{hub_ip}:{self.hub_port}",
            "ip": hub_ip,
            "gateway_server": hub_ip,
            "hostname": socket.gethostname(),
        }
        key = f"{self.url}|{hub_ip}"
        if key == self._last_notified:
            return
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.post(
                    self.url.rstrip("/") + "/api/hub", json=payload
                )
            if response.status_code < 400:
                self._last_notified = key
                log.info("told jetson hub is %s", hub_ip)
        except Exception as exc:
            log.debug("hub notify failed: %s", exc)

    def _accepts(self, body: Any) -> bool:
        if not isinstance(body, dict) or body.get("service") != "jetson-agent":
            return False
        host = str(body.get("hostname") or "").strip().lower()
        if self.expected_hostname and host and host != self.expected_hostname:
            return False
        return True

    async def probe_url(self, url: str, timeout: float = 1.5) -> dict | None:
        host = _host_from_url(url)
        if host and not _is_ip(host):
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(socket.getaddrinfo, host, self.port, socket.AF_INET),
                    timeout=1.0,
                )
            except Exception:
                return None
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url.rstrip("/") + "/health")
            body = response.json()
        except Exception:
            return None
        if response.status_code != 200 or not self._accepts(body):
            return None
        return body

    async def _remember_if_alive(self, url: str) -> bool:
        body = await self.probe_url(url)
        if not body:
            return False
        self.url = url.rstrip("/")
        self.reachable = True
        self._save(str(body.get("hostname") or ""))
        await self.notify_hub_address()
        return True

    async def _port_open(self, ip: str) -> bool:
        try:
            conn = asyncio.open_connection(ip, self.port)
            _reader, writer = await asyncio.wait_for(conn, timeout=0.8)
        except Exception:
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True

    def _candidate_urls(self) -> list[str]:
        urls = [self.url, self.configured_url]
        if self.expected_hostname:
            urls.append("http://%s.local:%s" % (self.expected_hostname, self.port))
        found: list[str] = []
        for url in urls:
            if url and url not in found:
                found.append(url)
        return found

    async def scan(self) -> dict:
        async with self._lock:
            self.scanning = True
            self.checked = 0
            try:
                await self._scan()
            finally:
                self.scanning = False
                self.last_scan_ts = time.time()
            return self.snapshot()

    async def _scan(self) -> dict:
        self.note = "searching"
        for url in self._candidate_urls():
            self.checked += 1
            if await self._remember_if_alive(url):
                log.info("%s", self.note)
                return self.snapshot()

        mine = _own_ips()
        hosts: list[str] = []
        for net in local_scan_networks():
            for ip in net.hosts():
                text = str(ip)
                if text not in mine:
                    hosts.append(text)
        sem = asyncio.Semaphore(80)

        async def check(ip: str) -> str | None:
            async with sem:
                self.checked += 1
                if not await self._port_open(ip):
                    return None
            return ip

        open_hosts = [ip for ip in await asyncio.gather(*(check(ip) for ip in hosts)) if ip]
        matches: list[dict] = []
        for ip in open_hosts:
            url = "http://%s:%s" % (ip, self.port)
            body = await self.probe_url(url)
            if body:
                matches.append({"url": url, "hostname": str(body.get("hostname") or "")})
        if not matches:
            self.reachable = False
            self.note = "no jetson-agent named %s on the local network" % (
                self.expected_hostname or "jetson-agent"
            )
            log.info("%s (%s hosts checked)", self.note, self.checked)
            return self.snapshot()
        chosen = matches[0]
        exact = [m for m in matches if m["hostname"].lower() == self.expected_hostname]
        if exact:
            chosen = exact[0]
        self.url = chosen["url"]
        self.reachable = True
        self._save(chosen["hostname"])
        if len(matches) > 1:
            self.note += " (%s agents answered)" % len(matches)
        log.info("%s", self.note)
        await self.notify_hub_address()
        return self.snapshot()

    async def watch(self) -> None:
        try:
            await self.scan()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("initial jetson search failed")
        while True:
            try:
                await asyncio.sleep(20)
                if await self._remember_if_alive(self.url):
                    continue
                self.reachable = False
                if self.last_scan_ts and (time.time() - self.last_scan_ts) < 60:
                    self.note = "saved address %s is not answering" % (
                        _host_from_url(self.url) or self.url
                    )
                    continue
                await self.scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("jetson search failed")
