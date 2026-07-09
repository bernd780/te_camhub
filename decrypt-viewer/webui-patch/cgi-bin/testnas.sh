#!/bin/bash
# Tests the NAS/CIFS connection with the values entered in the "Einstellungen"
# tab. If the password field is empty, the currently stored SHARE_PASSWORD from
# the conf is used. The password is never echoed back or logged.

CONF=/root/teslausb_setup_variables.conf
MNT=/tmp/nastest

reply() { printf 'HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n%s\n' "$1"; }
jesc() { local s=$1; s=${s//\\/\\\\}; s=${s//\"/\\\"}; printf '%s' "$s"; }
fail() { sudo umount "$MNT" 2>/dev/null; reply "{\"ok\":false,\"error\":\"$(jesc "$1")\"}"; exit 0; }

# read POST body
BODY=""
if [ "${REQUEST_METHOD:-}" = "POST" ] && [ -n "${CONTENT_LENGTH:-}" ]; then
  BODY=$(head -c "$CONTENT_LENGTH")
fi

urldecode() { local s=${1//+/ }; printf '%b' "${s//%/\\x}"; }
declare -A P
IFS='&' read -r -a PAIRS <<<"$BODY"
for kv in "${PAIRS[@]}"; do
  k=${kv%%=*}; v=${kv#*=}; [ "$k" = "$kv" ] && v=""
  P["$(urldecode "$k")"]="$(urldecode "$v")"
done

server=${P[archive_server]:-}
share=${P[share_name]:-}
user=${P[share_user]:-}
pass=${P[share_password]:-}

# fall back to stored values when a field was left blank
getval() {
  local line; line=$(sudo grep -m1 "^export $1=" "$CONF" 2>/dev/null); line=${line#export $1=}
  if [[ "$line" == \'*\' ]]; then line=${line#\'}; line=${line%\'}
  elif [[ "$line" == \"*\" ]]; then line=${line#\"}; line=${line%\"}; fi
  printf '%s' "$line"
}
[ -n "$server" ] || server=$(getval ARCHIVE_SERVER)
[ -n "$share" ]  || share=$(getval SHARE_NAME)
[ -n "$user" ]   || user=$(getval SHARE_USER)
[ -n "$pass" ]   || pass=$(getval SHARE_PASSWORD)
vers=$(getval CIFS_VERSION); [ -n "$vers" ] || vers=3.0

[ -n "$server" ] || fail "kein Server angegeben"
[ -n "$share" ]  || fail "kein Share angegeben"

sudo mkdir -p "$MNT"

# credentials via a temp file so they never appear in the process list
CREDS=$(mktemp)
chmod 600 "$CREDS"
{ printf 'username=%s\n' "$user"; printf 'password=%s\n' "$pass"; } > "$CREDS"

if ! out=$(sudo mount -t cifs "//$server/$share" "$MNT" \
        -o "credentials=$CREDS,vers=$vers,iocharset=utf8,ro" 2>&1); then
  rm -f "$CREDS"
  # retry once with vers negotiation off (older NAS)
  if ! out=$(sudo mount -t cifs "//$server/$share" "$MNT" \
          -o "username=$user,password=$pass,iocharset=utf8,ro" 2>&1); then
    fail "Mount fehlgeschlagen: ${out##*: }"
  fi
fi
rm -f "$CREDS" 2>/dev/null

# reachable + mounted. Test writability by remounting rw and touching a file.
writable=false
sudo umount "$MNT" 2>/dev/null
CREDS2=$(mktemp); chmod 600 "$CREDS2"
{ printf 'username=%s\n' "$user"; printf 'password=%s\n' "$pass"; } > "$CREDS2"
if sudo mount -t cifs "//$server/$share" "$MNT" -o "credentials=$CREDS2,vers=$vers,iocharset=utf8,rw" 2>/dev/null; then
  if sudo touch "$MNT/.teslausb_write_test" 2>/dev/null; then
    writable=true
    sudo rm -f "$MNT/.teslausb_write_test" 2>/dev/null
  fi
fi
rm -f "$CREDS2" 2>/dev/null
sudo umount "$MNT" 2>/dev/null

reply "{\"ok\":true,\"writable\":$writable}"
