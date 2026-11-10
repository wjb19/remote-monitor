#!/usr/bin/env bash
# Sync agent + systemd units to the Jetson and enable the agent service.
# Packet forwarder unit is installed but left disabled until SX1302 bring-up succeeds.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -z "${RMLAB_JETSON:-}" && -f "$ROOT/data/jetson_peer.json" ]]; then
  RMLAB_JETSON="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["ssh"])' "$ROOT/data/jetson_peer.json")"
fi
JETSON="${RMLAB_JETSON:-bill@192.168.1.19}"
JETSON_HOST="${JETSON#*@}"

# A new DHCP address presents the same host key under a new name. Accept it
# only when that key is already trusted for this Jetson.
if ! ssh-keygen -F "$JETSON_HOST" >/dev/null; then
  scan="$(ssh-keyscan -T 5 -t ed25519,ecdsa "$JETSON_HOST" 2>/dev/null || true)"
  if [[ -n "$scan" ]]; then
    known_fps="$(ssh-keygen -l -f "$HOME/.ssh/known_hosts" 2>/dev/null | awk '{print $2}')"
    while IFS= read -r line; do
      [[ -z "$line" || "$line" == \#* ]] && continue
      fp="$(printf '%s\n' "$line" | ssh-keygen -lf - | awk '{print $2}')"
      if printf '%s\n' "$known_fps" | grep -qx "$fp"; then
        printf '%s\n' "$line" >> "$HOME/.ssh/known_hosts"
      fi
    done <<< "$scan"
  fi
fi

echo "Deploying to $JETSON ..."
rsync -a --delete \
  --exclude '.venv' --exclude 'data' --exclude '.git' \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$ROOT/" "$JETSON:~/rmlab-src/"

ssh -o BatchMode=yes "$JETSON" 'bash -s' <<'EOF'
set -e
sudo mkdir -p /opt/rmlab /opt/rmlab/data /etc/rmlab
sudo chown bill:bill /opt/rmlab/data
sudo rsync -a --delete \
  --exclude '.venv' --exclude 'data' --exclude '.git' \
  "$HOME/rmlab-src/" /opt/rmlab/
# Keep a previously discovered hub IP across deploys.
PREV_SERVER=""
if [[ -f /etc/rmlab/global_conf.json ]]; then
  PREV_SERVER="$(python3 -c 'import json; print(json.load(open("/etc/rmlab/global_conf.json")).get("gateway_conf",{}).get("server_address",""))' 2>/dev/null || true)"
fi
sudo cp /opt/rmlab/deploy/jetson/global_conf.json /etc/rmlab/global_conf.json
if [[ -n "$PREV_SERVER" && "$PREV_SERVER" != "127.0.0.1" ]]; then
  # Keep the prior address only if the hub still answers there; otherwise the
  # agent will rediscover after restart.
  if python3 -c "import urllib.request; urllib.request.urlopen('http://$PREV_SERVER:8080/health', timeout=2)" >/dev/null 2>&1; then
    sudo python3 - <<PY
import json
path = "/etc/rmlab/global_conf.json"
with open(path) as fh:
    conf = json.load(fh)
conf.setdefault("gateway_conf", {})["server_address"] = "$PREV_SERVER"
with open(path, "w") as fh:
    json.dump(conf, fh, indent=4)
    fh.write("\n")
print("preserved gateway server_address", "$PREV_SERVER")
PY
  else
    echo "dropping stale gateway server_address $PREV_SERVER"
  fi
fi
sudo cp /opt/rmlab/deploy/jetson/direct-link.sh /usr/local/sbin/rmlab-direct-link.sh
sudo chmod 755 /usr/local/sbin/rmlab-direct-link.sh
sudo cp /opt/rmlab/deploy/jetson/rmlab-direct-link.service /etc/systemd/system/
sudo cp /opt/rmlab/deploy/jetson/dnsmasq-tk1.conf /etc/rmlab/dnsmasq-tk1.conf
sudo cp /opt/rmlab/deploy/jetson/rmlab-tk1-dhcp.service /etc/systemd/system/
sudo cp /opt/rmlab/deploy/jetson/rmlab-jetson-agent.service /etc/systemd/system/
sudo cp /opt/rmlab/deploy/jetson/rmlab-lora-pkt-fwd.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rmlab-direct-link.service rmlab-tk1-dhcp.service
sudo systemctl restart rmlab-direct-link.service
sudo systemctl restart rmlab-tk1-dhcp.service
sudo systemctl enable rmlab-jetson-agent.service
sudo systemctl restart rmlab-jetson-agent.service
sudo systemctl enable rmlab-lora-pkt-fwd.service
sudo systemctl restart rmlab-lora-pkt-fwd.service
sleep 2
echo "=== health ==="
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8090/health').read().decode())"
echo
systemctl --no-pager --full status rmlab-jetson-agent.service | head -15
echo "=== direct-link / tk1-dhcp ==="
systemctl is-active rmlab-direct-link rmlab-tk1-dhcp
ip -4 -br addr show eth0
EOF

echo
echo "Jetson agent: http://${JETSON_HOST}:8090/health"
