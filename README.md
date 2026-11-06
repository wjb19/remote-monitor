# Remote Monitor Lab

Wildlife / IoT lab: main host (cameras, CV, ChirpStack, dashboard), Jetson Nano (LoRaWAN packet forwarder, GPS, headless agent), and Jetson TK1 (motion and supply agent).

How the Nano and the TK1 were brought up is in [docs/jetson.md](docs/jetson.md) and [docs/tk1.md](docs/tk1.md).

## Hosts

| Role | Address |
|------|---------|
| Main | `192.168.1.14` (`bill-GB-BXBT-1900`) |
| Jetson Nano | `192.168.1.19` (`bill-desktop`, SSH alias `jetson`) |
| Jetson TK1 | `169.254.2.224` on the Nano's Ethernet (SSH alias `tk1`) |

## Quick start (main)

```bash
# One-time: Docker (needs sudo)
./scripts/install-docker.sh
newgrp docker
docker compose -f deploy/main/docker-compose.yml up -d

# Core API + cameras + dashboard
./scripts/start-core.sh
```

- Dashboard / API: http://192.168.1.14:8080
- ChirpStack UI: http://192.168.1.14:8081 (admin / admin)
- MQTT: `192.168.1.14:1883`
- Camera ring data: `data/cameras/<id>/ring/`


## Quick start (Jetson)

```bash
./scripts/deploy-jetson.sh
```

## Layout

- `services/core` — FastAPI API + status dashboard
- `services/jetson_agent` — headless edge agent
- `deploy/main` — Docker Compose (Mosquitto, ChirpStack, Postgres, Redis)
- `deploy/jetson` — systemd units, gateway conf, and `boot/` images from the Nano
- `docs/` — Nano and TK1 bring-up notes
- `configs/` — shared example configs
