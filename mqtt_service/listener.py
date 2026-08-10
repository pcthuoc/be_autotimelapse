"""Listener MQTT long-running: subscribe camera/# và cập nhật DB + Redis cache.

Chạy: ``python manage.py mqtt_listen``
"""
import json
import logging
from decimal import Decimal, InvalidOperation

import paho.mqtt.client as mqtt
from django.core.cache import cache
from django.db import close_old_connections
from django.utils import timezone

from mqtt_service.client import connect, make_client

log = logging.getLogger(__name__)

# TTL presence trên Redis (giây).
# Phải lớn hơn capture_interval tối đa của camera (thường 15–20 phút).
ONLINE_TTL = 1800  # 30 phút


def _dec(val):
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _get_device(code):
    from core.models.camera import Camera, CameraDevice
    try:
        camera = Camera.objects.get(code=code)
    except Camera.DoesNotExist:
        return None, None
    device, _ = CameraDevice.objects.get_or_create(camera=camera)
    return camera, device


# ── handlers ─────────────────────────────────────────────────────────────────

def _handle_data(code, payload):
    """Telemetry: pin / solar / nhiệt ẩm / sóng SIM (đo đạc bởi CM4) & trạng thái nguồn 2 lõi."""
    camera, device = _get_device(code)
    if not device:
        return
    m = {
        "battery_percent": payload.get("battery_percent"),
        "battery_voltage": _dec(payload.get("battery_voltage")),
        "solar_voltage": _dec(payload.get("solar_voltage")),
        "solar_percent": payload.get("solar_percent"),
        "temperature_c": _dec(payload.get("temperature_c")),
        "humidity_percent": payload.get("humidity_percent"),
        "sim_signal_dbm": payload.get("sim_signal_dbm"),
    }
    fields = []
    for f, v in m.items():
        if v is not None:
            setattr(device, f, v)
            fields.append(f)
    if isinstance(payload.get("is_charging"), bool):
        device.is_charging = payload["is_charging"]
        fields.append("is_charging")
    if isinstance(payload.get("cell_voltages"), list):
        device.cell_voltages = payload["cell_voltages"]
        fields.append("cell_voltages")
    if payload.get("firmware_version"):
        device.firmware_version = str(payload["firmware_version"])[:32]
        fields.append("firmware_version")

    node = payload.get("node", "esp32")
    now = timezone.now()
    device.last_seen_at = now
    fields.append("last_seen_at")

    if node == "cm4":
        device.cm4_last_seen_at = now
        fields.append("cm4_last_seen_at")
        device.cm4_power_state = payload.get("cm4_power_state", "running")
        fields.append("cm4_power_state")
    else:
        device.esp32_last_seen_at = now
        fields.append("esp32_last_seen_at")

    if payload.get("cm4_power_state"):
        device.cm4_power_state = payload["cm4_power_state"]
        fields.append("cm4_power_state")

    if payload.get("sim_active_node"):
        device.sim_active_node = payload["sim_active_node"]
        fields.append("sim_active_node")

    fields.append("updated_at")
    device.save(update_fields=fields)
    cache.set(f"cam:online:{code}", True, ONLINE_TTL)
    cache.set(f"cam:telemetry:{code}", payload, ONLINE_TTL)


def _handle_status(code, payload):
    camera, device = _get_device(code)
    if not device:
        return
    online = payload.get("online", payload.get("status") == "online")
    if online:
        device.last_seen_at = timezone.now()
        device.save(update_fields=["last_seen_at", "updated_at"])
        was_online = cache.get(f"cam:online:{code}")
        cache.set(f"cam:online:{code}", True, ONLINE_TTL)
        # Thiết bị vừa (re)connect → đồng bộ capture interval xuống
        if not was_online and device.capture_interval_sec:
            from mqtt_service import config_publisher
            try:
                config_publisher.push_interval(code, device.capture_interval_sec)
                log.info("Đã đẩy capture_interval=%ds xuống %s",
                         device.capture_interval_sec, code)
            except Exception:
                log.exception("push_interval lỗi cho %s", code)
    else:
        # LWT/thông báo offline rõ ràng → ghi False để UI không fallback
        # sang last_seen_at (vốn có thể vẫn trong cửa sổ online).
        cache.set(f"cam:online:{code}", False, ONLINE_TTL)


