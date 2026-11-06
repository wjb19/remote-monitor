#!/bin/bash
# Apt update for the Tegra K1. Needs root. Does not flash L4T.
# R21.1 -> R21.8 is a recovery-mode flash, not a package upgrade.
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi
cp -a /etc/apt/sources.list /etc/apt/sources.list.bak.rmlab
if ! apt-get update; then
  # Trusty has left the main archive on some mirrors.
  sed -i \
    -e 's|http://ports.ubuntu.com/ubuntu-ports|http://old-releases.ubuntu.com/ubuntu-ports|g' \
    -e 's|http://archive.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' \
    /etc/apt/sources.list
  apt-get update
fi
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
if [[ -f /var/opencv4tegra-repo/libopencv4tegra-python_2.4.10.1_armhf.deb ]]; then
  dpkg -i /var/opencv4tegra-repo/libopencv4tegra-python_2.4.10.1_armhf.deb || true
fi
apt-get install -y python-numpy alsa-utils
echo "Package upgrade finished. L4T release is still whatever nv_tegra_release says."
head -1 /etc/nv_tegra_release || true
if [[ -x /home/ubuntu/rmlab/deploy/tk1/enable-supply-i2c.sh ]]; then
  /home/ubuntu/rmlab/deploy/tk1/enable-supply-i2c.sh
fi
