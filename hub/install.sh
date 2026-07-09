#!/bin/bash -eu
# TeslaCam Hub installer.
#
# Run as root on a Pi where teslausb's own one-step setup has already
# completed (i.e. after first boot). Installs the Hub as the sole HTTPS
# service on 443 (with an 80->443 redirect) and disables teslausb's own
# nginx/cgi-bin web UI entirely -- the Hub replaces it, there's no fallback
# UI anymore. teslausb's core (gadget/snapshots/archive, and the cgi-bin
# *.sh scripts the Hub itself still shells out to for BLE/drive-toggle) is
# untouched -- this only turns off the old HTTP-facing layer.
#
#   sudo bash hub/install.sh
#
# Safe to re-run: copies the app fresh each time and restarts the service.

HUB_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUB_DST=/opt/teslacam-hub
STATE_DIR=/backingfiles/decrypt-viewer-state
TLS_DIR=/mutable/tls

if [ "$(id -u)" -ne 0 ]; then
  echo "Must run as root (sudo bash hub/install.sh)" >&2
  exit 1
fi

echo "[hub-install] remounting / rw"
mount / -o remount,rw

echo "[hub-install] installing OS packages (python3-pip, ffmpeg)"
apt-get update -y
apt-get install -y --no-install-recommends python3-pip ffmpeg openssl

echo "[hub-install] installing python deps (pycryptodome)"
pip3 install --break-system-packages --quiet pycryptodome 2>/dev/null \
  || pip3 install --quiet pycryptodome

echo "[hub-install] copying app to $HUB_DST"
mkdir -p "$HUB_DST"
rsync -a --delete --exclude '__pycache__' "$HUB_SRC/app/" "$HUB_DST/app/"
mkdir -p "$STATE_DIR" /dev/shm/teslacam "$TLS_DIR"

echo "[hub-install] generating self-signed TLS cert (if missing)"
if [ ! -f "$TLS_DIR/cert.pem" ] || [ ! -f "$TLS_DIR/key.pem" ]; then
  HOST=$(hostname)
  openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout "$TLS_DIR/key.pem" -out "$TLS_DIR/cert.pem" \
    -subj "/CN=$HOST" >/dev/null 2>&1
fi

echo "[hub-install] disabling teslausb's own nginx web UI (Hub replaces it; cgi-bin *.sh files stay on disk, the Hub still shells out to them directly)"
systemctl disable --now nginx 2>/dev/null || true

echo "[hub-install] installing snapshot-pointer helper + timer"
cp "$HUB_SRC/update-latest-snapshot.sh" "$HUB_DST/update-latest-snapshot.sh"
chmod +x "$HUB_DST/update-latest-snapshot.sh"
cp "$HUB_SRC/teslacam-latest-snapshot.service" /etc/systemd/system/teslacam-latest-snapshot.service
cp "$HUB_SRC/teslacam-latest-snapshot.timer" /etc/systemd/system/teslacam-latest-snapshot.timer
systemctl daemon-reload
systemctl enable teslacam-latest-snapshot.timer
systemctl start teslacam-latest-snapshot.timer
systemctl start teslacam-latest-snapshot.service

echo "[hub-install] installing systemd unit"
cp "$HUB_SRC/teslacam-hub.service" /etc/systemd/system/teslacam-hub.service
systemctl daemon-reload
systemctl enable teslacam-hub
systemctl restart teslacam-hub

echo "[hub-install] remounting / ro"
mount / -o remount,ro

echo "[hub-install] done."
echo "  Primary UI: https://$(hostname).local/  (or https://<pi-ip>/)"
echo "  First visit sets up the vault (encryption passphrase = login password)."
