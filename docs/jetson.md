# Jetson Nano bring-up

The board is `bill-desktop`, user `bill`, kernel `4.9.337-tegra`. SSH from the hub is the `jetson` alias: `bill@192.168.1.19`. The host key is ED25519 `SHA256:PdvLYPCdAkKMV+bGFvAWplsgX9iAfbSSKI8MVNVtRUE`. `sudo -n` works for `bill`. `192.168.1.16` and `192.168.1.5` are not this board.

The hub is `192.168.1.14` on Wi-Fi `NETGEAR41`. The Jetson reaches it over `wlan0`. The dashboard and API are `http://192.168.1.14:8080` (`GET /health`). ChirpStack is on port 8081. Gateway UDP is `192.168.1.14:1700`.

Deploy from the hub with `./scripts/deploy-jetson.sh`. That syncs this tree to `/opt/rmlab`, installs `/etc/rmlab/global_conf.json`, and restarts `rmlab-jetson-agent` and `rmlab-lora-pkt-fwd`. Wi-Fi and the direct Ethernet address are a separate step, `deploy/jetson/setup-wifi.sh`. The passphrase is read from the hub's saved `NETGEAR41` profile and is not stored in this repo.

## What is running

| Unit | State on 24 Oct 2026 | Role |
|------|----------------------|------|
| `rmlab-jetson-agent` | enabled, active | Headless agent on port 8090 |
| `rmlab-lora-pkt-fwd` | enabled, active | SX1302 packet forwarder |
| `rmlab-direct-link` | enabled, active | Path to the TK1 over `eth0` |
| `gps-rx-mark` | disabled, inactive | Would release header pin 8. Left off. |
| `nvgetty` | disabled, inactive | Stock script would put a getty on `ttyTHS1` |

`bill` is in `dialout`. `/dev/ttyTHS1` is `root:dialout` mode `0660`.

## Wi-Fi

The USB stick is a Realtek RTL8852BU. It enumerates as a virtual CD, `0bda:1a2b`, until `usb_modeswitch -v 0bda -p 1a2b -K` switches it to `0bda:b832`. The stock `/lib/udev/rules.d/40-usb_modeswitch.rules` does not list that id, so the config file alone never runs.

Installed on the board, and saved here:

- `deploy/jetson/wifi/modeswitch.rules` → `/etc/udev/rules.d/40-rmlab-rtl8852bu.rules`
- `deploy/jetson/wifi/usb-modeswitch.conf` → `/etc/usb_modeswitch.d/0bda:1a2b`
- `deploy/jetson/wifi/modules-load.conf` → `/etc/modules-load.d/8852bu.conf`
- `deploy/jetson/wifi/modprobe.conf` → `/etc/modprobe.d/rtl8852bu.conf`

The driver is `8852bu` from `afrociberdelio/rtl8852bu_drivers`, branch `1.19.3`, commit `f6fbecaae0d33ab323d73893a0887e3e8ed76dd8`. The source tree lives only on the Jetson at `/home/bill/src/rtl8852bu`. It is not vendored into this repo. `deploy/jetson/wifi/driver.txt` records the commit. The module installed under `/lib/modules/4.9.337-tegra` reports `v1.19.3-45-g7583a804a.20230505`.

NetworkManager connection `NETGEAR41` (UUID `28961e14-a8a8-4cf0-847e-a1ea1efa4f4f`) is bound to `wlan0` and uses DHCP. After the power cycles on 24 Oct 2026 the lease came back as `192.168.1.19/24`. Do not add a second NetworkManager connection bound to the hub's `wlp2s0`, and do not leave a second address on the hub's `NETGEAR41` profile.

## Ethernet toward the TK1

`eth0` MAC is `00:04:4b:eb:00:51`.

`Wired connection 1` (UUID `a3ab28c0-254c-3175-8a7b-6c17eeb89dbc`) is the saved profile `192.168.8.1/24`, `never-default`, copied from `deploy/jetson/wifi/eth0.nmconnection`. It was not the active connection when this was written. The active `eth0` profile is a manual `192.168.1.17/32`.

