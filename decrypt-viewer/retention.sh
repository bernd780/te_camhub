#!/bin/bash -u
# Retention for local recordings. Two mutually exclusive modes (conf var
# RETENTION_MODE): "time" (delete older than RETENTION_DAYS) or "space"
# (keep RETENTION_FREE_GB free). Default "off" = do nothing.
#
# SAFETY: nothing is deleted unless it is confirmed present on the NAS. The
# check is done LIVE against a read-only NAS mount by matching basename+size;
# if the NAS cannot be mounted, the run aborts without deleting anything.
# The encrypted originals live on cam_disk.bin, which the car accesses via the
# USB gadget — that filesystem is only mounted here when the gadget is proven
# to be unbound, otherwise cam_disk cleanup is skipped (never risk corruption).

CONF=/root/teslausb_setup_variables.conf
MNT=/mnt/nasret
CAM=/mnt/cam
LOG=/mutable/retention.log

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
getval() {
  local line; line=$(grep -m1 "^export $1=" "$CONF" 2>/dev/null); line=${line#export $1=}
  if [[ "$line" == \'*\' ]]; then line=${line#\'}; line=${line%\'}
  elif [[ "$line" == \"*\" ]]; then line=${line#\"}; line=${line%\"}; fi
  printf '%s' "$line"
}

MODE=$(getval RETENTION_MODE)
[ "$MODE" = "time" ] || [ "$MODE" = "space" ] || exit 0

DAYS=$(getval RETENTION_DAYS)
FREE_GB=$(getval RETENTION_FREE_GB)

# Serialize against snapshot / sync work.
exec 9>/backingfiles/snapshots/.retlock 2>/dev/null || exit 0
flock -n 9 || { log "another job holds the lock, skipping"; exit 0; }

# --- Build the authoritative "on NAS" index (basename \t size) --------------
SERVER=$(getval ARCHIVE_SERVER)
SHARE_NAME=$(getval SHARE_NAME)
USER=$(getval SHARE_USER)
PASS=$(getval SHARE_PASSWORD)
VERS=$(getval CIFS_VERSION); [ -n "$VERS" ] || VERS=3.0
SHAREROOT=${SHARE_NAME%%/*}
[ -n "$SERVER" ] && [ -n "$SHAREROOT" ] || { log "NAS not configured; abort"; exit 0; }

mkdir -p "$MNT"
CREDS=$(mktemp); chmod 600 "$CREDS"
{ printf 'username=%s\n' "$USER"; printf 'password=%s\n' "$PASS"; } > "$CREDS"
if ! mount -t cifs "//$SERVER/$SHAREROOT" "$MNT" -o "credentials=$CREDS,vers=$VERS,iocharset=utf8,ro" 2>>"$LOG"; then
  rm -f "$CREDS"; log "NAS mount failed; abort (nothing deleted)"; exit 1
fi
rm -f "$CREDS"

declare -A ONNAS
while IFS=$'\t' read -r sz nm; do
  [ -n "$nm" ] && ONNAS["$nm|$sz"]=1
done < <(find "$MNT" -type f -printf '%s\t%f\n' 2>/dev/null)
umount "$MNT" 2>/dev/null
log "NAS index: ${#ONNAS[@]} files"
[ "${#ONNAS[@]}" -gt 0 ] || { log "empty NAS index; abort (nothing deleted)"; exit 0; }

on_nas() {  # $1 = local file path
  local sz nm
  sz=$(stat -c '%s' "$1" 2>/dev/null) || return 1
  nm=$(basename "$1")
  [ -n "${ONNAS["$nm|$sz"]:-}" ]
}

deleted=0
delete_if_safe() { if on_nas "$1"; then rm -f "$1" && deleted=$((deleted+1)) && log "deleted $1"; fi; }

# --- Candidate selection ----------------------------------------------------
# No plaintext decrypted cache exists anymore (new security model: decryption is
# RAM-only). Retention operates ONLY on the encrypted originals on cam_disk.

if [ "$MODE" = "time" ]; then
  [[ "$DAYS" =~ ^[0-9]+$ ]] && [ "$DAYS" -gt 0 ] || { log "invalid RETENTION_DAYS='$DAYS'; abort"; exit 0; }

  # encrypted originals on cam_disk — ONLY if the USB gadget is unbound.
  UDC=$(cat /sys/kernel/config/usb_gadget/teslausb/UDC 2>/dev/null)
  if [ -n "${UDC//[[:space:]]/}" ]; then
    log "USB gadget active (UDC='$UDC'); skipping cam_disk cleanup this run"
  elif mountpoint -q "$CAM"; then
    log "$CAM already mounted by another process; skipping cam_disk cleanup"
  else
    if mount "$CAM" 2>>"$LOG"; then
      while IFS= read -r f; do delete_if_safe "$f"; done \
        < <(find "$CAM/TeslaCam/SavedClips" "$CAM/TeslaCam/SentryClips" \
                 -type f -mtime +"$DAYS" 2>/dev/null)
      # prune now-empty event folders
      find "$CAM/TeslaCam/SavedClips" "$CAM/TeslaCam/SentryClips" \
           -mindepth 1 -type d -empty -delete 2>/dev/null
      sync; umount "$CAM" 2>/dev/null
    else
      log "could not mount $CAM; skipping cam_disk cleanup"
    fi
  fi

elif [ "$MODE" = "space" ]; then
  # cam_disk space is handled by teslausb's own freespacemanager /
  # clean_cam_mount (which honors RETENTION_FREE_GB via the small archiveloop
  # patch). Nothing to do here beyond validating the value; teslausb deletes the
  # oldest clips from cam_disk when free space drops below the threshold.
  [[ "$FREE_GB" =~ ^[0-9]+$ ]] && [ "$FREE_GB" -gt 0 ] || { log "invalid RETENTION_FREE_GB='$FREE_GB'; abort"; exit 0; }
  log "space mode: cam_disk cleanup delegated to teslausb freespacemanager (reserve ${FREE_GB}G)"
fi

log "retention run complete (mode=$MODE, deleted=$deleted)"
