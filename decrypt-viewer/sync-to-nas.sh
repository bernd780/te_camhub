#!/bin/bash -u
# Copies the "extra" content that teslausb's own archiveloop does NOT handle
# to the NAS: decrypted clips, LightShow (incl. Wraps), Boombox, and Music.
# COPY ONLY — never deletes anything on the NAS or locally. Guarded by
# SYNC_ALL_CONTENT in the conf and a flock so it never runs concurrently with
# teslausb's snapshot/free-space work.
#
# Encrypted original clips remain teslausb's job (archive_teslacam_clips).

CONF=/root/teslausb_setup_variables.conf
MNT=/mnt/nassync
STATE=/backingfiles/decrypt-viewer-state
MANIFEST=$STATE/synced.list
LOG=/mutable/sync-to-nas.log

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

getval() {
  local line; line=$(grep -m1 "^export $1=" "$CONF" 2>/dev/null); line=${line#export $1=}
  if [[ "$line" == \'*\' ]]; then line=${line#\'}; line=${line%\'}
  elif [[ "$line" == \"*\" ]]; then line=${line#\"}; line=${line%\"}; fi
  printf '%s' "$line"
}

[ "$(getval SYNC_ALL_CONTENT)" = "true" ] || exit 0

# Serialize against snapshot / free-space work.
exec 9>/backingfiles/snapshots/.synclock 2>/dev/null || exit 0
flock -n 9 || { log "another job holds the lock, skipping"; exit 0; }

SERVER=$(getval ARCHIVE_SERVER)
SHARE_NAME=$(getval SHARE_NAME)
USER=$(getval SHARE_USER)
PASS=$(getval SHARE_PASSWORD)
VERS=$(getval CIFS_VERSION); [ -n "$VERS" ] || VERS=3.0
SHAREROOT=${SHARE_NAME%%/*}         # e.g. Tesla_Video

[ -n "$SERVER" ] && [ -n "$SHAREROOT" ] || { log "NAS not configured"; exit 0; }

# Reachable?
if ! timeout 6 /root/bin/archive-is-reachable.sh "$SERVER" >/dev/null 2>&1; then
  ping -c1 -W3 "$SERVER" >/dev/null 2>&1 || { log "NAS $SERVER unreachable"; exit 0; }
fi

mkdir -p "$MNT" "$STATE"
CREDS=$(mktemp); chmod 600 "$CREDS"
{ printf 'username=%s\n' "$USER"; printf 'password=%s\n' "$PASS"; } > "$CREDS"

if ! mount -t cifs "//$SERVER/$SHAREROOT" "$MNT" \
      -o "credentials=$CREDS,vers=$VERS,iocharset=utf8,rw,file_mode=0777,dir_mode=0777" 2>>"$LOG"; then
  rm -f "$CREDS"; log "mount failed"; exit 1
fi
rm -f "$CREDS"

# src|dest-subdir pairs. rsync copies (no --delete): NAS keeps everything.
# NOTE: no decrypted clips are ever synced — the new security model keeps
# plaintext only in RAM. The NAS holds encrypted originals (via teslausb) plus
# the non-clip partitions and the encrypted key material below.
sync_tree() {
  local src=$1 dst=$2
  [ -d "$src" ] || return 0
  mkdir -p "$MNT/$dst"
  # -r recurse, -t times, --size-only (avoid perm/owner churn on CIFS),
  # --no-perms/--no-owner/--no-group for CIFS sanity. No --delete.
  rsync -rt --size-only --no-perms --no-owner --no-group \
        "$src/" "$MNT/$dst/" >>"$LOG" 2>&1 && log "synced $src -> $dst" \
        || log "rsync issue for $src (continuing)"
}

sync_tree /var/www/html/fs/LightShow     LightShow
sync_tree /var/www/html/fs/Boombox       Boombox
# Only sync Music if teslausb's own music archive isn't configured.
[ -z "$(getval MUSIC_SHARE_NAME)" ] && sync_tree /var/www/html/fs/Music Music

# Encrypted key material:
#  - the whole (passphrase-wrapped) vault, as an off-stick backup
#  - the per-clip encrypted key sidecars staged by the viewer service, placed
#    next to the encrypted clips in the TeslaCam tree on the NAS.
STATE=/backingfiles/decrypt-viewer-state
mkdir -p "$MNT/teslausb-keys-backup"
for f in vault.enc vault.wrap; do
  [ -f "$STATE/$f" ] && rsync -t "$STATE/$f" "$MNT/teslausb-keys-backup/" >>"$LOG" 2>&1
done
if [ -d "$STATE/keysidecars" ]; then
  # sidecars mirror EncryptedClips/... -> NAS TeslaCam/EncryptedClips/...
  mkdir -p "$MNT/TeslaCam"
  rsync -rt --no-perms --no-owner --no-group \
        "$STATE/keysidecars/" "$MNT/TeslaCam/" >>"$LOG" 2>&1 && log "synced key sidecars" \
        || log "sidecar rsync issue (continuing)"
fi

# Rebuild the manifest of what is now on the NAS (path + size), used by the
# retention job as a fast reference (retention still re-verifies live).
: > "$MANIFEST.tmp"
for d in TeslaCam LightShow Boombox Music; do
  [ -d "$MNT/$d" ] || continue
  ( cd "$MNT/$d" && find . -type f -printf '%s %p\n' 2>/dev/null | sed "s#^\([0-9]*\) \./#\1 $d/#" ) >> "$MANIFEST.tmp"
done
mv "$MANIFEST.tmp" "$MANIFEST"

sync
umount "$MNT" 2>/dev/null
log "sync run complete"
