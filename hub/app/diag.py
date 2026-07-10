"""
Diagnostics/actions for the Hub: system status, log tails, reboot, drive toggle,
sync/retention/BLE triggers. Thin wrappers over the existing teslausb scripts and
standard tools (the Hub runs as root via systemd).
"""
import os, subprocess, urllib.request, tarfile, io, json, time, threading
import hubconf

# The Pi has exactly one Bluetooth adapter; two tesla-control invocations
# at once collide ("device or resource busy") instead of queueing. Every
# BLE command (reads, actions, pairing, status) goes through this lock so
# concurrent callers (page auto-load, MQTT loop, manual retries) serialize
# instead of failing each other.
_ble_lock = threading.Lock()

def _tc_run(args, timeout=30):
    """Run a tesla-control invocation serialized against the single
    Bluetooth adapter (see _ble_lock above)."""
    with _ble_lock:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None

def status():
    def out(cmd):
        r = _run(cmd)
        return (r.stdout.strip() if r and r.returncode == 0 else "")
    temp = ""
    r = _run(["vcgencmd", "measure_temp"])
    if r and r.stdout:
        temp = r.stdout.strip().replace("temp=", "")
    df = {}
    r = _run(["df", "-h", "/backingfiles", "/mnt/cam"])
    if r and r.stdout:
        df["raw"] = r.stdout.strip()
    up = out(["uptime", "-p"])
    ssid = out(["iwgetid", "-r"])
    return {"temp": temp, "uptime": up, "wifi_ssid": ssid, "disks": df.get("raw", ""),
            "gadget_active": _gadget_active(), "teslausb_active": _svc_active("teslausb")}

def _gadget_active():
    try:
        return bool(open("/sys/kernel/config/usb_gadget/teslausb/UDC").read().strip())
    except Exception:
        return None

def _svc_active(name):
    r = _run(["systemctl", "is-active", name])
    return bool(r and r.stdout.strip() == "active")

def tail_log(which, lines=200):
    path = {"archiveloop": "/mutable/archiveloop.log",
            "setup": "/teslausb/teslausb-headless-setup.log",
            "sync": "/mutable/sync-to-nas.log",
            "retention": "/mutable/retention.log"}.get(which)
    if not path or not os.path.isfile(path):
        return ""
    r = _run(["tail", "-n", str(lines), path])
    return r.stdout if r else ""

def reboot():
    subprocess.Popen(["reboot"])
    return {"ok": True}

def toggle_drives():
    r = _run(["/var/www/html/cgi-bin/toggledrives.sh"], timeout=30)
    return {"ok": bool(r and r.returncode == 0)}

def trigger_sync():
    """Force an immediate archive cycle for the camera clips: restarting the
    teslausb service disconnects+reconnects the USB gadget and runs
    archiveloop's archive pass right away, instead of waiting for the car
    to go idle on its own schedule."""
    r = subprocess.run(["systemctl", "restart", "teslausb"], capture_output=True)
    return {"ok": r.returncode == 0}

# ---- BLE: multiple named keys, each pairable with its own vehicle-command
# role, so lower-privilege roles (e.g. charging_manager, vehicle_monitor)
# can be tried out independently instead of always enrolling with 'owner'
# like teslausb's original single-key cgi scripts did.
BLE_BIN = "/root/bin"
BLE_DIR = "/root/.ble"
BLE_BINARIES_URL = ("https://github.com/MikeBishop/tesla-vehicle-command-arm-binaries"
                     "/releases/latest/download/vehicle-command-binaries-linux-armv6.tar.gz")


def ble_binaries_installed():
    return os.path.isfile(f"{BLE_BIN}/tesla-control") and os.path.isfile(f"{BLE_BIN}/tesla-keygen")


