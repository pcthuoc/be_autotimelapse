"""Views Settings — ngưỡng cảnh báo và cấu hình hệ thống."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models.camera import AlertSettings, Camera
from core.models.permission import ClientMembership


def _has_perm(user, code):
    if user.is_staff:
        return True
    m = ClientMembership.objects.filter(user=user).first()
    if not m:
        return False
    if code in ('camera.view', 'media.view'):
        return True
    return m.role == 'admin'


@login_required(login_url="/login/")
def alert_settings_list(request):
    """Danh sách cài đặt cảnh báo cho từng camera."""
    if not _has_perm(request.user, "camera.manage"):
        return HttpResponseForbidden()

    if request.user.is_staff:
        cameras = Camera.objects.select_related("site__client").order_by("site__name", "code")
    else:
        m = ClientMembership.objects.filter(user=request.user, role='admin').first()
        cameras = (
            Camera.objects.filter(site__client=m.client)
            .select_related("site__client").order_by("code")
            if m else Camera.objects.none()
        )

    cam_data = []
    for cam in cameras:
        alert, _ = AlertSettings.objects.get_or_create(camera=cam)
        cam_data.append({"camera": cam, "alert": alert})

    return render(request, "home/alert_settings.html", {
        "segment": "settings",
        "cam_data": cam_data,
    })


@login_required(login_url="/login/")
def alert_settings_save(request, camera_pk):
    """Lưu ngưỡng cảnh báo cho 1 camera (POST)."""
    camera = get_object_or_404(Camera, pk=camera_pk)
    if not camera.is_editable_by(request.user) and not _has_perm(request.user, "camera.manage"):
        return HttpResponseForbidden()

    alert, _ = AlertSettings.objects.get_or_create(camera=camera)

    if request.method == "POST":
        alert.enabled             = request.POST.get("enabled") == "1"
        alert.battery_low_pct     = int(request.POST.get("battery_low_pct", 20))
        alert.battery_critical_pct = int(request.POST.get("battery_critical_pct", 10))
        alert.signal_weak_dbm     = int(request.POST.get("signal_weak_dbm", -90))
        alert.offline_minutes     = int(request.POST.get("offline_minutes", 30))
        alert.temperature_high_c  = int(request.POST.get("temperature_high_c", 50))
        alert.daily_photo_min     = int(request.POST.get("daily_photo_min", 0))
        alert.notify_email        = request.POST.get("notify_email", "").strip()
        alert.save()

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": True})
        return redirect("alert_settings_list")

    return render(request, "home/alert_settings_form.html", {
        "segment": "settings",
        "camera": camera,
        "alert": alert,
    })
