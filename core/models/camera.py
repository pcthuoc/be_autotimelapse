import secrets as _secrets
import uuid

from django.conf import settings
from django.db import models


class Client(models.Model):
    """Khách hàng / Chủ đầu tư."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name="Tên khách hàng")
    contact_name = models.CharField(max_length=150, blank=True, verbose_name="Người liên hệ")
    contact_email = models.EmailField(blank=True, verbose_name="Email")
    phone = models.CharField(max_length=30, blank=True, verbose_name="Điện thoại")
    address = models.TextField(blank=True, verbose_name="Địa chỉ")
    notes = models.TextField(blank=True, verbose_name="Ghi chú")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Client"
        verbose_name_plural = "Clients"
        ordering = ("name",)

    def __str__(self):
        return self.name

    @property
    def project_count(self):
        return self.projects.count()

    @property
    def camera_count(self):
        return sum(p.cameras.count() for p in self.projects.all())


class Site(models.Model):
    """Dự án / Project — nhóm camera theo công trình."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
        verbose_name="Khách hàng",
    )
    name = models.CharField(max_length=255, verbose_name="Tên dự án")
    description = models.TextField(blank=True, verbose_name="Mô tả")
    location = models.CharField(max_length=255, blank=True, verbose_name="Địa điểm")
    start_date = models.DateField(null=True, blank=True, verbose_name="Ngày bắt đầu")
    end_date = models.DateField(null=True, blank=True, verbose_name="Ngày kết thúc")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Project"
        verbose_name_plural = "Projects"
        ordering = ("name",)

    def __str__(self):
        return self.name


