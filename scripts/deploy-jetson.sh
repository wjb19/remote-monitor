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
sudo mkdir -p /opt/rmlab /etc/rmlab
sudo rsync -a --delete \
  --exclude '.venv' --exclude 'data' --exclude '.git' \
  "$HOME/rmlab-src/" /opt/rmlab/
sudo cp /opt/rmlab/deploy/jetson/global_conf.json /etc/rmlab/global_conf.json
sudo cp /opt/rmlab/deploy/jetson/rmlab-jetson-agent.service /etc/systemd/system/
sudo cp /opt/rmlab/deploy/jetson/rmlab-lora-pkt-fwd.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rmlab-jetson-agent.service
sudo systemctl restart rmlab-jetson-agent.service
sudo systemctl enable rmlab-lora-pkt-fwd.service
sudo systemctl restart rmlab-lora-pkt-fwd.service
sleep 1
echo "=== health ==="
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8090/health').read().decode())"
echo
systemctl --no-pager --full status rmlab-jetson-agent.service | head -25
EOF

echo
echo "Jetson agent: http://${JETSON_HOST}:8090/health"
