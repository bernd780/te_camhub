"""
Viewer engine for the TeslaCam Hub. Scans the TeslaCam tree (which contains both
encrypted `EncryptedClips/...` and, on older firmware, plain
`RecentClips|SavedClips|SentryClips/...`) plus any configured extra roots.

Per camera .mp4:
  - plain   : not eCryptfs           -> stream directly
  - ready   : encrypted + in RAM cache
  - key     : encrypted + FEK in vault -> decrypt on demand
  - locked  : encrypted + no FEK yet

Decryption is ON DEMAND into a tmpfs cache (OUT_DIR, e.g. /dev/shm) — never onto
the stick. Keys come from the vault (RAM-only). ffmpeg makes thumbnails.
"""
import os, glob, json, re, posixpath, hashlib, datetime, math, base64, threading, time
from concurrent.futures import ThreadPoolExecutor
from ecryptfs import EcryptfsFile
from keybridge import is_ecryptfs
import keybridge, pipeline

TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(.+)\.mp4$", re.I)
ENC_PREFIX = "EncryptedClips"
TRIP_GAP_MIN = 20


class Viewer:
    def __init__(self, scan_dir, out_dir, vault, extra_roots=None,
                 tmpfs_cap=200 * 1024 * 1024):
        self.scan_dir = scan_dir
        self.out_dir = out_dir
        self.vault = vault
        self.extra_roots = extra_roots or []
        self.tmpfs_cap = tmpfs_cap
        self._enc = {}                      # sr -> is_encrypted
        self._meta = {}
        self._lcache = {"t": 0.0, "data": None}
        self._guard = threading.Lock()
        self._prep_locks = {}
        os.makedirs(os.path.join(out_dir, ".thumbs"), exist_ok=True)

    # ---- keys from the vault ------------------------------------------------
    def _keys(self):
        try:
            return self.vault.keys() if self.vault.is_unlocked() else {}
        except Exception:
            return {}

    # ---- path helpers -------------------------------------------------------
    def _cache(self, sr): return os.path.normpath(os.path.join(self.out_dir, sr))
    def _src(self, sr):   return os.path.normpath(os.path.join(self.scan_dir, sr))
    def _is_enc_sr(self, sr): return sr == ENC_PREFIX or sr.startswith(ENC_PREFIX + "/")
    def _enc_id(self, sr):    return sr[len(ENC_PREFIX) + 1:] if self._is_enc_sr(sr) else sr

    def _is_encrypted(self, sr):
        if sr in self._enc:
            return self._enc[sr]
        try:
            with open(self._src(sr), "rb") as f:
                res = is_ecryptfs(f.read(28))
        except Exception:
            res = False
        self._enc[sr] = res
        return res

    def _key_for(self, sr, keys):
        if sr in keys: return base64.b64decode(keys[sr])
        eid = self._enc_id(sr)
        if eid in keys: return base64.b64decode(keys[eid])
        return None

    # ---- state --------------------------------------------------------------
    def _cam_state(self, sr, keys):
        if self._is_encrypted(sr):
            if os.path.exists(self._cache(sr)):
                return {"state": "ready", "url": "media/" + sr}
            if sr in keys or self._enc_id(sr) in keys:
                return {"state": "key"}
            return {"state": "locked"}
        return {"state": "plain", "url": "media/" + sr}

    def _telsr(self, folder, ts):
        return (folder + "/" if folder else "") + f"{ts}-front.telemetry.json"

    def _scan(self, keys=None):
        if keys is None:
            keys = self._keys()
        clips = {}
        for path in glob.glob(os.path.join(self.scan_dir, "**", "*.mp4"), recursive=True):
            m = TS_RE.search(os.path.basename(path))
            if not m:
                continue
            ts, cam = m.group(1), m.group(2).lower()
            sr = os.path.relpath(path, self.scan_dir).replace("\\", "/")
            folder = posixpath.dirname(sr)
            ck = folder + "|" + ts
            c = clips.setdefault(ck, {"id": ck, "folder": folder, "timestamp": ts,
                                      "cameras": {}, "telemetry": None})
            c["cameras"][cam] = self._cam_state(sr, keys)
            if cam == "front" and os.path.exists(self._cache(self._telsr(folder, ts))):
                c["telemetry"] = "media/" + self._telsr(folder, ts)
        out = [self._finalize(c) for c in clips.values()]
        out.sort(key=lambda x: x["timestamp"], reverse=True)
        return out

    def _finalize(self, c):
        sts = [cm["state"] for cm in c["cameras"].values()]
        c["needs_prepare"] = "key" in sts
        c["has_locked"] = "locked" in sts
        c["playable"] = any(s in ("plain", "ready") for s in sts)
        c["encrypted"] = any(s in ("ready", "key", "locked") for s in sts)
        enc_sts = [s for s in sts if s in ("ready", "key", "locked")]
        c["cams_encrypted"] = len(enc_sts)
        c["cams_keyed"] = sum(1 for s in enc_sts if s in ("ready", "key"))
        cached = self._meta.get(c["id"])
        if cached is None:
            cached = self._compute_meta(c)
            self._meta[c["id"]] = cached
        c.update(cached)
        return c

    def _compute_meta(self, c):
        telp = self._cache(self._telsr(c["folder"], c["timestamp"]))
        ht, gps, track, reason = False, None, [], None
        if os.path.isfile(telp):
            try:
                tel = json.load(open(telp, encoding="utf-8"))
                ht = tel.get("frame_count", 0) > 0
                pts = [[f["lat"], f["lon"]] for f in tel.get("frames", []) if f.get("lat") and f.get("lon")]
                if pts:
                    gps = {"center_lat": sum(p[0] for p in pts) / len(pts),
                           "center_lon": sum(p[1] for p in pts) / len(pts)}
                    track = pts[::max(1, len(pts) // 40)]
            except Exception:
                pass
        ejp = os.path.join(self.scan_dir, c["folder"], "event.json")
        he = os.path.isfile(ejp)
        if he:
            try:
                ev = json.load(open(ejp, encoding="utf-8"))
                reason = ev.get("reason") or None
                if not gps:
                    lat = float(ev.get("est_lat") or ev.get("lat") or 0)
                    lon = float(ev.get("est_lon") or ev.get("lon") or 0)
                    if lat and lon:
                        gps = {"center_lat": lat, "center_lon": lon}
            except Exception:
                pass
        return {"has_tel": ht, "has_event": he, "gps_bounds": gps,
                "has_data": ht or he, "reason": reason, "_track": track}

    def event_data(self, cid):
        """event.json for a clip's folder, incl. the seek offset (seconds
        into the clip) computed from the event timestamp vs. the clip's
        start timestamp -- lets the player jump straight to the trigger."""
        folder, ts = cid.rsplit("|", 1) if "|" in cid else ("", cid)
        ejp = os.path.join(self.scan_dir, folder, "event.json")
        if not os.path.isfile(ejp):
            return None
        try:
            ev = json.load(open(ejp, encoding="utf-8"))
        except Exception:
            return None
        result = {}
        et = ev.get("timestamp", "")
        if et:
            try:
                cs = datetime.datetime.strptime(ts, "%Y-%m-%d_%H-%M-%S")
                evt = datetime.datetime.strptime(et[:19], "%Y-%m-%dT%H:%M:%S")
                off = (evt - cs).total_seconds()
                if 0 <= off <= 3600:
                    result["seek"] = off
            except Exception:
                pass
        lat = float(ev.get("est_lat") or ev.get("lat") or 0)
        lon = float(ev.get("est_lon") or ev.get("lon") or 0)
        if lat and lon:
            result["lat"] = lat
            result["lon"] = lon
        for k in ("reason", "city", "street"):
            if ev.get(k):
                result[k] = ev[k]
        if ev.get("camera") is not None:
            result["camera"] = ev["camera"]
        return result or None

    def clips(self, ttl=10):
        now = time.time()
        with self._guard:
            if self._lcache["data"] is None or now - self._lcache["t"] >= ttl:
                self._lcache["data"] = self._scan()
                self._lcache["t"] = now
            return self._lcache["data"]

    def invalidate(self):
        self._lcache["t"] = 0.0

    def counts(self):
        cl = self.clips()
        cams = [cm for c in cl for cm in c["cameras"].values()]
        return {"clips": len(cl),
                "encrypted": sum(1 for cm in cams if cm["state"] in ("ready", "key", "locked")),
                "plain": sum(1 for cm in cams if cm["state"] == "plain"),
                "ready": sum(1 for cm in cams if cm["state"] == "ready"),
                "locked": sum(1 for cm in cams if cm["state"] == "locked")}

    # ---- decrypt on demand --------------------------------------------------
    def _clip_lock(self, cid):
        with self._guard:
            l = self._prep_locks.get(cid)
            if l is None:
                l = threading.Lock(); self._prep_locks[cid] = l
            return l

    def _clip_cams(self, cid):
        folder, ts = cid.rsplit("|", 1) if "|" in cid else ("", cid)
        cams = {}
        for path in glob.glob(os.path.join(self.scan_dir, folder, f"{ts}-*.mp4")):
            m = TS_RE.search(os.path.basename(path))
            if m:
                cam = m.group(2).lower()
                cams[cam] = (folder + "/" if folder else "") + f"{ts}-{cam}.mp4"
        return folder, ts, cams

    def prepare(self, cid):
        keys = self._keys()
        folder, ts, cams = self._clip_cams(cid)
        if not cams:
            return {"ok": False, "error": "clip not found"}
        errs = []
        def do(item):
            cam, sr = item
            try:
                if self._is_encrypted(sr):
                    if not os.path.exists(self._cache(sr)):
                        fek = self._key_for(sr, keys)
                        if fek:
                            pipeline.decrypt_and_cache(self._src(sr), self._cache(sr), fek)
                elif cam == "front":
                    telp = os.path.splitext(self._cache(sr))[0] + ".telemetry.json"
                    pipeline.telemetry_for_plain(self._src(sr), telp)
            except Exception as e:
                errs.append(f"{cam}: {e}")
        with self._clip_lock(cid):
            with ThreadPoolExecutor(max_workers=6) as ex:
                list(ex.map(do, cams.items()))
        self._cap_tmpfs()
        self._meta.pop(cid, None)
        self.invalidate()
        keys2 = self._keys()
        cameras = {cam: self._cam_state(sr, keys2) for cam, sr in cams.items()}
        return {"ok": not errs, "errors": errs, "cameras": cameras}

    def clip_paths(self, cid):
        """Absolute source path per camera for a clip (for on-demand key fetch)."""
        _folder, _ts, cams = self._clip_cams(cid)
        return {cam: self._src(sr) for cam, sr in cams.items()}

    def bulk_targets(self):
        """Clips that still need work: locked/keyed cameras to decrypt, or a
        plain front camera whose telemetry hasn't been extracted yet."""
        out = []
        for c in self.clips():
            front = c["cameras"].get("front", {})
            if c["needs_prepare"] or (front.get("state") == "plain" and not c.get("has_tel")):
                out.append(c["id"])
        return out

    def bulk_prepare(self, on_progress=None):
        """Decrypt + extract metadata (telemetry/thumbnail) for every clip
        that needs it. Calls on_progress(done, total, cid) after each clip."""
        targets = self.bulk_targets()
        errors = []
        for i, cid in enumerate(targets, 1):
            res = self.prepare(cid)
            if not res.get("ok"):
                errors.extend(res.get("errors", []))
            try:
                self.make_thumb(cid)
            except Exception:
                pass
            if on_progress:
                on_progress(i, len(targets), cid)
        return {"ok": not errors, "total": len(targets), "errors": errors}

    def resolve_media(self, sr):
        sr = posixpath.normpath(sr).lstrip("/")
        cp = self._cache(sr)
        if cp.startswith(os.path.normpath(self.out_dir)) and os.path.isfile(cp):
            return cp
        if not self._is_enc_sr(sr):
            sp = self._src(sr)
            if sp.startswith(os.path.normpath(self.scan_dir)) and os.path.isfile(sp):
                return sp
        return None

    def make_thumb(self, cid):
        folder, ts, cams = self._clip_cams(cid)
        if not cams and "|" in cid:
            folder, ts = cid.rsplit("|", 1)
        if not self._is_enc_sr(folder):
            tp = os.path.join(self.scan_dir, folder, "thumb.png")
            if os.path.isfile(tp):
                return tp
        cache = os.path.join(self.out_dir, ".thumbs",
                             hashlib.sha1(cid.encode()).hexdigest()[:20] + ".jpg")
        if os.path.isfile(cache):
            return cache
        front = (folder + "/" if folder else "") + f"{ts}-front.mp4"
        keys = self._keys()
        with self._clip_lock(cid):
            if os.path.isfile(cache):
                return cache
            if self._is_enc_sr(front):
                cp = self._cache(front)
                if not os.path.isfile(cp):
                    fek = self._key_for(front, keys)
                    if not fek:
                        return None
                    try:
                        pipeline.decrypt_and_cache(self._src(front), cp, fek)
                    except Exception:
                        return None
                src = cp
            else:
                src = self._src(front)
                if not os.path.isfile(src):
                    return None
            seek = 1.0
            ev = self.event_data(cid)
            if ev and "seek" in ev and 0 <= ev["seek"] <= 120:
                seek = ev["seek"]
            return cache if pipeline.make_thumbnail(src, cache, seek=seek) else None

    def _cap_tmpfs(self):
        try:
            files = []
            for root, _, names in os.walk(self.out_dir):
                for nm in names:
                    if nm.endswith(".mp4"):
                        fp = os.path.join(root, nm)
                        try:
                            st = os.stat(fp); files.append((st.st_atime, st.st_size, fp))
                        except OSError:
                            pass
            total = sum(f[1] for f in files)
            if total <= self.tmpfs_cap:
                return
            for _at, sz, fp in sorted(files):
                if total <= self.tmpfs_cap:
                    break
                try:
                    os.remove(fp); total -= sz
                except OSError:
                    pass
        except Exception:
            pass

    def clear_cache(self):
        for root, _, names in os.walk(self.out_dir):
            for nm in names:
                if nm.endswith((".mp4", ".jpg", ".png", ".telemetry.json")):
                    try:
                        os.remove(os.path.join(root, nm))
                    except OSError:
                        pass

    # ---- trips / gps --------------------------------------------------------
    def all_gps(self):
        pts = []
        for c in self.clips():
            gb = c.get("gps_bounds")
            if gb:
                pts.append([gb["center_lat"], gb["center_lon"], c["id"]])
        return pts

    def trips(self, gap_min=TRIP_GAP_MIN):
        cl = sorted(self.clips(), key=lambda c: c["timestamp"])
        trips, group, prev = [], [], None
        def flush(g):
            if not g:
                return
            route = []
            for c in g:
                route += self._meta.get(c["id"], {}).get("_track") or (
                    [[c["gps_bounds"]["center_lat"], c["gps_bounds"]["center_lon"]]] if c.get("gps_bounds") else [])
            dist = sum(_haversine(*route[i], *route[i + 1]) for i in range(len(route) - 1))
            trips.append({"start": g[0]["timestamp"], "end": g[-1]["timestamp"],
                          "clip_ids": [c["id"] for c in g], "clip_count": len(g),
                          "distance_km": round(dist, 2), "route": route})
        for c in cl:
            dt = datetime.datetime.strptime(c["timestamp"], "%Y-%m-%d_%H-%M-%S")
            if group and prev and (dt - prev).total_seconds() > gap_min * 60:
                flush(group); group = []
            group.append(c); prev = dt
        flush(group)
        trips.sort(key=lambda t: t["start"], reverse=True)
        return trips


def _haversine(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
