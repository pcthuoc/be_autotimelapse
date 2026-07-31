"""
Adapter publisher cho mqtt_service.
Cung cấp publish_cmd để tương thích các view gọi publish_cmd và publish_command.
"""
from mqtt_service.config_publisher import publish_command

def publish_cmd(camera_code: str, command: str, payload: dict | None = None, qos: int = 1, timeout: int = 5) -> str | None:
    return publish_command(camera_code, command, payload, qos, timeout)
