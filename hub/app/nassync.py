"""
NAS archive coverage + per-clip key sidecar sync for the Hub.

ARCHIVE_SERVER/SHARE_NAME already point directly at the same TeslaCam/
EncryptedClips folder that teslausb's own archiveloop writes to, so local and
remote relative paths line up 1:1 -- no basename/size guessing needed, unlike
the old decrypt-viewer/retention.sh approach.

Two independent jobs, both driven by nas_sync_loop() in server.py:
  - refresh_status(): mounts read-only, compares local vs. remote clip
    (folder+timestamp) groups -> cached coverage percentage.
  - push_key_sidecars(): for every local clip whose FEK is already in the
    vault AND whose video is confirmed archived, writes a small encrypted
    per-video key file (<video>.mp4.key.json) next to it on the NAS, plus a
    one-time README explaining what the files are for. The sidecar is
    self-describing (names the exact video it belongs to) and is useless
    without the vault passphrase (FEK is sealed with the vault's MK).
"""
import os, re, json, time, base64, datetime, subprocess, tempfile, threading
import hubconf

TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(.+)\.mp4$", re.I)
README_NAME = "SCHLUESSEL-INFO.txt"
README_TEXT = (
    "Diese *.mp4.key.json Dateien enthalten den Entschluesselungs-Schluessel\n"
    "(FEK) fuer die gleichnamige Videodatei im selben Ordner, verschluesselt\n"
    "mit dem Tresor-Passwort der TeslaCam Hub App. Ohne dieses Passwort sind\n"
    "sie nutzlos. 'IMG_0001-front.mp4.key.json' gehoert zu 'IMG_0001-front.mp4'.\n"
)

_guard = threading.Lock()
_op_lock = threading.Lock()   # serializes mount operations (loop vs. manual refresh)
_cache = {"t": 0.0, "total": 0, "on_nas": 0, "percent": 0, "ok": None, "error": None}


def _mount(mnt, rw):
    server = hubconf.getval("ARCHIVE_SERVER")
    share = hubconf.getval("SHARE_NAME")
    user = hubconf.getval("SHARE_USER")
    password = hubconf.getval("SHARE_PASSWORD")
    vers = hubconf.getval("CIFS_VERSION") or "3.0"
    if not server or not share:
        raise RuntimeError("NAS nicht konfiguriert")
    os.makedirs(mnt, exist_ok=True)
    creds = tempfile.NamedTemporaryFile("w", delete=False)
    creds.write("username=%s\npassword=%s\n" % (user, password)); creds.close()
    os.chmod(creds.name, 0o600)
    opts = "credentials=%s,vers=%s,iocharset=utf8,%s" % (creds.name, vers, "rw" if rw else "ro")
    try:
        r = subprocess.run(["mount", "-t", "cifs", "//%s/%s" % (server, share), mnt,
                            "-o", opts], capture_output=True, text=True, timeout=25)
    finally:
        try: os.remove(creds.name)
        except OSError: pass
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "Mount fehlgeschlagen").splitlines()[-1][:200])


def _umount(mnt):
    subprocess.run(["umount", mnt], capture_output=True)


def _clip_groups(files):
    """{"folder|timestamp": {cam: relpath}} from a list of relpaths."""
    groups = {}
    for rel in files:
        m = TS_RE.search(os.path.basename(rel))
        if not m:
            continue
        ts, cam = m.group(1), m.group(2).lower()
        folder = os.path.dirname(rel)
        groups.setdefault(folder + "|" + ts, {})[cam] = rel
    return groups


def refresh_status(scan_dir):
    """Recompute local-vs-NAS clip coverage. scan_dir = .../TeslaCam"""
    src = os.path.join(scan_dir, "EncryptedClips")
    local = []
    for root, _, names in os.walk(src):
        for nm in names:
            if nm.endswith(".mp4"):
                local.append(os.path.relpath(os.path.join(root, nm), src).replace("\\", "/"))
    local_groups = _clip_groups(local)
    total = len(local_groups)
    on_nas = 0
    err = None
    mnt = "/tmp/hub_nas_status"
    if total:
        with _op_lock:
            try:
                _mount(mnt, rw=False)
                try:
                    remote = set()
                    for root, _, names in os.walk(mnt):
                        for nm in names:
                            if nm.endswith(".mp4"):
                                remote.add(os.path.relpath(os.path.join(root, nm), mnt).replace("\\", "/"))
                    for cams in local_groups.values():
                        if all(rel in remote for rel in cams.values()):
                            on_nas += 1
                finally:
                    _umount(mnt)
            except Exception as e:
                err = str(e)
    with _guard:
        _cache.update(t=time.time(), total=total, on_nas=on_nas,
                       percent=(round(on_nas * 100 / total) if total else 100),
                       ok=(err is None), error=err)
    return dict(_cache)


def status():
    with _guard:
        return dict(_cache)


def push_key_sidecars(scan_dir, vault):
    """Write a sealed per-video key sidecar on the NAS for every local clip
    whose FEK is already in the vault and whose video is already archived."""
    if not vault.is_unlocked():
        return {"ok": False, "error": "vault locked"}
    keys = vault.keys()
    if not keys:
        return {"ok": True, "written": 0}
    src = os.path.join(scan_dir, "EncryptedClips")
    mnt = "/tmp/hub_nas_keys"
    _op_lock.acquire()
    try:
        _mount(mnt, rw=True)
    except Exception as e:
        _op_lock.release()
        return {"ok": False, "error": str(e)}
    written, errors = 0, []
    try:
        readme = os.path.join(mnt, README_NAME)
        if not os.path.isfile(readme):
            try:
                with open(readme, "w", encoding="utf-8") as f:
                    f.write(README_TEXT)
            except Exception:
                pass
        for cid, fek_b64 in keys.items():
            rel = cid.lstrip("/")
            if not os.path.isfile(os.path.join(src, rel)):
                continue          # key for a clip no longer on the stick
            remote_mp4 = os.path.join(mnt, rel)
            if not os.path.isfile(remote_mp4):
                continue          # video not archived yet -- nothing to attach to
            sidecar = remote_mp4 + ".key.json"
            if os.path.isfile(sidecar):
                continue
            try:
                sealed = vault.seal(base64.b64decode(fek_b64))
                payload = json.dumps({
                    "video": os.path.basename(rel),
                    "for_file": rel,
                    "algo": "AES-256-GCM (TeslaCam Hub vault)",
                    "created": datetime.datetime.utcnow().isoformat() + "Z",
                    "key_sealed_b64": base64.b64encode(sealed).decode("ascii"),
                }, indent=2)
                tmp = sidecar + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp, sidecar)
                written += 1
            except Exception as e:
                errors.append(f"{rel}: {e}")
    finally:
        _umount(mnt)
        _op_lock.release()
    return {"ok": not errors, "written": written, "errors": errors}
