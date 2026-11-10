# Remote Monitor Lab

Wildlife / IoT lab: main host (cameras, CV, ChirpStack, dashboard), Jetson Nano (LoRaWAN packet forwarder, GPS, headless agent), Jetson TK1 (motion agent), and LoRa-E5 edge nodes (USB bring-up → LoRaWAN).

## Docs

| Doc | Contents |
|-----|----------|
| [docs/jetson.md](docs/jetson.md) | Nano bring-up, Wi-Fi, SX1302, GPS, hub discovery |
| [docs/tk1.md](docs/tk1.md) | TK1 bring-up, static `192.168.8.2`, agent |
| [docs/lora-e5.md](docs/lora-e5.md) | E5 edge nodes, ADS1115 + MQ array, firmware / flash |
| [firmware/lora-e5-gas/README.md](firmware/lora-e5-gas/README.md) | Gas-node sketch wiring + flash notes |

## Hosts

Wi-Fi addresses are DHCP and move. The hub and Jetson discover each other on the LAN. The Nano↔TK1 link is a fixed point-to-point Ethernet network.

| Role | How to reach |
|------|----------------|
| Main (hub) | this host (`bill-GB-BXBT-1900`), API / dashboard `:8080` |
| Jetson Nano | SSH alias `jetson` (`bill-desktop` / DHCP lease, e.g. `.29`), agent `:8090` |
| Jetson TK1 | `192.168.8.2` via Nano `192.168.8.1` (SSH alias `tk1`, ProxyJump) |
| LoRa-E5 (USB) | on the Nano: CP2102N → `/dev/ttyUSB0` @ 9600 |

## Quick start (main)

```bash
# One-time: Docker (needs sudo)
./scripts/install-docker.sh
newgrp docker
docker compose -f deploy/main/docker-compose.yml up -d

# Core API + cameras + dashboard
./scripts/start-core.sh
```

- Dashboard / API: `http://<hub-lan-ip>:8080` (or `http://127.0.0.1:8080` on the hub)
- ChirpStack UI: http://127.0.0.1:8081 (admin / admin)
- MQTT: `127.0.0.1:1883` on the hub
- Camera ring data: `data/cameras/<id>/ring/`

### Dashboard tabs

- **Time series** — all metrics; single chart or configurable matrix of every series
- **Cameras** — hub feeds + edge motion windows
- **Jetson** — reachability, POM voltage, ambient board temp (AO-therm), LoRa-E5
- **TK1** — supply, ambient board temp (`Tboard_tegra`), motion
- **Map & GPS** — OpenStreetMap centered on LoRa gateway GPS when available; map features
- **Alerts & config** — voltage threshold, notifications stub, motion window

## Quick start (Jetson / TK1)

```bash
./scripts/deploy-jetson.sh
./scripts/deploy-tk1.sh
```

Nano `eth0` is `192.168.8.1/24` and can DHCP-reserve the TK1 at `.2` (`rmlab-tk1-dhcp`). The TK1 agent learns the hub URL from the Jetson (`hub_from_jetson`) instead of a hard-coded Wi-Fi address.

## LoRa-E5 gas node

Stock E5 AT firmware cannot talk to the ADS1115. Custom firmware is in `firmware/lora-e5-gas/` (MQ-2 / MQ-8 / MQ-135 / MQ-5 on ADS1115 A0–A3). Build with PlatformIO; flash with ST-Link:

```bash
./scripts/flash-lora-e5-gas.sh
```

See [docs/lora-e5.md](docs/lora-e5.md) for the full develop / flash / payload / agent flow.

## Layout

- `services/core` — FastAPI API, telemetry store, ChirpStack ingest, dashboard static files
- `services/jetson_agent` — Nano agent (voltage, thermal, motion, LoRa-E5 USB, hub discover)
- `services/tk1_agent` — TK1 motion / thermal / supply agent (Python 2.7)
- `firmware/lora-e5-gas` — PlatformIO sketch for the ADS1115 + MQ array
- `web/public` — dashboard
- `deploy/main` — Docker Compose (Mosquitto, ChirpStack, Postgres, Redis)
- `deploy/jetson` — systemd units, gateway conf, direct-link / TK1 DHCP, boot images
- `deploy/tk1` — agent launcher, static eth setup
- `docs/` — bring-up and edge-node notes
- `configs/` — shared example configs