class Camera(models.Model):
    """Thiết bị camera vật lý."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        MAINTENANCE = "maintenance", "Maintenance"

    class Model(models.TextChoices):
        """Dòng máy ảnh — mỗi giá trị tương ứng 1 profile settings."""
        NIKON_D5300  = "nikon_d5300",  "Nikon D5300"
        NIKON_D3500  = "nikon_d3500",  "Nikon D3500"
        NIKON_D7500  = "nikon_d7500",  "Nikon D7500"
        NIKON_Z50    = "nikon_z50",    "Nikon Z50"
        CANON_200D   = "canon_200d",   "Canon EOS 200D"
        CANON_90D    = "canon_90d",    "Canon EOS 90D"
        GENERIC      = "generic",      "Generic (other)"""

    # NOTE: thêm model mới ở đây; mỗi Model cần 1 profile trong core/camera_specs.py

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    # Mật khẩu MQTT của thiết bị (username = code). Sinh tự động khi tạo.
    mqtt_password = models.CharField(max_length=64, blank=True, default="")
    site = models.ForeignKey(
        Site,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cameras",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    timezone = models.CharField(max_length=50, default="UTC")
    camera_model = models.CharField(
        max_length=32,
        choices=Model.choices,
        default=Model.GENERIC,
        db_index=True,
        verbose_name="Camera model",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Camera"
        verbose_name_plural = "Camera"

    def __str__(self):
        return f"{self.code} – {self.name}"

    def save(self, *args, **kwargs):
        """Auto-generate a unique token code + MQTT password if missing."""
        if not self.code:
            for _ in range(10):
                candidate = _secrets.token_urlsafe(20)
                if not Camera.objects.filter(code=candidate).exists():
                    self.code = candidate
                    break
            else:
                raise RuntimeError("Could not generate unique camera code.")
        if not self.mqtt_password:
            self.mqtt_password = _secrets.token_urlsafe(16)
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------ #
    # Object-level permission helpers (CODING_RULES §2, deny-by-default)
    # ------------------------------------------------------------------ #

    def is_accessible_by(self, user):
        """User có quyền xem camera này không."""
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        if not self._has_role_perm(user, "camera.view"):
            return False
        return self._has_camera_scope(user, "can_view")

    def is_editable_by(self, user):
        """User có quyền chỉnh sửa cấu hình camera này không."""
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        if not self._has_role_perm(user, "camera.manage"):
            return False
        return self._has_camera_scope(user, "can_manage")

    def is_media_viewable_by(self, user):
        """Xem ảnh của camera này: cần role perm media.view + scope can_view."""
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        if not self._has_role_perm(user, "media.view"):
            return False
        return self._has_camera_scope(user, "can_view")

    def is_media_downloadable_by(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        if not self._has_role_perm(user, "media.download"):
            return False
        return self._has_camera_scope(user, "can_download")

    def is_media_deletable_by(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        if not self._has_role_perm(user, "media.delete"):
            return False
        return self._has_camera_scope(user, "can_delete_media")

    # -- helpers nội bộ ------------------------------------------------- #

    def _has_role_perm(self, user, perm_code):
        from core.models.permission import UserRole
        return UserRole.objects.filter(
            user=user,
            role__role_permissions__permission__code=perm_code,
        ).exists()

    def _has_camera_scope(self, user, field):
        return UserCameraAccess.objects.filter(
            user=user, camera=self, **{field: True}
        ).exists()


class CameraCredential(models.Model):
    """
    Thông tin xác thực của thiết bị camera để xin presigned URL upload.
    Mỗi camera CÓ THỂ có nhiều credential (để rotate key không downtime).
    Secret không bao giờ lưu dạng plaintext.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    camera = models.ForeignKey(
        Camera, on_delete=models.CASCADE, related_name="credentials"
    )
    # key_id: định danh công khai, camera gửi lên kèm request
    key_id = models.CharField(max_length=64, unique=True, db_index=True)
    # secret_hash: PBKDF2/bcrypt hash của secret; không bao giờ lưu raw
    secret_hash = models.CharField(max_length=256)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    last_rotated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Credential camera"
        verbose_name_plural = "Credential camera"

    def __str__(self):
        return f"{self.camera.code} / {self.key_id[:12]}… [{self.status}]"

    @classmethod
    def generate_credential(cls, camera):
        """Tạo key_id + secret ngẫu nhiên, lưu hash, trả (cred, raw_secret).
        Lặp tối đa 5 lần nếu key_id trùng (rất hiếm).
        """
        from django.contrib.auth.hashers import make_password
        from django.db import IntegrityError, transaction

        for _ in range(5):
            key_id = _secrets.token_urlsafe(16)   # 22 ký tự URL-safe
            raw_secret = _secrets.token_urlsafe(32)  # 43 ký tự
            try:
                with transaction.atomic():
                    cred = cls.objects.create(
                        camera=camera,
                        key_id=key_id,
                        secret_hash=make_password(raw_secret),
                    )
                return cred, raw_secret
            except IntegrityError:
                continue
        raise RuntimeError("Could not generate unique credential key_id.")


class UserCameraAccess(models.Model):
    """
    Scope per-camera: xác định user nào được làm gì trên camera nào.
    Admin cấp / thu hồi tại bảng này.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="camera_accesses",
    )
    camera = models.ForeignKey(
        Camera, on_delete=models.CASCADE, related_name="user_accesses"
    )
    can_view = models.BooleanField(default=True)
    can_manage = models.BooleanField(default=False)
    can_download = models.BooleanField(default=False)
    can_delete_media = models.BooleanField(default=False)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="granted_accesses",
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "camera")
        verbose_name = "Camera access"
        verbose_name_plural = "Camera accesses"

    def __str__(self):
        flags = [
            f for f, v in [
                ("view", self.can_view), ("manage", self.can_manage),
                ("download", self.can_download), ("del_media", self.can_delete_media),
            ] if v
        ]
        return f"{self.user} → {self.camera.code} [{', '.join(flags)}]"


class CameraDevice(models.Model):
    """
    Trạng thái & cấu hình vận hành của thiết bị vật lý gắn với 1 Camera.

    - Cấu hình (server → device): chu kỳ chụp, yêu cầu chụp ngay (wake).
      Thiết bị đọc các trường này ở lần check-in kế tiếp.
    - Telemetry (device → server): sóng SIM, pin, điện áp cell, solar…
      Thiết bị POST lên khi thức dậy. (Hiện chỉnh tay qua admin nếu chưa có
      firmware gửi telemetry.)
    """

    camera = models.OneToOneField(
        Camera, on_delete=models.CASCADE, related_name="device"
    )

    # ── Cấu hình (server điều khiển) ─────────────────────────────────
    capture_interval_sec = models.PositiveIntegerField(
        default=3600, help_text="Chu kỳ chụp ảnh (giây)."
    )
    wake_requested_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Thời điểm yêu cầu thiết bị chụp ngay (đánh thức).",
    )
    wake_done_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Thời điểm thiết bị xác nhận đã xử lý wake.",
    )

    # ── Telemetry mạng / SIM ─────────────────────────────────────────
    sim_operator = models.CharField(max_length=64, blank=True)
    sim_number = models.CharField(max_length=32, blank=True)
    sim_iccid = models.CharField(max_length=32, blank=True)
    # RSSI dBm: 0 (tốt) … -120 (rất yếu). None = chưa biết.
    sim_signal_dbm = models.IntegerField(null=True, blank=True)
    # Yêu cầu thiết bị truy vấn & báo lại thông tin SIM ở lần check-in kế tiếp.
    sim_query_requested_at = models.DateTimeField(null=True, blank=True)
    sim_updated_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Lần cuối thông tin SIM được cập nhật.",
    )

    # ── Telemetry nguồn điện ─────────────────────────────────────────
    battery_percent = models.IntegerField(null=True, blank=True)
    battery_voltage = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True,
        help_text="Điện áp tổng của khối pin (V).",
    )
    is_charging = models.BooleanField(default=False)
    # Điện áp từng cell, ví dụ [3.72, 3.70, 3.71]
    cell_voltages = models.JSONField(default=list, blank=True)

    solar_voltage = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True,
        help_text="Điện áp tấm pin mặt trời (V).",
    )
    solar_percent = models.IntegerField(
        null=True, blank=True,
        help_text="Mức thu năng lượng solar ước tính (%).",
    )

    temperature_c = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True
    )
    humidity_percent = models.IntegerField(
        null=True, blank=True,
        help_text="Độ ẩm môi trường (%).",
    )
    firmware_version = models.CharField(max_length=32, blank=True)

    # ── Mốc thời gian ────────────────────────────────────────────────
    last_seen_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Lần cuối thiết bị check-in.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Camera device"
        verbose_name_plural = "Camera devices"

    def __str__(self):
        return f"Device[{self.camera.code}]"

    # ── Suy diễn hiển thị ────────────────────────────────────────────

    @property
    def wake_pending(self):
        """True nếu đang có yêu cầu chụp chưa được thiết bị xử lý."""
        if not self.wake_requested_at:
            return False
        if not self.wake_done_at:
            return True
        return self.wake_requested_at > self.wake_done_at

    @property
    def sim_query_pending(self):
        """True nếu đang chờ thiết bị báo lại thông tin SIM."""
        if not self.sim_query_requested_at:
            return False
        if not self.sim_updated_at:
            return True
        return self.sim_query_requested_at > self.sim_updated_at

    @property
    def signal_bars(self):
        """Quy đổi RSSI dBm → 0..4 vạch sóng."""
        dbm = self.sim_signal_dbm
        if dbm is None:
            return None
        if dbm >= -70:
            return 4
        if dbm >= -85:
            return 3
        if dbm >= -100:
            return 2
        if dbm >= -110:
            return 1
        return 0

    @property
    def signal_label(self):
        bars = self.signal_bars
        return {
            None: "—", 0: "No signal", 1: "Poor",
            2: "Fair", 3: "Good", 4: "Excellent",
        }.get(bars, "—")

    @property
    def is_online(self):
        """Coi là online nếu check-in trong vòng 2× chu kỳ chụp."""
        if not self.last_seen_at:
            return False
        from django.utils import timezone
        window = max(self.capture_interval_sec * 2, 600)
        return (timezone.now() - self.last_seen_at).total_seconds() <= window

    @property
    def cell_count(self):
        return len(self.cell_voltages or [])


