"""Publish lệnh từ server xuống trạm camera (ephemeral connection).

Mọi lệnh đi qua topic ``camera/{code}/cmd`` dạng JSON:
    {"request_id": "...", "command": "...", "payload": {...}}
Trạm phản hồi qua ``camera/{code}/ack``.
"""
import json
import logging
import secrets
import threading

import paho.mqtt.client as mqtt
from django.conf import settings

from mqtt_service.client import connect

log = logging.getLogger(__name__)


def publish_command(camera_code: str, command: str, payload: dict | None = None,
                    qos: int = 1, timeout: int = 5) -> str | None:
    """Publish 1 lệnh xuống trạm; trả về request_id nếu gửi thành công, None nếu lỗi."""
    if not settings.MQTT_ENABLED:
        log.info("MQTT disabled — skip %s to %s", command, camera_code)
        return None

    request_id = f"req-{secrets.token_hex(6)}"
    message = json.dumps({
        "request_id": request_id,
        "command": command,
        "payload": payload or {},
    })

    published = threading.Event()

    def on_connect(c, u, f, rc, props=None):
        # Không block trong callback — chờ xác nhận qua on_publish
        c.publish(f"camera/{camera_code}/cmd", message, qos=qos)

    def on_publish(c, u, mid, rc=None, props=None):
        published.set()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"django-config-pub-{secrets.token_hex(3)}",
    )
    client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASS)
    client.on_connect = on_connect
    client.on_publish = on_publish
    try:
        connect(client)
        client.loop_start()
        ok = published.wait(timeout)
        client.loop_stop()
        client.disconnect()
        if not ok:
            log.warning("publish_command(%s, %s) timeout", camera_code, command)
            return None
        return request_id
    except Exception:
        log.exception("publish_command(%s, %s) failed", camera_code, command)
        return None


def push_settings(camera_settings) -> str | None:
    """Đẩy thông số chụp mong muốn xuống máy ảnh (set_settings)."""
    return publish_command(
        camera_settings.camera.code, "set_settings", camera_settings.to_payload()
    )


def request_settings(camera_code: str) -> str | None:
    """Yêu cầu máy ảnh báo lại thông số hiện tại (get_settings)."""
    return publish_command(camera_code, "get_settings")


def request_sim_info(camera_code: str) -> str | None:
    return publish_command(camera_code, "get_sim_info")


def request_capture(camera_code: str) -> str | None:
    return publish_command(camera_code, "capture_now")


def push_interval(camera_code: str, interval_sec: int, schedule_enabled: bool = False,
                  work_start_time: str = "06:00", work_end_time: str = "18:00") -> str | None:
    return publish_command(camera_code, "set_interval", {
        "capture_interval_sec": interval_sec,
        "schedule_enabled": schedule_enabled,
        "work_start_time": work_start_time,
        "work_end_time": work_end_time,
    })


def push_schedules(camera_code: str, schedules: list) -> str | None:
    return publish_command(camera_code, "set_schedules", {"schedules": schedules})


def start_live_view(camera_code: str, session_id: str, fps: int = 1) -> str | None:
    """Yêu cầu trạm bật Live View và push frame về HTTP endpoint."""
    return publish_command(camera_code, "start_live_view",
                           {"session_id": session_id, "fps": fps})


def stop_live_view(camera_code: str, session_id: str) -> str | None:
    return publish_command(camera_code, "stop_live_view",
                           {"session_id": session_id})
