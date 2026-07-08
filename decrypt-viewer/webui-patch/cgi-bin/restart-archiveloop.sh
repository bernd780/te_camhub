#!/bin/bash
# Restarts the teslausb archiveloop service so config changes made via the
# "Einstellungen" tab take effect, without a full reboot.

sudo systemctl restart teslausb >/dev/null 2>&1
rc=$?

if [ "$rc" -eq 0 ]; then
  printf 'HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n{"ok":true}\n'
else
  printf 'HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n{"ok":false,"error":"restart failed (rc=%s)"}\n' "$rc"
fi
