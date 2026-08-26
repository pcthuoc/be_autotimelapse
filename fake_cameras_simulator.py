#!/usr/bin/env python3
"""
Fake Multi-Camera Simulator Agent (AutoTimelapse CM4 Simulator)
--------------------------------------------------------------
Mô phỏng đồng thời 3 (hoặc nhiều hơn) camera trạm CM4 / WiFi Agent:
  - Tự động kết nối MQTT broker, gửi Telemetry & trạng thái Online
  - Lắng nghe lệnh từ Web (Chụp ngay, Live View, Cài đặt thông số ISO/Shutter/Aperture, Bật/Tắt nguồn)
  - Tự động tạo ảnh giả lập chân thực (đổi màu trời theo giờ, vẽ di tích Quốc Tử Giám) và Upload qua luồng S3 Presigned URL + Complete
  - Chạy ngầm trong container độc lập (--rm) hoặc background process, tự động biến mất khi Reboot hoặc Kill.
"""

import io
import json
import logging
import math
import os
import random
import signal
import ssl
import sys
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

from PIL import Image, ImageDraw, ImageFont

# Khởi tạo SSL context bỏ qua verify nếu test nội bộ / self-signed
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("❌ Lỗi: Cần cài đặt paho-mqtt: pip install paho-mqtt pillow")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fake_simulator")

# ── Cấu hình kết nối ────────────────────────────────────────────────────────
MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto")
MQTT_PORT   = int(os.getenv("MQTT_PORT", "1883"))
SERVER_BASE = os.getenv("SERVER_BASE", "http://site:8000").rstrip("/")
CAPTURE_INTERVAL = int(os.getenv("CAPTURE_INTERVAL", "60"))  # Chụp mỗi 60s
TELEMETRY_INTERVAL = int(os.getenv("TELEMETRY_INTERVAL", "30"))  # Gửi telemetry mỗi 30s

USER_AGENT = "AutoTimelapse-CM4-Agent/2.0 (Simulated-MultiCam)"

# Danh sách 5 Camera mặc định (3 Quốc Tử Giám + 2 Hưng Miếu)
DEFAULT_CAMERAS = [
    {
        "code": "CAM-59AZ3T",
        "secret": "nfDT6Cq9EXnC9yBxZm2jqQ",
        "name": "CAM CONG CHINH",
        "site": "QUOC_TU_GIAM",
        "theme": "gate",
    },
    {
        "code": "CAM-4MHNEK",
        "secret": "-vnzG44UN13AH-q9WAL-JA",
        "name": "CAM NHA THAI HOC",
        "site": "QUOC_TU_GIAM",
        "theme": "pagoda",
    },
    {
        "code": "CAM-5QRE5T",
        "secret": "Upea5L2F5ey1-D50i3IcXw",
        "name": "CAM BEN TRONG NHA",
        "site": "QUOC_TU_GIAM",
        "theme": "courtyard",
    },
    {
        "code": "CAM-KDMJTV",
        "secret": "7XV0y_aLOeB__XmmSj8kvg",
        "name": "CONG CHINH",
        "site": "HUNG_MIEU",
        "theme": "temple",
    },
    {
        "code": "CAM-KCSHPT",
        "secret": "nT2A53g5UceB8daBKPWwPg",
        "name": "CAM GOC PHAI",
        "site": "HUNG_MIEU",
        "theme": "garden",
    },
]

SETTING_SPECS = {
    "iso":                   ("iso",                 True),
    "aperture":              ("f-number",            True),
    "shutter_speed":         ("shutterspeed2",       True),
    "exposure_compensation": ("exposurecompensation",True),
    "white_balance":         ("whitebalance",        True),
    "image_format":          ("imagequality",        True),
    "image_size":            ("imagesize",           True),
    "focus_mode":            ("focusmode2",          True),
    "autofocus":             ("autofocus",           True),
    "capture_mode":          ("capturemode",         True),
    "capture_target":        ("capturetarget",       True),
    "high_iso_nr":           ("highisonr",           True),
    "long_exp_nr":           ("longexpnr",           True),
    "liveview_af":           ("liveviewaffocus",     True),
    "exposure_mode":         ("expprogram",          False),
    "focus_switch":          ("focusmode",           False),
}


