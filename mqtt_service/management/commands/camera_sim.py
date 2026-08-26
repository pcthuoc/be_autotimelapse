"""Giả lập trạm camera (Nikon D5300) giao tiếp MQTT.

Chạy: python manage.py camera_sim [--code CODE] [--interval 30]

Trạm giả lập:
- Đăng nhập broker bằng chính credential thiết bị (username=code, password=mqtt_password).
- Publish telemetry định kỳ lên camera/{code}/data.
- Nhận lệnh trên camera/{code}/cmd: set_settings / get_settings / get_sim_info /
  capture_now / set_interval / start_live_view / stop_live_view
  → phản hồi qua camera/{code}/ack.
- Chụp ảnh giả theo chu kỳ (capture_interval_sec) và UPLOAD thật lên SeaweedFS
  qua device API (presign → PUT → complete) — cần --device-key/--device-secret.
- Live View: sinh frame JPEG giả và POST lên /api/device/live/frame theo fps.
"""
import io
import json
import logging
import random
import threading
import time
import urllib.request
import urllib.error

import paho.mqtt.client as mqtt
from django.conf import settings as dj
from django.core.management.base import BaseCommand

log = logging.getLogger("camera_sim")

_CAPABILITIES = {
    "iso": {"choices": [100, 125, 160, 200, 250, 320, 400, 500, 640, 800, 1600, 3200]},
    "aperture": {"choices": ["f/3.5", "f/4", "f/4.5", "f/5", "f/5.6", "f/8", "f/11", "f/16"]},
    "shutter_speed": {"choices": ["1/4000", "1/1000", "1/250", "1/60", "1/30", "1/4", "1", "30"]},
    "white_balance": {"choices": ["auto", "daylight", "cloudy", "shade", "tungsten", "fluorescent", "flash", "preset"]},
    "image_size": {"choices": ["large", "medium", "small"]},
}

_DEFAULT_APPLIED = {
    "iso": 200,
    "aperture": "f/8",
    "shutter_speed": "1/250",
    "exposure_compensation": 0.0,
    "exposure_mode": "M",           # num vật lý — read-only
    "autofocus": True,
    "focus_mode": "af_s",
    "image_format": "raw_jpeg_fine",
    "image_size": "large",
    "white_balance": "auto",
    "capture_mode": "single",
    "capture_target": "client",
    "long_exposure_nr": False,
    "high_iso_nr": "off",
}

_SIM_INFO = {
    "operator": "Viettel",
    "number": "+84987654321",
    "iccid": "8984047123456789012",
}


