from django.contrib import admin
from django.utils.html import format_html

from core.models import (
    Camera,
    CameraDevice,
    CameraSettings,
    Site,
)


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
    inlines = (CameraDeviceInline, CameraSettingsInline)

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
