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
import diag

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
_command_handler = None  # fn(action_id: str, value: str|None) -> None


def _topic(kind, oid):
    return f"{BASE}/{oid}/{kind}"


def _ble_topic(kind, action_id):
    return f"{BASE}/ble_{action_id}/{kind}"


def set_command_handler(fn):
    """Register the callback invoked when an HA button/number entity for a
    BLE action is triggered: fn(action_id, value_or_None)."""
    global _command_handler
    _command_handler = fn


def _on_message(client, userdata, msg):
    if _command_handler is None:
        return
    try:
        action_id = msg.topic.split("/")[1]
        if action_id.startswith("ble_"):
            action_id = action_id[len("ble_"):]
        payload = msg.payload.decode("utf-8", "replace").strip()
        _command_handler(action_id, payload or None)
    except Exception as e:
        print("[hub] mqtt command:", e, flush=True)


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

    # BLE reads -> one sensor per category, full values as JSON attributes
    # (avoids one HA entity per field; state is just a freshness marker).
    for read_id, (label, _args) in diag.BLE_READS.items():
        payload = {
            "name": f"BLE {label}",
            "unique_id": f"{DEVICE_ID}_ble_{read_id}",
            "state_topic": _ble_topic("state", read_id),
            "json_attributes_topic": _ble_topic("attributes", read_id),
            "availability_topic": AVAIL_TOPIC,
            "device": DEVICE_INFO,
            "icon": "mdi:car-electric",
        }
        client.publish(f"homeassistant/sensor/{DEVICE_ID}/ble_{read_id}/config",
                        json.dumps(payload), retain=True)

    # BLE actions -> HA button (no value) or number (needs a value) entities.
    for action_id, (label, args) in diag.BLE_ACTIONS.items():
        needs_value = action_id in ("charging_set_limit", "charging_set_amps")
        component = "number" if needs_value else "button"
        payload = {
            "name": f"BLE {label}",
            "unique_id": f"{DEVICE_ID}_ble_{action_id}",
            "command_topic": _ble_topic("set", action_id),
            "availability_topic": AVAIL_TOPIC,
            "device": DEVICE_INFO,
            "icon": "mdi:car-connected",
        }
        if needs_value:
            payload["state_topic"] = _ble_topic("state", action_id)
            if action_id == "charging_set_limit":
                payload.update({"min": 50, "max": 100, "step": 1, "unit_of_measurement": "%"})
            else:
                payload.update({"min": 5, "max": 32, "step": 1, "unit_of_measurement": "A"})
        client.publish(f"homeassistant/{component}/{DEVICE_ID}/ble_{action_id}/config",
                        json.dumps(payload), retain=True)
        client.subscribe(_ble_topic("set", action_id))


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
            c.on_message = _on_message
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


def publish_ble_read(read_id, values: dict):
    """Publish one BLE read result: a freshness-marker state (field count)
    plus the full flattened values as JSON attributes."""
    if _client is None or not _connected:
        return
    try:
        _client.publish(_ble_topic("state", read_id), str(len(values)))
        _client.publish(_ble_topic("attributes", read_id), json.dumps(values))
    except Exception:
        pass


def publish_ble_action_state(action_id, value):
    """Reflect the last commanded value for number-type BLE actions
    (charging_set_limit/charging_set_amps) back to their HA state_topic."""
    if _client is None or not _connected:
        return
    try:
        _client.publish(_ble_topic("state", action_id), str(value))
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
