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
