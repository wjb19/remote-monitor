# Jetson boot images saved 24 Oct 2026

These are copies of the Nano's own boot files, taken from `bill-desktop` while it was up. They are board images, not a generic L4T release.

| File | What it is |
|------|------------|
| `qspi-original-20261024.bin` | SPI NOR (`/dev/mtd0`, 4 MiB) before any GPS edit. MD5 `3391631b028e30572f2e26addea4a016`. The same bytes are also at `/home/bill/jetson-mtd-qspi-20261024.bin` on the hub and on the Jetson. |
| `qspi-current-20261024.bin` | The flash as read back after the GPS bootloader edit. MD5 `a6db277f3ca5be4f653a17db17af3509`. |
| `kernel-dtb-before-pinmux.dtb` | `/boot/...b00-user-custom.dtb.bak-gps-tx`. The SD-card FDT before the GPS pinmux edit. |
| `kernel-dtb-before-gpio-high.dtb` | `...dtb.bak-gps-high`. Pin 8 released in the kernel tree, gpio 48 not yet in `gpio-output-high`. |
| `kernel-dtb-current.dtb` | The FDT the board actually boots. `extlinux` label `JetsonIO`. |

How the two QSPI copies differ, and which pins the kernel tree drives, is written up in `docs/jetson.md`.
