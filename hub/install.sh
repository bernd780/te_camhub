#!/bin/bash -eu
# TeslaCam Hub installer.
#
# Run as root on a Pi where teslausb's own one-step setup has already
# completed (i.e. after first boot). Installs the Hub as the primary HTTPS
# service on 443 (with an 80->443 redirect), moves teslausb's own nginx UI
# to a fallback port (8080, reachable via the "Erweitert (Alt-UI)" link),
# and sets up the systemd unit. teslausb's core (gadget/snapshots/archive)
# is untouched -- this only adds/replaces the web-facing layer.
#
#   sudo bash hub/install.sh
#
# Safe to re-run: copies the app fresh each time and restarts the service.

HUB_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUB_DST=/opt/teslacam-hub
STATE_DIR=/backingfiles/decrypt-viewer-state
TLS_DIR=/mutable/tls
NGINX_CONF=/etc/nginx/sites-available/teslausb.nginx

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

if [ -f "$NGINX_CONF" ]; then
  echo "[hub-install] moving teslausb's own web UI to fallback port 8080"
  sed -i 's/listen 80 default_server;/listen 8080 default_server;/' "$NGINX_CONF"
  sed -i 's/listen \[::\]:80 default_server;/listen [::]:8080 default_server;/' "$NGINX_CONF"
  systemctl restart nginx || true
else
  echo "[hub-install] WARNING: $NGINX_CONF not found -- skipping nginx port move" \
       "(teslausb's own one-step setup may not have run yet)"
fi

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
echo "  Primary UI:  https://$(hostname).local/  (or https://<pi-ip>/)"
echo "  Alt-UI:      http://$(hostname).local:8080/"
echo "  First visit sets up the vault (encryption passphrase = login password)."