`rmlab-direct-link.service` runs `/usr/local/sbin/rmlab-direct-link.sh` (`deploy/jetson/direct-link.sh`). It adds `192.168.1.17/32` on `eth0` and a route to `192.168.1.18/32`, turns on forwarding and proxy ARP, and masquerades that one address (and `192.168.8.0/24`) out `wlan0`. Do not add a connected route for `192.168.1.0/24` on `eth0` while `wlan0` is the uplink. The `/32` is the whole point: the house subnet stays on Wi-Fi.

The TK1 did not answer `192.168.1.18` or `192.168.8.2` from the hub or from the Jetson while this page was written.

## LoRaWAN gateway

The HAT is the Waveshare SX1302/SX1303 LoRaWAN Gateway HAT. The concentrator answered chip id `0016C001F11F5052`. `lora_pkt_fwd` runs from `/home/bill/sx1302_hal/packet_forwarder` with `/etc/rmlab/global_conf.json`. The repo copy is `deploy/jetson/global_conf.json`. `gps_tty_path` is `/dev/ttyTHS1`. Gateway id, server `192.168.1.14`, and UDP port 1700 are in that file.

Header SPI on this Nano is not a live MMIO block. Do not map `0x7000d400`. `deploy/jetson/reset_lgw_jetson.py` writes the pinmux at `0x70003000` (PC0–PC3, offsets `0x50`, `0x54`, `0x58`, `0x5C`) and drives Jetson.GPIO BOARD pins:

| Signal | BOARD | BCM | sysfs gpio |
|--------|-------|-----|------------|
| `POWER_EN` | 12 | 18 | 79 |
| `RESET` (`SX1302_RESET`) | 16 | 23 | 232 |
| SX1261 | 15 | 22 | 194 |
| ADC | 33 | 13 | 38 |

Those four start high. `POWER_EN` stays high. `RESET` is pulsed high then low. SX1261 goes low then high. ADC goes low then high. The packet-forwarder unit calls `reset_lgw_jetson.py start` before `lora_pkt_fwd` and `stop` after. The reset script rewrites the SPI pinmux. It does not own header pins 8 and 10.

The two solid red LEDs beside the mini coax are the radio module's status LEDs.

Stopping the forwarder: do not `pkill -f` a Python process by a broad pattern. The core on the hub is the uvicorn pid from `ss -ltnp 'sport = :8080'`, started with `bash ./scripts/start-core.sh`.

## GPS

The receiver is a Quectel L76K on the HAT. It needs no firmware load and no AT commands. Default NMEA is 9600 8N1. On its own power, with the Jetson shut down and the HAT still powered, the TX LED blinks.

Wiring, from the Waveshare schematic (`SX1302_LoRaWAN_Gateway_HAT.pdf`):

- Module VCC and `RESET_N` are tied to the HAT 3.3 V regulator (RT9193-33 from the header 5 V). There is no software way to power-cycle or reset the receiver.
- `WAKEUP` (schematic `STANDBY`) is pulled to 3.3 V by R2, 10 kΩ, so the module wants continuous mode.
- `SET` (schematic `FORCE_ON`) is pulled to ground by R1, 10 kΩ: GPS + GLONASS.
- PPS drives LED `L_PPS1` only. It is not a Jetson GPIO.
- Header pin 8 (`P_TX`, BCM 14) — R10, 0 Ω — module RX (`Q_RX`).
- Header pin 10 (`P_RX`, BCM 15) — R11, 0 Ω — module TX (`Q_TX`).
- There is no jumper block. R10 and R11 are soldered. PWR, RXD, TXD, and PPS are the four small LEDs. They light when the named net is high.

On the Nano, pins 8 and 10 are UART2. The device node is `/dev/ttyTHS1` (`70006040.serial`). There is no `/dev/ttyTHS0`. `ttyS0` (`70006000.serial`) is the debug console on the extlinux `console=` line. Do not map the UART block at `0x70006000` or `0x70006040`. A read there clock-gates the block and hung the board.

