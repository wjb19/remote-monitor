#!/bin/bash
# Pin TK1 eth0 to 192.168.8.2/24 via the Jetson (192.168.8.1).
# Needs root once:  sudo ./setup-static-eth.sh
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi
SRC="$(cd "$(dirname "$0")" && pwd)/interfaces-eth0-static"
if [[ ! -f "$SRC" ]]; then
  echo "Missing $SRC" >&2
  exit 1
fi
cp -a /etc/network/interfaces "/etc/network/interfaces.bak.$(date +%Y%m%d%H%M%S)"
cp "$SRC" /etc/network/interfaces
# Apply without a full reboot so an open SSH session can move with us.
ifconfig eth0 192.168.8.2 netmask 255.255.255.0
route del default 2>/dev/null || true
route add default gw 192.168.8.1 eth0
# Kill a stale dhclient so it does not fight the static address.
if [[ -f /run/dhclient.eth0.pid ]]; then
  kill "$(cat /run/dhclient.eth0.pid)" 2>/dev/null || true
fi
pkill -f 'dhclient.*eth0' 2>/dev/null || true
echo "eth0 is now:"
ip -4 addr show eth0 2>/dev/null || ifconfig eth0
ip route 2>/dev/null || route -n
