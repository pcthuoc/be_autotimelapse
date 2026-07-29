from django.contrib import admin
from django.utils.html import format_html

from core.models import (
    Camera,
    CameraCredential,
    CameraDevice,
    CameraSettings,
    Site,
    UserCameraAccess,
)


class CameraCredentialInline(admin.TabularInline):
    model = CameraCredential
    extra = 0
    readonly_fields = ("key_id", "status", "last_rotated_at", "created_at")
    fields = ("key_id", "status", "last_rotated_at", "created_at")
    can_delete = False
    show_change_link = True


class CameraDeviceInline(admin.StackedInline):
    model = CameraDevice
    extra = 0
    can_delete = True
    fieldsets = (
        ("Config", {"fields": ("capture_interval_sec", "wake_requested_at", "wake_done_at")}),
        ("SIM / Network", {"fields": (
            "sim_operator", "sim_number", "sim_iccid", "sim_signal_dbm",
            "sim_query_requested_at", "sim_updated_at",
        )}),
        ("Power", {"fields": (
            "battery_percent", "battery_voltage", "is_charging",
            "cell_voltages", "solar_voltage", "solar_percent",
        )}),
        ("Environment", {"fields": ("temperature_c", "humidity_percent")}),
        ("Other", {"fields": ("firmware_version", "last_seen_at")}),
    )
    readonly_fields = ("wake_done_at",)


class CameraSettingsInline(admin.StackedInline):
    model = CameraSettings
    extra = 0
    can_delete = True
    fieldsets = (
        ("Exposure", {"fields": (
            "iso", "aperture", "shutter_speed",
            "exposure_compensation", "exposure_mode",
        )}),
        ("Focus", {"fields": ("autofocus", "focus_mode")}),
        ("Image", {"fields": ("image_format", "image_size", "white_balance")}),
        ("Capture", {"fields": ("capture_mode", "capture_target")}),
        ("Noise reduction", {"fields": ("long_exposure_nr", "high_iso_nr")}),
        ("Sync", {"fields": (
            "capabilities", "applied", "last_request_id",
            "last_command_at", "last_synced_at",
        )}),
    )
    readonly_fields = ("exposure_mode", "last_synced_at")


class UserCameraAccessInline(admin.TabularInline):
    model = UserCameraAccess
    extra = 0
    readonly_fields = ("granted_by", "granted_at")
    fields = (
        "user", "can_view", "can_manage",
        "can_download", "can_delete_media",
        "granted_by", "granted_at",
    )


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "camera_count", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Cameras")
    def camera_count(self, obj):
        return obj.cameras.count()


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "site", "camera_model", "status_badge", "timezone", "updated_at")
    list_filter = ("status", "camera_model", "site")
    search_fields = ("code", "name")
    readonly_fields = ("created_at", "updated_at")
    inlines = (CameraDeviceInline, CameraSettingsInline, CameraCredentialInline, UserCameraAccessInline)

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "active": "green",
            "inactive": "gray",
            "maintenance": "orange",
        }
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>',
            colors.get(obj.status, "black"),
            obj.get_status_display(),
        )


@admin.register(CameraCredential)
class CameraCredentialAdmin(admin.ModelAdmin):
    list_display = ("camera", "key_id_short", "status", "last_rotated_at", "created_at")
    list_filter = ("status", "camera__site")
    search_fields = ("camera__code", "key_id")
    readonly_fields = ("key_id", "secret_hash", "created_at")

    @admin.display(description="Key ID")
    def key_id_short(self, obj):
        return f"{obj.key_id[:12]}…"


@admin.register(UserCameraAccess)
class UserCameraAccessAdmin(admin.ModelAdmin):
    list_display = (
        "user", "camera", "can_view", "can_manage",
        "can_download", "can_delete_media", "granted_by", "granted_at",
    )
    list_filter = ("can_view", "can_manage", "can_download", "camera__site")
    search_fields = ("user__username", "camera__code")
    readonly_fields = ("granted_at",)
    autocomplete_fields = ("user", "camera", "granted_by")