Pinmux base is `0x70003000`. UART2 TX is offset `0xF4`, RX is `0xF8`. Bits `[1:0]` are the mux, bits `[3:2]` are pull (0 none, 1 down, 2 up), bit 4 is tristate (1 means the output driver is off), bit 6 is the input buffer. Port G: gpio 48 is header pin 8, gpio 49 is header pin 10. GPIO registers are at `0x6000D000`, CNF/OE/OUT at `0x108` / `0x118` / `0x128`. Writes that actually change the pad are masked: store `(0x100 | value) << bit` at `register + 0x80`.

`0x10` on pin 8 is tristate, no pull, input buffer off. `0x58` on pin 10 is pull-up, tristate, input buffer on. `0x00` with GPIO CNF, OE, and OUT set drives the pad as a GPIO. `0x46` on these pins is a bad value; it clamped pin 10 low.

### Why the receiver stayed silent

The pad's reset state pulls header pin 8 down, and the stock pinmux enables the UART transmitter, which rests low. The L76K treats that low on RX as a break. It leaves continuous mode and does not start again when the line later goes high or is released. Commands after that (`$PCAS00`, `$PCAS03`, `$PCAS04`, `$PCAS10`, and a bit-banged 9600 sender) returned nothing, at every baud that was tried. The TX wire samples as a solid high: the module is powered and idle, not sending.

A Tegra pull-up cannot hold the RX net up against the 1 kΩ RXD LED, so "released with a pull-up" is still a low. The working electrical state is the driver off and no pull, which is what the module sees when the Jetson is shut down.

Holding pin 8 high from Linux, including across a HAT-only power cycle, did not start NMEA. Releasing both pins while the module was already stopped did not start it either. Releasing both pins and then power-cycling only the HAT, with the Jetson left running, did.

### Boot files that were changed

`extlinux` default is `JetsonIO`. The FDT is `/boot/kernel_tegra210-p3448-0000-p3449-0000-b00-user-custom.dtb`. This 4.9 kernel does reapply that pinmux. The copies are in `deploy/jetson/boot/`.

Kernel FDT now:

- `pin8` and `uart2_tx_pg0`: function `uartb`, pull none, tristate 0, input disabled.
- `uart2_rx_pg1` and `pin10`: function `uartb`, pull up, tristate 1, input enabled. The original custom tree had the RX pin as pull-down with the driver enabled.
- `/gpio@6000d000/default` `gpio-output-high` is `<48 6 187 231>`. 48 is header pin 8. The original list was `<6 187 231>`.

That combination is "claim the pin as GPIO high, then let the kernel enable the output driver." After a software reboot the live registers were `pin8=0x00`, CNF/OE/OUT bit 0 set, `pin10=0x58`. The GPS was still silent, because the low happens before that GPIO claim.

QSPI NOR is `/dev/mtd0`, 4 MiB, erase size 4096. Two identical DTB slots, magic `DTB\0`, at `0xd0000` and `0x270000`. The FDT sits at `slot + 0x400`. The boot ROM checks an AES-CMAC with a 16-byte key of zeros. The MAC is stored at offset `0x118` of the slot. The hashed bytes start at offset `0x230` and run through the end of the signed blob. The MAC itself is outside the hash. Both slots have to be updated together. `dtc` round-trip of this tree is unsafe. The gpio cell was inserted by hand: four bytes `00 00 00 30` at the start of `gpio-output-high`, that property's length bumped from 12 to 16, and `totalsize`, `off_strings`, and `size_struct` each bumped by 4. Erase ioctl on this aarch64 kernel is `0x40084d02`. Compare the sector to the expected old bytes before erasing it.

Bootloader FDT now, in both slots of `qspi-current-20261024.bin`:

- `gpio-output-high` includes 48, same as the kernel.
- `uart2_tx_pg0` tristate is 1 and enable-input is 1, so nvtboot's early pinmux pass does not connect the UART transmitter. The kernel tree still turns the driver on later, after cboot has set the GPIO high.

Ninety-six 4 KiB sectors differ from the original image. That is the four-byte FDT insert shifting the rest of each blob, in both slots. The board booted after this write.

`gps-rx-mark.service` and `/usr/local/sbin/gps-rx-mark.py` are installed but disabled. The script releases pin 8 (pinmux `0x50`, GPIO claim cleared). A late release does not revive a module that already stopped, and enabling it would undo a deliberate drive. `deploy/jetson/99-gps-ttyTHS1.rules` is installed as `/etc/udev/rules.d/99-gps-ttyTHS1.rules` so ModemManager ignores `ttyTHS1`.

