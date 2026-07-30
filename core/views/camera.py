from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.forms import CameraForm, SiteForm
from core.models.camera import AlertSettings, Camera, CameraCredential, Site
from core.models.media import Media, MediaDayStat, VideoRender
from core.models.permission import UserRole

_PAGE_SIZE = 20


# ── permission helpers ────────────────────────────────────────────────────────

def _has_perm(user, perm_code):
    if user.is_staff:
        return True
    return UserRole.objects.filter(
        user=user,
        role__role_permissions__permission__code=perm_code,
    ).exists()


def _camera_qs(user):
    """Cameras user được phép xem (deny-by-default)."""
    if user.is_staff:
        return Camera.objects.select_related("site", "device").order_by("code")
    if not _has_perm(user, "camera.view"):
        return Camera.objects.none()
    from core.models.permission import ClientMembership
    membership = ClientMembership.objects.filter(user=user).first()
    if not membership:
        return Camera.objects.none()
    return (
        Camera.objects.select_related("site", "device")
        .filter(site__client=membership.client)
        .order_by("code").distinct()
    )


def _build_camera_rows(cameras, user):
    """Trả về list (camera, can_edit, edit_form, thumb_url) cho template.

    Đồng thời gắn ``cam.device_obj`` (CameraDevice | None) để hiện icon trạng thái.
    """
    from core.models.camera import CameraDevice
    from core.models.media import Media
    from core.utils import storage as _storage

    rows = []
    for cam in cameras:
        can_edit = cam.is_editable_by(user)
        edit_form = CameraForm(instance=cam) if can_edit else None
        # Device telemetry (OneToOne, có thể chưa tồn tại)
        try:
            cam.device_obj = cam.device
        except CameraDevice.DoesNotExist:
            cam.device_obj = None
        # Latest thumbnail (presigned URL, None if no photo or storage error)
        thumb_url = None
        try:
            latest = Media.objects.filter(camera=cam).order_by("-taken_at").first()
            if latest:
                thumb_url = _storage.presigned_get_url(latest.effective_thumb_key, expire=3600)
        except Exception:
            pass
        rows.append((cam, can_edit, edit_form, thumb_url))
    return rows


# ── views ─────────────────────────────────────────────────────────────────────

