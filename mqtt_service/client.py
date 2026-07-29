"""Helper tạo client MQTT dùng chung (đăng nhập admin)."""
import secrets
import socket

import paho.mqtt.client as mqtt
from django.conf import settings


def make_client(prefix: str) -> mqtt.Client:
    """Tạo paho client v2, đăng nhập bằng tài khoản admin từ settings."""
    client_id = settings.MQTT_CLIENT_ID or f"{prefix}-{socket.gethostname()}-{secrets.token_hex(3)}"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASS)
    return client


def connect(client: mqtt.Client, keepalive: int = 30) -> None:
    client.connect(settings.MQTT_BROKER, settings.MQTT_PORT, keepalive)
