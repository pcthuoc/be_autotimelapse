"""Views cho VideoRender — tạo / xem / tải video timelapse."""

import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models.camera import Camera
from core.models.media import VideoRender
from core.models.permission import UserRole
from core.utils import storage


def _has_perm(user, code):
    if user.is_staff:
        return True
    return UserRole.objects.filter(
        user=user, role__role_permissions__permission__code=code
    ).exists()


def _can_view_camera(user, camera):
    if user.is_staff:
        return True
    return camera.is_media_viewable_by(user)


@login_required(login_url="/login/")
@require_POST
def render_create(request, camera_pk):
    """Tạo 1 VideoRender job mới, đẩy vào Celery."""
    camera = get_object_or_404(Camera, pk=camera_pk)
    if not _can_view_camera(request.user, camera):
        return HttpResponseForbidden()

    date_from = request.POST.get("date_from")
    date_to   = request.POST.get("date_to")
    fps       = int(request.POST.get("fps", 24))
    resolution = request.POST.get("resolution", VideoRender.Resolution.R_1080)

    # Tần suất lấy ảnh (giây/ảnh), 0 = lấy tất cả
    try:
        frame_interval = int(request.POST.get("frame_interval", 0))
        if frame_interval < 0:
            frame_interval = 0
    except (ValueError, TypeError):
        frame_interval = 0

    if not date_from or not date_to:
        return JsonResponse({"ok": False, "error": "Thiếu ngày bắt đầu / kết thúc."}, status=400)

    if fps not in (6, 12, 24, 30):
        fps = 24
    if resolution not in dict(VideoRender.Resolution.choices):
        resolution = VideoRender.Resolution.R_1080

    vr = VideoRender.objects.create(
        camera=camera,
        requested_by=request.user,
        date_from=date_from,
        date_to=date_to,
        fps=fps,
        resolution=resolution,
        frame_interval=frame_interval,
    )

    # Dispatch SAU KHI transaction commit — tránh race condition
    from django.db import transaction
    from core.tasks import render_timelapse_video
    transaction.on_commit(lambda: render_timelapse_video.delay(str(vr.id)))

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "render_id": str(vr.id)})
    return redirect("camera_detail", pk=camera_pk)


@login_required(login_url="/login/")
def render_status(request, pk):
    """Trả JSON trạng thái của 1 VideoRender."""
    vr = get_object_or_404(VideoRender, pk=pk)
    if not vr.is_accessible_by(request.user):
        return HttpResponseForbidden()

    data = {
        "id": str(vr.id),
        "status": vr.status,
        "progress": vr.progress,
        "item_count": vr.item_count,
        "size_bytes": vr.size_bytes,
        "error": vr.error,
        "ready_at": vr.ready_at.isoformat() if vr.ready_at else None,
        "download_url": None,
    }
    if vr.status == VideoRender.Status.READY and vr.output_key:
        try:
            data["download_url"] = storage.presigned_get_url(vr.output_key, expire=3600)
        except Exception:
            pass

    return JsonResponse(data)


@login_required(login_url="/login/")
def render_download(request, pk):
    """Redirect tới presigned URL để tải video MP4."""
    vr = get_object_or_404(VideoRender, pk=pk)
    if not vr.is_accessible_by(request.user):
        return HttpResponseForbidden()
    if vr.status != VideoRender.Status.READY or not vr.output_key:
        return JsonResponse({"error": "Video chưa sẵn sàng."}, status=400)

    try:
        url = storage.presigned_get_url(vr.output_key, expire=3600)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    from django.http import HttpResponseRedirect
    return HttpResponseRedirect(url)


@login_required(login_url="/login/")
def render_list(request):
    """Danh sách tất cả VideoRender — hiển thị trên trang /renders/."""
    if request.user.is_staff:
        renders = VideoRender.objects.select_related("camera", "requested_by").order_by("-created_at")
    else:
        renders = VideoRender.objects.filter(
            requested_by=request.user
        ).select_related("camera", "requested_by").order_by("-created_at")

    # Build presigned download URLs cho những render đã READY
    render_rows = []
    for vr in renders:
        download_url = None
        if vr.status == VideoRender.Status.READY and vr.output_key:
            try:
                download_url = storage.presigned_get_url(vr.output_key, expire=3600)
            except Exception:
                pass
        render_rows.append({"vr": vr, "download_url": download_url})

    # Danh sách camera để chọn khi tạo render mới
    cameras = Camera.objects.select_related("site").order_by("name")

    has_active_render = render_rows and any(
        r["vr"].status in (VideoRender.Status.PENDING, VideoRender.Status.PROCESSING)
        for r in render_rows
    )

    return render(request, "home/render_list.html", {
        "segment": "renders",
        "render_rows": render_rows,
        "cameras": cameras,
        "resolution_choices": VideoRender.Resolution.choices,
        "has_active_render": has_active_render,
    })


@login_required(login_url="/login/")
def render_recent_json(request):
    """Trả JSON 3 render gần nhất cho quick-access panel."""
    if request.user.is_staff:
        renders = VideoRender.objects.select_related("camera").order_by("-created_at")[:5]
    else:
        renders = VideoRender.objects.filter(
            requested_by=request.user
        ).select_related("camera").order_by("-created_at")[:5]

    data = []
    for vr in renders:
        data.append({
            "id": str(vr.id),
            "camera": vr.camera.name,
            "date_from": str(vr.date_from),
            "date_to": str(vr.date_to),
            "status": vr.status,
            "fps": vr.fps,
            "resolution": vr.resolution,
        })
    return JsonResponse(data, safe=False)


@login_required(login_url="/login/")
@require_POST
def render_delete(request, pk):
    """Xoá VideoRender — chỉ owner hoặc staff."""
    vr = get_object_or_404(VideoRender, pk=pk)
    if not request.user.is_staff and vr.requested_by_id != request.user.id:
        return JsonResponse({"ok": False, "error": "Không có quyền."}, status=403)
    vr.delete()
    return JsonResponse({"ok": True})
