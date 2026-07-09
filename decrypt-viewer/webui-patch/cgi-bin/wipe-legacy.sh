#!/bin/bash
# Triggers the viewer's legacy-plaintext wipe (needs the unlocked vault, which
# lives in the :8099 process). Called server-side to avoid cross-origin fetch.
resp=$(curl -s -m 20 -X POST http://127.0.0.1:8099/api/vault/wipe_legacy 2>/dev/null)
[ -n "$resp" ] || resp='{"ok":false,"error":"viewer nicht erreichbar"}'
printf 'HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n%s\n' "$resp"
