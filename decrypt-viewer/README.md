# decrypt-viewer

Local, on-Pi decryption + web viewer for teslausb, for cars with dashcam
encryption enabled (firmware 2026.20+, `TeslaCam/EncryptedClips/...`).
Ported from [Te_FITI](https://github.com/bernd780/Te_FITI)'s `app/` module,
reused mostly unchanged since `server.py` already takes `--scan`/`--src`/`--out`
as CLI args.

Runs independently of `archiveloop`/`make_snapshot.sh` — it just watches
`/backingfiles/snapshots/` for the newest snapshot (see
`update-latest-snapshot.sh`) and points `server.py` at it via the stable
symlink `/run/teslacam-latest/mnt`. No changes to teslausb's own archive
pipeline are needed beyond the `TeslaCam/EncryptedClips` path fix in
`../run/make_snapshot.sh`.

- Decrypted clips + telemetry/thumbnails: `/backingfiles/decrypted/`
  (original encrypted files are left untouched; teslausb's own
  `archive_clips` still archives those to the NAS as configured).
- FEK keystore + Tesla OAuth token: `/backingfiles/decrypt-viewer-state/`.
- Web UI: `http://<pi-ip>:8099`.

## Install

```
sudo -i
cd /path/to/decrypt-viewer
./install.sh
```

Needs a one-time Tesla login (PKCE, `GET /api/login/url` in the UI) unless
an existing `token_store.json` is copied into
`/backingfiles/decrypt-viewer-state/` beforehand.
