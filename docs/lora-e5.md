# LoRa-E5 edge nodes

Lab edge nodes are Seeed **LoRa-E5** (STM32WLE5JC) modules. One is USB-attached to the Jetson Nano for bring-up (`/dev/ttyUSB0`, Silicon Labs CP2102N). Field nodes will use the same payload over LoRaWAN through the Waveshare SX1302 gateway.

## Hardware target (gas array)

| Piece | Detail |
|-------|--------|
| Radio / MCU | LoRa-E5 on USB-UART (CP2102N) while developing |
| ADC | **ADS1115** @ `0x48` on the E5 I2C bus (SDA=PA15, SCL=PB15) |
| A0 | MQ-2 (smoke / LPG / CO family) |
| A1 | MQ-8 (hydrogen) |
| A2 | MQ-135 (air quality / VOCs) |
| A3 | MQ-5 (natural gas / LPG) |

MQ heater rails are typically **5 V**. Match ADS1115 supply / use dividers if the ADC runs at 3.3 V.

A second, similar array may later attach directly to the Jetson I2C header; keep the same channel map.

## Why custom firmware

Stock Seeed AT firmware (e.g. `+VER: 4.0.11`) exposes `AT`, `AT+TEMP`, `AT+VDD`, LoRaWAN join/TX, etc. It does **not** expose I2C or ADC (`AT+I2C` / `AT+ADC` → `ERROR(-10)`). The ADS1115 is unreachable until the module runs our sketch.

Firmware lives in [`firmware/lora-e5-gas/`](../firmware/lora-e5-gas/). It replaces the factory AT image and speaks a small AT dialect over USB:

| Command | Response |
|---------|----------|
| `AT` | `+AT: OK` |
| `AT+VER` | `+VER: rmlab-gas 0.1.0` |
| `AT+TEMP` | `+TEMP: …` (placeholder until die-temp is wired) |
| `AT+VDD` | `+VDD: …V` |
| `AT+GAS` | `+GAS: mq2_mv=…,mq8_mv=…,mq135_mv=…,mq5_mv=…` |

Flashing **erases** factory LoRaWAN AT. USB poll is the first milestone; LoRaWAN uplink of the same payload comes next.

## Develop / build / flash

On the hub (PlatformIO + OpenOCD already used here):

```bash
# Build
export PATH="$HOME/.platformio/penv/bin:$PATH"
cd firmware/lora-e5-gas
pio run

# Flash (needs ST-Link on SWD: SWDIO, SWCLK, GND, 3V3 [, NRST])
./scripts/flash-lora-e5-gas.sh
```

USB-UART alone cannot program the chip. Leave the CP2102N cable on the Jetson for verify after SWD flash.

First-time unlock (also done by the flash script):

```bash
openocd -f interface/stlink.cfg -f target/stm32wlx.cfg \
  -c "init; reset halt; stm32wlx unlock 0; reset run; exit"
```

## Jetson agent (USB poll)

`services/jetson_agent/lora_e5.py` opens `/dev/ttyUSB0` at 9600, probes identity, and:

- stays on profile `temp_v1` while stock AT answers (TEMP + VDD only)
- switches to `gas_ads1115_v1` when `AT+GAS` returns channel millivolts

Samples are posted as hub metrics:

- `lora_e5.temperature_c`, `lora_e5.vdd_v`, `lora_e5.mq*_mv`
- `node.*` mirrors with `transport=usb-uart` (same decode path as LoRaWAN uplinks)

Deploy with `./scripts/deploy-jetson.sh`. Health: `GET http://<jetson>:8090/api/lora_e5`.

## Binary payload (`gas_ads1115_v1`)

Defined in `services/jetson_agent/lora_payload.py`, shared by USB encoding and ChirpStack ingest (`services/core/lora_ingest.py`):

| Offset | Field |
|--------|--------|
| 0 | `u8` profile `3` |
| 1–2 | `i16` temperature ×10 (°C), LE |
| 3–4 | `u16` VDD mV, LE |
| 5–6 | `u16` MQ-2 mV (A0) |
| 7–8 | `u16` MQ-8 mV (A1) |
| 9–10 | `u16` MQ-135 mV (A2) |
| 11–12 | `u16` MQ-5 mV (A3) |

Profiles `1` (`temp_v1`) and `2` (`temp_humi_v1`) remain supported.

## Roadmap

1. **Done (hub/agent):** payload codec, USB poller, metrics, firmware sketch, flash script  
2. **Next:** ST-Link flash of `rmlab-gas`, confirm `AT+GAS` on Jetson USB  
3. **Then:** LoRaWAN uplink of `gas_ads1115_v1` from the same firmware  
4. **Optional:** duplicate MQ array on Jetson header I2C with the same channel map  
5. **Later:** convert raw mV to engineering units (calibration / Rs/R0 curves)
