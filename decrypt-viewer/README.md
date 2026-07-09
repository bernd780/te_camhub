# decrypt-viewer

Local, on-Pi decryption + web viewer for teslausb, for cars with dashcam
encryption enabled (firmware 2026.20+, `TeslaCam/EncryptedClips/...`).
Ported from [Te_FITI](https://github.com/bernd780/Te_FITI)'s `app/` module.

## Security model (vault)

The stick must be worthless if pulled. Therefore:

- **No plaintext videos on the stick.** Clips are decrypted **on demand into
  RAM only** (`OUT_DIR=/dev/shm/teslacam`, tmpfs) and streamed to the browser;
  wiped on every reboot. Nothing decrypted is ever written to `/backingfiles`.
- **No unprotected secrets on the stick.** FEKs + the Tesla OAuth token live in
  an **encrypted vault** (`app/vault.py`): a random 32-byte master key (MK)
  encrypts `vault.enc` (AES-256-GCM); MK itself is wrapped by a scrypt-derived
  key from the user **passphrase** → `vault.wrap`. MK and the decrypted secrets
  exist only in the service's RAM while unlocked.
- **Keys fetched once.** A FEK obtained from Tesla is stored in the vault and
  never re-requested.
- **Per-clip key on the NAS, encrypted.** For each encrypted clip on the NAS,
  the service stages `<clip>.mp4.key` = the FEK sealed with the MK (never
  plaintext), so each NAS clip is self-describing yet protected.
- **Optional NAS auto-unlock.** With `VAULT_NAS_AUTOUNLOCK=true` the MK is also
  written to the NAS (`teslausb-keys-backup/vault.mk`, never on the stick); when
  the NAS is reachable the viewer unlocks without a passphrase. Pull the stick
  alone → still useless.
- **Vault gate.** All key/decrypt/token endpoints return HTTP 423 until the
  vault is unlocked. The viewer shows a setup screen (first run) or an unlock
  screen (`www/index.html` `#vaultgate`).

First run: open the viewer, set a passphrase (optionally import existing keys).
After each reboot the vault is locked until you unlock it (or NAS auto-unlock).

**Caveat:** the web UI has no auth and no TLS — the passphrase travels over
plain HTTP on the LAN and, once unlocked, anyone on the LAN can view. The vault
protects data **at rest** (the stated goal); add `.htpasswd`/TLS for LAN
hardening as a follow-up.

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
- Also embedded as an iframe in teslausb's own web UI (`http://<pi-ip>/`),
  replacing the built-in "Viewer" tab's encryption-unaware player — see
  `webui-patch/index.html` (patched copy of `/var/www/html/index.html`;
  only the `tab content7` block was changed, all other tabs are untouched).
  A pristine copy of `/var/www/html/index.html.bak` is left on the Pi by
  the patch for easy diffing/reverting.

  **Important:** the old player markup inside `content7` is *hidden*
  (`display:none`), not deleted. teslausb's own inline script references
  those element IDs unconditionally at load time — `showcontrols()`,
  `setLayout()`, a `ResizeObserver` on `#sentrymap`/`#tickmarkscanvas`, and
  the `videolist.sh` callback that populates the RecentClips/SavedClips/
  SentryClips dropdowns. Deleting the markup throws `TypeError`s there,
  which (since it's all one big `<script>` block) also stops the
  *unrelated* code further down in the same block from running — including
  the tab-label visibility logic, which is why an earlier attempt at this
  patch made the Viewer/Recordings/Files tab labels disappear entirely.
  Keep the old markup present-but-hidden if you ever regenerate this patch.

## Install

```
sudo -i
cd /path/to/decrypt-viewer
./install.sh
```

Needs a one-time Tesla login (PKCE, `GET /api/login/url` in the UI) unless
an existing `token_store.json` is copied into
`/backingfiles/decrypt-viewer-state/` beforehand.

To embed the viewer into teslausb's own "Viewer" tab (optional, done via
`webui-patch/index.html` in this repo):
```
sudo /root/bin/remountfs_rw
sudo cp /var/www/html/index.html /var/www/html/index.html.bak
sudo cp webui-patch/index.html /var/www/html/index.html
sudo cp webui-patch/cgi-bin/*.sh /var/www/html/cgi-bin/ && sudo chmod +x /var/www/html/cgi-bin/*.sh
sudo reboot   # remounting root back to ro live tends to report "busy"
```

## "Einstellungen" tab (config editor)

`webui-patch/index.html` also adds an eighth tab, **Einstellungen**, that edits
the most common values in `/root/teslausb_setup_variables.conf` from the
browser (NAS/archive: server, share, user, password, per-clip-type archiving;
network: SSID + WiFi password; system: timezone, snapshot interval, archive
delay). Backed by three CGI scripts in `webui-patch/cgi-bin/`:

- `readsettings.sh` — conf → JSON. **Passwords are never returned in clear**;
  only `*_set` booleans, so the field shows a "unverändert" placeholder.
- `writesettings.sh` — urlencoded POST → conf. **Allowlisted** variable names
  only; every value is single-quote-escaped (`'` → `'\''`) so the resulting
  `export VAR='...'` line stays safe to `source` (no shell injection); bools
  normalized to `true`/`false`, ints validated; password fields written only
  when a new value is supplied. Remounts `/` rw, backs up to `conf.web.bak`,
  replaces-or-appends each `export` line, remounts ro.
- `restart-archiveloop.sh` — `systemctl restart teslausb` so archive changes
  take effect without a full reboot. The Save button offers this via a confirm
  dialog ("Jetzt neu starten?").

The tab's JS lives in a **self-contained `<script>` inside the `content8`
div** (same reasoning as the hidden-player note above: an error there must not
take down the rest of teslausb's inline script).

**Security (status quo):** the teslausb web UI has no auth (`auth_basic off`),
so anyone on the LAN can set — but not read — the NAS/WiFi passwords via this
tab. That matches the existing exposure of the stock cgi-bin scripts
(`reboot.sh`, `rm.sh`, …). Add `.htpasswd` if that matters to you.