def install_ble_binaries():
    """Download tesla-control/tesla-keygen -- the same official binaries
    teslausb's own setup/pi/configure.sh installs, from
    MikeBishop/tesla-vehicle-command-arm-binaries -- plus the bluez package.
    Needed on any Pi where TESLA_BLE_VIN wasn't already set during the
    original one-step setup (that's the only place teslausb installs these
    itself)."""
    if ble_binaries_installed():
        return {"ok": True, "already": True}
    try:
        with urllib.request.urlopen(BLE_BINARIES_URL, timeout=60) as resp:
            data = resp.read()
    except Exception as e:
        return {"ok": False, "error": f"Download fehlgeschlagen: {e}"[:300]}
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        os.makedirs(BLE_BIN, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for member in tf.getmembers():
                base = os.path.basename(member.name)
                if base in ("tesla-control", "tesla-keygen"):
                    member.name = base
                    tf.extract(member, BLE_BIN)
                    os.chmod(os.path.join(BLE_BIN, base), 0o755)
        subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", "bluez"], capture_output=True)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)
    if not ble_binaries_installed():
        return {"ok": False, "error": "Download hat die Programme nicht bereitgestellt"}
    return {"ok": True}


def _ble_keypath(name):
    d = os.path.join(BLE_DIR, name)
    return os.path.join(d, "key_private.pem"), os.path.join(d, "key_public.pem")


def _ble_ensure_key(name):
    priv, pub = _ble_keypath(name)
    if os.path.isfile(priv) and os.path.isfile(pub):
        return {"ok": True, "already": True}
    if not ble_binaries_installed():
        return {"ok": False, "error": "BLE-Programme erst installieren"}
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        os.makedirs(os.path.dirname(priv), exist_ok=True)
        r = subprocess.run([f"{BLE_BIN}/tesla-keygen", "-key-file", priv, "-output", pub, "create"],
                            capture_output=True, text=True, timeout=30)
        if r.returncode != 0 or not (os.path.isfile(priv) and os.path.isfile(pub)):
            return {"ok": False, "error": (r.stderr or "Schlüsselerzeugung fehlgeschlagen").strip()[:200]}
        os.chmod(priv, 0o600)
        os.chmod(pub, 0o644)
        return {"ok": True}
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)


def ble_pair_role(name, role):
    """Generate (if needed) a named keypair and request pairing with a
    specific vehicle-command role ('driver', 'charging_manager',
    'vehicle_monitor', ...). Requires TESLA_BLE_VIN configured. After this
    call, confirm on the car: tap an existing key card to the console and
    accept the prompt on the touchscreen, same as adding any BLE key."""
    vin = hubconf.getval("TESLA_BLE_VIN")
    if not vin:
        return {"ok": False, "error": "Fahrzeug-VIN erst eintragen und speichern"}
    if not ble_binaries_installed():
        r = install_ble_binaries()
        if not r.get("ok"):
            return r
    kr = _ble_ensure_key(name)
    if not kr.get("ok"):
        return kr
    _priv, pub = _ble_keypath(name)
    r = _tc_run([f"{BLE_BIN}/tesla-control", "-ble", "-vin", vin.upper(),
                 "add-key-request", pub, role, "cloud_key"], timeout=60)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "Kopplungsanfrage fehlgeschlagen").strip()[:300]}
    return {"ok": True}


