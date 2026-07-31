"""Dual-Core Node Simulator (ESP32-S3 + CM4) with Simulated Camera & Sensor Data.

Gồm 2 lõi hoạt động song song / phối hợp:
1. ESP32-S3 (Always-on Node):
   - Nhận lệnh MQTT `power_on_cm4`, điều khiển relay/MOSFET để bật nguồn CM4.
   - Gửi telemetry định kỳ ở chế độ chờ (pin, solar, nhiệt độ, cm4_power_state).
   - Quản lý chu kỳ chụp định kỳ và chuyển tiếp lệnh từ Server tới CM4.

2. CM4 (Compute Node - Giả lập Máy ảnh & Cảm biến):
   - Khi CM4_STATE == "running": Giả lập chụp ảnh (tạo ảnh JPEG hợp lệ bằng PIL + timestamp text), upload qua HTTP API (`/upload/presign/` & `/complete/`), tạo Live View preview frames.
   - Phản hồi các thông số máy ảnh giả lập (ISO, Aperture, Shutter Speed, White Balance, v.v.).
   - Gửi telemetry chi tiết của node CM4 (Pin, Solar, Cell Voltages, Signal DBM, Nhiệt độ, Độ ẩm).

Chạy: python3 sim_dualcore.py
"""

import io
import json
import logging
import random
import threading
import queue as _queue
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from PIL import Image, ImageDraw, ImageFont

# ── Thông số kết nối MQTT & Server ──────────────────────────────────────────
CAMERA_CODE     = "CAM-F53RQV"
MQTT_PASSWORD   = "8_2Vhy43gl6GcPvMuDu3eQ"
MQTT_BROKER     = "atl-mosquitto"
MQTT_PORT       = 1883

SERVER_BASE     = "http://localhost:8000"
DEVICE_KEY      = CAMERA_CODE
DEVICE_SECRET   = MQTT_PASSWORD

TELEMETRY_SEC   = 30    # Chu kỳ gửi telemetry (giây)
CAPTURE_SEC     = 0     # Chu kỳ tự động chụp + upload (giây); 0 = tắt
LIVE_FPS_MAX    = 2     # Tốc độ frame live view tối đa
# ────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
log = logging.getLogger("sim_dualcore")

_SETTING_SPECS = {
    "iso":                  ("iso",                 True),
    "aperture":             ("f-number",            True),
    "shutter_speed":        ("shutterspeed2",       True),
    "exposure_compensation":("exposurecompensation", True),
    "white_balance":        ("whitebalance",        True),
    "image_format":         ("imagequality",        True),
    "image_size":           ("imagesize",           True),
    "focus_mode":           ("focusmode2",          True),
    "autofocus":            ("autofocus",           True),
    "capture_mode":         ("capturemode",         True),
    "capture_target":       ("capturetarget",       True),
    "high_iso_nr":          ("highisonr",           True),
    "long_exp_nr":          ("longexpnr",           True),
    "liveview_af":          ("liveviewaffocus",     True),
    "exposure_mode":        ("expprogram",          False),
    "focus_switch":         ("focusmode",           False),
}

_SIM_INFO = {
    "operator": "Viettel 4G (Simulated)",
    "number": "+84987654321",
    "iccid": "8984047123456789012",
}

_state = {"capture_interval_sec": CAPTURE_SEC, "upload_nef": False}
_live  = {"session_id": None, "fps": 1, "seq": 0}
_cmd_queue = _queue.SimpleQueue()
_cm4_state = {"power": "running"}   # "off", "powering_on", "running"

T_CMD    = f"camera/{CAMERA_CODE}/cmd"
T_ACK    = f"camera/{CAMERA_CODE}/ack"
T_DATA   = f"camera/{CAMERA_CODE}/data"
T_STATUS = f"camera/{CAMERA_CODE}/status"


# ── Giả lập Camera Backend (Nikon Simulated) ───────────────────────────────