class CameraSettings(models.Model):
    """
    Thông số chụp — lưu giá trị native gphoto2 (không dùng alias).

    Tất cả field lưu đúng chuỗi gphoto2 (vd 'NEF+Fine', 'MF (fixed)', 'Daylight').
    applied: snapshot từ camera → so sánh string equality với desired.
    exposure_mode/focus_switch: read-only vật lý, server chỉ đọc.
    Chi tiết: HW/NIKON_D5300_SETTINGS.md
    """

    # field_name → gphoto2 widget name
    FIELD_WIDGET_MAP = {
        "iso":                   "iso",
        "aperture":              "f-number",
        "shutter_speed":         "shutterspeed2",
        "exposure_compensation": "exposurecompensation",
        "white_balance":         "whitebalance",
        "image_format":          "imagequality",
        "image_size":            "imagesize",
        "capture_mode":          "capturemode",
        "capture_target":        "capturetarget",
        "high_iso_nr":           "highisonr",
        "long_exp_nr":           "longexpnr",
        "autofocus":             "autofocus",
        "liveview_af":           "liveviewaffocus",
        "focus_mode":            "focusmode2",
        # Read-only:
        "exposure_mode":         "expprogram",
        "focus_switch":          "focusmode",
    }

    SETTABLE_FIELDS = (
        "iso", "aperture", "shutter_speed", "exposure_compensation",
        "white_balance", "image_format", "image_size",
        "focus_mode", "autofocus", "liveview_af",
        "capture_mode", "capture_target",
        "high_iso_nr", "long_exp_nr",
    )

    camera = models.OneToOneField(
        Camera, on_delete=models.CASCADE, related_name="settings"
    )

    # ── Phơi sáng ────────────────────────────────────────────────────────
    iso = models.CharField(max_length=8, blank=True, default="")
    aperture = models.CharField(max_length=8, blank=True, default="")
    shutter_speed = models.CharField(max_length=16, blank=True, default="")
    exposure_compensation = models.CharField(max_length=8, blank=True, default="")
    # Physical dial (M/P/A/S…) — read-only
    exposure_mode = models.CharField(max_length=16, blank=True, default="")

    # ── Lấy nét ──────────────────────────────────────────────────────────
    autofocus = models.CharField(max_length=4, blank=True, default="")
    focus_mode = models.CharField(max_length=32, blank=True, default="")
    # Physical M/A switch on lens — read-only
    focus_switch = models.CharField(max_length=16, blank=True, default="")

    # ── Ảnh ──────────────────────────────────────────────────────────────
    image_format = models.CharField(max_length=16, blank=True, default="")
    image_size = models.CharField(max_length=16, blank=True, default="")
    white_balance = models.CharField(max_length=16, blank=True, default="")

    # Live View AF (tách biệt với focus_mode khi dùng viewfinder)
    liveview_af = models.CharField(max_length=32, blank=True, default="")

    # ── Chụp ─────────────────────────────────────────────────────────────
    capture_mode = models.CharField(max_length=32, blank=True, default="")
    capture_target = models.CharField(max_length=16, blank=True, default="")

    # ── Khử nhiễu ────────────────────────────────────────────────────────
    long_exp_nr = models.CharField(max_length=4, blank=True, default="")
    high_iso_nr = models.CharField(max_length=8, blank=True, default="")

    # ── Đồng bộ với camera ───────────────────────────────────────────────
    capabilities = models.JSONField(default=dict, blank=True)
    applied = models.JSONField(default=dict, blank=True)
    last_request_id = models.CharField(max_length=64, blank=True)
    last_command_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Camera settings"
        verbose_name_plural = "Camera settings"

    def __str__(self):
        return f"Settings[{self.camera.code}]"

    def to_payload(self, only=None):
        """Dựng payload cho set_settings (bỏ field rỗng)."""
        fields = only if only is not None else self.SETTABLE_FIELDS
        return {
            name: getattr(self, name)
            for name in fields
            if name in self.SETTABLE_FIELDS and getattr(self, name)
        }

    @property
    def in_sync(self):
        """True nếu mọi field settable khớp applied (string equality)."""
        if not self.applied:
            return False
        # focus_switch (physical lens switch) cho biết AF có khả dụng không
        focus_switch_is_af = self.applied.get("focus_switch", "") not in ("", "Manual")
        for name in self.SETTABLE_FIELDS:
            value = getattr(self, name)
            if not value:
                continue
            got = str(self.applied.get(name, ""))
            if got == str(value):
                continue
            # D5300 quirk: trong Live View, focusmode2 luôn trả MF (fixed).
            # Nếu focus_switch đang ở AF và liveview_af đã được set → bỏ qua.
            if name == "focus_mode" and focus_switch_is_af and self.liveview_af:
                continue
            return False
        return True