def _fake_jpeg(width, height, label):
    """Sinh 1 ảnh JPEG giả (gradient + timestamp) bằng Pillow."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height))
    px = img.load()
    base = random.randint(0, 120)
    for y in range(height):
        for x in range(0, width, 4):
            v = (base + x * 60 // width + y * 60 // height) % 255
            for dx in range(4):
                if x + dx < width:
                    px[x + dx, y] = (v, (v + 60) % 255, (v + 120) % 255)
    d = ImageDraw.Draw(img)
    d.text((10, 10), label, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return buf.getvalue()


class DeviceHttp:
    """Gọi device API bằng credential thiết bị (urllib — không thêm dependency)."""

    def __init__(self, base_url, key_id, secret):
        self.base = base_url.rstrip("/")
        self.headers = {"X-Device-Key": key_id, "X-Device-Secret": secret}

    def _req(self, path, data, content_type="application/json", extra=None):
        headers = dict(self.headers)
        headers["Content-Type"] = content_type
        if extra:
            headers.update(extra)
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")

    def post_json(self, path, obj):
        return self._req(path, json.dumps(obj).encode())

    def post_frame(self, path, frame, session_id, seq):
        return self._req(path, frame, "image/jpeg",
                         {"X-Live-Session": session_id, "X-Frame-Seq": str(seq)})

    @staticmethod
    def put_url(url, data, content_type):
        req = urllib.request.Request(url, data=data, method="PUT",
                                     headers={"Content-Type": content_type})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status


class Command(BaseCommand):
    help = "Giả lập trạm camera giao tiếp MQTT (telemetry + nhận lệnh)."

    def add_arguments(self, parser):
        parser.add_argument("--code", help="Camera code (mặc định: camera đầu tiên).")
        parser.add_argument("--interval", type=int, default=30,
                            help="Chu kỳ gửi telemetry (giây, mặc định 30).")
        parser.add_argument("--server", default="http://127.0.0.1:8000",
                            help="Base URL device API (mặc định http://127.0.0.1:8000).")
        parser.add_argument("--device-key", help="Mặc định dùng camera.code.")
        parser.add_argument("--device-secret", help="Mặc định dùng camera.mqtt_password.")
        parser.add_argument("--capture-interval", type=int, default=0,
                            help="Chu kỳ chụp+upload giả (giây). 0 = tắt.")

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        from core.models.camera import Camera

        if options["code"]:
            camera = Camera.objects.get(code=options["code"])
        else:
            camera = Camera.objects.first()
            if not camera:
                self.stderr.write("Chưa có camera nào trong DB.")
                return
        code, password = camera.code, camera.mqtt_password
        interval = options["interval"]
        applied = dict(_DEFAULT_APPLIED)
        state = {"capture_interval_sec": 3600}
        live = {"session_id": None, "fps": 1, "seq": 0}

        http = DeviceHttp(
            options["server"],
            options.get("device_key") or code,
            options.get("device_secret") or password,
        )
        if options.get("capture_interval"):
            state["capture_interval_sec"] = options["capture_interval"]

        t_cmd = f"camera/{code}/cmd"
        t_ack = f"camera/{code}/ack"
        t_data = f"camera/{code}/data"
        t_status = f"camera/{code}/status"

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=code)
        client.username_pw_set(code, password)
        client.will_set(t_status, json.dumps({"online": False}), qos=1, retain=True)

        def publish_telemetry():
            payload = {
                "battery_percent": random.randint(60, 95),
                "battery_voltage": round(random.uniform(11.5, 12.6), 3),
                "cell_voltages": [round(random.uniform(3.65, 3.85), 2) for _ in range(3)],
                "is_charging": random.random() > 0.5,
                "solar_voltage": round(random.uniform(13.0, 18.5), 2),
                "solar_percent": random.randint(40, 100),
                "temperature_c": round(random.uniform(26, 38), 1),
                "humidity_percent": random.randint(55, 90),
                "sim_signal_dbm": random.randint(-95, -60),
                "firmware_version": "sim-1.0.0",
            }
            client.publish(t_data, json.dumps(payload), qos=1)
            log.info("→ telemetry: pin %s%%, %s°C", payload["battery_percent"], payload["temperature_c"])

        def capture_and_upload():
            """Giả lập 1 lần chụp: sinh JPEG lớn + thumb, upload qua device API."""
            if not http:
                log.warning("Chưa cấu hình --device-key/--device-secret — bỏ qua upload.")
                return None
            from django.utils import timezone as tz
            taken_at = tz.now().isoformat()
            label = f"{code} {taken_at}"
            big = _fake_jpeg(1200, 800, label)
            thumb = _fake_jpeg(320, 214, label)
            st, pre = http.post_json("/api/device/upload/presign/",
                                     {"content_type": "image/jpeg",
                                      "taken_at": taken_at, "with_thumb": True})
            if st != 200:
                log.error("presign lỗi: %s %s", st, pre)
                return None
            DeviceHttp.put_url(pre["url"], big, "image/jpeg")
            DeviceHttp.put_url(pre["thumb_url"], thumb, "image/jpeg")
            st, done = http.post_json("/api/device/upload/complete/", {
                "media_id": pre["media_id"], "key": pre["key"],
                "thumb_key": pre["thumb_key"], "taken_at": taken_at,
                "width": 1200, "height": 800, "content_type": "image/jpeg",
            })
            if st == 200 and done.get("ok"):
                log.info("→ upload OK media=%s (%d bytes)", done["media_id"], len(big))
                return done["media_id"]
            log.error("complete lỗi: %s %s", st, done)
            return None

        def live_view_loop():
            """Gửi frame giả theo fps khi có session active."""
            while True:
                sid = live["session_id"]
                if not sid or not http:
                    time.sleep(0.5)
                    continue
                live["seq"] += 1
                frame = _fake_jpeg(640, 424,
                                   f"LIVE {code} seq={live['seq']} iso={applied['iso']} {applied['shutter_speed']}")
                try:
                    st, resp = http.post_frame("/api/device/live/frame/", frame,
                                               sid, live["seq"])
                    if st == 200 and resp.get("ok"):
                        log.info("→ live frame seq=%d (%d bytes)", live["seq"], len(frame))
                except urllib.error.HTTPError as e:
                    if e.code == 409:
                        log.info("Live session kết thúc (server báo 409).")
                        live["session_id"] = None
                        continue
                    log.warning("live frame lỗi HTTP %s", e.code)
                except Exception:
                    log.exception("live frame lỗi")
                time.sleep(max(0.2, 1.0 / max(1, live["fps"])))

        def capture_loop():
            """Chụp + upload theo chu kỳ capture_interval_sec."""
            while True:
                wait = state["capture_interval_sec"]
                # chờ theo chu kỳ, kiểm tra mỗi giây để nhận interval mới
                for _ in range(wait):
                    time.sleep(1)
                    if state["capture_interval_sec"] != wait:
                        break
                else:
                    if http:
                        try:
                            capture_and_upload()
                        except Exception:
                            log.exception("capture loop lỗi")

        def on_connect(c, u, f, rc, props=None):
            log.info("Đã kết nối broker (rc=%s) — username=%s", rc, code)
            c.subscribe(t_cmd, qos=1)
            c.publish(t_status, json.dumps({"online": True}), qos=1, retain=True)
            publish_telemetry()

        def on_message(c, u, msg):
            try:
                req = json.loads(msg.payload.decode())
            except ValueError:
                return
            cmd = req.get("command")
            rid = req.get("request_id", "")
            payload = req.get("payload") or {}
            log.info("← lệnh %s (%s)", cmd, rid)
            time.sleep(0.4)  # giả lập độ trễ xử lý gphoto2

            if cmd == "set_settings":
                for k, v in payload.items():
                    if k == "exposure_mode":
                        continue  # num vật lý — không set được
                    applied[k] = v
                resp = {"type": "set_settings", "request_id": rid, "status": "ok",
                        "data": {"applied": applied, "capabilities": _CAPABILITIES}}
            elif cmd == "get_settings":
                resp = {"type": "get_settings", "request_id": rid, "status": "ok",
                        "data": {"applied": applied, "capabilities": _CAPABILITIES}}
            elif cmd == "get_sim_info":
                resp = {"type": "get_sim_info", "request_id": rid, "status": "ok",
                        "data": {"sim": {**_SIM_INFO, "signal_dbm": random.randint(-95, -60)}}}
            elif cmd == "capture_now":
                media_id = None
                try:
                    media_id = capture_and_upload()
                except Exception:
                    log.exception("capture_now upload lỗi")
                if media_id:
                    resp = {"type": "capture_now", "request_id": rid, "status": "ok",
                            "data": {"media_id": media_id}}
                else:
                    resp = {"type": "capture_now", "request_id": rid, "status": "ok",
                            "data": {"note": "capture simulated (chưa upload — thiếu credential)"}}
            elif cmd == "set_interval":
                state["capture_interval_sec"] = payload.get("capture_interval_sec",
                                                            state["capture_interval_sec"])
                resp = {"type": "set_interval", "request_id": rid, "status": "ok",
                        "data": {"capture_interval_sec": state["capture_interval_sec"]}}
            elif cmd == "start_live_view":
                live["session_id"] = payload.get("session_id") or "lv-unknown"
                live["fps"] = max(1, min(2, int(payload.get("fps") or 1)))
                live["seq"] = 0
                resp = {"type": "start_live_view", "request_id": rid, "status": "ok",
                        "data": {"live_view": True, "session_id": live["session_id"],
                                 "fps": live["fps"], "width": 640, "height": 424}}
            elif cmd == "stop_live_view":
                live["session_id"] = None
                resp = {"type": "stop_live_view", "request_id": rid, "status": "ok",
                        "data": {"live_view": False}}
            else:
                resp = {"type": cmd, "request_id": rid, "status": "error",
                        "error": "unknown_command"}
            c.publish(t_ack, json.dumps(resp), qos=1)
            log.info("→ ack %s", resp["type"])

        client.on_connect = on_connect
        client.on_message = on_message
        client.reconnect_delay_set(1, 60)
        client.connect(dj.MQTT_BROKER, dj.MQTT_PORT, 30)

        stop = threading.Event()

        def telemetry_loop():
            while not stop.wait(interval):
                try:
                    publish_telemetry()
                except Exception:
                    log.exception("telemetry error")

        threading.Thread(target=telemetry_loop, daemon=True).start()
        threading.Thread(target=live_view_loop, daemon=True).start()
        if http:
            threading.Thread(target=capture_loop, daemon=True).start()
        self.stdout.write(f"Simulator chạy cho camera {code} (Ctrl+C để dừng)…")
        try:
            client.loop_forever(retry_first_connection=True)
        except KeyboardInterrupt:
            stop.set()
            client.publish(t_status, json.dumps({"online": False}), qos=1, retain=True)
            client.disconnect()
