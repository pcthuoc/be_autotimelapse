from datetime import datetime as _dt

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Camera, Media, VideoRender
from core.models.media import MediaArchive
from ._helpers import get_user_membership
from core.utils.storage import _r2_enabled


def _parse_dt(v):
    if not v:
        return None
    try:
        dt = _dt.fromisoformat(str(v))
    except ValueError:
        return None
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


@api_view(['POST'])
def api_archive_create(request, camera_pk):
    from core.tasks import build_media_archive
    try:
        cam = Camera.objects.get(pk=camera_pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not cam.is_editable_by(request.user):  # member không được tạo zip archive
        return Response({'detail': 'Chỉ admin mới được tạo archive'}, status=403)

    ids = request.data.get('ids') or []
    if len(ids) > 5000:
        return Response({'detail': 'Tối đa 5000 ảnh mỗi lần'}, status=400)

    date_from = _parse_dt(request.data.get('date_from'))
    date_to   = _parse_dt(request.data.get('date_to'))
    if not ids and not (date_from or date_to):
        return Response({'detail': 'Chọn ảnh hoặc khoảng thời gian'}, status=400)

    qs = Media.objects.filter(camera=cam)
    if ids:
        qs = qs.filter(pk__in=ids)
    if date_from:
        qs = qs.filter(taken_at__gte=date_from)
    if date_to:
        qs = qs.filter(taken_at__lte=date_to)

    photo_count = qs.count()
    if photo_count == 0:
        return Response({'detail': 'Không tìm thấy ảnh nào'}, status=400)
    max_items = getattr(settings, 'MEDIA_ARCHIVE_MAX_ITEMS', 5000)
    if photo_count > max_items:
        return Response({'detail': f'Quá {max_items} ảnh, hãy thu hẹp khoảng thời gian'}, status=400)

    priority = 9 if photo_count <= 100 else (5 if photo_count <= 1000 else 2)
    archive = MediaArchive.objects.create(
        requested_by=request.user, camera=cam,
        media_ids=[str(i) for i in ids],
        date_from=date_from, date_to=date_to, item_count=photo_count,
    )
    build_media_archive.apply_async(args=[str(archive.id)], priority=priority)
    return Response({'ok': True, 'id': str(archive.id)}, status=201)


@api_view(['GET', 'DELETE'])
def api_archive_detail(request, pk):
    try:
        archive = MediaArchive.objects.select_related('camera').get(pk=pk)
    except MediaArchive.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not archive.is_accessible_by(request.user):
        return Response({'detail': 'Permission denied'}, status=403)
    if request.method == 'GET':
        return Response({
            'id': str(archive.id), 'status': archive.status,
            'item_count': archive.item_count, 'size_bytes': archive.size_bytes,
            'error': archive.error, 'expired': archive.is_expired,
            'created_at': archive.created_at.isoformat(),
            'ready_at': archive.ready_at.isoformat() if archive.ready_at else None,
            'expires_at': archive.expires_at.isoformat() if archive.expires_at else None,
        })
    if not (request.user.is_staff or archive.requested_by_id == request.user.id
            or archive.camera.is_editable_by(request.user)):
        return Response({'detail': 'Permission denied'}, status=403)
    # post_delete signal xóa đúng object storage/bucket, kể cả khi cascade.
    archive.delete()
    return Response(status=204)


@api_view(['GET'])
def api_downloads(request):
    from core.utils import storage as _st
    from core.models.permission import ClientMembership
    user = request.user
    if user.is_staff:
        renders  = VideoRender.objects.select_related('camera').order_by('-created_at')[:50]
        archives = MediaArchive.objects.select_related('camera').order_by('-created_at')[:50]
    else:
        m = ClientMembership.objects.filter(user=user).first()
        if m:
            # client admin/member thấy toàn bộ item trong client của mình
            renders  = (VideoRender.objects.select_related('camera')
                        .filter(Q(requested_by=user) | Q(camera__site__client=m.client))
                        .distinct().order_by('-created_at')[:50])
            archives = (MediaArchive.objects.select_related('camera')
                        .filter(Q(requested_by=user) | Q(camera__site__client=m.client))
                        .distinct().order_by('-created_at')[:50])
        else:
            renders  = VideoRender.objects.select_related('camera').filter(requested_by=user).order_by('-created_at')[:50]
            archives = MediaArchive.objects.select_related('camera').filter(requested_by=user).order_by('-created_at')[:50]

    items = []
    for r in renders:
        url = None
        if r.status == 'ready' and r.output_key and not r.is_expired:
            try:
                url = _st.presigned_get_url(r.output_key, expire=3600,
                                            download_name=f"{r.camera.code}_{r.date_from}_{r.date_to}.mp4",
                                            storage=r.effective_output_storage,
                                            r2_output=r.uses_r2_output_bucket)
            except Exception:
                pass
        items.append({
            'id': str(r.id), 'kind': 'render',
            'camera_code': r.camera.code, 'camera_name': r.camera.name,
            'title': f"Video {r.date_from} → {r.date_to}",
            'status': r.status, 'progress': r.progress or 0,
            'size_bytes': r.size_bytes, 'item_count': r.item_count or 0,
            'download_url': url, 'error': r.error or '',
            'meta': f"{r.fps}fps · {r.resolution}",
            'created_at': r.created_at.isoformat(),
            'ready_at': r.ready_at.isoformat() if r.ready_at else None,
            'expires_at': None,
        })
    for a in archives:
        url = None
        if a.status == 'ready' and a.zip_key and not a.is_expired:
            try:
                url = _st.presigned_get_url(
                    a.zip_key,
                    expire=3600,
                    download_name=f"{a.camera.code}_photos.zip",
                    storage=a.effective_output_storage,
                    r2_output=a.uses_r2_output_bucket,
                )
            except Exception:
                pass
        items.append({
            'id': str(a.id), 'kind': 'archive',
            'camera_code': a.camera.code, 'camera_name': a.camera.name,
            'title': f"ZIP {a.item_count or len(a.media_ids or [])} ảnh",
            'status': a.status, 'progress': 0,
            'size_bytes': a.size_bytes or 0, 'item_count': a.item_count or 0,
            'download_url': url, 'error': getattr(a, 'error', '') or '',
            'meta': 'ZIP archive',
            'created_at': a.created_at.isoformat(),
            'ready_at': a.ready_at.isoformat() if getattr(a, 'ready_at', None) else None,
            'expires_at': a.expires_at.isoformat() if getattr(a, 'expires_at', None) else None,
        })

    items.sort(key=lambda x: x['created_at'], reverse=True)
    pending_count = sum(1 for i in items if i['status'] in ('pending', 'processing'))
    return Response({'results': items[:80], 'pending_count': pending_count})
