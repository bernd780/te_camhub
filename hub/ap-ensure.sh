#!/bin/bash -eu
# hub/ap-ensure.sh <ssid> <pass> [<ip>]
#
# Ensures the ap0 virtual interface + TESLAUSB_AP NetworkManager profile
# exist, mirroring teslausb's own setup/pi/configure-ap.sh (NetworkManager
# path -- this Pi doesn't use the legacy hostapd/wpa_supplicant path).
# Idempotent: safe to call repeatedly, e.g. every time the setting is saved.

SSID="${1:?ssid required}"
PASS="${2:?password required}"
IP="${3:-192.168.66.1}"

WLAN="$(nmcli -t -f TYPE,DEVICE c show --active | grep 802-11-wireless | grep -v ':ap0$' | cut -d: -f2 | head -1)"
if [ -z "$WLAN" ]; then
  WLAN="$(nmcli -t -f TYPE,DEVICE d | grep '^wifi:' | grep -v ':ap0$' | cut -d: -f2 | head -1)"
fi
if [ -z "$WLAN" ]; then
  echo "ap-ensure: no wifi client device found" >&2
  exit 1
fi

if ! iw dev ap0 info &> /dev/null; then
  iw dev "$WLAN" interface add ap0 type __ap || true
fi
iw "$WLAN" set power_save off || true
iw ap0 set power_save off || true

if nmcli -t -f NAME c show | grep -qx TESLAUSB_AP; then
  nmcli con modify TESLAUSB_AP 802-11-wireless.ssid "$SSID"
  nmcli con modify TESLAUSB_AP 802-11-wireless-security.psk "$PASS"
  nmcli con modify TESLAUSB_AP ipv4.addr "$IP/24"
else
  nmcli con add type wifi ifname ap0 mode ap con-name TESLAUSB_AP ssid "$SSID"
  nmcli con modify TESLAUSB_AP 802-11-wireless-security.key-mgmt wpa-psk
  nmcli con modify TESLAUSB_AP 802-11-wireless-security.psk "$PASS"
  nmcli con modify TESLAUSB_AP ipv4.addr "$IP/24"
  nmcli con modify TESLAUSB_AP ipv4.method shared
  nmcli con modify TESLAUSB_AP ipv6.method disabled
fi
echo "ap-ensure: TESLAUSB_AP ready (ssid=$SSID ip=$IP if=$WLAN)"
