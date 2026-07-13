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

apt-get update -y
# ffmpeg pulls in ~150 packages (X11/audio/video libs) via apt on this image
# -- on a Pi whose root partition is only ~1.8G (this one included), that
# alone can exhaust it. If a working ffmpeg binary is already on PATH (e.g.
# a manually-installed static build, the existing workaround on this box),
# skip asking apt for the real package instead of re-attempting (and
# re-failing) that huge pull on every install.sh re-run.
PKGS="python3-pip openssl wireguard-tools libzbar0 python3-pil python3-pyzbar"
if ! command -v ffmpeg > /dev/null; then
  PKGS="$PKGS ffmpeg"
fi
echo "[hub-install] installing OS packages ($PKGS)"
# libzbar0/python3-pil/python3-pyzbar (not zbar-tools) is the QR-code decode
# path used by diag.py's import_wg_qr -- zbar-tools' zbarimg CLI drags in the
# full ImageMagick/libmagickwand stack for image loading, which is the same
# kind of disk-busting pull as ffmpeg above; the pyzbar+Pillow path needs
# only these small libs.
apt-get install -y --no-install-recommends $PKGS

echo "[hub-install] installing python deps (pycryptodome, paho-mqtt, bleak)"
pip3 install --break-system-packages --quiet pycryptodome paho-mqtt bleak 2>/dev/null \
  || pip3 install --quiet pycryptodome paho-mqtt bleak

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

echo "[hub-install] installing AP-fallback helper + timer (disabled until enabled in Einstellungen)"
cp "$HUB_SRC/ap-ensure.sh" "$HUB_DST/ap-ensure.sh"
cp "$HUB_SRC/ap-fallback-watch.sh" "$HUB_DST/ap-fallback-watch.sh"
chmod +x "$HUB_DST/ap-ensure.sh" "$HUB_DST/ap-fallback-watch.sh"
cp "$HUB_SRC/teslacam-ap-fallback.service" /etc/systemd/system/teslacam-ap-fallback.service
cp "$HUB_SRC/teslacam-ap-fallback.timer" /etc/systemd/system/teslacam-ap-fallback.timer
systemctl daemon-reload

echo "[hub-install] installing hotspot + WireGuard helper scripts (off until enabled in Einstellungen)"
cp "$HUB_SRC/hotspot-ensure.sh" "$HUB_DST/hotspot-ensure.sh"
cp "$HUB_SRC/wg-ensure.sh" "$HUB_DST/wg-ensure.sh"
chmod +x "$HUB_DST/hotspot-ensure.sh" "$HUB_DST/wg-ensure.sh"

echo "[hub-install] installing systemd unit"
cp "$HUB_SRC/teslacam-hub.service" /etc/systemd/system/teslacam-hub.service
systemctl daemon-reload
systemctl enable teslacam-hub
systemctl restart teslacam-hub

echo "[hub-install] applying secure defaults on first run only (never overrides an existing conf value)"
CONF=/root/teslausb_setup_variables.conf
getconf_val() { grep "^export $1=" "$CONF" 2>/dev/null | tail -1 | sed -E "s/^export $1=//; s/^'(.*)'\$/\1/"; }
if [ -f "$CONF" ] && [ -z "$(getconf_val SSH_DISABLE_PASSWORD)" ]; then
  echo "[hub-install] WARNING: disabling SSH password login by default." \
       "Make sure an SSH key is authorized for this Pi BEFORE relying on remote SSH again" \
       "-- otherwise only physical/console access can get you back in." \
       "Revert any time via Einstellungen -> Sicherheit."
  echo "export SSH_DISABLE_PASSWORD='true'" >> "$CONF"
  mkdir -p /etc/ssh/sshd_config.d
  echo "PasswordAuthentication no" > /etc/ssh/sshd_config.d/99-teslausb.conf
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
fi
if [ -f "$CONF" ] && [ -z "$(getconf_val VAULT_AUTOLOCK_MIN)" ]; then
  echo "export VAULT_AUTOLOCK_MIN='180'" >> "$CONF"
fi
if [ -f "$CONF" ] && [ -z "$(getconf_val SAMBA_ENABLED)" ]; then
  echo "export SAMBA_ENABLED='true'" >> "$CONF"
fi

echo "[hub-install] ensuring SMB/Samba share of TeslaCam (Einstellungen -> SMB-Freigabe; on by default)"
if [ -f "$CONF" ] && [ "$(getconf_val SAMBA_ENABLED)" != "false" ]; then
  SAMBA_GUEST=false bash "$HUB_SRC/../setup/pi/configure-samba.sh"
  systemctl enable --now smbd nmbd 2>/dev/null || true
  if ! pdbedit -L 2>/dev/null | grep -q '^pi:'; then
    GENPW="$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c16)"
    printf '%s\n%s\n' "$GENPW" "$GENPW" | smbpasswd -s -a pi >/dev/null 2>&1
    echo "[hub-install] generated SMB password for user 'pi': $GENPW"
    echo "[hub-install]   change it any time in Einstellungen -> SMB-Freigabe"
  fi
else
  systemctl disable --now smbd nmbd 2>/dev/null || true
fi

echo "[hub-install] remounting / ro"
mount / -o remount,ro

echo "[hub-install] done."
echo "  Primary UI: https://$(hostname).local/  (or https://<pi-ip>/)"
echo "  First visit sets up the vault (encryption passphrase = login password)."
