#!/bin/sh
# Point-to-point Ethernet toward the TK1. The house subnet stays on wlan0;
# this cable is the private 192.168.8.0/24 link (Jetson .1, TK1 .2).
#
# Temporary host routes for the old .18 / link-local addresses remain so a
# TK1 that has not yet renewed DHCP is still reachable during cutover.
set -eu

IFACE="${RMLAB_TK1_IFACE:-eth0}"
UPLINK="${RMLAB_UPLINK_IFACE:-wlan0}"

ip addr replace 192.168.8.1/24 dev "$IFACE"
ip addr replace 192.168.1.17/32 dev "$IFACE"
ip route replace 192.168.1.18/32 dev "$IFACE" src 192.168.1.17
ip addr replace 169.254.17.1/32 dev "$IFACE"
ip route replace 169.254.2.224/32 dev "$IFACE" src 169.254.17.1

sysctl -w net.ipv4.ip_forward=1 >/dev/null
sysctl -w "net.ipv4.conf.${IFACE}.proxy_arp=1" >/dev/null

iptables -t nat -C POSTROUTING -s 192.168.8.0/24 -o "$UPLINK" -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -s 192.168.8.0/24 -o "$UPLINK" -j MASQUERADE
iptables -t nat -C POSTROUTING -s 192.168.1.18/32 -o "$UPLINK" -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -s 192.168.1.18/32 -o "$UPLINK" -j MASQUERADE
iptables -t nat -C POSTROUTING -s 169.254.2.224/32 -o "$UPLINK" -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -s 169.254.2.224/32 -o "$UPLINK" -j MASQUERADE

iptables -N DOCKER-USER 2>/dev/null || true
iptables -C DOCKER-USER -i "$IFACE" -o "$UPLINK" -j ACCEPT 2>/dev/null \
  || iptables -I DOCKER-USER -i "$IFACE" -o "$UPLINK" -j ACCEPT
iptables -C DOCKER-USER -i "$UPLINK" -o "$IFACE" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null \
  || iptables -I DOCKER-USER -i "$UPLINK" -o "$IFACE" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
