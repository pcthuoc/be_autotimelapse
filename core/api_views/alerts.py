from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import AlertSettings, Camera
from ._helpers import filter_cameras_for_user, get_user_membership, is_client_admin, user_can_access_camera


@api_view(['GET'])
def api_alert_settings(request):
    cams = filter_cameras_for_user(request.user).select_related('site', 'site__client').order_by('code')
    data = []
    for cam in cams:
        try:
            s = cam.alert_settings
        except Exception:
            s = None
        data.append({
            'camera_id': str(cam.id), 'camera_code': cam.code, 'camera_name': cam.name,
            'site_name': cam.site.name if cam.site else '',
            'client_name': cam.site.client.name if (cam.site and cam.site.client_id) else '',
            'enabled': s.enabled if s else False,
            'battery_low_pct': s.battery_low_pct if s else 20,
            'battery_critical_pct': s.battery_critical_pct if s else 10,
            'signal_weak_dbm': s.signal_weak_dbm if s else -100,
            'offline_minutes': s.offline_minutes if s else 60,
            'temperature_high_c': s.temperature_high_c if s else 60,
            'daily_photo_min': s.daily_photo_min if s else 0,
            'notify_email': s.notify_email if s else '',
        })
    return Response(data)


@api_view(['POST'])
def api_alert_settings_save(request, camera_pk):
    try:
        cam = Camera.objects.select_related('site').get(pk=camera_pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not request.user.is_staff:
        if not (is_client_admin(request.user) and user_can_access_camera(request.user, cam)):
            return Response({'detail': 'Permission denied'}, status=403)

    s, _ = AlertSettings.objects.get_or_create(camera=cam)

    if 'notify_email' in request.data and request.data['notify_email']:
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError as _VE
        try:
            validate_email(str(request.data['notify_email']))
        except _VE:
            return Response({'detail': 'notify_email không hợp lệ'}, status=400)

    _RANGES = {
        'battery_low_pct': (0, 100), 'battery_critical_pct': (0, 100),
        'signal_weak_dbm': (-150, 0), 'offline_minutes': (1, 10080),
        'temperature_high_c': (0, 100), 'daily_photo_min': (0, 10000),
    }
    for fname, (lo, hi) in _RANGES.items():
        if fname in request.data:
            try:
                v = int(request.data[fname])
            except (TypeError, ValueError):
                return Response({'detail': f'{fname} phải là số nguyên'}, status=400)
            if not (lo <= v <= hi):
                return Response({'detail': f'{fname} phải trong khoảng [{lo}, {hi}]'}, status=400)

    for field in ['enabled', 'battery_low_pct', 'battery_critical_pct', 'signal_weak_dbm',
                  'offline_minutes', 'temperature_high_c', 'daily_photo_min', 'notify_email']:
        if field in request.data:
            setattr(s, field, request.data[field])
    s.save()
    return Response({'ok': True})
