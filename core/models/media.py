import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Media(models.Model):
    """
    Metadata của 1 ảnh timelapse. Original ưu tiên nằm trên Cloudflare R2,
    thumbnail nhẹ nằm trên SeaweedFS,
    Django chỉ giữ metadata + key để tạo presigned URL.

    Quy ước key thống nhất giữa hai backend:
        <camera_id>/YYYY/MM/DD/<uuid>.jpg          → ảnh gốc
        <camera_id>/YYYY/MM/DD/<uuid>_thumb.jpg    → thumbnail
    """

    class Storage(models.TextChoices):
        SEAWEED = "seaweed", "SeaweedFS (local VPS)"
        R2 = "r2", "Cloudflare R2"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    camera = models.ForeignKey(
        "core.Camera",
        on_delete=models.CASCADE,
        related_name="media",
        db_index=True,
    )

    # Key trên object storage
    s3_key = models.CharField(max_length=512)
    thumb_key = models.CharField(max_length=512, blank=True)

    # Thông tin ảnh
    taken_at = models.DateTimeField(db_index=True)
    content_type = models.CharField(max_length=64, default="image/jpeg")
    size_bytes = models.BigIntegerField(default=0)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)

    storage = models.CharField(
        max_length=16,
        choices=Storage.choices,
        default=Storage.SEAWEED,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Media"
        verbose_name_plural = "Media"
        ordering = ("-taken_at",)
        indexes = [
            models.Index(fields=["camera", "-taken_at"]),
        ]

    def __str__(self):
        return f"{self.camera.code} @ {self.taken_at:%Y-%m-%d %H:%M:%S}"

    # ------------------------------------------------------------------ #
    # Object-level permission (CODING_RULES §2, deny-by-default).
    # Media kế thừa scope của camera chứa nó.
    # ------------------------------------------------------------------ #

    def is_accessible_by(self, user):
        """Ai xem được ảnh này = ai có quyền xem media của camera."""
        return self.camera.is_media_viewable_by(user)

    def is_downloadable_by(self, user):
        return self.camera.is_media_downloadable_by(user)

    def is_deletable_by(self, user):
        return self.camera.is_media_deletable_by(user)

    # ------------------------------------------------------------------ #

    @property
    def effective_thumb_key(self):
        """Key dùng cho thumbnail: có thumb thì dùng, không thì fallback ảnh gốc."""
        return self.thumb_key or self.s3_key

    @property
    def effective_thumb_storage(self):
        """Thumbnail thật nằm SeaweedFS; fallback ảnh gốc giữ đúng backend của ảnh."""
        return self.Storage.SEAWEED if self.thumb_key else self.storage


class MediaDayStat(models.Model):
    """
    Bảng rollup theo NGÀY cho mỗi camera. Cho phép:
      - Đếm tổng ảnh/camera (SUM) mà KHÔNG quét bảng media (dùng cho phân trang).
      - Hiện lịch/điều hướng theo ngày kèm số lượng, ảnh bìa.
    Cập nhật qua signal khi Media thêm/xoá; rebuild bằng command khi cần.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    camera = models.ForeignKey(
        "core.Camera", on_delete=models.CASCADE, related_name="day_stats"
    )
    day = models.DateField()
    count = models.PositiveIntegerField(default=0)
    cover = models.ForeignKey(
        "core.Media", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Daily media stats"
        verbose_name_plural = "Daily media stats"
        ordering = ("-day",)
        constraints = [
            models.UniqueConstraint(
                fields=["camera", "day"], name="uniq_media_daystat_camera_day"
            )
        ]
        indexes = [models.Index(fields=["camera", "-day"])]

    def __str__(self):
        return f"{self.camera.code} {self.day}: {self.count}"


class MediaArchive(models.Model):
    """
    Job "gom tải" (nén nhiều ảnh thành 1 ZIP) chạy nền qua Celery.

    Vòng đời: pending → processing → ready | failed. File ZIP có expires_at,
    hết hạn thì cleanup xoá khỏi SeaweedFS và chuyển sang expired.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="media_archives",
    )
    camera = models.ForeignKey(
        "core.Camera", on_delete=models.CASCADE, related_name="archives"
    )

    status = models.CharField(
        max_length=16, choices=Status.choices,
        default=Status.PENDING, db_index=True,
    )

    # Điều kiện chọn ảnh: theo khoảng thời gian và/hoặc danh sách id cụ thể.
    date_from = models.DateTimeField(null=True, blank=True)
    date_to = models.DateTimeField(null=True, blank=True)
    media_ids = models.JSONField(default=list, blank=True)

    item_count = models.PositiveIntegerField(default=0)
    zip_key = models.CharField(max_length=512, blank=True)
    output_storage = models.CharField(
        max_length=16, choices=Media.Storage.choices, blank=True, default=""
    )
    output_bucket = models.CharField(
        max_length=16,
        choices=(("media", "Media bucket"), ("output", "Output bucket")),
        blank=True,
        default="",
    )
    size_bytes = models.BigIntegerField(default=0)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Media archive"
        verbose_name_plural = "Media archives"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["requested_by", "-created_at"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self):
        return f"Archive {self.id} [{self.status}] {self.item_count} photos"

    # -- quyền: chỉ người yêu cầu (hoặc staff) thấy/tải gói của mình -------- #
    def is_accessible_by(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        return self.requested_by_id == user.id

    @property
    def is_expired(self):
        if self.status == self.Status.EXPIRED:
            return True
        return bool(self.expires_at and timezone.now() >= self.expires_at)

    @property
    def effective_output_storage(self):
        if self.output_storage:
            return self.output_storage
        from core.utils.storage import _r2_enabled
        return Media.Storage.R2 if _r2_enabled() else Media.Storage.SEAWEED

    @property
    def uses_r2_output_bucket(self):
        # Archive R2 đã luôn được ghi vào output bucket trước migration này.
        return self.effective_output_storage == Media.Storage.R2 and self.output_bucket != "media"


class VideoRender(models.Model):
    """
    Job render video timelapse từ ảnh của 1 camera trong khoảng thời gian.
    Chạy nền qua Celery. File video mới lưu ở R2 output bucket khi R2 bật,
    nếu không thì fallback về SeaweedFS.

    Vòng đời: pending → processing → ready | failed
    """

    class Status(models.TextChoices):
        PENDING    = "pending",    "Pending"
        PROCESSING = "processing", "Processing"
        READY      = "ready",      "Ready"
        FAILED     = "failed",     "Failed"
        EXPIRED    = "expired",    "Expired"

    class Resolution(models.TextChoices):
        R_4K   = "3840x2160", "4K (3840×2160)"
        R_1080 = "1920x1080", "Full HD (1920×1080)"
        R_720  = "1280x720",  "HD (1280×720)"
        R_480  = "854x480",   "SD (854×480)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    camera = models.ForeignKey(
        "core.Camera", on_delete=models.CASCADE, related_name="video_renders"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="video_renders",
    )

    status = models.CharField(
        max_length=16, choices=Status.choices,
        default=Status.PENDING, db_index=True,
    )

    # Khoảng thời gian lấy ảnh
    date_from = models.DateField()
    date_to   = models.DateField()

    # Tham số render
    fps        = models.PositiveIntegerField(default=24, verbose_name="FPS")
    resolution = models.CharField(
        max_length=16, choices=Resolution.choices,
        default=Resolution.R_1080, verbose_name="Độ phân giải"
    )

    # Tần suất lấy ảnh: lấy 1 ảnh mỗi N giây (0 = lấy tất cả)
    frame_interval = models.PositiveIntegerField(
        default=0,
        verbose_name="Tần suất (giây)",
        help_text="Lấy 1 ảnh mỗi N giây. 0 = lấy tất cả ảnh."
    )

    # Kết quả
    item_count  = models.PositiveIntegerField(default=0)
    output_key  = models.CharField(max_length=512, blank=True)
    # Để trống cho render legacy: code sẽ giữ cách lookup cũ (R2 media bucket
    # nếu R2 đang bật, ngược lại SeaweedFS). Render mới luôn ghi rõ hai field.
    output_storage = models.CharField(
        max_length=16, choices=Media.Storage.choices, blank=True, default=""
    )
    output_bucket = models.CharField(
        max_length=16,
        choices=(("media", "Media bucket"), ("output", "Output bucket")),
        blank=True,
        default="",
    )
    size_bytes  = models.BigIntegerField(default=0)
    error       = models.TextField(blank=True)
    progress    = models.PositiveIntegerField(default=0)  # 0-100

    created_at = models.DateTimeField(auto_now_add=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    ready_at   = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Video render"
        verbose_name_plural = "Video renders"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["camera", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Render {self.camera.code} {self.date_from}→{self.date_to} [{self.status}]"

    def is_accessible_by(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        return self.requested_by_id == user.id or self.camera.is_media_viewable_by(user)

    @property
    def is_expired(self):
        if self.status == self.Status.EXPIRED:
            return True
        return bool(self.expires_at and timezone.now() >= self.expires_at)

    @property
    def effective_output_storage(self):
        """Storage của output; giữ tương thích các render tạo trước migration."""
        if self.output_storage:
            return self.output_storage
        from core.utils.storage import _r2_enabled
        return Media.Storage.R2 if _r2_enabled() else Media.Storage.SEAWEED

    @property
    def uses_r2_output_bucket(self):
        return self.output_storage == Media.Storage.R2 and self.output_bucket == "output"
