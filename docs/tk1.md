# Jetson TK1 bring-up

The SSH alias is `tk1`: user `ubuntu`, reached through the Nano (`ProxyJump jetson`), with `PubkeyAcceptedAlgorithms +ssh-rsa` and `HostkeyAlgorithms +ssh-rsa`. The hostname is `tegra-ubuntu`. Ubuntu 14.04 and L4T R21. `sudo` is not passwordless until configured on the board.

## Network (current)

Single Ethernet cable Nano ↔ TK1 on a private subnet (not the house Wi-Fi):

| Host | `eth0` |
|------|--------|
| Jetson | `192.168.8.1/24` |
| TK1 | `192.168.8.2/24`, gateway `192.168.8.1` |

Static files: `deploy/tk1/interfaces-eth0-static`, applied by `deploy/tk1/setup-static-eth.sh`. The Nano can also DHCP-reserve `.2` via `rmlab-tk1-dhcp` / `dnsmasq-tk1.conf`.

From the hub, `ssh tk1` jumps through the Nano. The TK1 agent does **not** hard-code the hub Wi-Fi IP. `services/tk1_agent/hub_from_jetson.py` asks the Jetson agent (`http://192.168.8.1:8090`) for the current hub URL.

## Agent

`./scripts/deploy-tk1.sh` rsyncs `services/tk1_agent` and `deploy/tk1` to `ubuntu@tk1:~/rmlab-src`, then into `~/rmlab`. The process is Python 2.7, `services/tk1_agent/main.py`, launched by `deploy/tk1/run-tk1-agent.sh`. There is no user systemd on 14.04, so the script is started from the `ubuntu` crontab at `@reboot` and the pid is written to `~/rmlab/agent.pid`.

The launcher waits until `eth0` has an address, then serves port 8090. `GET /health` reports the service, motion camera, supply sample, and `thermal` (board / CPU).

OpenCV comes from the board's `/var/opencv4tegra-repo/libopencv4tegra-python_2.4.10.1_armhf.deb`, unpacked under `~/rmlab/vendor`. NumPy, if it is not already importable, is unpacked from the Ubuntu 14.04 `python-numpy` package. `PYTHONPATH` points at that vendor tree.

Motion video matches the Jetson window: in-memory pre-roll and post-roll, uploaded as JPEGs. The camera microphone is captured for the same window and uploaded as one WAV. `POST /api/motion` accepts `pre_s`, `post_s`, `threshold`, `cooldown_s`, `fps`, and `device`.

## Thermal

Ambient board temperature uses **Tboard_tegra** (NCT72), furthest from the CPU / closest onboard proxy to room air → metric `tk1.tboard_c`. CPU contrast is `tk1.tcpu_c` (`CPU-therm`). Health exposes these under `thermal` (not mixed into the INA3221 `supply` object).

## Supply current

The TK1's INA3221 sits on GEN2 I2C. `deploy/tk1/enable-supply-i2c.sh` installs `/etc/udev/rules.d/99-rmlab-tk1-i2c.rules` so `i2c-0` through `i2c-5` are group `ubuntu`, mode `0660`. The same script removes `kdump-tools` if a failed postinst left dpkg half-configured.

`services/tk1_agent/power.py` probes addresses `0x40`–`0x43` for manufacturer id `0x5449`. The 12 V barrel jack is the channel whose bus voltage sits near 12 V. Current uses a 10 milliohm shunt unless `RMLAB_SHUNT_OHM` is set. Samples are posted as `tk1.supply_current_a`, `tk1.supply_voltage_v`, `tk1.supply_power_w` every `RMLAB_POWER_INTERVAL_S` seconds (default 15). On some boards no INA3221 answers; thermal still works.

## Packages

`deploy/tk1/update-os.sh` needs root. It keeps a copy of `/etc/apt/sources.list`, runs `apt-get update`, and if the Trusty mirrors have gone, rewrites them to `old-releases.ubuntu.com` and updates again. It then upgrades installed packages, installs `python-numpy` and `alsa-utils`, and re-runs the I2C rule script. It does not flash L4T.

## Historical note

Earlier bring-up used link-local `169.254.2.224` and dual `/32` hacks on the Nano (`192.168.1.17` → `.18`). Prefer the `192.168.8.0/24` layout above.