def _handle_ack(code, payload):
    """Phản hồi lệnh: applied settings / SIM info / capture done."""
    camera, device = _get_device(code)
    if not device:
        return
    rtype = payload.get("type") or payload.get("command")
    data = payload.get("data") or {}
    now = timezone.now()

    if rtype in ("set_settings", "get_settings", "applied_settings"):
        from core.models.camera import CameraSettings
        cam_settings, _ = CameraSettings.objects.get_or_create(camera=camera)
        applied = data.get("applied") or data.get("settings")
        if payload.get("status") == "error" and not applied:
            # ACK lỗi (vd camera_offline) → không ghi đè applied
            cache.set(f"cam:ack:{code}:{payload.get('request_id', '')}", payload, 120)
            cache.set(f"cam:online:{code}", True, ONLINE_TTL)
            return
        applied = applied or data
        if isinstance(applied, dict) and applied and "code" not in applied:
            cam_settings.applied = applied
            cam_settings.last_synced_at = now
            fields = ["applied", "last_synced_at", "updated_at"]
            caps = data.get("capabilities")
            if isinstance(caps, dict) and caps:
                cam_settings.capabilities = caps
                fields.append("capabilities")
            # exposure_mode/focus_switch chỉ đọc từ máy
            if applied.get("exposure_mode"):
                cam_settings.exposure_mode = str(applied["exposure_mode"])[:16]
                fields.append("exposure_mode")
            if applied.get("focus_switch"):
                cam_settings.focus_switch = str(applied["focus_switch"])[:16]
                fields.append("focus_switch")
            # Pull (get_settings) → ghi applied vào desired để in_sync=True ngay
            if rtype == "get_settings":
                for fname in cam_settings.SETTABLE_FIELDS:
                    val = applied.get(fname)
                    if val:
                        setattr(cam_settings, fname, val)
                        if fname not in fields:
                            fields.append(fname)
            cam_settings.save(update_fields=fields)
        cache.set(f"cam:ack:{code}:{payload.get('request_id', '')}", payload, 120)

    elif rtype in ("get_sim_info", "sim_info"):
        sim = data.get("sim") or data
        if sim.get("operator") is not None:
            device.sim_operator = str(sim["operator"])[:64]
        if sim.get("number") is not None:
            device.sim_number = str(sim["number"])[:32]
        if sim.get("iccid") is not None:
            device.sim_iccid = str(sim["iccid"])[:32]
        if sim.get("signal_dbm") is not None:
            device.sim_signal_dbm = sim["signal_dbm"]
        device.sim_updated_at = now
        device.last_seen_at = now
        device.save(update_fields=[
            "sim_operator", "sim_number", "sim_iccid", "sim_signal_dbm",
            "sim_updated_at", "last_seen_at", "updated_at",
        ])
        cache.set(f"cam:ack:{code}:{payload.get('request_id', '')}", payload, 120)

    elif rtype in ("capture_now", "capture_done"):
        device.wake_done_at = now
        device.last_seen_at = now
        device.save(update_fields=["wake_done_at", "last_seen_at", "updated_at"])

    elif rtype == "set_interval":
        device.last_seen_at = now
        device.save(update_fields=["last_seen_at", "updated_at"])

    cache.set(f"cam:online:{code}", True, ONLINE_TTL)


_HANDLERS = {"data": _handle_data, "status": _handle_status, "ack": _handle_ack}


# ── main loop ────────────────────────────────────────────────────────────────

def _on_connect(client, userdata, flags, rc, props=None):
    log.info("Kết nối MQTT broker thành công (rc=%s).", rc)
    client.subscribe("camera/#", qos=1)
    log.info("Đã subscribe topic: camera/#")


def _on_message(client, userdata, msg):
    close_old_connections()
    parts = msg.topic.split("/")
    if len(parts) != 3 or parts[0] != "camera":
        return
    code, kind = parts[1], parts[2]
    handler = _HANDLERS.get(kind)
    if not handler:
        return
    try:
        payload = json.loads(msg.payload.decode())
    except (ValueError, UnicodeDecodeError):
        log.warning("Payload không phải JSON trên %s", msg.topic)
        return
    try:
        handler(code, payload)
        log.info("Đã xử lý %s từ %s", kind, code)
    except Exception:
        log.exception("Lỗi xử lý message %s", msg.topic)


def run():
    from django.conf import settings as dj

    if not dj.MQTT_SKIP_ACL_SETUP:
        from mqtt_service import device_manager
        device_manager.ensure_acl()
        log.info("Đã thiết lập ACL camera-role.")

        # Đăng ký (hoặc sync lại) tất cả cameras trong DB vào broker — idempotent
        try:
            from core.models.camera import Camera
            cameras = list(Camera.objects.all())
            for cam in cameras:
                errors = device_manager.register_device(cam)
                if errors:
                    log.warning("startup: register_device(%s) lỗi: %s", cam.code, errors)
                else:
                    log.info("startup: MQTT client OK — %s", cam.code)
        except Exception:
            log.exception("startup: không thể sync camera clients vào broker")

    client = make_client("djls")
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.reconnect_delay_set(min_delay=1, max_delay=120)
    log.info("Đang kết nối MQTT broker %s:%s ...", dj.MQTT_BROKER, dj.MQTT_PORT)
    connect(client)
    client.loop_forever(retry_first_connection=True)
