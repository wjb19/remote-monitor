#!/usr/bin/env bash
# Install Docker Engine + Compose plugin (requires sudo password once).
set -euo pipefail

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "Docker already installed: $(docker --version)"
  docker compose version
  exit 0
fi

sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker
echo
echo "Docker installed. Log out/in (or: newgrp docker) so group membership applies."
docker --version || true
