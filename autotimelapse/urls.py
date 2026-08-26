"""URL routes for the React SPA backend.

The legacy Django-template UI was removed from routing. Nginx serves the SPA;
Django only exposes admin, REST APIs, device upload APIs and health checks.
"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from core.views import device_api


def health(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("api/v1/", include("core.api_urls")),
    path("", include("core.api_urls")),
    # Stable hardware API: Camera/CM4 currently calls these exact URLs.
    path(
        "api/device/upload/presign/",
        device_api.upload_presign,
        name="device_upload_presign",
    ),
    path(
        "api/device/upload/complete/",
        device_api.upload_complete,
        name="device_upload_complete",
    ),
    path(
        "api/device/live/frame/",
        device_api.live_frame,
        name="device_live_frame",
    ),
    path(
        "api/device/config/",
        device_api.device_config,
        name="device_config",
    ),
]