@login_required
def camera_list(request):
    if not _has_perm(request.user, "camera.view"):
        raise Http404()

    from core.models.camera import Client
    from django.utils import timezone as _tz

    search      = request.GET.get("q", "").strip()
    site_filter = request.GET.get("site", "").strip()
    status_filter = request.GET.get("status", "").strip()
    online_filter = request.GET.get("online", "").strip()  # "1" = online only

    cameras = _camera_qs(request.user)

    if search:
        cameras = cameras.filter(
            name__icontains=search
        ) | cameras.filter(code__icontains=search)
        cameras = cameras.distinct()

    if site_filter == "none":
        cameras = cameras.filter(site__isnull=True)
    elif site_filter:
        cameras = cameras.filter(site__id=site_filter)

    if status_filter in ("active", "inactive", "maintenance"):
        cameras = cameras.filter(status=status_filter)

    # online filter: camera có device last_seen_at trong 5 phút
    if online_filter == "1":
        cutoff = _tz.now() - _tz.timedelta(minutes=5)
        cameras = cameras.filter(device__last_seen_at__gte=cutoff)

    paginator = Paginator(cameras, _PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    rows = _build_camera_rows(page_obj, request.user)
    new_cam_token = request.session.pop("_new_cam_token", None)
    new_cam_code = request.session.pop("_new_cam_name", None)
    new_cam_mqtt_pass = request.session.pop("_new_cam_mqtt_pass", None)

    # Clients cho filter dropdown
    all_clients = Client.objects.prefetch_related("projects").order_by("name")
    client_filter = request.GET.get("client", "").strip()
    if client_filter:
        cameras_for_count = cameras  # already filtered above by site if set

    return render(request, "home/cameras.html", {
        "segment": "cameras",
        "rows": rows,
        "page_obj": page_obj,
        "search": search,
        "sites": Site.objects.select_related("client").order_by("client__name", "name"),
        "all_clients": all_clients,
        "add_form": CameraForm(),
        "site_form": SiteForm(),
        "can_add": _has_perm(request.user, "camera.add"),
        "can_delete": _has_perm(request.user, "camera.delete"),
        "can_assign": _has_perm(request.user, "camera.assign"),
        "new_cam_token": new_cam_token,
        "new_cam_code": new_cam_code,
        "new_cam_mqtt_pass": new_cam_mqtt_pass,
        # filter state (để giữ giá trị trên form)
        "f_site": site_filter,
        "f_status": status_filter,
        "f_online": online_filter,
        "f_client": client_filter,
        "total_count": cameras.count(),
    })


@login_required
def camera_add(request):
    if not _has_perm(request.user, "camera.add"):
        raise Http404()
    if request.method == "POST":
        form = CameraForm(request.POST)
        if form.is_valid():
            camera = form.save()
            # Đăng ký client MQTT cho thiết bị trên broker (không chặn nếu lỗi)
            try:
                from mqtt_service import device_manager
                device_manager.register_device(camera)
            except Exception:
                pass
            request.session["_new_cam_token"] = camera.code
            request.session["_new_cam_name"] = camera.name
            request.session["_new_cam_mqtt_pass"] = camera.mqtt_password
            messages.success(request, f"Camera {camera.code} added.")
            return redirect("camera_list")
        cameras = _camera_qs(request.user)
        return render(request, "home/cameras.html", {
            "segment": "cameras",
            "rows": _build_camera_rows(cameras, request.user),
            "sites": Site.objects.order_by("name"),
            "add_form": form,
            "site_form": SiteForm(),
            "can_add": True,
            "can_delete": _has_perm(request.user, "camera.delete"),
            "open_add_modal": True,
        })
    return redirect("camera_list")


@login_required
def camera_edit(request, pk):
    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_editable_by(request.user):
        raise Http404()
    if request.method == "POST":
        form = CameraForm(request.POST, instance=camera)
        if form.is_valid():
            form.save()
            messages.success(request, f"Camera {camera.code} updated.")
            return redirect("camera_list")
        cameras = _camera_qs(request.user)
        # Build rows, overriding the form for this camera
        rows = []
        for cam in cameras:
            can_edit = cam.is_editable_by(request.user)
            if can_edit:
                ef = form if cam.pk == camera.pk else CameraForm(instance=cam)
            else:
                ef = None
            rows.append((cam, can_edit, ef, None))
        return render(request, "home/cameras.html", {
            "segment": "cameras",
            "rows": rows,
            "sites": Site.objects.order_by("name"),
            "add_form": CameraForm(),
            "site_form": SiteForm(),
            "can_add": _has_perm(request.user, "camera.add"),
            "can_delete": _has_perm(request.user, "camera.delete"),
            "open_edit_pk": str(camera.pk),
        })
    return redirect("camera_list")


@login_required
def camera_delete(request, pk):
    if not _has_perm(request.user, "camera.delete"):
        raise Http404()
    camera = get_object_or_404(Camera, pk=pk)
    if request.method == "POST":
        code = camera.code
        camera.delete()
        try:
            from mqtt_service import device_manager
            device_manager.unregister_device(code)
        except Exception:
            pass
        messages.success(request, f"Camera {code} deleted.")
    return redirect("camera_list")


@login_required
def camera_detail(request, pk):
    """Trang chi tiết camera: thống kê, ảnh gần nhất, render video, alert settings."""
    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_accessible_by(request.user):
        raise Http404()

    from core.utils import storage as _storage

    # Device & settings
    device = getattr(camera, "device", None)
    settings_obj = getattr(camera, "settings", None)

    # Alert settings
    alert, _ = AlertSettings.objects.get_or_create(camera=camera)

    # Ảnh gần nhất
    recent_media = Media.objects.filter(camera=camera).order_by("-taken_at")[:12]
    recent_with_thumb = []
    for m in recent_media:
        try:
            thumb = _storage.presigned_get_url(m.effective_thumb_key, expire=3600)
        except Exception:
            thumb = None
        recent_with_thumb.append({"media": m, "thumb_url": thumb})

    # Ảnh mới nhất full-res
    latest = recent_media.first() if recent_media else None
    latest_url = None
    if latest:
        try:
            latest_url = _storage.presigned_get_url(latest.s3_key, expire=3600)
        except Exception:
            pass

    # Sparkline 7 ngày
    from django.db.models import Sum
    today = timezone.now().date()
    days_7 = []
    for i in range(6, -1, -1):
        d = today - timezone.timedelta(days=i)
        cnt = MediaDayStat.objects.filter(camera=camera, day=d).aggregate(s=Sum("count"))["s"] or 0
        days_7.append({"date": d.strftime("%d/%m"), "count": cnt})

    # Video renders
    renders = VideoRender.objects.filter(camera=camera).order_by("-created_at")[:20]

    can_manage = camera.is_editable_by(request.user)

    return render(request, "home/camera_detail.html", {
        "segment": "cameras",
        "camera": camera,
        "device": device,
        "settings_obj": settings_obj,
        "alert": alert,
        "recent_media": recent_with_thumb,
        "latest_url": latest_url,
        "latest_media": latest,
        "days_7": days_7,
        "renders": renders,
        "can_manage": can_manage,
        "resolution_choices": VideoRender.Resolution.choices,
    })


@login_required
def site_add(request):
    if not _has_perm(request.user, "camera.add"):
        raise Http404()
    if request.method == "POST":
        form = SiteForm(request.POST)
        if form.is_valid():
            site = form.save()
            messages.success(request, f"Site \"{site.name}\" added.")
    return redirect("camera_list")


# ── Camera access management ──────────────────────────────────────────────────





# ── Live view ─────────────────────────────────────────────────────────────────

@login_required
def camera_live(request, pk):
    """Trang xem live: ảnh mới nhất + điều chỉnh trạng thái camera."""
    from core.models.media import Media
    from core.utils import storage

    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_accessible_by(request.user):
        raise Http404()

    latest = Media.objects.filter(camera=camera).order_by("-taken_at").first()
    latest_url = None
    if latest:
        latest_url = storage.presigned_get_url(latest.effective_thumb_key, expire=3600)

    today = timezone.now().date()
    today_count = Media.objects.filter(camera=camera, taken_at__date=today).count()
    total_count = Media.objects.filter(camera=camera).count()

    return render(request, "home/camera_live.html", {
        "segment": "cameras",
        "camera": camera,
        "latest": latest,
        "latest_url": latest_url,
        "today_count": today_count,
        "total_count": total_count,
        "can_manage": camera.is_editable_by(request.user),
        "status_choices": Camera.Status.choices,
    })


@login_required
def camera_live_latest(request, pk):
    """JSON API: trả về ảnh mới nhất + stats để frontend polling."""
    from core.models.media import Media
    from core.utils import storage

    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_accessible_by(request.user):
        raise Http404()

    latest = Media.objects.filter(camera=camera).order_by("-taken_at").first()
    if not latest:
        return JsonResponse({"photo": None, "stats": {"today_count": 0}})

    photo_url = storage.presigned_get_url(latest.s3_key, expire=3600)
    thumb_url = storage.presigned_get_url(latest.effective_thumb_key, expire=3600)
    today_count = Media.objects.filter(
        camera=camera, taken_at__date=timezone.now().date()
    ).count()

    return JsonResponse({
        "photo": {
            "id": str(latest.id),
            "url": photo_url,
            "thumb_url": thumb_url,
            "taken_at": latest.taken_at.isoformat(),
            "taken_at_display": latest.taken_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "width": latest.width,
            "height": latest.height,
            "size_bytes": latest.size_bytes,
        },
        "stats": {
            "today_count": today_count,
        },
        "camera": {
            "status": camera.status,
        },
    })


@login_required
@require_POST
def camera_live_settings(request, pk):  # @login_required MUST stay above @require_POST
    """JSON API: cập nhật trạng thái camera từ live view."""
    import json

    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_editable_by(request.user):
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    status = data.get("status")
    valid_statuses = {v for v, _ in Camera.Status.choices}
    if status and status in valid_statuses:
        camera.status = status
        camera.save(update_fields=["status", "updated_at"])

    return JsonResponse({"ok": True, "status": camera.status})


# ── Device management (modal) ─────────────────────────────────────────────────

def _get_or_create_device(camera):
    from core.models.camera import CameraDevice
    device, _ = CameraDevice.objects.get_or_create(camera=camera)
    return device


def _get_or_create_settings(camera):
    from core.models.camera import CameraSettings
    obj, _ = CameraSettings.objects.get_or_create(camera=camera)
    return obj


def _camera_online(camera, device):
    """Trạng thái online 3 lớp: cache MQTT (True/False rõ ràng) → fallback last_seen."""
    from django.core.cache import cache

    cached = cache.get(f"cam:online:{camera.code}")
    if cached is not None:
        return bool(cached)
    return device.is_online


@login_required
def camera_device_modal(request, pk):
    """Trả về HTML modal-content quản lý thiết bị (nạp bằng AJAX)."""
    from core.forms import CameraDeviceSettingsForm, CameraSettingsForm
    from core.models.media import Media
    from core.utils import storage

    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_accessible_by(request.user):
        raise Http404()

    device = _get_or_create_device(camera)
    cam_settings = _get_or_create_settings(camera)
    can_manage = camera.is_editable_by(request.user)

    latest = Media.objects.filter(camera=camera).order_by("-taken_at").first()
    latest_url = None
    if latest:
        try:
            latest_url = storage.presigned_get_url(latest.effective_thumb_key, expire=3600)
        except Exception:
            latest_url = None

    today = timezone.now().date()
    today_count = Media.objects.filter(camera=camera, taken_at__date=today).count()
    total_count = Media.objects.filter(camera=camera).count()

    return render(request, "home/_camera_device_modal.html", {
        "camera": camera,
        "device": device,
        "device_online": _camera_online(camera, device),
        "cam_settings": cam_settings,
        "can_manage": can_manage,
        "latest": latest,
        "latest_url": latest_url,
        "today_count": today_count,
        "total_count": total_count,
        "settings_form": CameraDeviceSettingsForm(instance=device),
        "camera_settings_form": CameraSettingsForm(
            instance=cam_settings,
            camera_model=camera.camera_model,
            capabilities=cam_settings.capabilities or {},
        ),
        "status_choices": Camera.Status.choices,
    })


@login_required
@require_POST
def camera_device_settings(request, pk):
    """POST JSON: cập nhật chu kỳ chụp + trạng thái camera."""
    import json

    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_editable_by(request.user):
        return JsonResponse({"error": "Permission denied"}, status=403)

    device = _get_or_create_device(camera)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    # Chu kỳ chụp
    interval = data.get("capture_interval_sec")
    if interval is not None:
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid interval"}, status=400)
        if not (30 <= interval <= 86400):
            return JsonResponse({"error": "Interval out of range (30..86400)"}, status=400)
        device.capture_interval_sec = interval
        device.save(update_fields=["capture_interval_sec", "updated_at"])
        from mqtt_service import config_publisher
        config_publisher.push_interval(camera.code, interval)

    # Trạng thái camera
    status = data.get("status")
    if status:
        valid = {v for v, _ in Camera.Status.choices}
        if status not in valid:
            return JsonResponse({"error": "Invalid status"}, status=400)
        camera.status = status
        camera.save(update_fields=["status", "updated_at"])

    return JsonResponse({
        "ok": True,
        "status": camera.status,
        "capture_interval_sec": device.capture_interval_sec,
    })


@login_required
@require_POST
def camera_settings_save(request, pk):
    """POST JSON: lưu thông số CHỤP của máy ảnh vào CSDL.

    Tạm thời chỉ lưu giá trị mong muốn — CHƯA gửi xuống máy ảnh (MQTT làm sau).
    """
    import json

    from core.forms import CameraSettingsForm

    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_editable_by(request.user):
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    cam_settings = _get_or_create_settings(camera)
    form = CameraSettingsForm(
        data,
        instance=cam_settings,
        camera_model=camera.camera_model,
        capabilities=cam_settings.capabilities or {},
    )
    if not form.is_valid():
        return JsonResponse(
            {"error": "Invalid data", "fields": form.errors}, status=400
        )

    obj = form.save_to(cam_settings)
    obj.last_command_at = timezone.now()
    obj.save()  # lưu TRƯỚC khi publish — tránh đè `applied` mà listener vừa ghi

    from core.models.camera import CameraSettings
    from mqtt_service import config_publisher
    request_id = config_publisher.push_settings(obj)
    if request_id:
        # update() trực tiếp để không ghi đè các field khác (race với listener)
        CameraSettings.objects.filter(pk=obj.pk).update(last_request_id=request_id)
        obj.last_request_id = request_id

    return JsonResponse({
        "ok": True,
        "settings": obj.to_payload(),
        "in_sync": obj.in_sync,
        "request_id": request_id,
        "sent": bool(request_id),
    })


@login_required
@require_POST
def camera_settings_pull(request, pk):
    """POST: yêu cầu máy ảnh báo lại thông số hiện tại (get_settings qua MQTT).

    Phản hồi của máy ảnh là async → listener cập nhật ``applied``; FE poll
    ``camera_device_state`` để lấy kết quả.
    """
    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_editable_by(request.user):
        return JsonResponse({"error": "Permission denied"}, status=403)

    device = _get_or_create_device(camera)
    cam_settings = _get_or_create_settings(camera)

    from mqtt_service import config_publisher
    request_id = config_publisher.request_settings(camera.code)

    settings_data = cam_settings.applied or cam_settings.to_payload()
    return JsonResponse({
        "ok": True,
        "connected": _camera_online(camera, device),
        "settings": settings_data,
        "capabilities": cam_settings.capabilities or {},
        "in_sync": cam_settings.in_sync,
        "last_synced_at": cam_settings.last_synced_at.isoformat() if cam_settings.last_synced_at else None,
        "request_id": request_id,
        "sent": bool(request_id),
        "pending": bool(request_id),
    })


@login_required
@require_POST
def camera_device_wake(request, pk):
    """POST: đánh thức thiết bị — yêu cầu chụp ảnh ngay ở lần check-in kế tiếp."""
    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_editable_by(request.user):
        return JsonResponse({"error": "Permission denied"}, status=403)

    device = _get_or_create_device(camera)
    device.wake_requested_at = timezone.now()
    device.save(update_fields=["wake_requested_at", "updated_at"])

    from mqtt_service import config_publisher
    config_publisher.request_capture(camera.code)

    return JsonResponse({
        "ok": True,
        "wake_requested_at": device.wake_requested_at.isoformat(),
        "wake_pending": device.wake_pending,
    })


@login_required
@require_POST
def camera_device_sim(request, pk):
    """POST: yêu cầu thiết bị truy vấn & báo lại thông tin SIM.

    Trả về thông tin SIM đang lưu (số, nhà mạng, ICCID, sóng) để hiển thị ngay.
    """
    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_accessible_by(request.user):
        raise Http404()

    device = _get_or_create_device(camera)

    # Chỉ người có quyền quản lý mới được đẩy lệnh truy vấn xuống thiết bị.
    sim_request_id = None
    if camera.is_editable_by(request.user):
        device.sim_query_requested_at = timezone.now()
        device.save(update_fields=["sim_query_requested_at", "updated_at"])
        from mqtt_service import config_publisher
        sim_request_id = config_publisher.request_sim_info(camera.code)

    return JsonResponse({
        "ok": True,
        "sim": {
            "operator": device.sim_operator or None,
            "number": device.sim_number or None,
            "iccid": device.sim_iccid or None,
            "signal_dbm": device.sim_signal_dbm,
            "signal_label": device.signal_label,
            "signal_bars": device.signal_bars,
            "updated_at": device.sim_updated_at.isoformat() if device.sim_updated_at else None,
        },
        "sim_query_pending": device.sim_query_pending,
        "request_id": sim_request_id,
        "sent": bool(sim_request_id),
    })


@login_required
def camera_device_state(request, pk):
    """GET JSON: trạng thái tổng hợp cho FE polling sau khi gửi lệnh MQTT.

    Gồm: settings mong muốn + applied + in_sync, SIM info, telemetry, online.
    Listener MQTT cập nhật DB/Redis; view chỉ đọc.
    """
    from django.core.cache import cache

    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_accessible_by(request.user):
        raise Http404()

    device = _get_or_create_device(camera)
    cam_settings = _get_or_create_settings(camera)

    online = _camera_online(camera, device)

    return JsonResponse({
        "ok": True,
        "online": online,
        "settings": {
            "requested": cam_settings.to_payload(),
            "applied": cam_settings.applied or {},
            "capabilities": cam_settings.capabilities or {},
            "in_sync": cam_settings.in_sync,
            "last_synced_at": cam_settings.last_synced_at.isoformat() if cam_settings.last_synced_at else None,
        },
        "sim": {
            "operator": device.sim_operator or None,
            "number": device.sim_number or None,
            "iccid": device.sim_iccid or None,
            "signal_dbm": device.sim_signal_dbm,
            "signal_label": device.signal_label,
            "signal_bars": device.signal_bars,
            "updated_at": device.sim_updated_at.isoformat() if device.sim_updated_at else None,
            "pending": device.sim_query_pending,
        },
        "telemetry": {
            "battery_percent": device.battery_percent,
            "battery_voltage": float(device.battery_voltage) if device.battery_voltage is not None else None,
            "is_charging": device.is_charging,
            "cell_voltages": device.cell_voltages or [],
            "solar_voltage": float(device.solar_voltage) if device.solar_voltage is not None else None,
            "solar_percent": device.solar_percent,
            "temperature_c": float(device.temperature_c) if device.temperature_c is not None else None,
            "humidity_percent": device.humidity_percent,
            "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        },
        "wake_pending": device.wake_pending,
    })


# ── Live View realtime (frame từ thiết bị qua Redis) ─────────────────────────

LIVE_SESSION_TTL = 60


@login_required
@require_POST
def live_view_start(request, pk):
    """Bật phiên Live View: đặt session vào Redis + gửi lệnh MQTT xuống trạm."""
    import secrets as _s

    from django.core.cache import cache

    from mqtt_service import config_publisher

    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_accessible_by(request.user):
        raise Http404()

    cam_id = str(camera.id)
    session_id = cache.get(f"live:{cam_id}:session")
    if not session_id:
        session_id = f"lv-{_s.token_hex(6)}"
        cache.set(f"live:{cam_id}:session", session_id, LIVE_SESSION_TTL)
        config_publisher.start_live_view(camera.code, session_id)
    else:
        # Đã có phiên (người khác đang xem) → dùng chung, chỉ gia hạn
        cache.set(f"live:{cam_id}:session", session_id, LIVE_SESSION_TTL)
    return JsonResponse({"ok": True, "session_id": session_id})


@login_required
@require_POST
def live_view_stop(request, pk):
    from django.core.cache import cache

    from mqtt_service import config_publisher

    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_accessible_by(request.user):
        raise Http404()

    cam_id = str(camera.id)
    session_id = cache.get(f"live:{cam_id}:session")
    if session_id:
        cache.delete(f"live:{cam_id}:session")
        cache.delete(f"live:{cam_id}:frame")
        cache.delete(f"live:{cam_id}:meta")
        config_publisher.stop_live_view(camera.code, session_id)
    return JsonResponse({"ok": True})


@login_required
def live_view_frame(request, pk):
    """Trả frame JPEG mới nhất. 204 nếu chưa có frame.

    FE polling kèm ?seq=N — chỉ nhận frame mới hơn (304 nếu chưa đổi).
    Đồng thời gia hạn session để camera tiếp tục gửi khi còn người xem.
    """
    from django.core.cache import cache
    from django.http import HttpResponse

    camera = get_object_or_404(Camera, pk=pk)
    if not camera.is_accessible_by(request.user):
        raise Http404()

    cam_id = str(camera.id)
    session_id = cache.get(f"live:{cam_id}:session")
    if session_id:
        cache.set(f"live:{cam_id}:session", session_id, LIVE_SESSION_TTL)

    meta = cache.get(f"live:{cam_id}:meta")
    frame = cache.get(f"live:{cam_id}:frame")
    if not frame or not meta:
        return HttpResponse(status=204)

    try:
        known_seq = int(request.GET.get("seq", "0"))
    except ValueError:
        known_seq = 0
    if known_seq and meta.get("seq") and meta["seq"] <= known_seq:
        return HttpResponse(status=304)

    resp = HttpResponse(frame, content_type="image/jpeg")
    resp["X-Frame-Seq"] = str(meta.get("seq", 0))
    resp["X-Frame-At"] = meta.get("at", "")
    resp["Cache-Control"] = "no-store"
    return resp
