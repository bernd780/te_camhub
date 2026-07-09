"""
Diagnostics/actions for the Hub: system status, log tails, reboot, drive toggle,
sync/retention/BLE triggers. Thin wrappers over the existing teslausb scripts and
standard tools (the Hub runs as root via systemd).
"""
import os, subprocess

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

def ble_status():
    r = _run(["/var/www/html/cgi-bin/checkBLEstatus.sh"], timeout=20)
    return {"raw": (r.stdout if r else "")}

def ble_pair():
    r = _run(["/var/www/html/cgi-bin/pairBLEkey.sh"], timeout=120)
    return {"ok": bool(r and r.returncode == 0), "raw": (r.stdout if r else "")}

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
