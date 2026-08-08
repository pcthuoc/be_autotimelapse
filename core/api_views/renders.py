from datetime import date as _date, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from core.models import Camera, Media, VideoRender
from ._helpers import get_user_membership


@api_view(['GET'])
def api_renders(request):
    from core.utils import storage as _st
    user = request.user
    if user.is_staff:
        qs = VideoRender.objects.select_related('camera').order_by('-created_at')
    else:
        m = get_user_membership(user)
        if m:
            qs = VideoRender.objects.select_related('camera').filter(
                Q(requested_by=user) | Q(camera__site__client=m.client)
            ).distinct().order_by('-created_at')
        else:
            qs = VideoRender.objects.select_related('camera').filter(requested_by=user).order_by('-created_at')

    if cam_id := request.query_params.get('camera'):
        qs = qs.filter(camera_id=cam_id)

    pg = PageNumberPagination(); pg.page_size = 50
    page = pg.paginate_queryset(qs, request)
    data = []
    for r in page:
        dl = stream = None
        if r.status == 'ready' and r.output_key:
            try:
                fname = f"{r.camera.code}_{r.date_from}_{r.date_to}.mp4"
                _rs = "r2" if _st._r2_enabled() else None
                dl = _st.presigned_get_url(r.output_key, expire=3600, download_name=fname, storage=_rs)
                stream = _st.presigned_get_url(r.output_key, expire=3600, inline_content_type='video/mp4', storage=_rs)
            except Exception:
                pass
        data.append({
            'id': str(r.id), 'camera_id': str(r.camera_id),
            'camera_code': r.camera.code, 'camera_name': r.camera.name,
            'status': r.status, 'progress': r.progress or 0,
            'item_count': r.item_count or 0, 'size_bytes': r.size_bytes,
            'download_url': dl, 'stream_url': stream, 'error': r.error or '',
            'date_from': str(r.date_from), 'date_to': str(r.date_to),
            'fps': r.fps, 'resolution': r.resolution, 'frame_interval': r.frame_interval,
            'created_at': r.created_at.isoformat(),
            'ready_at': r.ready_at.isoformat() if r.ready_at else None,
            'expires_at': r.expires_at.isoformat() if getattr(r, 'expires_at', None) else None,
        })
    return pg.get_paginated_response(data)


@api_view(['POST'])
def api_render_create(request, camera_pk):
    try:
        cam = Camera.objects.get(pk=camera_pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not cam.is_accessible_by(request.user):
        return Response({'detail': 'Permission denied'}, status=403)
    if not cam.is_editable_by(request.user):  # member không được tạo render
        return Response({'detail': 'Chỉ admin mới được tạo video render'}, status=403)

    date_from = request.data.get('date_from')
    date_to   = request.data.get('date_to')
    if not date_from or not date_to:
        return Response({'detail': 'date_from và date_to là bắt buộc'}, status=400)
    try:
        d_from = _date.fromisoformat(str(date_from))
        d_to   = _date.fromisoformat(str(date_to))
    except ValueError:
        return Response({'detail': 'Định dạng ngày không hợp lệ (YYYY-MM-DD)'}, status=400)
    if d_from > d_to:
        return Response({'detail': 'date_from phải trước date_to'}, status=400)
    if (d_to - d_from).days > 366:
        return Response({'detail': 'Khoảng thời gian tối đa 1 năm'}, status=400)

    try:
        fps = int(request.data.get('fps', 24))
    except (TypeError, ValueError):
        fps = 24
    if fps not in (6, 12, 24, 30):
        fps = 24
    resolution = request.data.get('resolution', VideoRender.Resolution.R_1080)
    if resolution not in dict(VideoRender.Resolution.choices):
        resolution = VideoRender.Resolution.R_1080
    try:
        frame_interval = max(0, int(request.data.get('frame_interval', 0)))
    except (TypeError, ValueError):
        frame_interval = 0

    vr = VideoRender.objects.create(
        camera=cam, requested_by=request.user,
        date_from=date_from, date_to=date_to,
        fps=fps, resolution=resolution, frame_interval=frame_interval,
    )
    from core.tasks import render_timelapse_video
    transaction.on_commit(lambda: render_timelapse_video.delay(str(vr.id)))
    return Response({'ok': True, 'render_id': str(vr.id)}, status=201)


@api_view(['DELETE'])
def api_render_detail(request, pk):
    try:
        vr = VideoRender.objects.select_related('camera').get(pk=pk)
    except VideoRender.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not (request.user.is_staff or vr.requested_by_id == request.user.id
            or vr.camera.is_editable_by(request.user)):
        return Response({'detail': 'Permission denied'}, status=403)
    if vr.output_key:
        try:
            from core.utils import storage as _st
            _st.delete_key(vr.output_key)
        except Exception:
            pass
    vr.delete()
    return Response(status=204)
