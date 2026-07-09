#!/usr/bin/env python3
"""
TeslaCam Hub — a single service that replaces the old stitched-together UI
(teslausb nginx/cgi + Te_FITI iframe). It serves one modern SPA over HTTPS with a
session login tied to the encrypted vault, and exposes viewer, files, settings and
diagnostics APIs. The teslausb core (gadget/snapshots/archive) is untouched.

  python server.py --port 443 --cert /mutable/tls/cert.pem --key /mutable/tls/key.pem \
                   --scan /run/teslacam-latest/mnt/TeslaCam --out /dev/shm/teslacam \
                   --state /backingfiles/decrypt-viewer-state [--redirect80]
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ssl, json, argparse, threading, time, secrets, base64, posixpath, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from vault import Vault, VaultError
from viewer import Viewer
from tesla_auth import TeslaAuth
import tesla_api, keybridge, hubconf, files as filemod, diag, nassync, mqtt_ha

WWW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")

CFG = {}          # filled in main()
VAULT = None
VIEWER = None
AUTH = None
_sessions = {}    # token -> expiry_ts
_sess_guard = threading.Lock()
_last_activity = time.time()

_bulk_job = {"running": False, "done": 0, "total": 0, "errors": []}
_bulk_guard = threading.Lock()


# ---------- sessions ----------------------------------------------------------
def _new_session():
    tok = secrets.token_urlsafe(24)
    with _sess_guard:
        _sessions[tok] = time.time() + 12 * 3600
    return tok

def _valid_session(tok):
    if not tok:
        return False
    with _sess_guard:
        exp = _sessions.get(tok)
        if not exp:
            return False
        if exp < time.time():
            _sessions.pop(tok, None)
            return False
    return VAULT.is_unlocked()

def _drop_sessions():
    with _sess_guard:
        _sessions.clear()

def _touch():
    global _last_activity
    _last_activity = time.time()

def sync_htpasswd(_pw):  # placeholder hook (no nginx here)
    pass


def autolock_loop():
    while True:
        time.sleep(20)
        try:
            mins = int(hubconf.getval("VAULT_AUTOLOCK_MIN") or "0")
        except ValueError:
            mins = 0
        if mins > 0 and VAULT.is_unlocked() and (time.time() - _last_activity) > mins * 60:
            VAULT.lock(); _drop_sessions(); VIEWER.clear_cache(); VIEWER.invalidate()
            print(f"[hub] auto-locked after {mins} min idle", flush=True)


def key_fetch_loop():
    """Fetch missing FEKs from Tesla once each into the vault (unlocked only)."""
    while True:
        time.sleep(60)
        try:
            if VAULT.is_unlocked() and AUTH.get_access_token():
                items = keybridge.scan_items(CFG["src"], VAULT.keys())
                if items:
                    got = 0
                    for i in range(0, len(items), 30):
                        try:
                            res = tesla_api.fetch_keys(items[i:i + 30], AUTH.get_access_token())
                        except tesla_api.DecryptApiError:
                            break
                        got += VAULT.merge_keys(res)
                    if got:
                        VIEWER.invalidate()
                        print(f"[hub] fetched {got} new keys", flush=True)
        except Exception as e:
            print("[hub] key fetch:", e, flush=True)


def _fetch_keys_for_clip(cid):
    """Try to fetch any missing FEKs for this clip's cameras right now (used
    when the user opens a clip, instead of waiting for the 60s background
    loop). Silently does nothing if the vault is locked or there's no valid
    Tesla token -- prepare() will then just report the cameras as locked."""
    if not (VAULT.is_unlocked() and AUTH.get_access_token()):
        return 0
    keys = VAULT.keys()
    items = []
    for path in VIEWER.clip_paths(cid).values():
        if not os.path.isfile(path):
            continue
        eid = keybridge.clip_id(CFG["src"], path)
        if eid in keys:
            continue
        try:
            with open(path, "rb") as f:
                head = f.read(keybridge.HEADER_SIZE)
        except OSError:
            continue
        if not keybridge.is_ecryptfs(head):
            continue
        try:
            wk = keybridge.parse_wrapped_key(head)
        except Exception:
            continue
        wk["id"] = eid
        items.append(wk)
    if not items:
        return 0
    try:
        res = tesla_api.fetch_keys(items, AUTH.get_access_token())
    except tesla_api.DecryptApiError:
        return 0
    got = VAULT.merge_keys(res)
    if got:
        VIEWER.invalidate()
    return got


def nas_sync_loop():
    """Periodically refresh the local-vs-NAS archive coverage percentage and
    push any newly-known per-video key sidecars to the NAS."""
    while True:
        try:
            nassync.refresh_status(CFG["scan"])
            if VAULT.is_unlocked():
                nassync.push_key_sidecars(CFG["scan"], VAULT)
            if hubconf.getval("SYNC_ALL_CONTENT") == "true":
                nassync.sync_media()
        except Exception as e:
            print("[hub] nas sync:", e, flush=True)
        time.sleep(600)


def mqtt_loop():
    """Publish Hub status to Home Assistant via MQTT Discovery every 30s.
    No-op (cheap getval() checks only) unless MQTT_ENABLED=true."""
    while True:
        try:
            if hubconf.getval("MQTT_ENABLED") == "true":
                host = hubconf.getval("MQTT_HOST")
                if mqtt_ha.ensure_connected(host, hubconf.getval("MQTT_PORT") or 1883,
                                             hubconf.getval("MQTT_USER"), hubconf.getval("MQTT_PASSWORD")):
                    counts = VIEWER.counts()
                    st = diag.status()
                    nas = nassync.status()
                    mqtt_ha.publish_state({
                        "clips": counts.get("clips", 0),
                        "encrypted": counts.get("encrypted", 0),
                        "nas_percent": nas.get("percent", 0),
                        "temp": (st.get("temp") or "").replace("'C", "").strip(),
                        "wifi_ssid": st.get("wifi_ssid") or "–",
                        "usb_connected": bool(st.get("gadget_active")),
                        "vault_unlocked": VAULT.is_unlocked(),
                    })
            else:
                mqtt_ha.disconnect()
        except Exception as e:
            print("[hub] mqtt:", e, flush=True)
        time.sleep(30)


def _bulk_worker():
    def progress(done, total, _cid):
        with _bulk_guard:
            _bulk_job["done"] = done
            _bulk_job["total"] = total
    try:
        res = VIEWER.bulk_prepare(on_progress=progress)
        with _bulk_guard:
            _bulk_job["errors"] = res.get("errors", [])
    except Exception as e:
        with _bulk_guard:
            _bulk_job["errors"].append(str(e))
    finally:
        with _bulk_guard:
            _bulk_job["running"] = False


# ---------- HTTP handler ------------------------------------------------------
class H(BaseHTTPRequestHandler):
    server_version = "TeslaCamHub"

    def log_message(self, *a):
        pass

    # -- helpers --
    def _cookie(self, name):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _auth_ok(self):
        return _valid_session(self._cookie("hub_session"))

    def _json(self, code, obj, extra=None):
        body = json.dumps(obj).encode()
        self._raw(code, body, "application/json", extra)

    def _raw(self, code, body, ctype, extra=None):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _sendfile(self, path, ctype, extra=None):
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        with open(path, "rb") as f:
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                start = int(a) if a else 0
                end = int(b) if b else size - 1
                end = min(end, size - 1)
                f.seek(start); chunk = f.read(end - start + 1)
                try:
                    self.send_response(206)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(len(chunk)))
                    for k, v in (extra or {}).items():
                        self.send_header(k, v)
                    self.end_headers(); self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(size))
                    for k, v in (extra or {}).items():
                        self.send_header(k, v)
                    self.end_headers()
                    while True:
                        chunk = f.read(1 << 20)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(n) if n else b""

    def _qs(self, key):
        return parse_qs(urlparse(self.path).query).get(key, [""])[0]

    # -- routing --
    def do_GET(self):
        path = urlparse(self.path).path
        # static SPA
        if path == "/" or path == "/index.html":
            return self._sendfile(os.path.join(WWW, "index.html"), "text/html",
                                  {"Cache-Control": "no-store"})
        if path.startswith("/static/"):
            fp = os.path.join(WWW, os.path.basename(path))
            if os.path.isfile(fp):
                ct = ("text/css" if fp.endswith(".css") else
                      "application/javascript" if fp.endswith(".js") else
                      "image/png" if fp.endswith(".png") else "application/octet-stream")
                return self._sendfile(fp, ct, {"Cache-Control": "max-age=86400"})
            return self._json(404, {"error": "not found"})
        # public
        if path == "/api/vault/status":
            return self._json(200, {"has_vault": VAULT.has_vault(),
                                    "unlocked": VAULT.is_unlocked(),
                                    "session": self._auth_ok()})
        # everything else needs a session
        if not self._auth_ok():
            return self._json(401, {"error": "auth"})
        _touch()

        if path == "/api/status":
            st = VIEWER.counts()
            st["login"] = AUTH.status()
            st["diag"] = diag.status()
            return self._json(200, st)
        if path == "/api/clips":
            return self._json(200, VIEWER.clips())
        if path == "/api/all_gps":
            return self._json(200, {"points": VIEWER.all_gps()})
        if path == "/api/trips":
            return self._json(200, VIEWER.trips())
        if path == "/api/event":
            ev = VIEWER.event_data(self._qs("id"))
            if ev is None:
                return self._json(404, {"error": "no event"})
            return self._json(200, ev)
        if path == "/api/thumb":
            t = VIEWER.make_thumb(self._qs("id"))
            if not t:
                return self._json(404, {"error": "no thumb"})
            return self._sendfile(t, "image/png" if t.endswith(".png") else "image/jpeg",
                                  {"Cache-Control": "max-age=86400"})
        if path.startswith("/media/"):
            full = VIEWER.resolve_media(path[len("/media/"):])
            if not full:
                return self._json(404, {"error": "not found"})
            ct = ("video/mp4" if full.endswith(".mp4") else
                  "application/json" if full.endswith(".json") else
                  "image/png" if full.endswith(".png") else "application/octet-stream")
            return self._sendfile(full, ct)
        if path == "/api/settings":
            return self._json(200, hubconf.read_settings())
        if path == "/api/files":
            entries = filemod.listdir(self._qs("path"))
            if entries is None:
                return self._json(404, {"error": "not found"})
            return self._json(200, {"roots": filemod.roots(), "entries": entries,
                                    "path": self._qs("path")})
        if path == "/api/files/download":
            full = filemod.resolve(self._qs("path"))
            if not full:
                return self._json(404, {"error": "not found"})
            name = os.path.basename(full)
            nl = name.lower()
            ct = ("image/jpeg" if nl.endswith((".jpg", ".jpeg")) else
                  "image/png" if nl.endswith(".png") else
                  "audio/mpeg" if nl.endswith(".mp3") else
                  "audio/wav" if nl.endswith(".wav") else
                  "audio/mp4" if nl.endswith(".m4a") else
                  "audio/ogg" if nl.endswith(".ogg") else
                  "audio/flac" if nl.endswith(".flac") else
                  "audio/aac" if nl.endswith(".aac") else "application/octet-stream")
            disp = None if self._qs("inline") else {"Content-Disposition": f'attachment; filename="{name}"'}
            return self._sendfile(full, ct, disp)
        if path == "/api/log":
            return self._json(200, {"text": diag.tail_log(self._qs("which"))})
        if path == "/api/tesla/login_url":
            return self._json(200, {"url": AUTH.make_login_url()})
        if path == "/api/nas/test":
            return self._json(200, hubconf.test_nas())
        if path == "/api/bulk_prepare":
            with _bulk_guard:
                return self._json(200, dict(_bulk_job))
        if path == "/api/nas/sync_status":
            return self._json(200, nassync.status())
        if path == "/api/nas/media_status":
            return self._json(200, nassync.media_status())
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = json.loads(self._body() or b"{}")
        except Exception:
            body = {}

        # public auth endpoints
        if path == "/api/setup":
            if VAULT.has_vault():
                return self._json(409, {"error": "vault exists"})
            pw = body.get("pass", "")
            if not pw:
                return self._json(400, {"error": "leeres Passwort"})
            imp_k, imp_t = _import_legacy() if body.get("import") else ({}, {})
            VAULT.create(pw, import_keys=imp_k, import_token=imp_t)
            tok = _new_session(); _touch(); VIEWER.invalidate()
            return self._json(200, {"ok": True, "imported": len(imp_k)}, self._setcookie(tok))
        if path == "/api/login":
            if VAULT.unlock_with_pass(body.get("pass", "")):
                tok = _new_session(); _touch(); VIEWER.invalidate()
                return self._json(200, {"ok": True}, self._setcookie(tok))
            return self._json(200, {"ok": False, "error": "falsches Passwort"})
        if path == "/api/logout":
            _drop_sessions(); VAULT.lock(); VIEWER.clear_cache()
            return self._json(200, {"ok": True})

        if not self._auth_ok():
            return self._json(401, {"error": "auth"})
        _touch()

        if path == "/api/vault/change_pass":
            old, new = body.get("old", ""), body.get("new", "")
            if not new:
                return self._json(400, {"ok": False, "error": "neues Passwort fehlt"})
            try:
                if not VAULT.change_pass(old, new):
                    return self._json(400, {"ok": False, "error": "aktuelles Passwort falsch"})
                return self._json(200, {"ok": True})
            except VaultError as e:
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/prepare":
            cid = body.get("id", "")
            _fetch_keys_for_clip(cid)
            return self._json(200, VIEWER.prepare(cid))
        if path == "/api/bulk_prepare":
            with _bulk_guard:
                if _bulk_job["running"]:
                    return self._json(200, dict(_bulk_job))
                _bulk_job.update(running=True, done=0, total=len(VIEWER.bulk_targets()), errors=[])
            threading.Thread(target=_bulk_worker, daemon=True).start()
            with _bulk_guard:
                return self._json(200, dict(_bulk_job))
        if path == "/api/settings":
            ok, err = hubconf.write_settings(body)
            if ok and "ssh_disable_password" in body:
                diag.apply_ssh(str(body.get("ssh_disable_password")) in ("true", "True", "1", "on"))
            if ok and "ap_fallback_only" in body:
                enabled = str(body.get("ap_fallback_only")) in ("true", "True", "1", "on")
                cur = hubconf.read_settings()
                apr = diag.apply_ap_fallback(enabled, ssid=cur.get("ap_ssid"),
                                              password=body.get("ap_pass") or None, ap_ip=cur.get("ap_ip"))
                if not apr.get("ok"):
                    ok = False
                    err = apr.get("error")
            return self._json(200 if ok else 400, {"ok": ok, "error": err})
        if path == "/api/files/mkdir":
            filemod.mkdir(body.get("path", "")); return self._json(200, {"ok": True})
        if path == "/api/files/delete":
            filemod.delete(body.get("path", "")); return self._json(200, {"ok": True})
        if path == "/api/files/rename":
            filemod.rename(body.get("path", ""), body.get("name", "")); return self._json(200, {"ok": True})
        if path == "/api/files/move":
            filemod.move(body.get("path", ""), body.get("dest", "")); return self._json(200, {"ok": True})
        if path == "/api/files/lockchime":
            try:
                filemod.set_lockchime(body.get("path", ""))
                return self._json(200, {"ok": True})
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/reboot":
            return self._json(200, diag.reboot())
        if path == "/api/toggle_drives":
            return self._json(200, diag.toggle_drives())
        if path == "/api/sync":
            return self._json(200, diag.trigger_sync())
        if path == "/api/ble/pair":
            return self._json(200, diag.ble_pair())
        if path == "/api/nas/sync_status/refresh":
            threading.Thread(target=lambda: nassync.refresh_status(CFG["scan"]), daemon=True).start()
            return self._json(200, {"ok": True})
        if path == "/api/nas/sync_media":
            threading.Thread(target=nassync.sync_media, daemon=True).start()
            return self._json(200, {"ok": True})
        if path == "/api/tesla/exchange":
            try:
                tok = AUTH.exchange_code(body.get("callback", ""))
                return self._json(200, {"ok": True, "refresh": bool(tok.get("refresh_token"))})
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})
        return self._json(404, {"error": "not found"})

    def do_PUT(self):
        # raw streaming upload: PUT /api/files/upload?path=<dir>&name=<file>
        path = urlparse(self.path).path
        if path != "/api/files/upload":
            return self._json(404, {"error": "not found"})
        if not self._auth_ok():
            return self._json(401, {"error": "auth"})
        _touch()
        destrel = self._qs("path"); name = self._qs("name")
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            saved = filemod.save_upload(destrel, name, _Limited(self.rfile, n))
            return self._json(200, {"ok": True, "name": saved})
        except Exception as e:
            return self._json(400, {"ok": False, "error": str(e)})

    def _setcookie(self, tok):
        secure = "; Secure" if CFG.get("tls") else ""
        return {"Set-Cookie": f"hub_session={tok}; HttpOnly; SameSite=Lax; Path=/{secure}"}


class _Limited:
    """Read exactly n bytes from a stream (for Content-Length uploads)."""
    def __init__(self, fp, n): self.fp, self.n = fp, n
    def read(self, sz=-1):
        if self.n <= 0:
            return b""
        want = self.n if sz < 0 else min(sz, self.n)
        data = self.fp.read(want)
        self.n -= len(data)
        return data


def _import_legacy():
    keys, tok = {}, {}
    state = CFG["state"]
    kp = os.path.join(state, "teslacam_keys.json")
    if os.path.isfile(kp):
        try:
            keys = json.load(open(kp, encoding="utf-8")) or {}
        except Exception:
            keys = {}
    tp = os.path.join(state, "token_store.json")
    if os.path.isfile(tp):
        try:
            tok = json.load(open(tp, encoding="utf-8")) or {}
        except Exception:
            tok = {}
    return keys, tok


def _redirect80():
    class R(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            host = (self.headers.get("Host", "") or "").split(":")[0]
            self.send_response(301)
            self.send_header("Location", f"https://{host}{self.path}")
            self.end_headers()
        do_POST = do_GET
    try:
        ThreadingHTTPServer(("0.0.0.0", 80), R).serve_forever()
    except Exception as e:
        print("[hub] port80 redirect unavailable:", e, flush=True)


def main():
    global VAULT, VIEWER, AUTH, CFG
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=443)
    p.add_argument("--cert"); p.add_argument("--key")
    p.add_argument("--scan", required=True)
    p.add_argument("--out", default="/dev/shm/teslacam")
    p.add_argument("--state", default="/backingfiles/decrypt-viewer-state")
    p.add_argument("--redirect80", action="store_true")
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True); os.makedirs(a.state, exist_ok=True)
    src = os.path.join(a.scan, "EncryptedClips")
    CFG = {"scan": a.scan, "src": src, "out": a.out, "state": a.state,
           "tls": bool(a.cert and a.key)}
    VAULT = Vault(a.state)
    AUTH = TeslaAuth(VAULT)
    VIEWER = Viewer(a.scan, a.out, VAULT)
    threading.Thread(target=autolock_loop, daemon=True).start()
    threading.Thread(target=key_fetch_loop, daemon=True).start()
    threading.Thread(target=nas_sync_loop, daemon=True).start()
    threading.Thread(target=mqtt_loop, daemon=True).start()
    if a.redirect80:
        threading.Thread(target=_redirect80, daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", a.port), H)
    scheme = "http"
    if CFG["tls"]:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(a.cert, a.key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    print(f"Hub {scheme}://0.0.0.0:{a.port} scan={a.scan} out={a.out} "
          f"vault={'present' if VAULT.has_vault() else 'none'}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
