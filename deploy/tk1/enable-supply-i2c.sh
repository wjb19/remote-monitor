#!/bin/bash
# Let the ubuntu agent read GEN2 I2C, where the TK1's INA3221 supply
# monitors sit. The permission has to be restored on every boot.
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi
# kdump cannot start on this Tegra, and its failed postinst left dpkg half-configured.
if dpkg -s kdump-tools >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get remove -y kdump-tools || dpkg --remove --force-remove-reinstreq kdump-tools || true
fi
cat >/etc/udev/rules.d/99-rmlab-tk1-i2c.rules <<'EOF'
KERNEL=="i2c-[0-5]", MODE="0660", GROUP="ubuntu"
EOF
for dev in /dev/i2c-[0-5]; do
  if [[ -e "$dev" ]]; then
    chgrp ubuntu "$dev"
    chmod 0660 "$dev"
  fi
done
if command -v udevadm >/dev/null; then
  udevadm control --reload-rules || true
  udevadm trigger --subsystem-match=i2c || true
fi
echo "I2C buses are readable by ubuntu"
ls -l /dev/i2c-*
