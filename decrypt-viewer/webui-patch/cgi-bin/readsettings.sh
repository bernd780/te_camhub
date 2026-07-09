#!/bin/bash
# Reads selected variables from /root/teslausb_setup_variables.conf and returns
# them as JSON for the "Einstellungen" tab. Passwords are NEVER returned in
# clear text — only a boolean *_set flag, so the UI can show a placeholder.

CONF=/root/teslausb_setup_variables.conf

# Return the raw value of an "export VAR=..." line with surrounding single or
# double quotes stripped. Empty string if unset.
getval() {
  local line
  line=$(sudo grep -m1 "^export $1=" "$CONF" 2>/dev/null)
  line=${line#export $1=}
  # strip one layer of matching quotes
  if [[ "$line" == \'*\' ]]; then
    line=${line#\'}; line=${line%\'}
  elif [[ "$line" == \"*\" ]]; then
    line=${line#\"}; line=${line%\"}
  fi
  printf '%s' "$line"
}

# JSON-escape a string value (backslash, quote, control chars).
jsonesc() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\t'/\\t}
  s=${s//$'\r'/\\r}
  s=${s//$'\n'/\\n}
  printf '%s' "$s"
}

isset() {
  if sudo grep -q "^export $1=" "$CONF" 2>/dev/null; then echo true; else echo false; fi
}

ARCHIVE_SYSTEM=$(jsonesc "$(getval ARCHIVE_SYSTEM)")
ARCHIVE_SERVER=$(jsonesc "$(getval ARCHIVE_SERVER)")
SHARE_NAME=$(jsonesc "$(getval SHARE_NAME)")
SHARE_USER=$(jsonesc "$(getval SHARE_USER)")
TIME_ZONE=$(jsonesc "$(getval TIME_ZONE)")
SNAPSHOT_INTERVAL=$(jsonesc "$(getval SNAPSHOT_INTERVAL)")
ARCHIVE_DELAY=$(jsonesc "$(getval ARCHIVE_DELAY)")
SSID=$(jsonesc "$(getval SSID)")

# booleans: default true only for RECENTCLIPS per teslausb defaults, but just
# report the literal stored value; UI treats missing as unchecked.
ARCHIVE_RECENTCLIPS=$(getval ARCHIVE_RECENTCLIPS)
ARCHIVE_SAVEDCLIPS=$(getval ARCHIVE_SAVEDCLIPS)
ARCHIVE_SENTRYCLIPS=$(getval ARCHIVE_SENTRYCLIPS)
ARCHIVE_TRACKMODECLIPS=$(getval ARCHIVE_TRACKMODECLIPS)

SYNC_ALL_CONTENT=$(getval SYNC_ALL_CONTENT)
RETENTION_MODE=$(getval RETENTION_MODE)
RETENTION_DAYS=$(jsonesc "$(getval RETENTION_DAYS)")
RETENTION_FREE_GB=$(jsonesc "$(getval RETENTION_FREE_GB)")
VAULT_NAS_AUTOUNLOCK=$(getval VAULT_NAS_AUTOUNLOCK)
WEB_AUTH=$(getval WEB_AUTH)
WEB_TLS=$(getval WEB_TLS)
SSH_DISABLE_PASSWORD=$(getval SSH_DISABLE_PASSWORD)
VAULT_AUTOLOCK_MIN=$(jsonesc "$(getval VAULT_AUTOLOCK_MIN)")
if sudo test -f /backingfiles/decrypt-viewer-state/teslacam_keys.json || sudo test -f /backingfiles/decrypt-viewer-state/token_store.json; then
  LEGACY_PRESENT=true
else
  LEGACY_PRESENT=false
fi
# nginx-based web-login/TLS is disabled on this image (no working SSL + reloads
# are no-ops). Always report unavailable so the UI greys the toggles out.
TLS_SUPPORTED=false

SHARE_PASSWORD_SET=$(isset SHARE_PASSWORD)
WIFIPASS_SET=$(isset WIFIPASS)

cat << EOF
HTTP/1.0 200 OK
Content-type: application/json

{
  "archive_system": "$ARCHIVE_SYSTEM",
  "archive_server": "$ARCHIVE_SERVER",
  "share_name": "$SHARE_NAME",
  "share_user": "$SHARE_USER",
  "share_password_set": $SHARE_PASSWORD_SET,
  "archive_recentclips": "$ARCHIVE_RECENTCLIPS",
  "archive_savedclips": "$ARCHIVE_SAVEDCLIPS",
  "archive_sentryclips": "$ARCHIVE_SENTRYCLIPS",
  "archive_trackmodeclips": "$ARCHIVE_TRACKMODECLIPS",
  "ssid": "$SSID",
  "wifipass_set": $WIFIPASS_SET,
  "time_zone": "$TIME_ZONE",
  "snapshot_interval": "$SNAPSHOT_INTERVAL",
  "archive_delay": "$ARCHIVE_DELAY",
  "sync_all_content": "$SYNC_ALL_CONTENT",
  "retention_mode": "$RETENTION_MODE",
  "retention_days": "$RETENTION_DAYS",
  "retention_free_gb": "$RETENTION_FREE_GB",
  "vault_nas_autounlock": "$VAULT_NAS_AUTOUNLOCK",
  "web_auth": "$WEB_AUTH",
  "web_tls": "$WEB_TLS",
  "ssh_disable_password": "$SSH_DISABLE_PASSWORD",
  "vault_autolock_min": "$VAULT_AUTOLOCK_MIN",
  "legacy_present": $LEGACY_PRESENT,
  "tls_supported": $TLS_SUPPORTED
}
EOF
