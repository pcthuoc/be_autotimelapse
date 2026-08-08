from django.conf import settings
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from core.models import Camera, Media, MediaDayStat
from ._helpers import user_can_access_camera


@api_view(['GET'])
def api_media_gallery(request, camera_pk):
    try:
        cam = Camera.objects.select_related('site').get(pk=camera_pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not user_can_access_camera(request.user, cam):
        return Response({'detail': 'Permission denied'}, status=403)

    qs = Media.objects.filter(camera=cam).order_by('-taken_at')
    dt_from = request.query_params.get('dt_from', '').strip()
    dt_to   = request.query_params.get('dt_to', '').strip()
    day     = request.query_params.get('day', '')

    if dt_from or dt_to:
        from datetime import datetime as _dt
        def _parse(v):
            if not v:
                return None
            try:
                d = _dt.fromisoformat(v)
            except ValueError:
                return None
            return timezone.make_aware(d) if timezone.is_naive(d) else d
        if d_from := _parse(dt_from):
            qs = qs.filter(taken_at__gte=d_from)
        if d_to := _parse(dt_to):
            qs = qs.filter(taken_at__lte=d_to)
    elif day:
        qs = qs.filter(taken_at__date=day)

    day_stats = [{'day': ds.day.isoformat(), 'date_label': ds.day.strftime('%d/%m'),
                  'count': ds.count, 'cover_thumb_url': None}
                 for ds in MediaDayStat.objects.filter(camera=cam).order_by('-day')[:90]]

    pg = PageNumberPagination(); pg.page_size = 60
    page = pg.paginate_queryset(qs, request)

    from core.utils import storage as _st
    photos = []
    for m in page:
        fname = f"{cam.code}_{m.taken_at.strftime('%Y%m%d_%H%M%S')}.jpg"
        photos.append({
            'id': str(m.id),
            'taken_at': m.taken_at.isoformat(),
            'size_bytes': m.size_bytes,
            'width': m.width,
            'height': m.height,
            'thumb_url': _st.presigned_get_url_cached(m.effective_thumb_key, expire=3600, storage='seaweed') or '',
            'view_url': _st.presigned_get_url_cached(m.s3_key, expire=3600, storage=m.storage) or '',
            'download_url': _st.presigned_get_url_cached(m.s3_key, expire=3600, download_name=fname, storage=m.storage) or '',
        })

    resp = pg.get_paginated_response(photos)
    resp.data.update({'camera_name': cam.name, 'camera_code': cam.code,
                      'site_name': cam.site.name if cam.site else '',
                      'total_count': Media.objects.filter(camera=cam).count(),
                      'day_stats': day_stats})
    return resp


@api_view(['DELETE'])
def api_media_delete(request, pk):
    media = Media.objects.select_related('camera').filter(pk=pk).first()
    if not media:
        return Response({'detail': 'Not found'}, status=404)
    if not media.is_deletable_by(request.user):
        return Response({'detail': 'Permission denied'}, status=403)
    media.delete()
    return Response(status=204)


@api_view(['GET'])
def api_media_download(request, pk):
    """Redirect tới presigned URL có Content-Disposition: attachment."""
    from django.http import HttpResponseRedirect
    media = Media.objects.select_related('camera').filter(pk=pk).first()
    if not media:
        return Response({'detail': 'Not found'}, status=404)
    if not media.is_downloadable_by(request.user):
        return Response({'detail': 'Permission denied'}, status=403)
    fname = f"{media.camera.code}_{media.taken_at.strftime('%Y%m%d_%H%M%S')}.jpg"
    url = _st.presigned_get_url(media.s3_key, expire=300, download_name=fname, storage=media.storage)
    if not url:
        return Response({'detail': 'File không tồn tại'}, status=404)
    return HttpResponseRedirect(url)


@api_view(['POST'])
def api_media_bulk_delete(request):
    ids = request.data.get('ids') or []
    if not ids or len(ids) > 500:
        return Response({'detail': 'ids required, max 500'}, status=400)
    deleted = 0
    for m in Media.objects.select_related('camera').filter(pk__in=ids):
        if m.is_deletable_by(request.user):
            m.delete()
            deleted += 1
    return Response({'deleted': deleted})
