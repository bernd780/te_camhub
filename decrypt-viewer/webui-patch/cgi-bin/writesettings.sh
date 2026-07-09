#!/bin/bash
# Writes selected variables to /root/teslausb_setup_variables.conf from the
# "Einstellungen" tab. Only an allowlisted set of variable names may be
# written; every value is single-quote-escaped so the resulting
# `export VAR='...'` line is safe to source (no shell injection).

CONF=/root/teslausb_setup_variables.conf

fail() {
  printf 'HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n{"ok":false,"error":"%s"}\n' "$1"
  exit 0
}

# Read the POST body (application/x-www-form-urlencoded).
BODY=""
if [ "${REQUEST_METHOD:-}" = "POST" ] && [ -n "${CONTENT_LENGTH:-}" ]; then
  BODY=$(head -c "$CONTENT_LENGTH")
fi
[ -n "$BODY" ] || fail "empty body"

# urldecode: '+' -> space, %XX -> byte.
urldecode() {
  local s=${1//+/ }
  printf '%b' "${s//%/\\x}"
}

# Parse key=value pairs into an associative array.
declare -A P
IFS='&' read -r -a PAIRS <<<"$BODY"
for kv in "${PAIRS[@]}"; do
  k=${kv%%=*}
  v=${kv#*=}
  [ "$k" = "$kv" ] && v=""
  P["$(urldecode "$k")"]="$(urldecode "$v")"
done

# Map of form field -> conf variable name (allowlist). Anything not here is
# ignored, so the browser can never write an arbitrary variable/line.
declare -A MAP=(
  [archive_server]=ARCHIVE_SERVER
  [share_name]=SHARE_NAME
  [share_user]=SHARE_USER
  [share_password]=SHARE_PASSWORD
  [archive_recentclips]=ARCHIVE_RECENTCLIPS
  [archive_savedclips]=ARCHIVE_SAVEDCLIPS
  [archive_sentryclips]=ARCHIVE_SENTRYCLIPS
  [archive_trackmodeclips]=ARCHIVE_TRACKMODECLIPS
  [ssid]=SSID
  [wifipass]=WIFIPASS
  [time_zone]=TIME_ZONE
  [snapshot_interval]=SNAPSHOT_INTERVAL
  [archive_delay]=ARCHIVE_DELAY
  [sync_all_content]=SYNC_ALL_CONTENT
  [retention_mode]=RETENTION_MODE
  [retention_days]=RETENTION_DAYS
  [retention_free_gb]=RETENTION_FREE_GB
  [vault_nas_autounlock]=VAULT_NAS_AUTOUNLOCK
  [web_auth]=WEB_AUTH
  [web_tls]=WEB_TLS
  [ssh_disable_password]=SSH_DISABLE_PASSWORD
  [vault_autolock_min]=VAULT_AUTOLOCK_MIN
)
BOOLS=" archive_recentclips archive_savedclips archive_sentryclips archive_trackmodeclips sync_all_content vault_nas_autounlock web_auth web_tls ssh_disable_password "
INTS=" snapshot_interval archive_delay retention_days retention_free_gb vault_autolock_min "
PASSWORDS=" share_password wifipass "
# retention_mode is a constrained enum
if [ -v "P[retention_mode]" ]; then
  case "${P[retention_mode]}" in off|time|space) ;; *) fail "invalid retention_mode" ;; esac
fi

# Build the list of "VAR<TAB>escaped-value" updates to apply.
declare -a UPDATES
for field in "${!MAP[@]}"; do
  var=${MAP[$field]}

  # password fields: only write when a (non-empty) new value was provided
  if [[ "$PASSWORDS" == *" $field "* ]]; then
    [ -n "${P[$field]:-}" ] || continue
  fi

  # field entirely absent from the POST -> leave conf untouched
  # (checkboxes always submit true/false from the form, so they're present)
  [ -v "P[$field]" ] || continue
  val=${P[$field]}

  if [[ "$BOOLS" == *" $field "* ]]; then
    if [ "$val" = "true" ] || [ "$val" = "1" ] || [ "$val" = "on" ]; then val=true; else val=false; fi
  elif [[ "$INTS" == *" $field "* ]]; then
    [[ "$val" =~ ^[0-9]+$ ]] || fail "$field must be a whole number"
  fi

  # single-quote escape: ' -> '\''
  esc=${val//\'/\'\\\'\'}
  UPDATES+=("$var"$'\t'"'$esc'")
done

[ ${#UPDATES[@]} -gt 0 ] || fail "nothing to write"

# Make root writable, back up, apply each update (replace existing export line
# or append), then remount read-only again.
sudo mount / -o remount,rw || fail "remount rw failed"
sudo cp "$CONF" "$CONF.web.bak" 2>/dev/null

for u in "${UPDATES[@]}"; do
  var=${u%%$'\t'*}
  quoted=${u#*$'\t'}
  newline="export $var=$quoted"
  if sudo grep -q "^export $var=" "$CONF"; then
    # replace the line; write via a temp file to avoid sed-escaping the value
    sudo bash -c '
      conf="$1"; var="$2"; newline="$3"
      tmp=$(mktemp)
      while IFS= read -r l || [ -n "$l" ]; do
        case "$l" in
          "export $var="*) printf "%s\n" "$newline" ;;
          *) printf "%s\n" "$l" ;;
        esac
      done < "$conf" > "$tmp"
      cat "$tmp" > "$conf"
      rm -f "$tmp"
    ' _ "$CONF" "$var" "$newline"
  else
    printf '%s\n' "$newline" | sudo tee -a "$CONF" >/dev/null
  fi
done

sync
sudo mount / -o remount,ro 2>/dev/null   # may report "busy"; harmless

# If any security-relevant field was part of this save, (re)apply sshd/nginx/TLS.
# apply-security.sh reads the conf itself and gates every nginx reload with
# `nginx -t`, so a bad config can never lock the user out. Discard its HTTP output.
for f in "${!P[@]}"; do
  case "$f" in
    web_auth|web_tls|ssh_disable_password)
      bash /var/www/html/cgi-bin/apply-security.sh >/dev/null 2>&1
      break ;;
  esac
done

printf 'HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n{"ok":true}\n'
