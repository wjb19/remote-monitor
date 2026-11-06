# Jetson TK1 bring-up

The SSH alias is `tk1`: user `ubuntu`, reached through the Nano, with `PubkeyAcceptedAlgorithms +ssh-rsa` and `HostkeyAlgorithms +ssh-rsa`. The hostname is `tegra-ubuntu`. Ubuntu 14.04 and L4T R21. `sudo` is not passwordless.

The deployment wiring is a single Ethernet cable from the TK1 to the Nano. On 24 Oct 2026 that link was up. The TK1's `eth0` is `iface eth0 inet dhcp` and there is no DHCP server on the cable, so Avahi left it at `169.254.2.224/16` (MAC `00:04:4b:2c:20:0d`). It was still ARPing for the hub at `192.168.1.14`. `192.168.1.18` did not answer.

## Where it sits on the network

The Jetson does not put `192.168.1.0/24` on `eth0`. `deploy/jetson/direct-link.sh`, started by `rmlab-direct-link.service`, adds `192.168.1.17/32` and `169.254.17.1/32` on the Jetson `eth0`, and host routes to `192.168.1.18` and `169.254.2.224`. Forwarding and proxy ARP are on. Both of those host addresses, and `192.168.8.0/24`, are masqueraded out the Jetson's `wlan0`.

From the hub, `ssh tk1` jumps through the Nano to `169.254.2.224`. The host key alias is `192.168.1.18`, because that is the name already stored for this board's ED25519 key `SHA256:q4SzOSE8IlUyZdoLY/iq9MSJuMuJC244SBu4FOidkAI`.

With that route in place the TK1 reached `192.168.1.14`. Ping succeeded, and `GET http://192.168.1.14:8080/health` returned the core service. The board itself is Ubuntu 14.04.6, L4T R21 revision 1.0, kernel `3.10.40-g8c4516e`, hostname `tegra-ubuntu`, up for about two days when it was reached. The agent pid was running. Motion on `/dev/video0` was open at 640×480. The INA3221 supply read failed: no chip answered on `/dev/i2c-0` through `/dev/i2c-5`. Board temperature was 24 °C.

The saved Jetson Ethernet profile `Wired connection 1` is `192.168.8.1/24` and `never-default` (`deploy/jetson/wifi/eth0.nmconnection`). Moving the TK1 to `192.168.8.2` on that link was not done. Do not reflash L4T unless that is asked for. R21.1 to R21.8 is a recovery-mode flash, not an `apt` upgrade.

## Agent

`./scripts/deploy-tk1.sh` rsyncs `services/tk1_agent` and `deploy/tk1` to `ubuntu@tk1:~/rmlab-src`, then into `~/rmlab`. The process is Python 2.7, `services/tk1_agent/main.py`, launched by `deploy/tk1/run-tk1-agent.sh`. There is no user systemd on 14.04, so the script is started from the `ubuntu` crontab at `@reboot` and the pid is written to `~/rmlab/agent.pid`.

The launcher waits until `eth0` has an address, then serves port 8090. `GET /health` reports the service, the motion camera, and the supply sample. `RMLAB_MAIN_URL` defaults to `http://192.168.1.14:8080`. `RMLAB_CAMERA_ID` defaults to `tk1-cam0`.

OpenCV comes from the board's `/var/opencv4tegra-repo/libopencv4tegra-python_2.4.10.1_armhf.deb`, unpacked under `~/rmlab/vendor`. NumPy, if it is not already importable, is unpacked from the Ubuntu 14.04 `python-numpy` package rather than built from source. `PYTHONPATH` points at that vendor tree.

Motion video matches the Jetson window: an in-memory pre-roll and post-roll, uploaded as JPEGs. The camera microphone is captured for the same window and uploaded as one WAV. `POST /api/motion` accepts `pre_s`, `post_s`, `threshold`, `cooldown_s`, `fps`, and `device`.

## Supply current

The TK1's INA3221 sits on GEN2 I2C. `deploy/tk1/enable-supply-i2c.sh` installs `/etc/udev/rules.d/99-rmlab-tk1-i2c.rules` so `i2c-0` through `i2c-5` are group `ubuntu`, mode `0660`, and reapplies that on the devices that already exist. The same script removes `kdump-tools` if a failed postinst left dpkg half-configured. kdump cannot start on this Tegra.

`services/tk1_agent/power.py` probes addresses `0x40`–`0x43` for manufacturer id `0x5449`. The 12 V barrel jack is the channel whose bus voltage sits near 12 V. Current uses a 10 milliohm shunt unless `RMLAB_SHUNT_OHM` is set. Samples are posted as `tk1.supply_current_a`, `tk1.supply_voltage_v`, `tk1.supply_power_w`, and `tk1.tboard_c` every `RMLAB_POWER_INTERVAL_S` seconds (default 15).

## Packages

`deploy/tk1/update-os.sh` needs root. It keeps a copy of `/etc/apt/sources.list`, runs `apt-get update`, and if the Trusty mirrors have gone, rewrites them to `old-releases.ubuntu.com` and updates again. It then upgrades installed packages, installs `python-numpy` and `alsa-utils`, and re-runs the I2C rule script. It does not flash L4T. `/etc/nv_tegra_release` is still the release the board was flashed with.