# Individually invokable BLE reads/actions -- empirically confirmed working
# against the real vehicle (charging_manager role). Commands that later
# started failing with a privilege error (charge-port-open/close, honk,
# flash-lights -- worked the morning this was built, rejected by the
# vehicle a few hours later the same day, same key/role) were removed
# rather than left in for _ble_unavailable to filter out at runtime, since
# the user re-tested by hand and confirmed it's not transient.
# id -> (label, args).
BLE_READS = {
    "charge": ("Ladezustand", ["state", "charge"]),
    "closures": ("Verriegelung/Türen", ["state", "closures"]),
    "climate": ("Klimazustand", ["state", "climate"]),
    "tire_pressure": ("Reifendruck", ["state", "tire-pressure"]),
    "location": ("Standort", ["state", "location"]),
    "drive": ("Fahrzustand", ["state", "drive"]),
    "media": ("Medienstatus", ["state", "media"]),
    "media_detail": ("Medien-Details", ["state", "media-detail"]),
    "charge_schedule": ("Lade-Zeitplan", ["state", "charge-schedule"]),
    "precondition_schedule": ("Vorklimatisierungs-Zeitplan", ["state", "precondition-schedule"]),
    "software_update": ("Software-Update-Status", ["state", "software-update"]),
    "parental_controls": ("Kindersicherung-Status", ["state", "parental-controls"]),
    "body_controller": ("Basiszustand (VCSEC)", ["body-controller-state"]),
    "list_keys": ("Alle Schlüssel", ["list-keys"]),
    "ping": ("Erreichbarkeit", ["ping"]),
}

# charge_port_open/charge_port_close/honk/flash_lights were confirmed
# working when this was first built (2026-07-10 morning), then confirmed by
# the user to fail with INSUFFICIENT_PRIVILEGES a few hours later on the
# same day, same role, same key -- Tesla's own docs warn role capabilities
# "may change". Removed here rather than left to the runtime
# _ble_unavailable filter, since the user re-tested by hand and this is now
# a known, durable fact rather than a one-off failure to auto-recover from.
BLE_ACTIONS = {
    "charging_start": ("Laden starten", ["charging-start"]),
    "charging_stop": ("Laden stoppen", ["charging-stop"]),
    "charging_set_limit": ("Ladegrenze setzen", ["charging-set-limit", "80"]),
    "charging_set_amps": ("Ladestrom setzen", ["charging-set-amps", "16"]),
    "charging_schedule_cancel": ("Lade-Zeitplan abbrechen", ["charging-schedule-cancel"]),
    "wake": ("Auto aufwecken", ["wake"]),
    "keep_accessory_power_on": ("Zubehör-Stromversorgung an", ["keep-accessory-power", "on"]),
    "keep_accessory_power_off": ("Zubehör-Stromversorgung aus", ["keep-accessory-power", "off"]),
}


def _ble_base(name):
    vin = hubconf.getval("TESLA_BLE_VIN")
    if not vin:
        return None, {"ok": False, "error": "Fahrzeug-VIN erst eintragen und speichern"}
    priv, _pub = _ble_keypath(name)
    if not os.path.isfile(priv):
        return None, {"ok": False, "error": "noch nicht gekoppelt"}
    return [f"{BLE_BIN}/tesla-control", "-ble", "-vin", vin.upper(), "-key-file", priv], None


def _scalarize(v):
    """Plain scalar as-is; a protobuf oneof enum serializes as {"Name": {}}
    -- reduce that to just the enum name. Anything else (nested objects with
    more than one key, lists) isn't a simple field, skip it."""
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict) and len(v) == 1:
        return next(iter(v.keys()))
    return None


def _flatten_state(raw_stdout):
    """`tesla-control` doesn't use one consistent JSON shape across commands:
    - most `state CATEGORY` calls wrap a single nested object in one
      top-level key (e.g. {"chargeState": {...}}) -- flatten that object.
    - `state drive` wraps *two* top-level keys, each its own nested object
      (driveState + locationState) -- flatten both, prefixed to avoid
      collisions.
    - `body-controller-state` has no wrapper at all, fields are already at
      the top level -- flatten those directly.
    Handles all three so read results are never silently empty."""
    try:
        data = json.loads(raw_stdout)
    except Exception:
        return {}
    if not isinstance(data, dict) or not data:
        return {}

    if len(data) == 1:
        inner = next(iter(data.values()))
        if isinstance(inner, dict):
            out = {k: _scalarize(v) for k, v in inner.items()}
            out = {k: v for k, v in out.items() if v is not None}
            if out:
                return out

    if all(isinstance(v, dict) for v in data.values()):
        out = {}
        for wrapper, inner in data.items():
            for k, v in inner.items():
                sv = _scalarize(v)
                if sv is not None:
                    out[f"{wrapper}.{k}"] = sv
        if out:
            return out

    out = {k: _scalarize(v) for k, v in data.items()}
    return {k: v for k, v in out.items() if v is not None}


