"""Dashboard tổng quan — thống kê theo công trường (Site) và camera."""

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import render
from django.utils import timezone

from core.models.camera import Camera, Site
from core.models.media import Media, MediaDayStat
from core.models.permission import UserRole
from core.utils import storage as _storage


def _has_perm(user, code):
    if user.is_staff:
        return True
    return UserRole.objects.filter(
        user=user, role__role_permissions__permission__code=code
    ).exists()


def _camera_qs(user):
    if user.is_staff:
        return Camera.objects.select_related("site").order_by("site__name", "code")
    return (
        Camera.objects.select_related("site")
        .filter(user_accesses__user=user, user_accesses__can_view=True)
        .order_by("site__name", "code")
    )


@login_required(login_url="/login/")
def dashboard(request):
    if not _has_perm(request.user, "camera.view"):
        from django.http import Http404
        raise Http404()

    now = timezone.now()
    today = now.date()
    cameras_qs = _camera_qs(request.user)
    camera_ids = list(cameras_qs.values_list("id", flat=True))

    # ── Số liệu tổng ──────────────────────────────────────────────────────────
    total_cameras = cameras_qs.count()
    total_sites = cameras_qs.values("site_id").exclude(site_id=None).distinct().count()

    # Camera online (có telemetry trong 5 phút gần nhất qua CameraDevice)
    try:
        from core.models.camera import CameraDevice
        online_ids = set(
            CameraDevice.objects.filter(
                camera_id__in=camera_ids,
                last_seen__gte=now - timezone.timedelta(minutes=5),
            ).values_list("camera_id", flat=True)
        )
    except Exception:
        online_ids = set()

    online_count = len(online_ids)

    # Ảnh hôm nay
    today_photos = Media.objects.filter(camera_id__in=camera_ids, taken_at__date=today).count()

    # Tổng ảnh
    total_photos = Media.objects.filter(camera_id__in=camera_ids).count()

    # Dung lượng tổng (bytes)
    total_bytes = Media.objects.filter(camera_id__in=camera_ids).aggregate(
        s=Sum("size_bytes")
    )["s"] or 0

    # ── Ảnh 7 ngày gần đây (cho sparkline) ───────────────────────────────────
    days_7 = []
    for i in range(6, -1, -1):
        d = today - timezone.timedelta(days=i)
        cnt = MediaDayStat.objects.filter(camera_id__in=camera_ids, day=d).aggregate(
            s=Sum("count")
        )["s"] or 0
        days_7.append({"date": d.strftime("%d/%m"), "count": cnt})

    # ── Dữ liệu theo Site ─────────────────────────────────────────────────────
    sites_data = []
    sites_with_cam = Site.objects.filter(cameras__id__in=camera_ids).distinct().order_by("name")

    for site in sites_with_cam:
        site_cams = cameras_qs.filter(site=site)
        site_cam_ids = [c.id for c in site_cams]
        site_online = len([i for i in site_cam_ids if i in online_ids])

        # Ảnh hôm nay cho site
        site_today = Media.objects.filter(
            camera_id__in=site_cam_ids, taken_at__date=today
        ).count()

        # Ảnh mới nhất của site
        latest_media = (
            Media.objects.filter(camera_id__in=site_cam_ids)
            .select_related("camera")
            .order_by("-taken_at")
            .first()
        )
        latest_thumb_url = None
        if latest_media:
            try:
                latest_thumb_url = _storage.presigned_get_url(
                    latest_media.effective_thumb_key, expire=3600
                )
            except Exception:
                pass

        # Danh sách camera trong site với ảnh mới nhất
        cam_list = []
        for cam in site_cams:
            last_m = (
                Media.objects.filter(camera=cam).order_by("-taken_at").first()
            )
            thumb_url = None
            if last_m:
                try:
                    thumb_url = _storage.presigned_get_url(last_m.effective_thumb_key, expire=3600)
                except Exception:
                    pass
            # Lấy device info
            device = getattr(cam, "device", None)
            battery = getattr(device, "battery_percent", None) if device else None
            signal = getattr(device, "sim_signal_dbm", None) if device else None

            cam_list.append({
                "cam": cam,
                "online": cam.id in online_ids,
                "thumb_url": thumb_url,
                "last_taken_at": last_m.taken_at if last_m else None,
                "battery": battery,
                "signal": signal,
            })

        sites_data.append({
            "site": site,
            "cam_count": len(site_cam_ids),
            "online_count": site_online,
            "today_count": site_today,
            "latest_thumb_url": latest_thumb_url,
            "latest_media": latest_media,
            "cameras": cam_list,
        })

    # Camera không có site
    no_site_cams = cameras_qs.filter(site__isnull=True)
    no_site_list = []
    for cam in no_site_cams:
        last_m = Media.objects.filter(camera=cam).order_by("-taken_at").first()
        thumb_url = None
        if last_m:
            try:
                thumb_url = _storage.presigned_get_url(last_m.effective_thumb_key, expire=3600)
            except Exception:
                pass
        device = getattr(cam, "device", None)
        no_site_list.append({
            "cam": cam,
            "online": cam.id in online_ids,
            "thumb_url": thumb_url,
            "last_taken_at": last_m.taken_at if last_m else None,
            "battery": getattr(device, "battery_percent", None) if device else None,
            "signal": getattr(device, "sim_signal_dbm", None) if device else None,
        })

    return render(request, "home/dashboard_main.html", {
        "segment": "dashboard",
        "total_cameras": total_cameras,
        "total_sites": total_sites,
        "online_count": online_count,
        "today_photos": today_photos,
        "total_photos": total_photos,
        "total_bytes": total_bytes,
        "days_7": days_7,
        "sites_data": sites_data,
        "no_site_list": no_site_list,
        "now": now,
    })
