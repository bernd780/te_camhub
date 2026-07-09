"""
Home Assistant integration via MQTT Discovery.

Publishes the Hub as a single HA device with a handful of read-only sensors
(clip counts, NAS-archive coverage, Pi temperature, USB/car connection,
vault lock state, WiFi SSID). Uses the standard HA MQTT Discovery topic
convention (homeassistant/<component>/<node_id>/<object_id>/config) so the
device just appears in HA -- no YAML config needed on the HA side.

Connection is optional and off by default (MQTT_ENABLED); credentials are
never logged. Uses paho-mqtt's own background network thread (loop_start),
so connect()/publish_state() here are non-blocking and safe to call from
server.py's periodic loop.
"""
import json, threading

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

DEVICE_ID = "teslacam_hub"
BASE = "teslacam_hub"
AVAIL_TOPIC = f"{BASE}/status"

DEVICE_INFO = {
    "identifiers": [DEVICE_ID],
    "name": "TeslaCam Hub",
    "manufacturer": "DIY (teslausb fork)",
    "model": "Raspberry Pi",
}

# object_id -> (component, name, device_class, unit, icon)
SENSORS = {
    "clips":     ("sensor", "Aufnahmen gesamt", None, None, "mdi:filmstrip"),
    "encrypted": ("sensor", "Verschlüsselte Aufnahmen", None, None, "mdi:lock"),
    "nas_percent": ("sensor", "NAS-Archivierung", None, "%", "mdi:cloud-upload"),
    "temp":      ("sensor", "Pi-Temperatur", "temperature", "°C", None),
    "wifi_ssid": ("sensor", "WLAN", None, None, "mdi:wifi"),
    "usb_connected": ("binary_sensor", "USB am Auto", "connectivity", None, None),
    "vault_unlocked": ("binary_sensor", "Tresor entsperrt", "lock", None, None),
}

_lock = threading.Lock()
_client = None
_connected = False


def _topic(kind, oid):
    return f"{BASE}/{oid}/{kind}"


def _on_connect(client, userdata, flags, rc, properties=None):
    global _connected
    _connected = (rc == 0)
    if not _connected:
        return
    client.publish(AVAIL_TOPIC, "online", retain=True)
    for oid, (component, name, device_class, unit, icon) in SENSORS.items():
        payload = {
            "name": name,
            "unique_id": f"{DEVICE_ID}_{oid}",
            "state_topic": _topic("state", oid),
            "availability_topic": AVAIL_TOPIC,
            "device": DEVICE_INFO,
        }
        if device_class:
            payload["device_class"] = device_class
        if unit:
            payload["unit_of_measurement"] = unit
        if icon:
            payload["icon"] = icon
        if component == "binary_sensor":
            payload["payload_on"] = "ON"
            payload["payload_off"] = "OFF"
        client.publish(f"homeassistant/{component}/{DEVICE_ID}/{oid}/config",
                        json.dumps(payload), retain=True)


def _on_disconnect(client, userdata, rc, properties=None):
    global _connected
    _connected = False


def ensure_connected(host, port, user, password):
    """(Re)connect if not already connected with the current settings. No-op
    if paho-mqtt isn't installed or host is empty."""
    global _client
    if mqtt is None or not host:
        return False
    with _lock:
        if _client is not None and _connected:
            return True
        try:
            if _client is not None:
                try:
                    _client.loop_stop()
                except Exception:
                    pass
            try:
                # paho-mqtt >=2.0 requires an explicit callback API version;
                # VERSION1 keeps the on_connect/on_disconnect signatures below
                # working unchanged across both 1.x and 2.x.
                c = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                                 client_id="teslacam-hub")
            except AttributeError:
                c = mqtt.Client(client_id="teslacam-hub", protocol=mqtt.MQTTv311)
            if user:
                c.username_pw_set(user, password or "")
            c.will_set(AVAIL_TOPIC, "offline", retain=True)
            c.on_connect = _on_connect
            c.on_disconnect = _on_disconnect
            c.connect(host, int(port or 1883), keepalive=60)
            c.loop_start()
            _client = c
            return True
        except Exception as e:
            print("[hub] mqtt connect failed:", e, flush=True)
            _client = None
            return False


def publish_state(values: dict):
    """values: {object_id: value}. Only publishes known sensors."""
    if _client is None or not _connected:
        return
    for oid, val in values.items():
        spec = SENSORS.get(oid)
        if not spec:
            continue
        component = spec[0]
        if component == "binary_sensor":
            val = "ON" if val else "OFF"
        try:
            _client.publish(_topic("state", oid), str(val))
        except Exception:
            pass


def disconnect():
    global _client, _connected
    with _lock:
        if _client is not None:
            try:
                _client.publish(AVAIL_TOPIC, "offline", retain=True)
                _client.loop_stop()
                _client.disconnect()
            except Exception:
                pass
        _client = None
        _connected = False
