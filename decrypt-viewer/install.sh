#!/bin/bash -eu
# Installs the local decryptor/viewer service onto a running teslausb Pi.
# Run this ON the Pi as root (sudo -i), from the directory this script
# lives in (expects ./app and the *.service/*.timer files next to it).

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Remounting root read-write..."
/root/bin/remountfs_rw

echo "Installing ffmpeg..."
apt-get update -qq
apt-get install -y ffmpeg

echo "Installing pycryptodome..."
pip3 install --break-system-packages --quiet pycryptodome

echo "Copying app files to /opt/teslacam-decryptor..."
mkdir -p /opt/teslacam-decryptor
cp -r "$here/app" /opt/teslacam-decryptor/
cp "$here/update-latest-snapshot.sh" "$here/sync-to-nas.sh" "$here/retention.sh" /opt/teslacam-decryptor/
chmod +x /opt/teslacam-decryptor/update-latest-snapshot.sh \
         /opt/teslacam-decryptor/sync-to-nas.sh \
         /opt/teslacam-decryptor/retention.sh

echo "Installing systemd units..."
cp "$here/teslacam-latest-snapshot.service" \
   "$here/teslacam-latest-snapshot.timer" \
   "$here/teslacam-decryptor.service" \
   "$here/teslacam-sync.service" \
   "$here/teslacam-sync.timer" \
   "$here/teslacam-retention.service" \
   "$here/teslacam-retention.timer" \
   /etc/systemd/system/

mkdir -p /backingfiles/decrypt-viewer-state /dev/shm/teslacam

systemctl daemon-reload
systemctl enable --now teslacam-latest-snapshot.timer
systemctl enable --now teslacam-decryptor.service
systemctl enable --now teslacam-sync.timer
systemctl enable --now teslacam-retention.timer

echo "Remounting root read-only again..."
mount / -o remount,ro

echo "Done. Viewer should be reachable at http://<pi-ip>:8099 shortly."
echo "Check: systemctl status teslacam-decryptor teslacam-latest-snapshot.timer"
