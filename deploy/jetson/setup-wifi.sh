#!/usr/bin/env bash
# Restore the Jetson USB Wi-Fi uplink and the direct Ethernet address.
# Run from the hub. The Wi-Fi passphrase is read from this machine's
# saved NETGEAR41 profile and is not written into the repo.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WIFI="$ROOT/deploy/jetson/wifi"
if [[ -z "${RMLAB_JETSON:-}" && -f "$ROOT/data/jetson_peer.json" ]]; then
  RMLAB_JETSON="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["ssh"])' "$ROOT/data/jetson_peer.json")"
fi
JETSON="${RMLAB_JETSON:-bill@192.168.1.19}"

eval "$(grep -E '^(url|branch|commit|module)=' "$WIFI/driver.txt" | sed 's/^/DRIVER_/')"

PSK="$(nmcli -s -g 802-11-wireless-security.psk connection show NETGEAR41)"
if [[ -z "$PSK" ]]; then
  echo "No NETGEAR41 passphrase saved on this machine." >&2
  exit 1
fi

ssh -o BatchMode=yes "$JETSON" 'mkdir -p /tmp/rmlab-wifi'
rsync -a \
  "$WIFI/usb-modeswitch.conf" \
  "$WIFI/modeswitch.rules" \
  "$WIFI/modprobe.conf" \
  "$WIFI/modules-load.conf" \
  "$WIFI/eth0.nmconnection" \
  "$WIFI/driver.txt" \
  "$JETSON:/tmp/rmlab-wifi/"
printf '%s\n' "$PSK" | ssh -o BatchMode=yes "$JETSON" 'umask 077; cat > /tmp/rmlab-wifi/psk'

ssh -o BatchMode=yes "$JETSON" 'bash -s' <<EOF
set -euo pipefail
PSK="\$(cat /tmp/rmlab-wifi/psk)"
rm -f /tmp/rmlab-wifi/psk
sudo mkdir -p /etc/usb_modeswitch.d /etc/modprobe.d /etc/modules-load.d /home/bill/src
sudo cp /tmp/rmlab-wifi/usb-modeswitch.conf /etc/usb_modeswitch.d/0bda:1a2b
sudo cp /tmp/rmlab-wifi/modeswitch.rules /etc/udev/rules.d/40-rmlab-rtl8852bu.rules
sudo cp /tmp/rmlab-wifi/modprobe.conf /etc/modprobe.d/rtl8852bu.conf
sudo cp /tmp/rmlab-wifi/modules-load.conf /etc/modules-load.d/8852bu.conf
if [[ ! -d /home/bill/src/rtl8852bu/.git ]]; then
  git clone --depth 1 --branch ${DRIVER_branch} ${DRIVER_url} /home/bill/src/rtl8852bu
fi
got="\$(git -C /home/bill/src/rtl8852bu rev-parse HEAD)"
if [[ "\$got" != "${DRIVER_commit}" ]]; then
  echo "rtl8852bu checkout is \$got, expected ${DRIVER_commit}" >&2
  exit 1
fi
if ! modinfo ${DRIVER_module} >/dev/null 2>&1; then
  make -C /home/bill/src/rtl8852bu -j4 ARCH=arm64 KSRC="/lib/modules/\$(uname -r)/build"
  sudo make -C /home/bill/src/rtl8852bu install
  sudo depmod -a
fi
sudo modprobe ${DRIVER_module} || true
sudo cp /tmp/rmlab-wifi/eth0.nmconnection "/etc/NetworkManager/system-connections/Wired connection 1"
sudo chmod 600 "/etc/NetworkManager/system-connections/Wired connection 1"
python3 - "\$PSK" <<'PY'
import pathlib, sys
psk = sys.argv[1]
path = pathlib.Path("/tmp/rmlab-wifi/NETGEAR41.nmconnection")
path.write_text("""[connection]
id=NETGEAR41
type=wifi
interface-name=wlan0

[wifi]
mode=infrastructure
ssid=NETGEAR41

[wifi-security]
key-mgmt=wpa-psk
psk=%s

[ipv4]
method=auto

[ipv6]
method=auto
""" % psk)
path.chmod(0o600)
PY
sudo cp /tmp/rmlab-wifi/NETGEAR41.nmconnection /etc/NetworkManager/system-connections/NETGEAR41
sudo chmod 600 /etc/NetworkManager/system-connections/NETGEAR41
rm -rf /tmp/rmlab-wifi
sudo nmcli connection reload
sudo nmcli connection up NETGEAR41 || true
sudo nmcli connection up "Wired connection 1" || true
ip -4 -br addr show wlan0 eth0
EOF
