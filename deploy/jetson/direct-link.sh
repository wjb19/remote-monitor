#!/bin/sh
# Reach the TK1 across the single Ethernet cable and let it use the
# Jetson's Wi-Fi as its way to the house network.
# With no DHCP server on this cable the TK1 falls back to its Avahi
# link-local address. 192.168.1.18 is kept for the old DHCP address.
# These are /32s so eth0 does not claim the house subnet.
set -eu
ip addr replace 192.168.1.17/32 dev eth0
ip route replace 192.168.1.18/32 dev eth0 src 192.168.1.17
ip addr replace 169.254.17.1/32 dev eth0
ip route replace 169.254.2.224/32 dev eth0 src 169.254.17.1
sysctl -w net.ipv4.ip_forward=1 >/dev/null
sysctl -w net.ipv4.conf.eth0.proxy_arp=1 >/dev/null
iptables -t nat -C POSTROUTING -s 192.168.1.18/32 -o wlan0 -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -s 192.168.1.18/32 -o wlan0 -j MASQUERADE
iptables -t nat -C POSTROUTING -s 169.254.2.224/32 -o wlan0 -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -s 169.254.2.224/32 -o wlan0 -j MASQUERADE
iptables -t nat -C POSTROUTING -s 192.168.8.0/24 -o wlan0 -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -s 192.168.8.0/24 -o wlan0 -j MASQUERADE
iptables -N DOCKER-USER 2>/dev/null || true
iptables -C DOCKER-USER -i eth0 -o wlan0 -j ACCEPT 2>/dev/null \
  || iptables -I DOCKER-USER -i eth0 -o wlan0 -j ACCEPT
iptables -C DOCKER-USER -i wlan0 -o eth0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null \
  || iptables -I DOCKER-USER -i wlan0 -o eth0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