class AlertSettings(models.Model):
    """Ngưỡng cảnh báo cho từng camera."""

    camera = models.OneToOneField(
        Camera, on_delete=models.CASCADE, related_name="alert_settings"
    )
    enabled = models.BooleanField(default=True, verbose_name="Bật thông báo")

    # Pin
    battery_low_pct = models.PositiveIntegerField(
        default=20, verbose_name="Pin yếu (% cảnh báo)"
    )
    battery_critical_pct = models.PositiveIntegerField(
        default=10, verbose_name="Pin nguy hiểm (%)"
    )

    # Tín hiệu
    signal_weak_dbm = models.IntegerField(
        default=-90, verbose_name="Tín hiệu yếu (dBm)"
    )

    # Offline
    offline_minutes = models.PositiveIntegerField(
        default=30, verbose_name="Cảnh báo offline sau (phút)"
    )

    # Nhiệt độ
    temperature_high_c = models.IntegerField(
        default=50, verbose_name="Nhiệt độ cao (°C)"
    )

    # Ảnh chụp tối thiểu mỗi ngày
    daily_photo_min = models.PositiveIntegerField(
        default=0, verbose_name="Số ảnh tối thiểu/ngày (0=tắt)"
    )

    # Kênh thông báo
    notify_email = models.EmailField(
        blank=True, verbose_name="Email nhận cảnh báo"
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Alert settings"
        verbose_name_plural = "Alert settings"

    def __str__(self):
        return f"Alert: {self.camera.code}"
