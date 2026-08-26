from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Media


@api_view(['GET'])
def api_storage_stats(request):
    if not request.user.is_staff:
        return Response({'detail': 'Admin only'}, status=403)

    from django.db.models import Sum, Count
    from django.conf import settings as _s
    from core.utils.storage import _r2_enabled

    breakdown = (Media.objects.values('storage')
                 .annotate(count=Count('id'), bytes=Sum('size_bytes'))
                 .order_by('storage'))
    seaweed_bytes = r2_bytes = seaweed_count = r2_count = 0
    for row in breakdown:
        if row['storage'] == 'r2':
            r2_bytes, r2_count = row['bytes'] or 0, row['count']
        else:
            seaweed_bytes, seaweed_count = row['bytes'] or 0, row['count']

    seaweed_volumes = seaweed_max_bytes = None
    try:
        import urllib.request, json as _json
        with urllib.request.urlopen('http://seaweed-master:9333/vol/status', timeout=3) as r:
            vdata = _json.loads(r.read())
        vols = (vdata.get('Volumes', {}).get('DataCenters', {})
                .get('DefaultDataCenter', {}).get('DefaultRack', {})
                .get('seaweed-volume:8080', []))
        seaweed_volumes = len(vols)
        seaweed_max_bytes = 20 * 1024 * 1024 * 1024
    except Exception:
        pass

    r2_cfg = getattr(_s, 'R2', {})
    return Response({
        'seaweed': {
            'enabled': True, 'count': seaweed_count, 'bytes': seaweed_bytes,
            'volumes': seaweed_volumes, 'max_bytes': seaweed_max_bytes,
            'usage_pct': round(seaweed_bytes / seaweed_max_bytes * 100, 1) if seaweed_max_bytes else None,
        },
        'r2': {
            'enabled': _r2_enabled(),
            'endpoint': r2_cfg.get('ENDPOINT_URL', ''),
            'bucket': r2_cfg.get('BUCKET', ''),
            'output_bucket': r2_cfg.get('OUTPUT_BUCKET', ''),
            'count': r2_count, 'bytes': r2_bytes,
        },
        'total': {'count': seaweed_count + r2_count, 'bytes': seaweed_bytes + r2_bytes},
        'config': {
            'mode': 'r2_original_seaweed_thumbnail',
            'render_ttl_days': getattr(_s, 'VIDEO_RENDER_TTL_DAYS', 7),
        },
    })
