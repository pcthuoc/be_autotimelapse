from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Client, Media
from core.models.camera import CameraDevice, Site as SiteModel
from ._helpers import (
    camera_to_dict, filter_cameras_for_user, get_user_membership,
)


@api_view(['GET'])
def api_dashboard(request):
    user = request.user
    cams = list(filter_cameras_for_user(user, __import__('core.models', fromlist=['Camera']).Camera.objects.select_related('site')))
    cam_ids = [c.id for c in cams]

    devices = {d.camera_id: d for d in CameraDevice.objects.filter(camera_id__in=cam_ids)}
    now = timezone.now()
    today = now.date()
    threshold = now - timedelta(minutes=5)
    online_ids = {d.camera_id for d in devices.values() if d.last_seen_at and d.last_seen_at >= threshold}

    total_bytes = Media.objects.filter(camera_id__in=cam_ids).aggregate(s=Sum('size_bytes'))['s'] or 0
    days_7 = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        days_7.append({'date': d.strftime('%d/%m'),
                       'count': Media.objects.filter(camera_id__in=cam_ids, taken_at__date=d).count()})

    site_ids = list({c.site_id for c in cams if c.site_id})
    sites = SiteModel.objects.select_related('client').filter(id__in=site_ids).order_by('name')
    sites_data = []
    for site in sites:
        site_cams = [c for c in cams if c.site_id == site.id]
        site_cam_ids = [c.id for c in site_cams]
        latest_media = Media.objects.filter(camera_id__in=site_cam_ids).order_by('-taken_at').first()
        latest_thumb_url = None
        if latest_media:
            try:
                from core.utils import storage as _st
                latest_thumb_url = _st.presigned_get_url(
                    latest_media.effective_thumb_key,
                    expire=3600,
                    storage=latest_media.effective_thumb_storage,
                )
            except Exception:
                pass
        cam_data = []
        for cam in site_cams:
            dev = devices.get(cam.id)
            cam_data.append({'cam': camera_to_dict(cam, request), 'online': cam.id in online_ids,
                             'thumb_url': None,
                             'battery': dev.battery_percent if dev else None,
                             'signal': dev.sim_signal_dbm if dev else None})
        sites_data.append({
            'site': {'id': str(site.id), 'name': site.name,
                     'client_id': str(site.client_id) if site.client_id else None,
                     'client_name': site.client.name if site.client_id else None},
            'cam_count': len(site_cams),
            'online_count': len([c for c in site_cams if c.id in online_ids]),
            'today_count': Media.objects.filter(camera_id__in=site_cam_ids, taken_at__date=today).count(),
            'latest_thumb_url': latest_thumb_url,
            'cameras': cam_data,
        })

    m = get_user_membership(user)
    if user.is_staff:
        role, client_name = 'superadmin', None
        total_clients = Client.objects.count()
        total_members = None
    elif m:
        role, client_name = m.role, m.client.name
        total_clients = None
        from core.models import ClientMembership
        total_members = ClientMembership.objects.filter(client=m.client).count()
    else:
        role = client_name = total_clients = total_members = None

    return Response({
        'role': role, 'client_name': client_name,
        'total_clients': total_clients, 'total_members': total_members,
        'total_sites': sites.count(), 'total_cameras': len(cams),
        'online_count': len(online_ids),
        'today_photos': Media.objects.filter(camera_id__in=cam_ids, taken_at__date=today).count(),
        'total_photos': Media.objects.filter(camera_id__in=cam_ids).count(),
        'total_bytes': total_bytes,
        'days_7': days_7, 'sites_data': sites_data,
    })
