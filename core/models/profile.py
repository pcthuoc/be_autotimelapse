from django.conf import settings
from django.db import models


class Profile(models.Model):
    """
    Mở rộng User mặc định của Django.
    Mỗi User có đúng 1 Profile (tạo tự động qua signal).
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    full_name = models.CharField(max_length=150, blank=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Profile"
        verbose_name_plural = "Profiles"

    def __str__(self):
        return f"{self.user.username} – {self.full_name or '(no name)'}"

    # ------------------------------------------------------------------ #
    # Object-level permission helpers (CODING_RULES §2, deny-by-default)
    # ------------------------------------------------------------------ #

    def is_accessible_by(self, actor):
        """Xem profile: bản thân hoặc staff."""
        if not actor or not actor.is_authenticated:
            return False
        if actor.is_staff:
            return True
        return actor.pk == self.user_id

    def is_editable_by(self, actor):
        """Sửa profile: bản thân hoặc staff."""
        if not actor or not actor.is_authenticated:
            return False
        if actor.is_staff:
            return True
        return actor.pk == self.user_id
