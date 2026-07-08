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
  "archive_delay": "$ARCHIVE_DELAY"
}
EOF
