"""
Camera profile — định nghĩa thông số và choices cho từng model máy ảnh.

Mỗi profile là một dict với cấu trúc:
{
    "fields": {
        "<field_name>": {
            "widget":    str,         # tên widget gphoto2
            "label":     str,         # nhãn hiển thị trên UI
            "type":      str,         # "RADIO" | "RANGE" | "TOGGLE"
            "choices":   list[str],   # [] nếu phụ thuộc focal-length / dynamic
            "dynamic":   bool,        # True → choices lấy từ capabilities mỗi lần
            "readonly":  bool,        # True → hiển thị nhưng không cho chỉnh
            "note":      str,         # ghi chú kỹ thuật
        },
        ...
    },
    "readonly_display": [str, ...],   # widgets chỉ đọc cần hiển thị trên UI
    "settable":         [str, ...],   # thứ tự fields người dùng có thể chỉnh
    "defaults_day":     dict,         # bộ mặc định timelapse ban ngày
    "defaults_night":   dict,         # bộ mặc định timelapse ban đêm
}
"""

# ── Nikon D5300 ───────────────────────────────────────────────────────────────
# Test ngày 2026-07-26 — set + readback tất cả fields xác nhận OK.
# Xem chi tiết: HW/NIKON_D5300_SETTINGS.md

