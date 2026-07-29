import uuid

from django.conf import settings
from django.db import models


class Role(models.Model):
    """Nhóm quyền theo vai trò (admin, operator, viewer...)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name = "Role"
        verbose_name_plural = "Roles"

    def __str__(self):
        return f"{self.name} ({self.code})"


class Permission(models.Model):
    """Quyền ở mức hành động nhỏ nhất (camera.view, media.delete...)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Permission"
        verbose_name_plural = "Permissions"

    def __str__(self):
        return self.code


class RolePermission(models.Model):
    """Ánh xạ Role ↔ Permission (many-to-many thủ công để dễ audit)."""

    role = models.ForeignKey(
        Role, on_delete=models.CASCADE, related_name="role_permissions"
    )
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="role_permissions"
    )

    class Meta:
        unique_together = ("role", "permission")
        verbose_name = "Role permission"
        verbose_name_plural = "Role permissions"

    def __str__(self):
        return f"{self.role.code} → {self.permission.code}"


class UserRole(models.Model):
    """Ánh xạ User ↔ Role."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_roles",
    )
    role = models.ForeignKey(
        Role, on_delete=models.CASCADE, related_name="user_roles"
    )

    class Meta:
        unique_together = ("user", "role")
        verbose_name = "User role"
        verbose_name_plural = "User roles"

    def __str__(self):
        return f"{self.user} → {self.role.code}"


class ClientMembership(models.Model):
    """
    Phân tầng quyền theo Client (xem PERMISSION_ARCHITECTURE.md).

    - 1 user thuộc tối đa 1 client.
    - role=admin: quản trị viên của client (quản lý site/camera/member).
    - role=member: thành viên thường, xem camera trong client.
    - Superadmin (is_staff) không cần membership — thấy toàn hệ thống.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Client Admin"
        MEMBER = "member", "Client Member"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="client_memberships",
    )
    client = models.ForeignKey(
        "core.Client",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=10, choices=Role.choices, default=Role.MEMBER
    )
    can_download = models.BooleanField(default=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitations_sent",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "client")
        verbose_name = "Client membership"
        verbose_name_plural = "Client memberships"

    def __str__(self):
        return f"{self.user} → {self.client} [{self.role}]"

