"""
View xem & tải ảnh media theo camera — thiết kế cho khối lượng RẤT LỚN.

Kiến trúc:
  - Django KHÔNG serve/nén file trong request. Thumbnail presign hàng loạt →
    <img> tải thẳng SeaweedFS. Ảnh gốc mở qua modal (302 presigned).
  - Phân trang đánh số 1..N, tổng số lấy từ rollup MediaDayStat (không COUNT
    toàn bảng). Điều hướng theo ngày (index range-scan).
  - Gom tải nhiều ảnh = job nền Celery nén ZIP; client poll trạng thái, khi
    "nén xong" mới hiện link tải (URL có vòng đời/hết hạn).

Quyền (CODING_RULES §5, deny-by-default): không quyền → Http404.
"""

import math
from datetime import datetime, time

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models.camera import Camera
from core.models.media import Media, MediaArchive, MediaDayStat
from core.tasks import build_media_archive
from core.utils import storage

_PAGE_SIZE = 60
_THUMB_TTL = 3600  # presigned thumbnail sống 1h


# ── helpers ───────────────────────────────────────────────────────────────────

def _viewable_cameras(user):
    if user.is_staff:
        return Camera.objects.select_related("site").order_by("code")
    return (
        Camera.objects.select_related("site")
        .filter(user_accesses__user=user, user_accesses__can_view=True)
        .order_by("code")
        .distinct()
    )


