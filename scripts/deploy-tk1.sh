#!/usr/bin/env bash
# Install the TK1 motion/audio agent into the ubuntu account and start it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${RMLAB_TK1:-tk1}"

echo "Deploying to $TARGET ..."
ssh -o BatchMode=yes "$TARGET" 'mkdir -p "$HOME/rmlab-src"'
rsync -a \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$ROOT/services/tk1_agent" "$ROOT/deploy/tk1" \
  "$TARGET:~/rmlab-src/"

ssh -o BatchMode=yes "$TARGET" 'bash -s' <<'EOF'
set -e
mkdir -p "$HOME/rmlab/services" "$HOME/rmlab/deploy" "$HOME/rmlab/vendor" "$HOME/rmlab/log"
rsync -a "$HOME/rmlab-src/tk1_agent/" "$HOME/rmlab/services/tk1_agent/"
rsync -a "$HOME/rmlab-src/tk1/" "$HOME/rmlab/deploy/tk1/"
chmod 755 "$HOME/rmlab/deploy/tk1/run-tk1-agent.sh" "$HOME/rmlab/deploy/tk1/update-os.sh" "$HOME/rmlab/deploy/tk1/enable-supply-i2c.sh"
export PYTHONPATH="$HOME/rmlab/vendor/usr/lib/python2.7/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
if ! python -c "import cv2,numpy" >/dev/null 2>&1; then
  VENDOR="$HOME/rmlab/vendor"
  TMP="$HOME/rmlab/vendor/debs"
  mkdir -p "$TMP"
  DEB="$TMP/python-numpy_1.8.1-1ubuntu1_armhf.deb"
  if [[ ! -f "$DEB" ]]; then
    wget -O "$DEB" \
      "http://old-releases.ubuntu.com/ubuntu-ports/pool/main/p/python-numpy/python-numpy_1.8.1-1ubuntu1_armhf.deb" \
      || wget -O "$DEB" \
      "http://ports.ubuntu.com/ubuntu-ports/pool/main/p/python-numpy/python-numpy_1.8.1-1ubuntu1_armhf.deb"
  fi
  dpkg-deb -x "$DEB" "$VENDOR"
  dpkg-deb -x /var/opencv4tegra-repo/libopencv4tegra-python_2.4.10.1_armhf.deb "$VENDOR"
fi
python -c "import cv2,numpy; print('cv2', cv2.__version__, 'numpy', numpy.__version__)"
# Replace any previous agent, then start.
if [[ -f "$HOME/rmlab/agent.pid" ]]; then
  old=$(cat "$HOME/rmlab/agent.pid" || true)
  if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
    kill "$old" || true
    sleep 1
  fi
fi
pkill -f "/rmlab/services/tk1_agent/main.py" || true
sleep 1
nohup "$HOME/rmlab/deploy/tk1/run-tk1-agent.sh" >/dev/null 2>&1 &
echo $! > "$HOME/rmlab/agent.pid"
# Start on boot without root. Upstart would need sudo.
(crontab -l 2>/dev/null | grep -v "run-tk1-agent.sh" || true
 echo "@reboot $HOME/rmlab/deploy/tk1/run-tk1-agent.sh") | crontab -
sleep 2
python - <<'PY'
import urllib2
print(urllib2.urlopen("http://127.0.0.1:8090/health", timeout=5).read())
PY
EOF