# Command ids that have actually failed with a privilege/authorization
# error against the real vehicle in this run. Tesla's own docs warn role
# capabilities "may change as new features are added" -- confirmed true in
# practice (charge-port-open/honk/flash-lights worked earlier the same day
# this was built, then started failing) -- so a command that was allowed
# once is not trusted to stay allowed. Read-only in-memory: resets on Hub
# restart, and can be cleared via ble_reset_unavailable() to let a command
# be tried again (e.g. after Tesla changes something back).
_ble_unavailable = set()
_PRIVILEGE_ERROR_MARKERS = ("INSUFFICIENT_PRIVILEGES", "UNAUTHORIZED")


def _looks_like_privilege_error(text):
    t = (text or "").upper()
    return any(m in t for m in _PRIVILEGE_ERROR_MARKERS)


def ble_available_commands():
    """BLE_READS/BLE_ACTIONS minus anything that has actually failed with a
    privilege error this run -- this is the list the UI should show."""
    return (
        {i: v for i, v in BLE_READS.items() if i not in _ble_unavailable},
        {i: v for i, v in BLE_ACTIONS.items() if i not in _ble_unavailable},
    )


def ble_reset_unavailable():
    _ble_unavailable.clear()
    return {"ok": True}


def ble_read(name, read_id):
    """Run exactly one confirmed-allowed read command and return parsed
    values, for the "read now" UI and for MQTT sensor publishing."""
    spec = BLE_READS.get(read_id)
    if not spec:
        return {"ok": False, "error": "unbekannter Lesebefehl"}
    label, args = spec
    base, err = _ble_base(name)
    if err:
        return err
    r = _tc_run(base + args)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "Fehler").strip()[:300]
        if _looks_like_privilege_error(err):
            _ble_unavailable.add(read_id)
        return {"ok": False, "error": err}
    if read_id == "list_keys":
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        return {"ok": True, "label": label, "values": {"anzahl_schluessel": len(lines)}}
    if read_id == "ping":
        return {"ok": True, "label": label, "values": {"erreichbar": True}}
    return {"ok": True, "label": label, "values": _flatten_state(r.stdout)}


def ble_exec(name, action_id, value=None):
    """Run exactly one confirmed-allowed action command."""
    spec = BLE_ACTIONS.get(action_id)
    if not spec:
        return {"ok": False, "error": "unbekannter Befehl"}
    label, args = spec
    base, err = _ble_base(name)
    if err:
        return err
    final_args = list(args)
    if action_id in ("charging_set_limit", "charging_set_amps") and value is not None:
        try:
            final_args[-1] = str(int(value))
        except (TypeError, ValueError):
            return {"ok": False, "error": "ungültiger Wert"}
    r = _tc_run(base + final_args)
    ok = r.returncode == 0
    lines = (r.stderr or r.stdout or "").strip().splitlines()
    detail = lines[-1] if lines else ("OK" if ok else "Fehler")
    if not ok and _looks_like_privilege_error(detail):
        _ble_unavailable.add(action_id)
    return {"ok": ok, "label": label, "detail": detail[:200]}


