#!/usr/bin/env bash
# Unlock + flash custom rmlab-gas firmware onto the Seeed LoRa-E5 (STM32WLE5JC).
# Requires an ST-Link on SWD (SWDIO / SWCLK / GND / 3V3, NRST optional).
# USB CP2102N alone cannot program the chip.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FW_DIR="$ROOT/firmware/lora-e5-gas"
BIN="$FW_DIR/.pio/build/lora_e5_mini/firmware.bin"
PATH="${HOME}/.platformio/penv/bin:${PATH}"

need_stlink() {
  if lsusb 2>/dev/null | grep -qiE 'ST(Link|.-Link)|0483:374[abe]|0483:375'; then
    return 0
  fi
  return 1
}

echo "Looking for ST-Link…"
if ! need_stlink; then
  cat >&2 <<'EOF'
No ST-Link USB device found on this machine.

Wire ST-Link → LoRa-E5 SWD:
  SWDIO, SWCLK, GND, 3V3  (and NRST if your probe has it)
Leave the CP2102N USB cable plugged in for serial verify afterwards.

Then re-run:  ./scripts/flash-lora-e5-gas.sh
EOF
  exit 2
fi

echo "Building firmware…"
( cd "$FW_DIR" && pio run )

echo "Unlocking STM32WL readout protection (safe if already unlocked)…"
openocd -f interface/stlink.cfg -f target/stm32wlx.cfg \
  -c "init; reset halt; stm32wlx unlock 0; reset run; exit" || true

echo "Flashing $BIN …"
( cd "$FW_DIR" && pio run -t upload )

echo "Flash done. Waiting for USB serial…"
sleep 2
echo "If the E5 is on the Jetson, restart the agent there and check AT+GAS."
echo "  ssh jetson 'sudo systemctl restart rmlab-jetson-agent'"
echo "  curl -s http://<jetson>:8090/api/lora_e5 | python3 -m json.tool | head"