### The state that produced NMEA

On 24 Oct 2026, with the Jetson already up for about 75 minutes, pin 8 was set to `0x10` and pin 10 to `0x10`, and the HAT was unplugged until the GPS power LED went out and then plugged back in. The Jetson did not reboot. Sampling pin 10 then showed a live transmit line (thousands of edges in a few seconds).

Eight seconds on `/dev/ttyTHS1` at 9600 8N1 returned NMEA, including a GGA fix and `$GPTXT` reporting `ANTENNA OK`. The sentence is not stored in this tree. Later fixes are kept by the dashboard as location metadata on the inputs that arrive while that fix is current.

`gps_tty_path` was changed to `/dev/ttyTHS1` only after that fix was read. The stock HAL drops this module's RMC sentence, because NMEA 4.10 adds a status field, and it only copies a position after RMC. `deploy/jetson/l76k-gps.patch` accepts that 14-field sentence and also copies the position from GGA. That patch is applied in `/home/bill/sx1302_hal` on the Nano, which is where `lora_pkt_fwd` was rebuilt. The gateway bridge keeps publishing protobuf, which is what ChirpStack reads, and the core takes the fix from that stats message.

The packet forwarder holds `/dev/ttyTHS1`. Opening that port did not change the pinmux: pin 8 stayed `0x10`, pin 10 stayed `0x58`, and the transmit line kept moving. A second reader sees an empty buffer because the forwarder already owns the bytes.

A full power-on of the Jetson and the HAT together can stop the receiver again. The pad is pulled down from reset until the bootloader pinmux runs, which is long enough for the L76K to latch. The recovery that worked is: leave the Jetson up, leave pin 8 undriven (`0x10`, GPIO claim clear), and cycle only the HAT 5 V. Driving the pin high, sending `$PCAS` commands, and releasing the pin after the module has already stopped do not replace that power cycle. Driving pin 8 high across a HAT 5 V cycle can also back-power the module through the RX ESD diode, so the pad must stay Hi-Z during that cycle.

`POWER_EN` (BOARD 12) only enables the SX1302 Mini-PCIe LDOs. The L76K rides the HAT `RT9193-33` from header 5 V, so toggling `POWER_EN` does not restart GPS. Confirmed 28 Oct 2026 after a reboot: pin 10 stayed at zero edges through a 3 s `POWER_EN` low with pin 8 already released.

### Reboot-proof fix

Desolder **R10** (0 Ω) on the HAT. That link is `P_TX` (header pin 8) → `Q_RX` (L76K RX). With R10 open, the Tegra boot low never reaches the module, so NMEA survives Jetson reboots. R11 stays in place for TX → `P_RX`. The gateway is receive-only for GPS, so losing the host→module command path is fine. `reset_lgw_jetson.py start` always releases pin 8 before `lora_pkt_fwd` opens `/dev/ttyTHS1`, and prints a quiet/alive edge count from pin 10 (`deploy/jetson/gps_tx_check.py`).

Until R10 is removed, every Jetson reboot needs the HAT-only 5 V cycle with pin 8 left undriven. `gps-rx-mark.service` remains optional; the release is part of the packet-forwarder `ExecStartPre`.

Listen with `time.monotonic()`. A `time.time()` deadline ended early when NTP stepped the clock during boot.

## Do not

- Map `0x7000d400` (SPI) or `0x70006000` / `0x70006040` (UART).
- Reflash the bootloader image that sets `uart2_tx` tristate to 0 in the QSPI DTB. That was `jetson-mtd-high.bin` and it drives a break before GPIO init. It is not in `deploy/jetson/boot/`. The saved current image is the tristate-1 bootloader tree.
- Round-trip the QSPI DTB through `dtc`.
- Set `uart2_tx_pg0` function to `rsvd0`. The valid functions are UARTB, I2S4A, SPDIF, and UART.
- Put the Wi-Fi passphrase or the `rtl8852bu` source tree in the repo.
- `pkill -f` uvicorn. Stop the hub core by the pid listening on port 8080.