def _presign_thumbs(items):
    return [
        {
            "obj": m,
            "thumb_url": storage.presigned_get_url(
                m.effective_thumb_key, expire=_THUMB_TTL
            ),
        }
        for m in items
    ]


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_dt(value):
    """Nhận 'YYYY-MM-DDTHH:MM' (datetime-local) hoặc ISO."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(value, fmt)
            return timezone.make_aware(naive, timezone.get_current_timezone())
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _page_range(page, num_pages, width=2):
    lo = max(1, page - width)
    hi = min(num_pages, page + width)
    return list(range(lo, hi + 1))


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


# ── danh sách camera (có search) ──────────────────────────────────────────────

@login_required
def media_home(request):
    """Media đã được tích hợp thẳng vào trang Cameras.

    Giữ URL ``/media/`` cho bookmark cũ — chuyển hướng về hub Cameras, nơi mỗi
    camera có nút "Photos" mở gallery timelapse.
    """
    return redirect("camera_list")


# ── gallery 1 camera: phân trang 1..N + lọc ngày ──────────────────────────────

@login_required
def media_gallery(request, camera_pk):
    camera = get_object_or_404(Camera, pk=camera_pk)
    if not camera.is_media_viewable_by(request.user):
        raise Http404

    qs = Media.objects.filter(camera=camera)

    date_str = (request.GET.get("date") or "").strip()
    active_date = _parse_date(date_str)
    if active_date:
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(active_date, time.min), tz)
        end = timezone.make_aware(datetime.combine(active_date, time.max), tz)
        qs = qs.filter(taken_at__gte=start, taken_at__lte=end)
        total = (
            MediaDayStat.objects.filter(camera=camera, day=active_date)
            .values_list("count", flat=True)
            .first()
            or qs.count()
        )
    else:
        total = (
            MediaDayStat.objects.filter(camera=camera).aggregate(n=Sum("count"))[
                "n"
            ]
            or 0
        )

    num_pages = max(1, math.ceil(total / _PAGE_SIZE))
    try:
        page = int(request.GET.get("page", 1))
    except ValueError:
        page = 1
    page = min(max(1, page), num_pages)
    offset = (page - 1) * _PAGE_SIZE

    items = list(qs.order_by("-taken_at")[offset: offset + _PAGE_SIZE])
    photos = _presign_thumbs(items)

    can_download = camera.is_media_downloadable_by(request.user)

    ctx = {
        "camera": camera,
        "photos": photos,
        "page": page,
        "num_pages": num_pages,
        "page_range": _page_range(page, num_pages),
        "total": total,
        "active_date": date_str if active_date else "",
        "can_download": can_download,
    }

    if _is_ajax(request):
        return render(request, "home/_media_page.html", ctx)

    ctx["segment"] = "cameras"
    # danh sách ngày có ảnh (điều hướng nhanh) — lấy từ rollup, rẻ
    ctx["day_stats"] = list(
        MediaDayStat.objects.filter(camera=camera).order_by("-day")[:120]
    )
    # Render jobs của camera này
    from core.models.media import VideoRender
    ctx["renders"] = list(
        VideoRender.objects.filter(camera=camera)
        .order_by("-created_at")[:20]
    )
    ctx["resolution_choices"] = VideoRender.Resolution.choices
    ctx["can_render"] = camera.is_media_viewable_by(request.user)
    return render(request, "home/media_gallery.html", ctx)


# ── serve / thumb / download 1 ảnh (302 presigned) ────────────────────────────

@login_required
def media_serve(request, pk):
    media = get_object_or_404(Media.objects.select_related("camera"), pk=pk)
    if not media.is_accessible_by(request.user):
        raise Http404
    url = storage.presigned_get_url(media.s3_key)
    if not url:
        raise Http404
    return HttpResponseRedirect(url)


@login_required
def media_thumb(request, pk):
    media = get_object_or_404(Media.objects.select_related("camera"), pk=pk)
    if not media.is_accessible_by(request.user):
        raise Http404
    url = storage.presigned_get_url(media.effective_thumb_key)
    if not url:
        raise Http404
    return HttpResponseRedirect(url)


@login_required
def media_download(request, pk):
    media = get_object_or_404(Media.objects.select_related("camera"), pk=pk)
    if not media.is_downloadable_by(request.user):
        raise Http404
    name = f"{media.camera.code}_{media.taken_at:%Y%m%d_%H%M%S}.jpg"
    url = storage.presigned_get_url(media.s3_key, download_name=name)
    if not url:
        raise Http404
    return HttpResponseRedirect(url)


# ── gom tải (archive) ─────────────────────────────────────────────────────────

def _archive_payload(archive, request):
    data = {
        "id": str(archive.id),
        "status": archive.status,
        "status_display": archive.get_status_display(),
        "item_count": archive.item_count,
        "size_bytes": archive.size_bytes,
        "error": archive.error,
        "created_at": archive.created_at.isoformat(),
        "expires_at": (
            archive.expires_at.isoformat() if archive.expires_at else None
        ),
        "status_url": reverse("media_archive_status", args=[archive.id]),
        "download_url": None,
    }
    if archive.status == MediaArchive.Status.READY and not archive.is_expired:
        data["download_url"] = reverse("media_archive_download", args=[archive.id])
    return data


@login_required
@require_POST
def media_archive_create(request, camera_pk):
    """Tạo job nén ZIP theo lựa chọn (ids cụ thể HOẶC khoảng ngày/giờ)."""
    camera = get_object_or_404(Camera, pk=camera_pk)
    if not camera.is_media_downloadable_by(request.user):
        raise Http404

    ids = request.POST.getlist("ids")
    date_from = _parse_dt(request.POST.get("date_from"))
    date_to = _parse_dt(request.POST.get("date_to"))
    # lọc theo 1 ngày (từ ô chọn ngày trên gallery)
    day = _parse_date((request.POST.get("day") or "").strip())
    if day and not (date_from or date_to):
        tz = timezone.get_current_timezone()
        date_from = timezone.make_aware(datetime.combine(day, time.min), tz)
        date_to = timezone.make_aware(datetime.combine(day, time.max), tz)

    if not ids and not (date_from or date_to):
        return JsonResponse(
            {"ok": False, "error": "No photos or time range selected."},
            status=400,
        )

    archive = MediaArchive.objects.create(
        requested_by=request.user,
        camera=camera,
        media_ids=ids,
        date_from=date_from,
        date_to=date_to,
    )
    build_media_archive.delay(str(archive.id))
    return JsonResponse(
        {
            "ok": True,
            "id": str(archive.id),
            "status_url": reverse("media_archive_status", args=[archive.id]),
        }
    )


@login_required
def media_archive_status(request, pk):
    archive = get_object_or_404(MediaArchive, pk=pk)
    if not archive.is_accessible_by(request.user):
        raise Http404
    return JsonResponse(_archive_payload(archive, request))


@login_required
def media_archive_download(request, pk):
    archive = get_object_or_404(MediaArchive, pk=pk)
    if not archive.is_accessible_by(request.user):
        raise Http404
    if archive.status != MediaArchive.Status.READY or archive.is_expired:
        raise Http404
    name = f"{archive.camera.code}_{archive.created_at:%Y%m%d_%H%M%S}.zip"
    url = storage.presigned_get_url(archive.zip_key, download_name=name)
    if not url:
        raise Http404
    return HttpResponseRedirect(url)


@login_required
def media_archive_list(request):
    """Danh sách các gói tải của user (poll/hiển thị lịch sử)."""
    archives = MediaArchive.objects.select_related("camera").filter(
        requested_by=request.user
    )[:50]
    data = [{**_archive_payload(a, request), "camera": a.camera.code} for a in archives]
    return JsonResponse({"items": data})


@login_required
def downloads_panel_data(request):
    """AJAX — trả combined JSON: archives + renders cho Settings panel."""
    from core.models.media import VideoRender
    from core.utils import storage as _storage

    # Archives gần đây
    archives_qs = MediaArchive.objects.select_related("camera").filter(
        requested_by=request.user
    ).order_by("-created_at")[:10]

    archives = []
    for a in archives_qs:
        payload = _archive_payload(a, request)
        payload["camera_name"] = a.camera.name
        payload["camera_code"] = a.camera.code
        archives.append(payload)

    # Renders gần đây
    renders_qs = VideoRender.objects.select_related("camera").filter(
        requested_by=request.user
    ).order_by("-created_at")[:10]

    renders = []
    for vr in renders_qs:
        dl_url = None
        if vr.status == VideoRender.Status.READY and vr.output_key:
            try:
                dl_url = _storage.presigned_get_url(vr.output_key, expire=3600)
            except Exception:
                pass
        renders.append({
            "id": str(vr.id),
            "camera_name": vr.camera.name,
            "camera_code": vr.camera.code,
            "camera_pk": str(vr.camera.pk),
            "date_from": str(vr.date_from),
            "date_to": str(vr.date_to),
            "fps": vr.fps,
            "resolution": vr.resolution,
            "status": vr.status,
            "progress": vr.progress,
            "size_bytes": vr.size_bytes,
            "error": vr.error,
            "created_at": vr.created_at.isoformat(),
            "download_url": dl_url,
            "status_url": f"/renders/{vr.id}/status/",
        })

    return JsonResponse({"archives": archives, "renders": renders})
