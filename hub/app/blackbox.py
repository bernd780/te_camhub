"""
Blackbox mode: per-trip GPS/telemetry point log + GPX export.

Only active while BLACKBOX_ENABLED is set and a trip is actually detected
(see server.py's trip_watch_loop) -- this module itself just knows how to
write/list/read/convert trip files, not when to record.

One JSONL file per trip under <state_dir>/blackbox/, named by the trip's
start timestamp so files sort chronologically. Each line is one point:
{"ts": "...", "lat":..., "lon":..., "heading":..., "odometer_mi":..., "shiftState":...}
Speed isn't recorded directly (Tesla's BLE "drive" state doesn't expose
it) -- it's derived at export/summary time from the odometer delta
between consecutive points, which is more accurate than a GPS-distance
estimate and needs no extra field.
"""
import os, json, glob, math, datetime

_state_dir = None


def init(state_dir):
    global _state_dir
    _state_dir = os.path.join(state_dir, "blackbox")
    os.makedirs(_state_dir, exist_ok=True)


def _trip_path(trip_id):
    safe = os.path.basename(trip_id)  # no path traversal via the id
    return os.path.join(_state_dir, safe + ".jsonl")


def start_trip(start_ts):
    """start_ts: 'YYYY-MM-DDTHH-MM-SS' (colons already replaced -- safe for
    a filename). Returns the trip_id to pass to append_point/end_trip."""
    trip_id = start_ts
    open(_trip_path(trip_id), "a", encoding="utf-8").close()
    return trip_id


def append_point(trip_id, ts, lat, lon, heading=None, odometer_mi=None, shift_state=None):
    entry = {"ts": ts, "lat": lat, "lon": lon}
    if heading is not None:
        entry["heading"] = heading
    if odometer_mi is not None:
        entry["odometer_mi"] = odometer_mi
    if shift_state is not None:
        entry["shiftState"] = shift_state
    try:
        with open(_trip_path(trip_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _read_points(trip_id):
    p = _trip_path(trip_id)
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def trip_summary(trip_id):
    points = _read_points(trip_id)
    if not points:
        return {"trip_id": trip_id, "points": 0}
    first, last = points[0], points[-1]
    distance_km = None
    if "odometer_mi" in first and "odometer_mi" in last:
        try:
            distance_km = (last["odometer_mi"] - first["odometer_mi"]) * 1.60934
        except Exception:
            distance_km = None
    if distance_km is None:
        # fall back to summing GPS point-to-point distance
        distance_km = 0.0
        for a, b in zip(points, points[1:]):
            distance_km += _haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
    return {
        "trip_id": trip_id,
        "start": first["ts"],
        "end": last["ts"],
        "points": len(points),
        "distance_km": round(distance_km, 2) if distance_km is not None else None,
    }


def list_trips():
    files = sorted(glob.glob(os.path.join(_state_dir, "*.jsonl")), reverse=True)
    out = []
    for f in files:
        trip_id = os.path.splitext(os.path.basename(f))[0]
        out.append(trip_summary(trip_id))
    return out


def to_gpx(trip_id):
    points = _read_points(trip_id)
    try:
        dt = datetime.datetime.strptime(trip_id, "%Y-%m-%dT%H-%M-%S")
        name = f"Fahrt {dt.strftime('%Y-%m-%d %H:%M')}"
    except ValueError:
        name = f"Fahrt {trip_id}"
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="TeslaCam Hub" xmlns="http://www.topografix.com/GPX/1/1">',
        f"  <trk><name>{name}</name><trkseg>",
    ]
    for p in points:
        ts = p["ts"]
        if "T" in ts and not ts.endswith("Z"):
            ts = ts + "Z" if len(ts) <= 19 else ts
        parts.append(f'    <trkpt lat="{p["lat"]}" lon="{p["lon"]}"><time>{ts}</time></trkpt>')
    parts.append("  </trkseg></trk>")
    parts.append("</gpx>")
    return "\n".join(parts)
