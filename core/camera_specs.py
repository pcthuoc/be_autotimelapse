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


# ── Canon EOS (6D, 5D, 7D, 200D, 90D, R series) ──────────────────────────────
CANON_EOS = {
    "label": "Canon EOS",
    "fields": {
        "iso": {
            "widget": "iso",
            "label": "ISO",
            "type": "RADIO",
            "choices": [
                "Auto", "100", "125", "160", "200", "250", "320",
                "400", "500", "640", "800", "1000", "1250",
                "1600", "2000", "2500", "3200", "4000", "5000",
                "6400", "8000", "10000", "12800", "25600",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "ISO 100–25600 + Auto",
        },
        "aperture": {
            "widget": "aperture",
            "label": "Aperture",
            "type": "RADIO",
            "choices": [
                "f/1.4", "f/1.6", "f/1.8", "f/2", "f/2.2", "f/2.5",
                "f/2.8", "f/3.2", "f/3.5", "f/4", "f/4.5", "f/5",
                "f/5.6", "f/6.3", "f/7.1", "f/8", "f/9", "f/10",
                "f/11", "f/13", "f/14", "f/16", "f/18", "f/20",
                "f/22", "f/25", "f/29", "f/32",
            ],
            "dynamic": True,
            "readonly": False,
            "note": "Thay đổi theo lens — dùng capabilities",
        },
        "shutter_speed": {
            "widget": "shutterspeed",
            "label": "Shutter Speed",
            "type": "RADIO",
            "choices": [
                "1/4000", "1/3200", "1/2500", "1/2000", "1/1600", "1/1250",
                "1/1000", "1/800", "1/640", "1/500", "1/400", "1/320",
                "1/250", "1/200", "1/160", "1/125", "1/100", "1/80",
                "1/60", "1/50", "1/40", "1/30", "1/25", "1/20",
                "1/15", "1/13", "1/10", "1/8", "1/6", "1/5",
                "1/4", "0.3", "0.4", "0.5", "0.6", "0.8",
                "1", "1.3", "1.6", "2", "2.5", "3.2",
                "4", "5", "6", "8", "10", "13",
                "15", "20", "25", "30", "Bulb",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "Canon PTP shutterspeed values",
        },
        "exposure_compensation": {
            "widget": "exposurecompensation",
            "label": "EV",
            "type": "RADIO",
            "choices": [
                "-5", "-4.666", "-4.333", "-4", "-3.666", "-3.333",
                "-3", "-2.666", "-2.333", "-2", "-1.666", "-1.333",
                "-1", "-0.666", "-0.333", "0",
                "+0.333", "+0.666", "+1", "+1.333", "+1.666",
                "+2", "+2.333", "+2.666", "+3", "+3.333", "+3.666",
                "+4", "+4.333", "+4.666", "+5",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "Exposure compensation range",
        },
        "white_balance": {
            "widget": "whitebalance",
            "label": "White Balance",
            "type": "RADIO",
            "choices": [
                "Auto", "Daylight", "Shade", "Cloudy", "Tungsten",
                "White Fluorescent", "Flash", "Custom", "Color Temperature",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "",
        },
        "image_format": {
            "widget": "imageformat",
            "label": "Image Format",
            "type": "RADIO",
            "choices": [
                "Large Fine JPEG", "Large Normal JPEG", "Medium Fine JPEG", "Medium Normal JPEG",
                "Small Fine JPEG", "Small Normal JPEG", "Smaller JPEG", "Tiny JPEG",
                "RAW", "mRAW", "sRAW",
                "RAW + Large Fine JPEG", "RAW + Large Normal JPEG", "RAW + Medium Fine JPEG", "RAW + Medium Normal JPEG",
                "RAW + Small Fine JPEG", "RAW + Small Normal JPEG", "RAW + Smaller JPEG", "RAW + Tiny JPEG",
                "mRAW + Large Fine JPEG", "mRAW + Large Normal JPEG", "mRAW + Medium Fine JPEG", "mRAW + Medium Normal JPEG",
                "mRAW + Small Fine JPEG", "mRAW + Small Normal JPEG", "mRAW + Smaller JPEG", "mRAW + Tiny JPEG",
                "sRAW + Large Fine JPEG", "sRAW + Large Normal JPEG", "sRAW + Medium Fine JPEG", "sRAW + Medium Normal JPEG",
                "sRAW + Small Fine JPEG", "sRAW + Small Normal JPEG", "sRAW + Smaller JPEG", "sRAW + Tiny JPEG",
                "L", "M", "S1", "S2", "S3",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "Canon tích hợp cả Image Size (Large 20MP, Medium 8.9MP, Small 5MP...) và Định dạng (JPEG/RAW) vào cùng widget này.",
        },
        "image_size": {
            "widget": "aspectratio",
            "label": "Image Size / Aspect",
            "type": "RADIO",
            "choices": ["3:2 (Full 20MP)", "4:3", "16:9", "1:1"],
            "dynamic": False,
            "readonly": False,
            "note": "Tỉ lệ khung hình (Kích thước chính được điều khiển qua Image Format)",
        },
        "aspect_ratio": {
            "widget": "aspectratio",
            "label": "Aspect Ratio",
            "type": "RADIO",
            "choices": ["3:2", "4:3", "16:9", "1:1"],
            "dynamic": False,
            "readonly": False,
            "note": "",
        },
        "capture_mode": {
            "widget": "drivemode",
            "label": "Capture Mode",
            "type": "RADIO",
            "choices": [
                "Single", "Continuous", "Single silent",
                "Continuous silent", "Timer 2 sec", "Timer 10 sec",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "Tương đương Drive Mode trên Canon EOS",
        },
        "drivemode": {
            "widget": "drivemode",
            "label": "Drive Mode",
            "type": "RADIO",
            "choices": [
                "Single", "Continuous", "Single silent",
                "Continuous silent", "Timer 2 sec", "Timer 10 sec",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "Single cho timelapse",
        },
        "capture_target": {
            "widget": "capturetarget",
            "label": "Capture Target",
            "type": "RADIO",
            "choices": ["Internal RAM", "Memory card"],
            "dynamic": False,
            "readonly": False,
            "note": "Internal RAM (không cần thẻ nhớ) hoặc Memory card",
        },
        "focus_mode": {
            "widget": "focusmode",
            "label": "Focus Mode",
            "type": "RADIO",
            "choices": ["One Shot", "AI Focus", "AI Servo", "Manual"],
            "dynamic": False,
            "readonly": False,
            "note": "Khi gạt Lens MF, máy giữ ở Manual. Khi gạt AF, hỗ trợ One Shot / AI Focus / AI Servo",
        },
        "autofocus": {
            "widget": "eosremoterelease",
            "label": "AF Remote Release",
            "type": "RADIO",
            "choices": [
                "None", "Press Half", "Press Full", "Immediate",
                "Release Half", "Release Full", "Press 1", "Press 2", "Press 3",
            ],
            "dynamic": False,
            "readonly": False,
            "note": "Press Half (lấy nét AF), Press Full (chụp AF)",
        },
        "manual_focus_drive": {
            "widget": "manualfocusdrive",
            "label": "Manual Focus Drive",
            "type": "RADIO",
            "choices": ["Near 1", "Near 2", "Near 3", "None", "Far 1", "Far 2", "Far 3"],
            "dynamic": False,
            "readonly": False,
            "note": "Vi chỉnh khoảng cách lấy nét từ xa (Near: Gần lại, Far: Ra xa)",
        },
        "liveview_af": {
            "widget": "liveviewsize",
            "label": "Live View AF / Size",
            "type": "RADIO",
            "choices": ["Large", "Medium", "Small"],
            "dynamic": False,
            "readonly": False,
            "note": "Độ phân giải Live View preview",
        },
        "high_iso_nr": {
            "widget": "highisonr",
            "label": "High ISO NR",
            "type": "RADIO",
            "choices": ["Off", "Low", "Normal", "High", "Multi-Shot"],
            "dynamic": False,
            "readonly": False,
            "note": "",
        },
        "mirror_lockup": {
            "widget": "mirrorlock",
            "label": "Mirror Lockup",
            "type": "RADIO",
            "choices": ["0", "1"],
            "dynamic": False,
            "readonly": False,
            "note": "0=Tắt, 1=Bật",
        },
        "auto_power_off": {
            "widget": "autopoweroff",
            "label": "Auto Power Off",
            "type": "RADIO",
            "choices": ["0", "60", "120", "240", "480", "900", "1800", "1 min", "2 min", "4 min", "8 min", "15 min", "30 min", "Off", "Disable"],
            "dynamic": False,
            "readonly": False,
            "note": "0 hoặc Off để tránh máy ngủ khi timelapse",
        },
        "metering_mode": {
            "widget": "meteringmode",
            "label": "Metering Mode",
            "type": "RADIO",
            "choices": ["Evaluative", "Partial", "Spot", "Center-weighted average"],
            "dynamic": False,
            "readonly": False,
            "note": "",
        },
    },
    "settable": [
        "iso", "aperture", "shutter_speed", "exposure_compensation",
        "white_balance", "image_format", "image_size", "capture_mode",
        "drivemode", "capture_target", "focus_mode", "autofocus",
        "liveview_af", "high_iso_nr", "mirror_lockup", "auto_power_off", "metering_mode",
    ],
    "readonly_display": ["exposure_mode", "battery_level"],
    "defaults_day": {
        "iso":                  "200",
        "aperture":             "f/8",
        "shutter_speed":        "1/250",
        "exposure_compensation": "0",
        "white_balance":        "Daylight",
        "image_format":         "Large Fine JPEG",
        "drivemode":            "Single",
        "capture_target":       "Memory card",
        "auto_power_off":       "0",
    },
    "defaults_night": {
        "iso":                  "3200",
        "aperture":             "f/5.6",
        "shutter_speed":        "30",
        "exposure_compensation": "0",
        "white_balance":        "Auto",
        "image_format":         "Large Fine JPEG",
        "drivemode":            "Single",
        "capture_target":       "Memory card",
        "auto_power_off":       "0",
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
    "nikon_d3500": NIKON_D5300,   # D3500 tương tự D5300 — dùng cùng profile
    "nikon_d7100": NIKON_D5300,   # D7100 cùng dải ISO/shutter với D5300
    "nikon_d7500": NIKON_D5300,
    "nikon_z50":   NIKON_D5300,
    "canon_6d":    CANON_EOS,
    "canon_6d2":   CANON_EOS,
    "canon_5d3":   CANON_EOS,
    "canon_5d4":   CANON_EOS,
    "canon_5ds":   CANON_EOS,
    "canon_7d":    CANON_EOS,
    "canon_7d2":   CANON_EOS,
    "canon_200d":  CANON_EOS,
    "canon_90d":   CANON_EOS,
    "canon_r":     CANON_EOS,
    "canon_r5":    CANON_EOS,
    "canon_r6":    CANON_EOS,
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
