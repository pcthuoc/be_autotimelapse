"""Quản lý tài khoản/ACL thiết bị trên broker qua Dynamic Security Plugin.

Mỗi Camera là 1 client MQTT riêng:
    username = camera.code
    password = camera.mqtt_password
    group    = cameras  (role camera-role, %u = username → cách ly topic)
"""
import json
import logging
import secrets
import threading

import paho.mqtt.client as mqtt
from django.conf import settings

from mqtt_service.client import connect

log = logging.getLogger(__name__)

_CTRL_TOPIC = "$CONTROL/dynamic-security/v1"
_RESP_TOPIC = "$CONTROL/dynamic-security/v1/response"

_GROUP = "cameras"
_ROLE = "camera-role"
_SERVER_ROLE = "camera-server"


def _send_commands(commands, timeout=8):
    """Kết nối ephemeral → gửi list command dynsec → đợi response → disconnect."""
    responses = []
    done = threading.Event()

    def on_connect(c, u, f, rc, props=None):
        c.subscribe(_RESP_TOPIC)
        c.publish(_CTRL_TOPIC, json.dumps({"commands": commands}))

    def on_message(c, u, msg):
        try:
            data = json.loads(msg.payload.decode())
            responses.extend(data.get("responses", []))
        except Exception:
            pass
        done.set()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"django-device-mgr-{secrets.token_hex(3)}",
    )
    client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message
    connect(client)
    client.loop_start()
    done.wait(timeout)
    client.loop_stop()
    client.disconnect()
    return responses


def _err(responses, allow=("already exists", "already in this group", "already in this role", "already has this role")):
    """Trả list lỗi thực sự (bỏ qua lỗi 'already exists/in role')."""
    out = []
    for r in responses:
        e = r.get("error")
        if e and not any(a in e.lower() for a in allow):
            out.append(f"{r.get('command')}: {e}")
    return out


def ensure_acl():
    """Tạo role + group cho thiết bị camera (idempotent)."""
    commands = [
        {"command": "createRole", "rolename": _ROLE},
        # Trạm publish lên các topic của chính nó
        *[
            {"command": "addRoleACL", "rolename": _ROLE, "acltype": "publishClientSend",
             "topic": f"camera/%u/{t}", "allow": True}
            for t in ("data", "status", "ack")
        ],
        # Trạm nhận lệnh (subscribePattern vì subscribeLiteral không thay thế %u)
        {"command": "addRoleACL", "rolename": _ROLE, "acltype": "publishClientReceive",
         "topic": "camera/%u/cmd", "allow": True},
        {"command": "addRoleACL", "rolename": _ROLE, "acltype": "subscribePattern",
         "topic": "camera/%u/cmd", "allow": True},
        {"command": "createGroup", "groupname": _GROUP},
        {"command": "addGroupRole", "groupname": _GROUP, "rolename": _ROLE},
        # Role cho server (listener/publisher) — gắn vào tài khoản admin
        {"command": "createRole", "rolename": _SERVER_ROLE},
        {"command": "addRoleACL", "rolename": _SERVER_ROLE, "acltype": "publishClientSend",
         "topic": "camera/#", "allow": True},
        {"command": "addRoleACL", "rolename": _SERVER_ROLE, "acltype": "publishClientReceive",
         "topic": "camera/#", "allow": True},
        {"command": "addRoleACL", "rolename": _SERVER_ROLE, "acltype": "subscribePattern",
         "topic": "camera/#", "allow": True},
        {"command": "addClientRole", "username": settings.MQTT_USER,
         "rolename": _SERVER_ROLE, "priority": -1},
    ]
    errors = _err(_send_commands(commands),
                  allow=("already exists", "already in this group", "already in this role",
                         "already has this role", "internal error"))  # broker trả internal error khi role đã có
    if errors:
        log.warning("ensure_acl errors: %s", errors)
    return errors


def register_device(camera):
    """Tạo client broker cho camera (username=code). Idempotent."""
    commands = [
        {
            "command": "createClient",
            "username": camera.code,
            "password": camera.mqtt_password,
            "groups": [{"groupname": _GROUP, "priority": -1}],
        },
    ]
    responses = _send_commands(commands)
    errors = _err(responses)
    if any("already exists" in (r.get("error") or "").lower() for r in responses):
        # Client đã có → đồng bộ lại password bằng modifyClient
        errors += _err(_send_commands([
            {"command": "modifyClient", "username": camera.code,
             "password": camera.mqtt_password},
            {"command": "addGroupClient", "groupname": _GROUP,
             "username": camera.code, "priority": -1},
        ]))
    if errors:
        log.warning("register_device(%s) errors: %s", camera.code, errors)
    return errors


def unregister_device(code):
    """Xóa client broker khi xóa camera."""
    errors = _err(_send_commands([{"command": "deleteClient", "username": code}]),
                  allow=("already exists", "not found"))
    if errors:
        log.warning("unregister_device(%s) errors: %s", code, errors)
    return errors


def get_device_status(camera_code: str) -> dict:
    """Kiểm tra trạng thái MQTT của 1 camera trên broker.

    Trả về dict:
        {
            "registered": bool,   # client tồn tại trên broker
            "in_group": bool,     # thuộc group cameras
            "groups": list[str],
            "roles": list[str],
        }
    """
    responses = _send_commands([{"command": "getClient", "username": camera_code}])
    if not responses:
        return {"registered": False, "in_group": False, "groups": [], "roles": []}

    resp = responses[0]
    err = (resp.get("error") or "").lower()
    if "not found" in err or resp.get("command") != "getClient":
        return {"registered": False, "in_group": False, "groups": [], "roles": []}

    data = resp.get("data", {}).get("client", {})
    groups = [g.get("groupname") for g in data.get("groups", [])]
    roles  = [r.get("rolename")  for r in data.get("roles",  [])]
    return {
        "registered": True,
        "in_group": _GROUP in groups,
        "groups": groups,
        "roles": roles,
    }


def ensure_device_registered(camera) -> dict:
    """Đảm bảo camera đã đăng ký đầy đủ: ACL + client + group. Idempotent.
    Trả về {"ok": bool, "errors": list, "status": dict}
    """
    acl_errors = ensure_acl()
    reg_errors  = register_device(camera)
    all_errors  = acl_errors + reg_errors
    status      = get_device_status(camera.code)
    return {
        "ok": len(all_errors) == 0 and status.get("registered", False) and status.get("in_group", False),
        "errors": all_errors,
        "status": status,
    }
