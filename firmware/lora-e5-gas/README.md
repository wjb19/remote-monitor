# LoRa-E5 gas node (ADS1115 + MQ array)

Stock Seeed AT firmware on the LoRa-E5 has **no I2C/ADC commands**. This sketch replaces it so the Jetson can poll the ADS1115 over USB UART (`/dev/ttyUSB0`).

Full lab notes (payload, agent metrics, roadmap): [docs/lora-e5.md](../../docs/lora-e5.md).

## Wiring

| ADS1115 | Sensor | Notes |
|---------|--------|-------|
| A0 | MQ-2 | smoke / LPG / CO family |
| A1 | MQ-8 | hydrogen |
| A2 | MQ-135 | air quality / VOCs |
| A3 | MQ-5 | natural gas / LPG |

- ADS1115 on the E5 Grove/I2C bus: **SDA=PA15**, **SCL=PB15**, address **0x48**
- MQ heaters usually want **5 V**; match ADS1115 VDD / use dividers if the ADC is on 3.3 V

## Build / flash (SWD)

```bash
export PATH="$HOME/.platformio/penv/bin:$PATH"
pio run                          # → .pio/build/lora_e5_mini/firmware.bin
# from repo root, with ST-Link attached:
../../scripts/flash-lora-e5-gas.sh
```

Or manually:

1. ST-Link → SWDIO / SWCLK / GND / 3V3 (NRST optional)
2. Unlock once if needed: `openocd -f interface/stlink.cfg -f target/stm32wlx.cfg -c "init; reset halt; stm32wlx unlock 0; reset run; exit"`
3. `pio run -t upload`
4. USB serial: `AT` → `+AT: OK`, `AT+GAS` → millivolt readings

Flashing **erases** the factory LoRaWAN AT image. LoRaWAN uplink of `gas_ads1115_v1` can be added later; USB poll is first.

## Jetson poller

After flash, restart the Jetson agent. It detects `AT+GAS`, switches to profile `gas_ads1115_v1`, and publishes `node.mq2_mv` … `node.mq5_mv` (and `lora_e5.mq*_mv`).
