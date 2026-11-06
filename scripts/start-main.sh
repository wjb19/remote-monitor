#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found. Run: ./scripts/install-docker.sh" >&2
  exit 1
fi

echo "Starting ChirpStack / Mosquitto stack..."
docker compose -f deploy/main/docker-compose.yml up -d

echo "Starting core API on :8080..."
export PYTHONPATH="$ROOT/packages${PYTHONPATH:+:$PYTHONPATH}"
export RMLAB_PORT=8080
exec .venv/bin/uvicorn services.core.app.main:app --host 0.0.0.0 --port 8080 --reload
