"""
Diagnostics/actions for the Hub: system status, log tails, reboot, drive toggle,
sync/retention/BLE triggers. Thin wrappers over the existing teslausb scripts and
standard tools (the Hub runs as root via systemd).
"""
import os, subprocess, urllib.request, tarfile, io, json, time
import hubconf

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
    r = subprocess.run([f"{BLE_BIN}/tesla-control", "-ble", "-vin", vin.upper(),
                         "add-key-request", pub, role, "cloud_key"],
                        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "Kopplungsanfrage fehlgeschlagen").strip()[:300]}
    return {"ok": True}


def ble_test_role(name, role):
    """Actually exercise a paired key against the real vehicle instead of
    just checking session-info, so pairing success is unambiguous:
    - charging_manager: opens then closes the charge port (visible/audible
      at the car -- also the real-world test of whether this role can wake
      a sleeping vehicle).
    - everything else (vehicle_monitor etc.): reads closures state
      (locked, doors, sentry mode) over BLE, which works even while the
      infotainment system is asleep."""
    vin = hubconf.getval("TESLA_BLE_VIN")
    if not vin:
        return {"ok": False, "error": "Fahrzeug-VIN erst eintragen und speichern"}
    priv, _pub = _ble_keypath(name)
    if not os.path.isfile(priv):
        return {"ok": False, "error": "noch nicht gekoppelt"}
    base = [f"{BLE_BIN}/tesla-control", "-ble", "-vin", vin.upper(), "-key-file", priv]

    if role == "charging_manager":
        r1 = subprocess.run(base + ["charge-port-open"], capture_output=True, text=True, timeout=30)
        if r1.returncode != 0:
            return {"ok": False, "error": (r1.stderr or r1.stdout or "Ladeport öffnen fehlgeschlagen").strip()[:300]}
        time.sleep(4)
        r2 = subprocess.run(base + ["charge-port-close"], capture_output=True, text=True, timeout=30)
        if r2.returncode != 0:
            return {"ok": True, "detail": "Ladeport wurde geöffnet, Schließen aber fehlgeschlagen: "
                                           + (r2.stderr or r2.stdout or "Fehler").strip()[:200]}
        return {"ok": True, "detail": "Ladeport wurde geöffnet und wieder geschlossen -- am Auto sichtbar/hörbar gewesen?"}

    r = subprocess.run(base + ["state", "closures"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "Abfrage fehlgeschlagen").strip()[:300]}
    try:
        data = json.loads(r.stdout).get("closuresState", {})
        locked = data.get("locked")
        sentry = next(iter((data.get("sentryModeState") or {}).keys()), "?")
        ts = data.get("timestamp", "")
        detail = f"Fahrzeug hat geantwortet -- verriegelt: {'ja' if locked else 'nein'}, Sentry: {sentry}, Stand: {ts}"
    except Exception:
        detail = (r.stdout or "").strip()[:300]
    return {"ok": True, "detail": detail}


# Every command tried against the real vehicle to empirically determine
# what a paired key's role can actually do -- confirmed 2026-07-10 against
# a real charging_manager key. Read commands always succeed; actuation
# commands outside the charging domain are consistently rejected by the
# vehicle itself (INSUFFICIENT_PRIVILEGES / GENERICERROR_UNAUTHORIZED),
# not merely by our own client -- exit code and vehicle error text are
# authoritative, this isn't a guess.
BLE_PROBE_COMMANDS = [
    ("Ladezustand lesen", ["state", "charge"]),
    ("Verriegelung/Türen lesen", ["state", "closures"]),
    ("Klimazustand lesen", ["state", "climate"]),
    ("Reifendruck lesen", ["state", "tire-pressure"]),
    ("Standort lesen", ["state", "location"]),
    ("Fahrzustand lesen", ["state", "drive"]),
    ("Medienstatus lesen", ["state", "media"]),
    ("Medien-Details lesen", ["state", "media-detail"]),
    ("Lade-Zeitplan lesen", ["state", "charge-schedule"]),
    ("Vorklimatisierungs-Zeitplan lesen", ["state", "precondition-schedule"]),
    ("Software-Update-Status lesen", ["state", "software-update"]),
    ("Kindersicherung-Status lesen", ["state", "parental-controls"]),
    ("Basiszustand lesen (VCSEC, auch bei schlafendem Auto)", ["body-controller-state"]),
    ("Alle Schlüssel auflisten", ["list-keys"]),
    ("Erreichbarkeit prüfen (ping)", ["ping"]),
    ("Ladeport öffnen", ["charge-port-open"]),
    ("Ladeport schließen", ["charge-port-close"]),
    ("Laden starten", ["charging-start"]),
    ("Laden stoppen", ["charging-stop"]),
    ("Ladegrenze setzen (aktueller Wert, kein Effekt)", ["charging-set-limit", "80"]),
    ("Ladestrom setzen (aktueller Wert, kein Effekt)", ["charging-set-amps", "16"]),
    ("Lade-Zeitplan abbrechen", ["charging-schedule-cancel"]),
    ("Auto aufwecken", ["wake"]),
    ("Hupen", ["honk"]),
    ("Lichter blinken", ["flash-lights"]),
    ("Zubehör-Stromversorgung an", ["keep-accessory-power", "on"]),
    ("Zubehör-Stromversorgung aus", ["keep-accessory-power", "off"]),
    ("Lautstärke lauter", ["media-volume-up"]),
    ("Lautstärke leiser", ["media-volume-down"]),
    ("Nächster Titel", ["media-next-track"]),
    ("Vorheriger Titel", ["media-previous-track"]),
    ("Play/Pause", ["media-toggle-playback"]),
    ("Nächster Favorit", ["media-next-favorite"]),
    ("Vorheriger Favorit", ["media-previous-favorite"]),
    ("Entriegeln", ["unlock"]),
    ("Fenster einen Spalt öffnen", ["windows-vent"]),
    ("Fenster schließen", ["windows-close"]),
    ("Klima einschalten", ["climate-on"]),
    ("Klima ausschalten", ["climate-off"]),
    ("Sentry-Modus an", ["sentry-mode", "on"]),
    ("Sentry-Modus aus", ["sentry-mode", "off"]),
    ("Tonneau öffnen (nur Cybertruck)", ["tonneau-open"]),
    ("Lenkradheizung aus", ["steering-wheel-heater", "off"]),
]