NIKON_D5300 = {
    "label": "Nikon D5300",
    "fields": {
        # ── Phơi sáng ────────────────────────────────────────────────────
        "iso": {
            "widget": "iso",
            "label": "ISO",
            "type": "RADIO",
            "choices": [
                "100", "125", "160", "200", "250", "320",
                "400", "500", "640", "800", "1000", "1250",
                "1600", "2000", "2500", "3200", "4000", "5000",
                "6400", "8000", "10000", "12800", "16000", "20000", "25600",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "ISO 100–25600 (L0.3–H2.0)",
        },
        "aperture": {
            "widget": "f-number",
            "label": "Aperture",
            "type": "RADIO",
            # choices phụ thuộc focal length → phải lấy từ capabilities mỗi lần
            "choices": [
                "f/1.4", "f/1.6", "f/1.8", "f/2", "f/2.2", "f/2.5",
                "f/2.8", "f/3.2", "f/3.5", "f/4", "f/4.5", "f/5",
                "f/5.6", "f/6.3", "f/7.1", "f/8", "f/9", "f/10",
                "f/11", "f/13", "f/14", "f/16", "f/18", "f/20",
                "f/22", "f/25", "f/29", "f/32", "f/36",
            ],
            "dynamic": True,   # capabilities override choices
            "readonly": False,
            "note": "Thay đổi theo focal length — dùng capabilities",
        },
        "shutter_speed": {
            "widget": "shutterspeed2",
            "label": "Shutter Speed",
            "type": "RADIO",
            "choices": [
                "1/4000", "1/3200", "1/2500", "1/2000", "1/1600", "1/1250",
                "1/1000", "1/800", "1/640", "1/500", "1/400", "1/320",
                "1/250", "1/200", "1/160", "1/125", "1/100", "1/80",
                "1/60", "1/50", "1/40", "1/30", "1/25", "1/20",
                "1/15", "1/13", "1/10", "1/8", "1/6", "1/5",
                "1/4", "1/3", "10/25", "1/2", "10/16", "10/13",
                "1", "13/10", "16/10", "2", "25/10", "3",
                "4", "5", "6", "8", "10", "13",
                "15", "20", "25", "30", "Time", "Bulb",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "Dùng shutterspeed2 (không phải shutterspeed)",
        },
        "exposure_compensation": {
            "widget": "exposurecompensation",
            "label": "EV",
            "type": "RADIO",
            # RADIO widget — giá trị PHẢI khớp chính xác chuỗi dưới đây
            "choices": [
                "-5", "-4.666", "-4.333", "-4", "-3.666", "-3.333",
                "-3", "-2.666", "-2.333", "-2", "-1.666", "-1.333",
                "-1", "-0.666", "-0.333", "0",
                "0.333", "0.666", "1", "1.333", "1.666",
                "2", "2.333", "2.666", "3", "3.333", "3.666",
                "4", "4.333", "4.666", "5",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "RADIO — không nhập số tự do; phải chọn đúng chuỗi",
        },
        # ── Màu sắc / trắng ──────────────────────────────────────────────
        "white_balance": {
            "widget": "whitebalance",
            "label": "White Balance",
            "type": "RADIO",
            "choices": [
                "Automatic", "Daylight", "Fluorescent", "Tungsten",
                "Flash", "Cloudy", "Shade", "Preset",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "",
        },
        # ── Ảnh ─────────────────────────────────────────────────────────
        "image_format": {
            "widget": "imagequality",
            "label": "Image Format",
            "type": "RADIO",
            "choices": [
                "JPEG Basic", "JPEG Normal", "JPEG Fine",
                "NEF (Raw)", "NEF+Basic", "NEF+Normal", "NEF+Fine",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "NEF = RAW. NEF+* = RAW + JPEG cùng lúc",
        },
        "image_size": {
            "widget": "imagesize",
            "label": "Image Size",
            "type": "RADIO",
            "choices": ["6000x4000", "4496x3000", "2992x2000"],
            "dynamic": False,
            "readonly": False,
            "note": "Chỉ ảnh hưởng JPEG; NEF luôn full 24MP",
        },
        # ── Focus ────────────────────────────────────────────────────────
        "focus_mode": {
            "widget": "focusmode2",
            "label": "Focus Mode",
            "type": "RADIO",
            "choices": ["AF-S", "AF-C", "AF-A", "MF (fixed)", "MF (selection)"],
            "dynamic": False,
            "readonly": False,
            "note": "Bị giới hạn bởi công tắc vật lý trên lens (M/A). "
                    "Nếu switch ở M: chỉ MF variants hoạt động.",
        },
        "autofocus": {
            "widget": "autofocus",
            "label": "Autofocus",
            "type": "RADIO",
            "choices": ["On", "Off"],
            "dynamic": False,
            "readonly": False,
            "note": "Bật/tắt AF-assist và AF khi bấm shutter",
        },
        "liveview_af": {
            "widget": "liveviewaffocus",
            "label": "Live View AF",
            "type": "RADIO",
            "choices": ["Single-servo AF", "Full-time-servo AF",
                        "Manual Focus (fixed)", "Manual Focus (selection)"],
            "dynamic": False,
            "readonly": False,
            "note": "AF mode khi Live View — tách biệt với focusmode2",
        },
        # ── Chụp ────────────────────────────────────────────────────────
        "capture_mode": {
            "widget": "capturemode",
            "label": "Capture Mode",
            "type": "RADIO",
            "choices": [
                "Single Shot", "Burst", "Continuous Low Speed",
                "Timer", "Quick Response Remote", "Delayed Remote", "Quiet Release",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "",
        },
        "capture_target": {
            "widget": "capturetarget",
            "label": "Capture Target",
            "type": "RADIO",
            "choices": ["Internal RAM", "Memory card"],
            "dynamic": False,
            "readonly": False,
            "note": "Internal RAM: server tự kéo ảnh qua PTP (khuyến nghị). "
                    "Memory card: ảnh lưu thẻ SD.",
        },
        # ── Noise reduction ──────────────────────────────────────────────
        "high_iso_nr": {
            "widget": "highisonr",
            "label": "High ISO NR",
            "type": "RADIO",
            "choices": ["Off", "Low", "Normal", "High"],
            "dynamic": False,
            "readonly": False,
            "note": "",
        },
        "long_exp_nr": {
            "widget": "longexpnr",
            "label": "Long-exp. NR",
            "type": "RADIO",
            "choices": ["On", "Off"],
            "dynamic": False,
            "readonly": False,
            "note": "Dark frame subtraction khi tốc độ ≥ 1s; tốn gấp đôi thời gian chụp",
        },
        # ── Read-only (physical controls) — hiển thị không chỉnh ────────
        "exposure_mode": {
            "widget": "expprogram",
            "label": "Exposure Mode",
            "type": "RADIO",
            "choices": ["M", "P", "A", "S", "Auto", "Portrait", "Landscape", "Macro",
                        "Sports", "Night Landscape", "Children"],
            "dynamic": False,
            "readonly": True,
            "note": "Physical dial — chỉ đọc",
        },
        "focus_switch": {
            "widget": "focusmode",
            "label": "Focus Switch",
            "type": "RADIO",
            "choices": ["Manual", "AF-S", "AF-C", "AF-A"],
            "dynamic": False,
            "readonly": True,
            "note": "Physical switch trên lens — chỉ đọc",
        },
    },

    # Thứ tự field chỉnh được trên form UI
    "settable": [
        "iso", "aperture", "shutter_speed", "exposure_compensation",
        "white_balance", "image_format", "image_size",
        "focus_mode", "autofocus", "liveview_af",
        "capture_mode", "capture_target",
        "high_iso_nr", "long_exp_nr",
    ],

    # Thứ tự field chỉ hiển thị (read-only)
    "readonly_display": ["exposure_mode", "focus_switch"],

    # Bộ mặc định timelapse ban ngày
    "defaults_day": {
        "iso":                  "200",
        "aperture":             "f/8",
        "shutter_speed":        "1/250",
        "exposure_compensation": "0",
        "white_balance":        "Daylight",
        "image_format":         "NEF+Fine",
        "image_size":           "2992x2000",
        "capture_mode":         "Single Shot",
        "capture_target":       "Internal RAM",
        "high_iso_nr":          "Normal",
        "long_exp_nr":          "Off",
        "autofocus":            "Off",
        "liveview_af":          "Manual Focus (fixed)",
        "focus_mode":           "MF (fixed)",
    },

    # Bộ mặc định timelapse ban đêm
    "defaults_night": {
        "iso":                  "3200",
        "aperture":             "f/5.6",
        "shutter_speed":        "30",
        "exposure_compensation": "0",
        "white_balance":        "Automatic",
        "image_format":         "NEF+Fine",
        "image_size":           "2992x2000",
        "capture_mode":         "Single Shot",
        "capture_target":       "Internal RAM",
        "high_iso_nr":          "High",
        "long_exp_nr":          "On",
        "autofocus":            "Off",
        "focus_mode":           "MF (fixed)",
    },
}


# ── Generic (placeholder cho máy chưa có profile) ─────────────────────────────
GENERIC = {
    "label": "Generic Camera",
    "fields": {},
    "settable": [],
    "readonly_display": [],
    "defaults_day": {},
    "defaults_night": {},
}


# ── Registry — tra cứu bằng Camera.camera_model ───────────────────────────────
CAMERA_SPECS: dict[str, dict] = {
    "nikon_d5300": NIKON_D5300,
    "nikon_d3500": NIKON_D5300,   # D3500 tương tự D5300 — dùng tạm cùng profile
    "nikon_d7500": GENERIC,       # chưa có profile riêng
    "nikon_z50":   GENERIC,
    "canon_200d":  GENERIC,
    "canon_90d":   GENERIC,
    "generic":     GENERIC,
}


def get_spec(camera_model: str) -> dict:
    """Trả về profile cho camera_model; fallback về GENERIC nếu chưa có."""
    return CAMERA_SPECS.get(camera_model, GENERIC)


def get_settable_fields(camera_model: str) -> list[str]:
    """Danh sách field có thể chỉnh cho model này."""
    return get_spec(camera_model).get("settable", [])


def get_field_choices(camera_model: str, field_name: str) -> list[str]:
    """Choices tĩnh cho 1 field; [] nếu dynamic hoặc không tồn tại."""
    spec = get_spec(camera_model)
    fdef = spec.get("fields", {}).get(field_name, {})
    return fdef.get("choices", [])


def is_field_readonly(camera_model: str, field_name: str) -> bool:
    """True nếu field chỉ đọc (vật lý)."""
    spec = get_spec(camera_model)
    fdef = spec.get("fields", {}).get(field_name, {})
    return bool(fdef.get("readonly", False))