class SimulatedCameraBackend:
    """Giả lập toàn bộ thao tác máy ảnh Nikon D5300 mà không cần phần cứng gphoto2 thật."""

    def __init__(self):
        self._lock = threading.Lock()
        self._applied = {
            "iso": "100",
            "aperture": "f/4",
            "shutter_speed": "1/200",
            "exposure_compensation": "0.0",
            "white_balance": "Auto",
            "image_format": "JPEG Fine",
            "image_size": "6000x4000",
            "focus_mode": "AF-S",
            "autofocus": "On",
            "capture_mode": "Single Shot",
            "capture_target": "Memory Card",
            "high_iso_nr": "Off",
            "long_exp_nr": "Off",
            "liveview_af": "Normal Area",
            "exposure_mode": "Manual",
            "focus_switch": "AF",
        }
        self._capabilities = {
            k: {
                "writable": v[1],
                "current": self._applied[k],
                "choices": [self._applied[k], "Option1", "Option2"] if v[1] else [],
            }
            for k, v in _SETTING_SPECS.items()
        }
        # Thêm lựa chọn phong phú hơn cho các setting thông dụng
        self._capabilities["iso"]["choices"] = ["100", "200", "400", "800", "1600", "3200", "6400"]
        self._capabilities["aperture"]["choices"] = ["f/2.8", "f/4", "f/5.6", "f/8", "f/11", "f/16"]
        self._capabilities["shutter_speed"]["choices"] = ["1/4000", "1/2000", "1/1000", "1/500", "1/200", "1/100", "1/50", "1/4"]
        self._capabilities["white_balance"]["choices"] = ["Auto", "Daylight", "Cloudy", "Shade", "Tungsten", "Fluorescent"]

    def get_settings(self):
        if _cm4_state["power"] != "running":
            raise RuntimeError("CM4 Core đang OFF! Không thể truy vấn camera.")
        with self._lock:
            return dict(self._applied), dict(self._capabilities)

    def set_settings(self, requested):
        if _cm4_state["power"] != "running":
            raise RuntimeError("CM4 Core đang OFF! Không thể áp dụng thông số.")
        with self._lock:
            settable = {f for f, (_, ok) in _SETTING_SPECS.items() if ok}
            for field, val in requested.items():
                if field in settable:
                    self._applied[field] = str(val)
                    if field in self._capabilities:
                        self._capabilities[field]["current"] = str(val)
            mismatches = {}
            return dict(self._applied), dict(self._capabilities), mismatches

    def get_status(self):
        applied, capabilities = self.get_settings()
        return {
            "online": True,
            "model": "Simulated Nikon D5300 (Dual-Core)",
            "battery_percent": random.randint(75, 99),
            "applied": applied,
            "capabilities": capabilities,
        }

    def generate_simulated_image(self, width=1920, height=1080, title="AutoTimelapse Frame"):
        """Tạo 1 file ảnh JPEG giả lập đẹp mắt kèm thông số & timestamp."""
        img = Image.new("RGB", (width, height), color=(20, 24, 33))
        draw = ImageDraw.Draw(img)

        # Gradient hiệu ứng nền
        for y in range(height):
            r = int(20 + (y / height) * 30)
            g = int(24 + (y / height) * 40)
            b = int(33 + (y / height) * 60)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        # Khung viền & thông tin
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        draw.rectangle([40, 40, width - 40, height - 40], outline=(0, 210, 255), width=3)
        draw.rectangle([60, 60, width - 60, 140], fill=(10, 15, 25))

        draw.text((80, 80), f"📷 {title} - {CAMERA_CODE}", fill=(0, 230, 255))
        draw.text((80, 110), f"🕒 Timestamp: {now_str} UTC | CM4 Core: RUNNING", fill=(200, 220, 240))

        # Vẽ hình mẫu giả lập cảnh công trình
        draw.rectangle([100, 200, 400, height - 100], fill=(45, 55, 72), outline=(100, 116, 139), width=2)
        draw.rectangle([450, 300, 800, height - 100], fill=(30, 41, 59), outline=(100, 116, 139), width=2)
        draw.polygon([(450, 300), (625, 180), (800, 300)], fill=(71, 85, 105))

        # Hiển thị thông số máy ảnh giả lập
        iso = self._applied.get("iso", "100")
        aperture = self._applied.get("aperture", "f/4")
        shutter = self._applied.get("shutter_speed", "1/200")
        wb = self._applied.get("white_balance", "Auto")
        info_text = f"ISO: {iso} | Aperture: {aperture} | Shutter: {shutter} | WB: {wb}"
        draw.text((80, height - 80), f"⚙️ {info_text}", fill=(160, 255, 160))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    def preview(self):
        if _cm4_state["power"] != "running":
            raise RuntimeError("CM4 Core OFF")
        return self.generate_simulated_image(width=640, height=424, title="Live View Stream")

    def capture(self):
        if _cm4_state["power"] != "running":
            raise RuntimeError("CM4 Core đang OFF! Không thể chụp ảnh.")
        log.info("📸 CM4 kích hoạt chụp ảnh giả lập...")
        time.sleep(0.8) # Giả lập độ trễ màn trập
        img_bytes = self.generate_simulated_image(width=1920, height=1080, title="Full High-Res Capture")
        filename = f"SIM_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        return [(filename, img_bytes, None)]


