"""Shared helpers dùng chung cho tất cả api_views modules."""
from django.utils import timezone
from django.core.cache import cache

from core.models import Camera, Media, Site
from core.models.camera import CameraDevice

# Ngưỡng fallback (giây) khi Redis cache miss — phải khớp với ONLINE_TTL (1800s)
# để không báo offline sai cho camera gửi data mỗi 15–20 phút.
_ONLINE_FALLBACK_SEC = 1800


def camera_to_dict(cam, request=None):
    device = getattr(cam, 'device', None)
    try:
        from core.utils import storage as _st
        latest = Media.objects.filter(camera=cam).order_by('-taken_at').first()
        thumb_url = _st.presigned_get_url(
            latest.effective_thumb_key,
            expire=3600,
            storage=latest.effective_thumb_storage,
        ) if latest else None
    except Exception:
        thumb_url = None
    data = {
        'id': str(cam.id),
        'code': cam.code,
        'name': cam.name,
        'status': cam.status,
        'camera_model': cam.camera_model or 'generic',
        'timezone': str(cam.timezone),
        'site': {
            'id': str(cam.site.id),
            'name': cam.site.name,
            'client_id': str(cam.site.client_id) if cam.site.client_id else None,
            'client_name': cam.site.client.name if cam.site.client_id else None,
        } if cam.site else None,
        'latest_thumb_url': thumb_url,
        'is_online': is_online(device),
        'device': device_to_dict(device) if device else None,
    }
    # Credential chỉ xuất hiện với superadmin hoặc client admin sở hữu camera.
    # Member vẫn xem được camera/telemetry nhưng không nhận secret MQTT.
    user = getattr(request, 'user', None) if request is not None else None
    if cam.is_editable_by(user):
        data['mqtt_password'] = cam.mqtt_password
    return data


def device_to_dict(dev):
    if not dev:
        return None
    sig = dev.sim_signal_dbm
    if sig is None:
        bars, label = 0, '—'
    elif sig >= -70:
        bars, label = 4, 'Excellent'
    elif sig >= -85:
        bars, label = 3, 'Good'
    elif sig >= -100:
        bars, label = 2, 'Weak'
    else:
        bars, label = 1, 'Poor'
    cm4_state = getattr(dev, 'effective_cm4_power_state', getattr(dev, 'cm4_power_state', 'off')) or 'off'
    # Tự động cập nhật DB nếu CM4 đã tắt hoặc quá timeout không có tín hiệu
    raw_state = getattr(dev, 'cm4_power_state', 'off')
    if raw_state != 'off' and cm4_state == 'off':
        try:
            dev.cm4_power_state = 'off'
            dev.save(update_fields=['cm4_power_state', 'updated_at'])
        except Exception:
            pass

    return {
        'id': str(dev.id),
        'last_seen_at': dev.last_seen_at.isoformat() if dev.last_seen_at else None,
        'esp32_last_seen_at': dev.esp32_last_seen_at.isoformat() if getattr(dev, 'esp32_last_seen_at', None) else None,
        'esp32_firmware': getattr(dev, 'esp32_firmware', '') or '',
        'cm4_power_state': cm4_state,
        'cm4_last_seen_at': dev.cm4_last_seen_at.isoformat() if getattr(dev, 'cm4_last_seen_at', None) else None,
        'sim_active_node': getattr(dev, 'sim_active_node', 'esp32') or 'esp32',
        'battery_percent': dev.battery_percent,
        'battery_voltage': float(dev.battery_voltage) if dev.battery_voltage else None,
        'is_charging': dev.is_charging,
        'cell_voltages': dev.cell_voltages if hasattr(dev, 'cell_voltages') else [],
        'solar_voltage': float(dev.solar_voltage) if getattr(dev, 'solar_voltage', None) else None,
        'solar_percent': getattr(dev, 'solar_percent', None),
        'sim_signal_dbm': dev.sim_signal_dbm,
        'sim_operator': getattr(dev, 'sim_operator', '') or '',
        'sim_number': getattr(dev, 'sim_number', '') or '',
        'sim_iccid': getattr(dev, 'sim_iccid', '') or '',
        'temperature_c': float(dev.temperature_c) if dev.temperature_c else None,
        'humidity_percent': getattr(dev, 'humidity_percent', None),
        'firmware_version': getattr(dev, 'firmware_version', '') or '',
        'force_power_on': getattr(dev, 'force_power_on', False),
        'capture_interval_sec': getattr(dev, 'capture_interval_sec', None),
        'schedule_enabled': getattr(dev, 'schedule_enabled', False),
        'work_start_time': getattr(dev, 'work_start_time', '06:00') or '06:00',
        'work_end_time': getattr(dev, 'work_end_time', '18:00') or '18:00',
        'signal_bars': bars,
        'signal_label': label,
    }


def is_online(device):
    """Kiểm tra camera có online không.

    Ưu tiên Redis cache (cam:online:<code>) do MQTT listener set/clear.
    Nếu cache miss (Redis không có key), fallback về last_seen_at với
    ngưỡng rộng (_ONLINE_FALLBACK_SEC) để tránh báo offline sai cho camera
    gửi telemetry mỗi 4–5 phút.
    """
    if not device:
        return False
    try:
        code = device.camera.code
        cached = cache.get(f"cam:online:{code}")
        if cached is not None:
            return bool(cached)
    except Exception:
        pass
    # Fallback: dùng last_seen_at với ngưỡng rộng hơn interval của thiết bị
    last_seen = getattr(device, 'esp32_last_seen_at', None) or device.last_seen_at
    if not last_seen:
        return False
    return (timezone.now() - last_seen).total_seconds() < _ONLINE_FALLBACK_SEC


def get_user_membership(user):
    if not user or not user.is_authenticated or user.is_staff:
        return None
    from core.models import ClientMembership
    return ClientMembership.objects.select_related('client').filter(user=user).first()


def get_user_client(user):
    m = get_user_membership(user)
    return m.client if m else None


def is_client_admin(user):
    m = get_user_membership(user)
    return bool(m and m.role == 'admin')


def filter_cameras_for_user(user, qs=None):
    if qs is None:
        qs = Camera.objects.all()
    if user.is_staff:
        return qs
    m = get_user_membership(user)
    if m:
        return qs.filter(site__client=m.client)
    return qs.none()


def filter_sites_for_user(user, qs=None):
    if qs is None:
        qs = Site.objects.all()
    if user.is_staff:
        return qs
    m = get_user_membership(user)
    if m:
        return qs.filter(client=m.client)
    return qs.none()


def user_can_access_camera(user, camera):
    if user.is_staff:
        return True
    m = get_user_membership(user)
    if not m:
        return False
    return camera.site_id is not None and camera.site.client_id == m.client_id


def _user_perms(u):
    if u.is_staff:
        return {
            'client_role': 'superadmin',
            'can_manage_cameras': True,
            'can_manage_members': True,
            'can_manage_clients': True,
            'can_download': True,
            'can_view_renders': True,
            'can_manage_settings': True,
        }
    m = get_user_membership(u)
    role = m.role if m else None
    is_admin = role == 'admin'
    return {
        'client_role': role,
        'can_manage_cameras': is_admin,
        'can_manage_members': is_admin,
        'can_manage_clients': False,
        'can_download': m.can_download if m else False,
        'can_view_renders': m is not None,
        'can_manage_settings': is_admin,
    }
