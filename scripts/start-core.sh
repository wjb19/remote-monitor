#!/usr/bin/env bash
# Run the core API + status dashboard (no Docker required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/packages:$ROOT/services${PYTHONPATH:+:$PYTHONPATH}"
export RMLAB_PORT="${RMLAB_PORT:-8080}"
export RMLAB_DATA_ROOT="${RMLAB_DATA_ROOT:-$ROOT/data}"
exec .venv/bin/uvicorn services.core.app.main:app --host 0.0.0.0 --port "$RMLAB_PORT"