camera_backend = SimulatedCameraBackend()


# ── HTTP API Helper ─────────────────────────────────────────────────────────

def _http_post_json(path, obj):
    body = json.dumps(obj).encode()
    req = urllib.request.Request(
        SERVER_BASE + path, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Device-Key": DEVICE_KEY, "X-Device-Secret": DEVICE_SECRET},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, json.loads(r.read().decode() or "{}")


def _http_post_frame(session_id, seq, frame_bytes):
    req = urllib.request.Request(
        SERVER_BASE + "/api/device/live/frame/",
        data=frame_bytes, method="POST",
        headers={"Content-Type": "image/jpeg",
                 "X-Device-Key": DEVICE_KEY, "X-Device-Secret": DEVICE_SECRET,
                 "X-Live-Session": session_id, "X-Frame-Seq": str(seq)},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read().decode() or "{}")


def _http_put(url, data, content_type):
    req = urllib.request.Request(url, data=data, method="PUT",
                                 headers={"Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


# ── Capture & Upload ────────────────────────────────────────────────────────

def capture_and_upload():
    if _cm4_state["power"] != "running":
        log.warning("⚠️ Lệnh chụp được gửi nhưng CM4 đang OFF — Bật CM4 tự động để chụp...")
        _cm4_state["power"] = "powering_on"
        time.sleep(1.5)
        _cm4_state["power"] = "running"

    taken_at = datetime.now(timezone.utc).isoformat()
    captured_files = camera_backend.capture()
    media_ids = []
    for camera_name, image_bytes, thumb in captured_files:
        media_id = _upload_capture_file(taken_at, camera_name, image_bytes, thumb)
        if media_id:
            media_ids.append(media_id)
    return media_ids


def _upload_capture_file(taken_at, camera_name, image_bytes, thumb):
    content_type = "image/jpeg"
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
        if thumb is None:
            image.thumbnail((480, 320))
            output = io.BytesIO()
            image.save(output, "JPEG", quality=78)
            thumb = output.getvalue()

    try:
        st, pre = _http_post_json("/api/device/upload/presign/", {
            "content_type": content_type,
            "taken_at": taken_at,
            "with_thumb": thumb is not None,
        })
        if st != 200:
            log.error("presign lỗi: %s %s", st, pre)
            return None

        _http_put(pre["url"], image_bytes, content_type)
        if thumb is not None:
            _http_put(pre["thumb_url"], thumb, "image/jpeg")

        st, done = _http_post_json("/api/device/upload/complete/", {
            "media_id":    pre["media_id"],
            "key":         pre["key"],
            "thumb_key":   pre.get("thumb_key"),
            "taken_at":    taken_at,
            "width":       width,
            "height":      height,
            "content_type": content_type,
            "source_name": camera_name,
            "size_bytes": len(image_bytes),
        })
        if st == 200 and done.get("ok"):
            log.info("→ Upload OK media_id=%s file=%s (%d bytes)",
                     done["media_id"], camera_name, len(image_bytes))
            return done["media_id"]
        log.error("complete lỗi: %s %s", st, done)
    except Exception as exc:
        log.exception("Upload ảnh giả lập thất bại: %s", exc)
    return None


# ── Threads ─────────────────────────────────────────────────────────────────

def _live_view_loop():
    while True:
        sid = _live["session_id"]
        if not sid:
            time.sleep(0.5)
            continue
        _live["seq"] += 1
        seq = _live["seq"]
        try:
            frame = camera_backend.preview()
            st, resp = _http_post_frame(sid, seq, frame)
            if st == 200 and resp.get("ok"):
                log.info("→ Live frame seq=%d (%d bytes)", seq, len(frame))
        except Exception as e:
            log.warning("Live view frame error: %s", e)
        time.sleep(max(0.3, 1.0 / max(1, _live["fps"])))


def _capture_loop():
    while True:
        wait = _state["capture_interval_sec"]
        if wait <= 0:
            time.sleep(2)
            continue
        log.info("Chu kỳ chụp kế tiếp sau %ds", wait)
        for _ in range(wait):
            time.sleep(1)
            if _state["capture_interval_sec"] != wait:
                break
        else:
            try:
                capture_and_upload()
            except Exception:
                log.exception("capture loop error")


# ── MQTT Telemetry & Messages ──────────────────────────────────────────────

def _publish_telemetry(client, node="esp32"):
    payload = {
        "node": node,
        "cm4_power_state": _cm4_state["power"],
        "sim_active_node": "cm4" if _cm4_state["power"] == "running" else "esp32",
        "battery_percent":  random.randint(70, 98),
        "battery_voltage":  round(random.uniform(11.8, 12.6), 3),
        "cell_voltages":    [round(random.uniform(3.7, 3.85), 2) for _ in range(3)],
        "is_charging":      random.random() > 0.4,
        "solar_voltage":    round(random.uniform(14.0, 18.5), 2),
        "solar_percent":    random.randint(50, 100),
        "temperature_c":    round(random.uniform(27.0, 36.5), 1),
        "humidity_percent": random.randint(50, 85),
        "sim_signal_dbm":   random.randint(-85, -60),
        "firmware_version": "sim-dualcore-v2.5",
    }
    client.publish(T_DATA, json.dumps(payload), qos=1)
    log.info("→ Telemetry [%s]: Pin %s%%, %.1f°C, CM4_Power=%s",
             node.upper(), payload["battery_percent"], payload["temperature_c"], _cm4_state["power"])


def on_connect(client, userdata, flags, rc, props=None):
    log.info("✅ Đã kết nối MQTT Broker (rc=%s) — Camera Code: %s", rc, CAMERA_CODE)
    client.subscribe(T_CMD, qos=1)
    client.publish(T_STATUS, json.dumps({"online": True}), qos=1, retain=True)
    _publish_telemetry(client, node="esp32")
    if _cm4_state["power"] == "running":
        _publish_telemetry(client, node="cm4")


def on_message(client, userdata, msg):
    try:
        raw = json.loads(msg.payload.decode())
        _cmd_queue.put((client, raw))
    except Exception as exc:
        log.error("on_message error: %s", exc)


def _process_message(client, req):
    cmd = req.get("command", "")
    rid = req.get("request_id", "")
    payload = req.get("payload") or {}
    log.info("← Lệnh MQTT nhận được: %s (req_id=%s)", cmd, rid)

    try:
        if cmd == "power_on_cm4":
            _cm4_state["power"] = "powering_on"
            _publish_telemetry(client, node="esp32")
            log.info("⚡ ESP32-S3 đệm lệnh & bật nguồn MOSFET cấp cho CM4...")
            time.sleep(1.5)
            _cm4_state["power"] = "running"
            _publish_telemetry(client, node="cm4")
            log.info("🟢 CM4 khởi động thành công, đo cảm biến Pin/Solar & sẵn sàng nhận lệnh.")
            resp = {"type": "power_on_cm4", "request_id": rid, "status": "ok",
                    "data": {"cm4_power_state": "running"}}

        elif cmd == "set_settings":
            if _cm4_state["power"] != "running":
                log.info("⚡ ESP32-S3 đệm lệnh set_settings & bật CM4...")
                _cm4_state["power"] = "running"
                _publish_telemetry(client, node="cm4")
            applied, capabilities, mismatches = camera_backend.set_settings(payload)
            resp = {"type": cmd, "request_id": rid, "status": "ok",
                    "data": {"requested": payload, "applied": applied,
                             "capabilities": capabilities, "mismatches": mismatches}}

        elif cmd in ("get_settings", "get_capabilities", "get_status"):
            if _cm4_state["power"] != "running":
                _cm4_state["power"] = "running"
                _publish_telemetry(client, node="cm4")
            applied, capabilities = camera_backend.get_settings()
            resp = {"type": cmd, "request_id": rid, "status": "ok",
                    "data": {"online": True, "applied": applied, "capabilities": capabilities,
                             "live_view": bool(_live["session_id"])}}

        elif cmd == "get_sim_info":
            resp = {"type": "get_sim_info", "request_id": rid, "status": "ok",
                    "data": {"sim": {**_SIM_INFO, "signal_dbm": random.randint(-85, -60)}}}

        elif cmd in ("capture_now", "capture"):
            media_ids = capture_and_upload()
            if media_ids:
                resp = {"type": cmd, "request_id": rid, "status": "ok",
                        "data": {"media_id": media_ids[0], "media_ids": media_ids}}
            else:
                resp = {"type": cmd, "request_id": rid, "status": "error",
                        "data": {"note": "Upload giả lập thất bại"}}

        elif cmd == "set_interval":
            interval = max(0, int(payload.get("capture_interval_sec", _state["capture_interval_sec"])))
            _state["capture_interval_sec"] = interval
            resp = {"type": "set_interval", "request_id": rid, "status": "ok",
                    "data": {"capture_interval_sec": interval}}

        elif cmd == "start_live_view":
            if _cm4_state["power"] != "running":
                _cm4_state["power"] = "running"
                _publish_telemetry(client, node="cm4")
            _live["session_id"] = payload.get("session_id") or "lv-sim"
            _live["fps"] = max(1, min(LIVE_FPS_MAX, int(payload.get("fps") or 1)))
            _live["seq"] = 0
            resp = {"type": "start_live_view", "request_id": rid, "status": "ok",
                    "data": {"live_view": True, "session_id": _live["session_id"], "fps": _live["fps"]}}

        elif cmd == "stop_live_view":
            _live["session_id"] = None
            resp = {"type": "stop_live_view", "request_id": rid, "status": "ok", "data": {"live_view": False}}

        else:
            resp = {"type": cmd, "request_id": rid, "status": "error",
                    "data": {"code": "unknown_command", "note": f"Lệnh không hỗ trợ: {cmd}"}}

    except Exception as exc:
        log.exception("Xử lý lệnh %s thất bại: %s", cmd, exc)
        resp = {"type": cmd, "request_id": rid, "status": "error", "data": {"note": str(exc)}}

    client.publish(T_ACK, json.dumps(resp), qos=1)


def _cmd_worker():
    while True:
        client, raw = _cmd_queue.get()
        try:
            _process_message(client, raw)
        except Exception as exc:
            log.error("_cmd_worker exception: %s", exc)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"🚀 Khởi tạo Dual-Core Simulator cho Camera: {CAMERA_CODE}")
    print(f"📡 MQTT Broker: {MQTT_BROKER}:{MQTT_PORT} | HTTP Base: {SERVER_BASE}")

    threading.Thread(target=_live_view_loop, daemon=True, name="liveview").start()
    threading.Thread(target=_capture_loop,   daemon=True, name="capture").start()
    threading.Thread(target=_cmd_worker,     daemon=True, name="cmd_worker").start()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CAMERA_CODE)
    client.username_pw_set(CAMERA_CODE, MQTT_PASSWORD)
    client.will_set(T_STATUS, json.dumps({"online": False}), qos=1, retain=True)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()

    last_telemetry = time.time()
    try:
        while True:
            time.sleep(1)
            if time.time() - last_telemetry >= TELEMETRY_SEC:
                _publish_telemetry(client, node="esp32")
                if _cm4_state["power"] == "running":
                    _publish_telemetry(client, node="cm4")
                last_telemetry = time.time()
    except KeyboardInterrupt:
        print("\n⏹ Dừng Dual-Core Simulator.")
    finally:
        client.publish(T_STATUS, json.dumps({"online": False}), qos=1, retain=True)
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