def ble_status_role(name):
    """session-info has been observed to report a false negative
    (paired=False) in situations where a plain read succeeds moments
    later against the same key -- it's a stricter/different probe than an
    actual command needs, apparently sensitive to momentary BLE flakiness.
    One retry before giving up avoids that intermittent false negative
    from hiding the whole BLE UI/blocking the MQTT loop."""
    vin = hubconf.getval("TESLA_BLE_VIN")
    priv, _pub = _ble_keypath(name)
    if not vin or not os.path.isfile(priv) or not ble_binaries_installed():
        return {"paired": False}
    args = [f"{BLE_BIN}/tesla-control", "-ble", "-vin", vin.upper(),
            "session-info", priv, "infotainment"]
    for attempt in range(2):
        r = _tc_run(args, timeout=20)
        if r.returncode == 0:
            return {"paired": True}
    return {"paired": False}

def apply_ap_fallback(enabled, ssid=None, password=None, ap_ip=None):
    """Toggle 'AP only as fallback when home WiFi is unavailable' mode.

    Enabling: makes sure the TESLAUSB_AP NetworkManager profile exists
    (creating it via ap-ensure.sh if needed -- requires ssid+password the
    first time), turns off its autoconnect so it never starts on its own,
    and enables the watcher timer that brings it up/down based on WLAN
    connectivity.
    Disabling: stops the watcher and reverts to teslausb's normal
    always-on secondary-AP behavior (autoconnect back on, AP started now).
    """
    r = subprocess.run(["nmcli", "-t", "-f", "NAME", "c", "show"], capture_output=True, text=True)
    has_ap = "TESLAUSB_AP" in (r.stdout or "").splitlines()

    if enabled:
        if not has_ap and not (ssid and password):
            return {"ok": False, "error": "zuerst Access-Point-SSID und -Passwort eintragen und speichern"}
        if ssid and password:
            r = subprocess.run(["bash", "/opt/teslacam-hub/ap-ensure.sh", ssid, password, ap_ip or "192.168.66.1"],
                                capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or "AP-Einrichtung fehlgeschlagen").strip()[:200]}
        subprocess.run(["nmcli", "con", "modify", "TESLAUSB_AP", "connection.autoconnect", "no"],
                        capture_output=True)
        subprocess.run(["systemctl", "enable", "--now", "teslacam-ap-fallback.timer"], capture_output=True)
        subprocess.run(["bash", "/opt/teslacam-hub/ap-fallback-watch.sh"], capture_output=True)
    else:
        subprocess.run(["systemctl", "disable", "--now", "teslacam-ap-fallback.timer"], capture_output=True)
        if has_ap:
            subprocess.run(["nmcli", "con", "modify", "TESLAUSB_AP", "connection.autoconnect", "yes"],
                            capture_output=True)
            subprocess.run(["nmcli", "con", "up", "TESLAUSB_AP"], capture_output=True)
    return {"ok": True}


def set_ssh_password(password):
    """Set/reset the Linux login password for the 'pi' user -- the SSH
    login used throughout setup and by this Hub's own deploy workflow.
    Deliberately a separate secret from the vault passphrase (not derived
    from or synced to it): the vault password is never stored in
    retrievable plaintext and can be reset independently (forgot-password
    flow), so tying SSH auth to it would be both technically awkward and a
    good way to accidentally lock out SSH access."""
    if not password or len(password) < 8:
        return {"ok": False, "error": "Passwort muss mindestens 8 Zeichen haben"}
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        r = subprocess.run(["chpasswd"], input=f"pi:{password}\n", text=True,
                            capture_output=True, timeout=10)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "chpasswd fehlgeschlagen").strip()[:200]}
        return {"ok": True}
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)


def apply_ssh(disable):
    """Enable/disable SSH password login via a drop-in (audit hardening)."""
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    dropin = "/etc/ssh/sshd_config.d/99-teslausb.conf"
    if disable:
        with open(dropin, "w") as f:
            f.write("PasswordAuthentication no\n")
    elif os.path.exists(dropin):
        os.remove(dropin)
    subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)
    subprocess.run(["systemctl", "reload", "ssh"], capture_output=True) or \
        subprocess.run(["systemctl", "reload", "sshd"], capture_output=True)
    return {"ok": True}