# Commands that exist in tesla-control but are never auto-tested, with the
# reason -- either the vehicle-side effect can't be reliably undone, the
# command needs parameters we can't choose safely, or (key/software-update/
# guest-data) the blast radius of an unexpected success is too high to
# probe just for curiosity.
BLE_UNTESTED_COMMANDS = [
    ("Fernstart (drive)", "Risiko einer ungewollten Fahrzeugbewegung"),
    ("Frunk öffnen", "kein Gegenbefehl zum Schließen vorhanden"),
    ("Kofferraum öffnen/schließen/toggeln", "Schließen nicht auf allen Modellen verfügbar, Zustand nicht sicher wiederherstellbar"),
    ("Sitzheizung setzen", "braucht Position + Stufe, zu viele Kombinationen"),
    ("Zieltemperatur setzen", "braucht konkreten Temperaturwert"),
    ("Auto-Sitz+Klima-Automatik", "verändert Komfort-Einstellungen ohne klaren Rückweg"),
    ("Valet-Modus an/aus", "verändert Fahrzeugverhalten dauerhaft"),
    ("Gast-Modus an/aus", "verändert Fahrzeugkonfiguration dauerhaft"),
    ("Kindersicherung setzen/Geschwindigkeitslimit", "kann Fahrverhalten einschränken"),
    ("Niedrig-Energie-Modus", "könnte Erreichbarkeit für weitere Tests verschlechtern"),
    ("Lade-/Vorklimatisierungs-Zeitplan hinzufügen/entfernen", "braucht komplexe Parameter (Zeit, Ort)"),
    ("Autosecure (nur Model X)", "schließt Flügeltüren und verriegelt -- deckt sich mit unlock-Test"),
    ("Software-Update starten/abbrechen", "störend/destruktiv"),
    ("Gastdaten löschen", "destruktiv"),
    ("Schlüssel hinzufügen/entfernen/umbenennen", "sicherheitskritisch -- Risiko, eigenen Zugriff zu verlieren"),
    ("Produktinfo / beliebiger API-Aufruf (get/post)", "braucht Fleet-API-OAuth-Token, über BLE nicht nutzbar"),
]


def ble_probe_role(name):
    """Empirically determine which commands a paired key can actually
    execute against the real vehicle: try every command in
    BLE_PROBE_COMMANDS and record the vehicle's real response, rather than
    relying on Tesla's documentation of what a role "should" be allowed to
    do. Includes both reads and actuation commands (some expected to be
    rejected) -- see BLE_UNTESTED_COMMANDS for the ones deliberately left
    out and why."""
    vin = hubconf.getval("TESLA_BLE_VIN")
    if not vin:
        return {"ok": False, "error": "Fahrzeug-VIN erst eintragen und speichern"}
    priv, _pub = _ble_keypath(name)
    if not os.path.isfile(priv):
        return {"ok": False, "error": "noch nicht gekoppelt"}
    base = [f"{BLE_BIN}/tesla-control", "-ble", "-vin", vin.upper(), "-key-file", priv]
    results = []
    for label, args in BLE_PROBE_COMMANDS:
        r = subprocess.run(base + args, capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        detail = lines[-1] if lines else ("OK" if ok else "Fehler")
        results.append({"label": label, "ok": ok, "detail": detail[:200]})
    return {"ok": True, "results": results,
            "untested": [{"label": l, "reason": r} for l, r in BLE_UNTESTED_COMMANDS]}


def ble_status_role(name):
    vin = hubconf.getval("TESLA_BLE_VIN")
    priv, _pub = _ble_keypath(name)
    if not vin or not os.path.isfile(priv) or not ble_binaries_installed():
        return {"paired": False}
    r = subprocess.run([f"{BLE_BIN}/tesla-control", "-ble", "-vin", vin.upper(),
                         "session-info", priv, "infotainment"],
                        capture_output=True, text=True, timeout=20)
    return {"paired": r.returncode == 0}

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
