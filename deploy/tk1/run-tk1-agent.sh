#!/bin/bash
# User-level launcher. Ubuntu 14.04 on this board has no user systemd.
ROOT="${RMLAB_ROOT:-$HOME/rmlab}"
export PYTHONPATH="$ROOT/vendor/usr/lib/python2.7/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
export RMLAB_MAIN_URL="${RMLAB_MAIN_URL:-http://192.168.1.14:8080}"
export RMLAB_CAMERA_ID="${RMLAB_CAMERA_ID:-tk1-cam0}"
export RMLAB_PORT="${RMLAB_PORT:-8090}"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if ip addr show eth0 2>/dev/null | grep -q 'inet '; then
    break
  fi
  sleep 2
done
mkdir -p "$ROOT/log"
exec /usr/bin/python "$ROOT/services/tk1_agent/main.py" >>"$ROOT/log/agent.log" 2>&1