class SimulatedCameraClient:
    """Mô phỏng 1 máy ảnh CM4 độc lập."""

    def __init__(self, config, broker, port, server_base, capture_interval):
        self.code = config["code"]
        self.secret = config["secret"]
        self.name = config.get("name", self.code)
        self.site_name = config.get("site", "QUOC_TU_GIAM")
        self.theme = config.get("theme", "gate")
        self.broker = broker
        self.port = port
        self.server_base = server_base
        self.capture_interval = capture_interval

        self.log = logging.getLogger(f"Cam-{self.code}")
        self.running = True
        self.is_powered = True
        self.live_session_id = None
        self.live_fps = 1
        self.live_seq = 0

        self.t_cmd    = f"camera/{self.code}/cmd"
        self.t_ack    = f"camera/{self.code}/ack"
        self.t_data   = f"camera/{self.code}/data"
        self.t_status = f"camera/{self.code}/status"

        self.settings = {
            "iso": "100", "aperture": "f/4", "shutter_speed": "1/200",
            "exposure_compensation": "0.0", "white_balance": "Auto",
            "image_format": "JPEG Fine", "image_size": "6000x4000",
            "focus_mode": "AF-S", "autofocus": "On", "capture_mode": "Single Shot",
            "capture_target": "Memory Card", "high_iso_nr": "Off",
            "long_exp_nr": "Off", "liveview_af": "Normal Area",
            "exposure_mode": "Manual", "focus_switch": "AF",
        }
        self.capabilities = {
            k: {
                "writable": v[1],
                "current": self.settings[k],
                "choices": ["Auto", "100", "200", "400", "800", "1600", "3200", "6400"] if k == "iso"
                else ["f/2.8", "f/4", "f/5.6", "f/8", "f/11", "f/16"] if k == "aperture"
                else ["1/4000", "1/2000", "1/1000", "1/500", "1/200", "1/100", "1/50", "1/4"] if k == "shutter_speed"
                else ["Auto", "Daylight", "Cloudy", "Shade", "Tungsten", "Fluorescent"] if k == "white_balance"
                else [self.settings[k]],
            }
            for k, v in SETTING_SPECS.items()
        }

        self.mqtt_client = None

    def start(self):
        """Khởi động các luồng MQTT, Telemetry, Capture và Live Stream."""
        threading.Thread(target=self._mqtt_thread, daemon=True, name=f"mqtt-{self.code}").start()
        threading.Thread(target=self._telemetry_thread, daemon=True, name=f"telem-{self.code}").start()
        threading.Thread(target=self._capture_thread, daemon=True, name=f"cap-{self.code}").start()
        threading.Thread(target=self._live_stream_thread, daemon=True, name=f"live-{self.code}").start()
        self.log.info("🚀 Khởi chạy giả lập cho %s (%s - %s)", self.code, self.name, self.site_name)

    def stop(self):
        self.running = False
        if self.mqtt_client:
            try:
                self.mqtt_client.publish(self.t_status, json.dumps({"online": False}), qos=1, retain=True)
                self.mqtt_client.disconnect()
            except Exception:
                pass

    # ── MQTT Client ──────────────────────────────────────────────────────────

    def _mqtt_thread(self):
        while self.running:
            try:
                client_id = f"fake_cm4_{self.code}_{random.randint(1000, 9999)}"
                client = mqtt.Client(client_id=client_id, clean_session=True)
                client.username_pw_set(self.code, self.secret)

                client.on_connect = self._on_mqtt_connect
                client.on_message = self._on_mqtt_message
                client.on_disconnect = self._on_mqtt_disconnect

                self.mqtt_client = client
                self.log.info("🔌 Đang kết nối MQTT Broker %s:%d...", self.broker, self.port)
                client.connect(self.broker, self.port, keepalive=60)
                client.loop_forever()
            except Exception as e:
                self.log.warning("MQTT lỗi kết nối: %s. Thử lại sau 5s...", e)
                time.sleep(5)

    def _on_mqtt_connect(self, client, userdata, flags, rc, props=None):
        if rc == 0:
            self.log.info("✅ Đã kết nối MQTT thành công!")
            client.subscribe(self.t_cmd, qos=1)
            client.publish(self.t_status, json.dumps({"online": True}), qos=1, retain=True)
            self._send_telemetry()
        else:
            self.log.error("❌ Kết nối MQTT thất bại với mã rc=%s", rc)

    def _on_mqtt_disconnect(self, client, userdata, rc):
        self.log.warning("⚠️ Mất kết nối MQTT (rc=%s)", rc)

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            req = json.loads(msg.payload.decode())
            cmd = req.get("command", "")
            rid = req.get("request_id", "")
            payload = req.get("payload") or {}
            self.log.info("📥 Nhận lệnh MQTT: %s (req_id=%s)", cmd, rid)

            resp = {"status": "ok", "request_id": rid, "type": cmd, "data": {}}

            if cmd in ("power_on_cm4", "power_on"):
                self.is_powered = True
                resp["data"] = {"cm4_power_state": "running", "camera_power": "on"}
                self._send_telemetry()

            elif cmd in ("power_off_camera", "power_off"):
                self.is_powered = False
                resp["data"] = {"camera_power": "off"}
                self._send_telemetry()

            elif cmd == "set_settings":
                for k, v in payload.items():
                    if k in self.settings:
                        self.settings[k] = str(v)
                        if k in self.capabilities:
                            self.capabilities[k]["current"] = str(v)
                resp["data"] = {"applied": self.settings, "capabilities": self.capabilities, "mismatches": {}}

            elif cmd in ("get_settings", "get_capabilities", "get_status"):
                resp["data"] = {
                    "online": True,
                    "settings": self.settings,
                    "capabilities": self.capabilities,
                    "power": "on" if self.is_powered else "off",
                    "mode": "SIMULATED_CM4",
                }

            elif cmd in ("trigger_capture", "capture"):
                self.log.info("📸 Thực hiện lệnh CHỤP ẢNH NGAY từ server...")
                media_ids = self.capture_and_upload()
                resp["data"] = {"media_ids": media_ids, "status": "captured"}

            elif cmd == "live_view_start":
                self.live_session_id = payload.get("session_id") or f"live_{int(time.time())}"
                self.live_fps = max(1, min(5, int(payload.get("fps", 1))))
                self.live_seq = 0
                self.log.info("🔴 Bắt đầu LIVE VIEW session: %s (fps=%d)", self.live_session_id, self.live_fps)
                resp["data"] = {"session_id": self.live_session_id, "fps": self.live_fps}

            elif cmd == "live_view_stop":
                self.log.info("⏹️ Dừng Live View session: %s", self.live_session_id)
                self.live_session_id = None
                resp["data"] = {"status": "stopped"}

            elif cmd in ("reboot", "restart_service"):
                resp["data"] = {"status": "rebooting"}

            if rid:
                client.publish(self.t_ack, json.dumps(resp), qos=1)

        except Exception as exc:
            self.log.error("Lỗi xử lý message: %s", exc)

    # ── Telemetry Loop ───────────────────────────────────────────────────────

    def _telemetry_thread(self):
        while self.running:
            time.sleep(TELEMETRY_INTERVAL)
            self._send_telemetry()

    def _send_telemetry(self):
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            return
        try:
            now_dt = datetime.now(timezone(timedelta(hours=7)))
            hour = now_dt.hour
            is_day = 6 <= hour <= 18
            solar_v = round(random.uniform(14.0, 18.2), 2) if is_day else round(random.uniform(0.1, 0.5), 2)

            data = {
                "camera_code": self.code,
                "node": "cm4",
                "cm4_power_state": "running" if self.is_powered else "off",
                "camera_gpio_power": "ON" if self.is_powered else "OFF",
                "camera_hw_mode": "SIMULATED_PIL",
                "cpu_percent": round(random.uniform(12.0, 38.0), 1),
                "memory_percent": round(random.uniform(28.0, 52.0), 1),
                "temperature_c": round(random.uniform(32.0, 44.0), 1),
                "humidity_percent": random.randint(55, 82),
                "battery_voltage": round(random.uniform(12.1, 12.6), 3),
                "battery_percent": random.randint(75, 99),
                "cell_voltages": [round(random.uniform(3.8, 4.1), 2) for _ in range(3)],
                "solar_voltage": solar_v,
                "solar_percent": random.randint(60, 100) if is_day else 0,
                "is_charging": is_day,
                "sim_signal_dbm": random.randint(-75, -55),
                "sim_source": "Quectel EC25 (Simulated)",
                "firmware_version": "cm4-autotimelapse-v2.0-sim",
                "timestamp": now_dt.isoformat(),
            }
            self.mqtt_client.publish(self.t_data, json.dumps(data), qos=1)
            self.log.info("📡 Telemetry: Pin %d%% (%.2fV), Solar %.1fV, Nhiệt độ %.1f°C",
                          data["battery_percent"], data["battery_voltage"], data["solar_voltage"], data["temperature_c"])
        except Exception as e:
            self.log.warning("Lỗi gửi telemetry: %s", e)

    # ── Chụp & Upload Ảnh Giả Lập ──────────────────────────────────────────

    def _capture_thread(self):
        # Đợi một chút khi vừa start để staggered upload
        time.sleep(random.uniform(2.0, 8.0))
        # Chụp 1 phát ngay lúc khởi động để có ảnh luôn trên Web!
        self.capture_and_upload()

        while self.running:
            if self.capture_interval > 0:
                time.sleep(self.capture_interval)
                self.capture_and_upload()
            else:
                time.sleep(10)

    def capture_and_upload(self):
        """Tạo ảnh giả lập JPEG đẹp mắt + upload lên Server."""
        try:
            taken_at = datetime.now(timezone.utc).isoformat()
            raw_bytes, thumb_bytes, w, h = self._generate_photo()
            filename = f"{self.code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

            # 1. Xin Presigned PUT URL
            st, pre = self._http_post_json("/api/device/upload/presign/", {
                "content_type": "image/jpeg",
                "taken_at": taken_at,
                "with_thumb": True,
            })
            if st != 200 or "url" not in pre:
                self.log.error("❌ Lỗi Presign (%s): %s", st, pre)
                return []

            # 2. PUT Ảnh gốc lên S3 / SeaweedFS / R2
            put_st = self._http_put(pre["url"], raw_bytes, "image/jpeg")
            if put_st not in (200, 204):
                self.log.error("❌ PUT Ảnh gốc thất bại (HTTP %s)", put_st)
                return []

            # 3. PUT Thumbnail lên SeaweedFS
            if "thumb_url" in pre and thumb_bytes:
                self._http_put(pre["thumb_url"], thumb_bytes, "image/jpeg")

            # 4. Báo Complete lên Server để ghi DB
            st_done, done = self._http_post_json("/api/device/upload/complete/", {
                "media_id": pre["media_id"],
                "key": pre["key"],
                "thumb_key": pre.get("thumb_key"),
                "taken_at": taken_at,
                "width": w,
                "height": h,
                "content_type": "image/jpeg",
                "source_name": filename,
                "size_bytes": len(raw_bytes),
            })

            if st_done == 200 and done.get("ok"):
                self.log.info("🎉 Upload THÀNH CÔNG! media_id=%s, file=%s (%d KB, %dx%d)",
                              done.get("media_id"), filename, len(raw_bytes)//1024, w, h)
                return [done.get("media_id")]
            else:
                self.log.error("❌ Complete upload thất bại: %s", done)
                return []

        except Exception as e:
            self.log.exception("Lỗi chu trình capture & upload: %s", e)
            return []

    # ── Live Stream Loop ─────────────────────────────────────────────────────

    def _live_stream_thread(self):
        while self.running:
            sid = self.live_session_id
            if not sid:
                time.sleep(0.5)
                continue

            self.live_seq += 1
            seq = self.live_seq
            try:
                frame_bytes = self._generate_live_frame()
                req = urllib.request.Request(
                    self.server_base + "/api/device/live/frame/",
                    data=frame_bytes,
                    method="POST",
                    headers={
                        "Content-Type": "image/jpeg",
                        "X-Device-Key": self.code,
                        "X-Device-Secret": self.secret,
                        "X-Live-Session": sid,
                        "X-Frame-Seq": str(seq),
                        "User-Agent": USER_AGENT,
                    },
                )
                with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                    pass
            except Exception as e:
                self.log.debug("Live stream frame err: %s", e)

            time.sleep(max(0.2, 1.0 / max(1, self.live_fps)))

    # ── HTTP Helpers ─────────────────────────────────────────────────────────

    def _http_post_json(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.server_base + path,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Device-Key": self.code,
                "X-Device-Secret": self.secret,
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")

    def _http_put(self, url, data, content_type):
        # Nếu URL nội bộ trỏ tới cloud.congnghetimelapse.com nhưng chạy trong docker network,
        # và SeaweedFS nội bộ lắng nghe ở seaweed-filer:8333 hoặc site
        upload_url = url
        req = urllib.request.Request(
            upload_url,
            data=data,
            method="PUT",
            headers={
                "Content-Type": content_type,
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return r.status

    # ── Vẽ Ảnh Giả Lập Bằng PIL ──────────────────────────────────────────────

    def _generate_photo(self, width=1920, height=1080):
        """Sinh ảnh timelapse giả lập nghệ thuật theo thời gian thực."""
        vn_time = datetime.now(timezone(timedelta(hours=7)))
        hour = vn_time.hour
        now_str = vn_time.strftime("%Y-%m-%d %H:%M:%S")

        img = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(img)

        # Màu bầu trời theo giờ trong ngày
        if 5 <= hour < 7:   # Bình minh
            sky_top, sky_bot = (30, 20, 50), (230, 120, 60)
        elif 7 <= hour < 16:  # Ban ngày trong xanh
            sky_top, sky_bot = (40, 120, 210), (140, 200, 245)
        elif 16 <= hour < 18: # Hoàng hôn vàng cam
            sky_top, sky_bot = (60, 30, 80), (240, 130, 40)
        elif 18 <= hour < 20: # Chạng vạng
            sky_top, sky_bot = (15, 15, 40), (70, 40, 90)
        else:                 # Ban đêm huyền ảo
            sky_top, sky_bot = (5, 8, 18), (20, 30, 50)

        # Vẽ Gradient nền trời
        for y in range(height):
            ratio = y / height
            r = int(sky_top[0] * (1 - ratio) + sky_bot[0] * ratio)
            g = int(sky_top[1] * (1 - ratio) + sky_bot[1] * ratio)
            b = int(sky_top[2] * (1 - ratio) + sky_bot[2] * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        # Mặt trời / Mặt trăng
        if 6 <= hour <= 18:
            sun_x = int(width * ((hour - 6) / 12.0) * 0.7 + width * 0.15)
            sun_y = int(height * 0.25 - math.sin(((hour - 6) / 12.0) * math.pi) * 120)
            draw.ellipse([sun_x - 45, sun_y - 45, sun_x + 45, sun_y + 45], fill=(255, 245, 180))
            draw.ellipse([sun_x - 60, sun_y - 60, sun_x + 60, sun_y + 60], outline=(255, 220, 100, 100), width=4)
        else:
            moon_x, moon_y = int(width * 0.8), int(height * 0.2)
            draw.ellipse([moon_x - 30, moon_y - 30, moon_x + 30, moon_y + 30], fill=(240, 240, 255))
            # Vẽ các vì sao nhỏ
            for sx, sy in [(200, 100), (450, 80), (700, 150), (1200, 90), (1500, 140), (1750, 70)]:
                draw.ellipse([sx, sy, sx + 3, sy + 3], fill=(255, 255, 255))

        # Vẽ kiến trúc Quốc Tử Giám giả lập (Khuê Văn Các / Cổng Tam Quan / Nhà Thái Học)
        ground_y = int(height * 0.75)
        # Nền đất / cỏ
        ground_color = (30, 50, 30) if hour >= 6 and hour <= 18 else (10, 20, 12)
        draw.rectangle([0, ground_y, width, height], fill=ground_color)

        building_color = (60, 40, 35) if hour >= 6 and hour <= 18 else (25, 20, 22)
        roof_color = (160, 60, 40) if hour >= 6 and hour <= 18 else (70, 30, 25)

        cx = width // 2
        if self.theme == "gate":
            # Cổng chính / Khuê Văn Các silhouette
            draw.rectangle([cx - 280, ground_y - 260, cx + 280, ground_y], fill=building_color)
            # Mái ngói cong cổ kính
            draw.polygon([(cx - 360, ground_y - 250), (cx, ground_y - 370), (cx + 360, ground_y - 250)], fill=roof_color)
            draw.polygon([(cx - 280, ground_y - 350), (cx, ground_y - 450), (cx + 280, ground_y - 350)], fill=roof_color)
            # Cửa vòm trung tâm
            draw.ellipse([cx - 90, ground_y - 200, cx + 90, ground_y], fill=(15, 15, 20))
            draw.rectangle([cx - 90, ground_y - 100, cx + 90, ground_y], fill=(15, 15, 20))
        elif self.theme == "pagoda":
            # Nhà Thái Học rộng lớn
            draw.rectangle([cx - 450, ground_y - 220, cx + 450, ground_y], fill=building_color)
            draw.polygon([(cx - 520, ground_y - 210), (cx, ground_y - 340), (cx + 520, ground_y - 210)], fill=roof_color)
            for col_x in range(cx - 380, cx + 400, 90):
                draw.rectangle([col_x, ground_y - 200, col_x + 25, ground_y], fill=(20, 15, 15))
        elif self.theme == "temple":
            # Hưng Miếu - Điện Thờ Cổ Kính
            draw.rectangle([cx - 380, ground_y - 240, cx + 380, ground_y], fill=building_color)
            draw.polygon([(cx - 440, ground_y - 230), (cx, ground_y - 380), (cx + 440, ground_y - 230)], fill=(180, 50, 40))
            draw.polygon([(cx - 320, ground_y - 360), (cx, ground_y - 460), (cx + 320, ground_y - 360)], fill=(180, 50, 40))
            # Cột đình son thiếp vàng
            for col_x in range(cx - 300, cx + 320, 75):
                draw.rectangle([col_x, ground_y - 220, col_x + 20, ground_y], fill=(130, 30, 20))
        elif self.theme == "garden":
            # Hưng Miếu - Góc Phải Sân Vườn Cây Cảnh
            draw.rectangle([cx - 200, ground_y - 180, cx + 400, ground_y], fill=building_color)
            draw.polygon([(cx - 250, ground_y - 170), (cx + 100, ground_y - 290), (cx + 450, ground_y - 170)], fill=roof_color)
            # Cây cổ thụ silhouette bên góc trái
            draw.rectangle([cx - 360, ground_y - 220, cx - 320, ground_y], fill=(45, 30, 20))
            draw.ellipse([cx - 460, ground_y - 340, cx - 220, ground_y - 180], fill=(25, 55, 25))
            draw.ellipse([cx - 420, ground_y - 380, cx - 260, ground_y - 240], fill=(30, 65, 30))
        else:
            # Sân trong Quốc Tử Giám & Vườn Bia Tiến Sĩ
            draw.rectangle([cx - 350, ground_y - 180, cx + 350, ground_y], fill=building_color)
            draw.polygon([(cx - 400, ground_y - 170), (cx, ground_y - 280), (cx + 400, ground_y - 170)], fill=roof_color)
            # Bia tiến sĩ nhỏ
            for bx in range(cx - 300, cx + 320, 80):
                draw.rectangle([bx, ground_y - 60, bx + 35, ground_y], fill=(80, 85, 90))

        # Khung viền & Bảng Thông Tin HUD hiện đại
        draw.rectangle([30, 30, width - 30, height - 30], outline=(0, 215, 255, 180), width=3)
        # Header banner
        draw.rectangle([50, 50, width - 50, 130], fill=(10, 16, 26))
        draw.rectangle([50, 50, width - 50, 130], outline=(0, 180, 230), width=1)

        draw.text((75, 62), f"🏛️ SITE: {self.site_name} | 📷 CAMERA: {self.code} - {self.name}", fill=(0, 235, 255))
        draw.text((75, 95), f"🕒 {now_str} (UTC+7) | Mode: SIMULATED HARDWARE | Status: 🟢 RUNNING", fill=(200, 225, 245))

        # Bottom stats banner
        draw.rectangle([50, height - 110, width - 50, height - 50], fill=(10, 16, 26))
        draw.rectangle([50, height - 110, width - 50, height - 50], outline=(0, 180, 230), width=1)

        iso = self.settings.get("iso", "100")
        aperture = self.settings.get("aperture", "f/4")
        shutter = self.settings.get("shutter_speed", "1/200")
        wb = self.settings.get("white_balance", "Auto")
        specs_str = f"⚙️ ISO: {iso} | Shutter: {shutter} | Aperture: {aperture} | WB: {wb} | Res: {width}x{height}"
        bat_str = f"🔋 Pin: {random.randint(80, 98)}% | ☀️ Solar: {round(random.uniform(14.5, 18.0), 1)}V | 📶 4G: -68dBm"
        draw.text((75, height - 98), specs_str, fill=(150, 255, 160))
        draw.text((75, height - 72), bat_str, fill=(255, 215, 100))

        # Xuất buffer JPEG
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        raw_bytes = buf.getvalue()

        # Tạo thumbnail (480x270)
        thumb_im = img.copy()
        thumb_im.thumbnail((480, 320))
        t_buf = io.BytesIO()
        thumb_im.save(t_buf, format="JPEG", quality=80)
        thumb_bytes = t_buf.getvalue()

        return raw_bytes, thumb_bytes, width, height

    def _generate_live_frame(self):
        """Tạo frame live view kích thước 640x360 cho luồng phát trực tiếp."""
        raw, _, _, _ = self._generate_photo(width=640, height=360)
        return raw


def main():
    log.info("=" * 65)
    log.info("  AUTOTIMELAPSE MULTI-CAMERA SIMULATOR DAEMON")
    log.info("  Server API Base: %s", SERVER_BASE)
    log.info("  MQTT Broker:     %s:%d", MQTT_BROKER, MQTT_PORT)
    log.info("  Capture Interval: %ds", CAPTURE_INTERVAL)
    log.info("=" * 65)

    clients = []
    for cam_cfg in DEFAULT_CAMERAS:
        client = SimulatedCameraClient(
            config=cam_cfg,
            broker=MQTT_BROKER,
            port=MQTT_PORT,
            server_base=SERVER_BASE,
            capture_interval=CAPTURE_INTERVAL,
        )
        client.start()
        clients.append(client)

    def shutdown_handler(signum, frame):
        log.info("🛑 Đang dừng toàn bộ camera giả lập...")
        for c in clients:
            c.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Giữ tiến trình sống
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
