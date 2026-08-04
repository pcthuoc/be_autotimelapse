"""
REST API v1 — AutoTimelapse
All endpoints require session authentication (CSRF + cookie).
"""
import json
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.conf import settings
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from core.models import (
    AlertSettings,
    Camera,
    CameraCredential,
    CameraDevice,
    CameraSettings,
    Client,
    Media,
    MediaDayStat,
    Site,
    VideoRender,
)

User = get_user_model()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def camera_to_dict(cam, request=None):
    device = getattr(cam, 'device', None)
    try:
        from core.utils import storage as _st
        latest = Media.objects.filter(camera=cam).order_by('-taken_at').first()
        thumb_url = _st.presigned_get_url(latest.effective_thumb_key, expire=3600) if latest else None
    except Exception:
        thumb_url = None

    return {
        'id': str(cam.id),
        'code': cam.code,
        'name': cam.name,
        'status': cam.status,
        'camera_model': cam.camera_model or 'generic',
        'timezone': str(cam.timezone),
        'mqtt_password': cam.mqtt_password,
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


def device_to_dict(dev):
    if not dev:
        return None
    sig = dev.sim_signal_dbm
    if sig is None:
        bars = 0
        label = '—'
    elif sig >= -70:
        bars, label = 4, 'Excellent'
    elif sig >= -85:
        bars, label = 3, 'Good'
    elif sig >= -100:
        bars, label = 2, 'Weak'
    else:
        bars, label = 1, 'Poor'
    return {
        'id': str(dev.id),
        'last_seen_at': dev.last_seen_at.isoformat() if dev.last_seen_at else None,
        'esp32_last_seen_at': dev.esp32_last_seen_at.isoformat() if getattr(dev, 'esp32_last_seen_at', None) else None,
        'esp32_firmware': getattr(dev, 'esp32_firmware', '') or '',
        'cm4_power_state': getattr(dev, 'cm4_power_state', 'off') or 'off',
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
        'capture_interval_sec': getattr(dev, 'capture_interval_sec', None),
        'signal_bars': bars,
        'signal_label': label,
    }


def is_online(device):
    if not device:
        return False
    # Online status follows ESP32-S3 (or last_seen_at fallback)
    last_seen = getattr(device, 'esp32_last_seen_at', None) or device.last_seen_at
    if not last_seen:
        return False
    delta = timezone.now() - last_seen
    return delta.total_seconds() < 300  # 5 min


# ─────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def api_login(request):
    username = request.data.get('username', '')
    password = request.data.get('password', '')
    remember = request.data.get('remember', False)
    user = authenticate(request, username=username, password=password)
    if not user:
        return Response({'detail': 'Sai username hoặc password'}, status=status.HTTP_401_UNAUTHORIZED)
    auth_login(request, user)
    if not remember:
        request.session.set_expiry(0)
    return Response({'id': user.id, 'username': user.username, 'is_staff': user.is_staff})


@api_view(['POST'])
@permission_classes([AllowAny])
def api_logout(request):
    auth_logout(request)
    return Response({'ok': True})


def get_user_membership(user):
    """Trả về ClientMembership của user (None nếu superadmin hoặc chưa gán)."""
    if not user or not user.is_authenticated or user.is_staff:
        return None
    from core.models import ClientMembership
    return (
        ClientMembership.objects
        .select_related('client')
        .filter(user=user)
        .first()
    )


def get_user_client(user):
    """Trả về Client mà user thuộc về (None nếu superadmin -> thấy tất cả)."""
    membership = get_user_membership(user)
    return membership.client if membership else None


def is_client_admin(user):
    """True nếu user là admin của client (không tính superadmin)."""
    m = get_user_membership(user)
    return bool(m and m.role == 'admin')


def filter_cameras_for_user(user, qs=None):
    """Lọc camera theo tầng quyền: staff -> all, member -> camera trong client."""
    if qs is None:
        qs = Camera.objects.all()
    if user.is_staff:
        return qs
    membership = get_user_membership(user)
    if membership:
        return qs.filter(site__client=membership.client)
    return qs.none()


def filter_sites_for_user(user, qs=None):
    """Lọc site theo tầng quyền: staff -> all, member -> site trong client."""
    if qs is None:
        qs = Site.objects.all()
    if user.is_staff:
        return qs
    membership = get_user_membership(user)
    if membership:
        return qs.filter(client=membership.client)
    return qs.none()


def user_can_access_camera(user, camera):
    """True nếu user được xem camera này."""
    if user.is_staff:
        return True
    membership = get_user_membership(user)
    if not membership:
        return False
    return camera.site_id is not None and camera.site.client_id == membership.client_id


def _user_perms(u):
    """Perms dict cho frontend ẩn/hiện UI theo quyền (theo PERMISSION_ARCHITECTURE.md)."""
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
    membership = get_user_membership(u)
    role = membership.role if membership else None
    is_admin = role == 'admin'
    return {
        'client_role': role,
        'can_manage_cameras': is_admin,
        'can_manage_members': is_admin,
        'can_manage_clients': False,   # chỉ superadmin
        'can_download': membership.can_download if membership else False,
        'can_view_renders': membership is not None,
        'can_manage_settings': is_admin,
    }


@api_view(['GET'])
def api_me(request):
    u = request.user
    if not u or not u.is_authenticated:
        return Response({'detail': 'Chưa đăng nhập'}, status=status.HTTP_401_UNAUTHORIZED)
    membership = get_user_membership(u)
    client_role = 'superadmin' if u.is_staff else (membership.role if membership else None)
    return Response({
        'id': u.id, 'username': u.username, 'email': u.email,
        'is_staff': u.is_staff, 'is_active': u.is_active,
        'date_joined': u.date_joined.isoformat(),
        'client_id': str(membership.client_id) if membership else None,
        'client_name': membership.client.name if membership else None,
        'client_role': client_role,
        'perms': _user_perms(u),
    })



# ─────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────

@api_view(['GET'])
def api_dashboard(request):
    from datetime import date, timedelta
    from django.db.models import Sum
    from core.models.camera import CameraDevice
    from core.models.camera import Site as SiteModel

    user = request.user

    # Camera queryset theo quyền (xem PERMISSION_ARCHITECTURE.md)
    cams_qs = filter_cameras_for_user(user, Camera.objects.select_related('site'))

    cams = list(cams_qs)
    cam_ids = [c.id for c in cams]

    # Device map
    devices = {d.camera_id: d for d in CameraDevice.objects.filter(camera_id__in=cam_ids)}

    now = timezone.now()
    today = now.date()
    threshold = now - timezone.timedelta(minutes=5)
    online_ids = set(
        d.camera_id for d in devices.values()
        if d.last_seen_at and d.last_seen_at >= threshold
    )

    # Stats
    total_cameras = len(cams)
    online_count = len(online_ids)
    today_photos = Media.objects.filter(camera_id__in=cam_ids, taken_at__date=today).count()
    total_photos = Media.objects.filter(camera_id__in=cam_ids).count()
    total_bytes = Media.objects.filter(camera_id__in=cam_ids).aggregate(s=Sum('size_bytes'))['s'] or 0

    # Sparkline 7 days
    days_7 = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        cnt = Media.objects.filter(camera_id__in=cam_ids, taken_at__date=d).count()
        days_7.append({'date': d.strftime('%d/%m'), 'count': cnt})

    # Sites
    sites_data = []
    site_ids = list({c.site_id for c in cams if c.site_id})
    sites = SiteModel.objects.select_related('client').filter(id__in=site_ids).order_by('name')
    total_sites = sites.count()

    for site in sites:
        site_cams = [c for c in cams if c.site_id == site.id]
        site_cam_ids = [c.id for c in site_cams]

        latest_media = Media.objects.filter(camera_id__in=site_cam_ids).order_by('-taken_at').first()
        latest_thumb_url = None
        if latest_media:
            try:
                from core.utils import storage as _storage
                latest_thumb_url = _storage.presigned_get_url(latest_media.thumb_key or latest_media.s3_key, expire=3600)
            except Exception:
                pass

        cam_data = []
        for cam in site_cams:
            dev = devices.get(cam.id)
            cam_data.append({
                'cam': camera_to_dict(cam, request),
                'online': cam.id in online_ids,
                'thumb_url': None,
                'battery': dev.battery_percent if dev else None,
                'signal': dev.sim_signal_dbm if dev else None,
            })

        sites_data.append({
            'site': {
                'id': str(site.id),
                'name': site.name,
                'client_id': str(site.client_id) if site.client_id else None,
                'client_name': site.client.name if site.client_id else None,
            },
            'cam_count': len(site_cams),
            'online_count': len([c for c in site_cams if c.id in online_ids]),
            'today_count': Media.objects.filter(camera_id__in=site_cam_ids, taken_at__date=today).count(),
            'latest_thumb_url': latest_thumb_url,
            'cameras': cam_data,
        })

    # Metadata theo tầng quyền (PERMISSION_ARCHITECTURE.md)
    membership = get_user_membership(user)
    if user.is_staff:
        role = 'superadmin'
        client_name = None
        total_clients = Client.objects.count()
        total_members = None
    elif membership:
        role = membership.role
        client_name = membership.client.name
        total_clients = None
        from core.models import ClientMembership
        total_members = ClientMembership.objects.filter(client=membership.client).count()
    else:
        role = None
        client_name = None
        total_clients = None
        total_members = None

    return Response({
        'role': role,
        'client_name': client_name,
        'total_clients': total_clients,
        'total_members': total_members,
        'total_sites': total_sites,
        'total_cameras': total_cameras,
        'online_count': online_count,
        'today_photos': today_photos,
        'total_photos': total_photos,
        'total_bytes': total_bytes,
        'days_7': days_7,
        'sites_data': sites_data,
    })


# ─────────────────────────────────────────────
# Cameras
# ─────────────────────────────────────────────

@api_view(['GET', 'POST'])
def api_cameras(request):
    if request.method == 'GET':
        qs = filter_cameras_for_user(
            request.user,
            Camera.objects.select_related('site', 'site__client').prefetch_related('device'),
        ).order_by('code')
        q = request.query_params.get('q', '')
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(code__icontains=q) |
                Q(site__name__icontains=q) |
                Q(site__client__name__icontains=q)
            )
        status_f = request.query_params.get('status', '')
        if status_f:
            qs = qs.filter(status=status_f)
        site_f = request.query_params.get('site', '')
        if site_f:
            qs = qs.filter(site_id=site_f)

        paginator = PageNumberPagination()
        paginator.page_size = 200
        page = paginator.paginate_queryset(qs, request)
        data = [camera_to_dict(c, request) for c in page]
        return paginator.get_paginated_response(data)

    # POST — chỉ superadmin hoặc client admin
    if not (request.user.is_staff or is_client_admin(request.user)):
        return Response({'detail': 'Permission denied'}, status=403)

    name = (request.data.get('name') or '').strip()
    code = (request.data.get('code') or '').strip()
    timezone_value = (request.data.get('timezone') or 'Asia/Ho_Chi_Minh').strip() or 'Asia/Ho_Chi_Minh'
    model_value = (request.data.get('camera_model') or Camera.Model.GENERIC).strip() or Camera.Model.GENERIC
    site_id = (request.data.get('site_id') or '').strip()

    if not name:
        return Response({'detail': 'name required'}, status=400)
    if not code:
        # Tự sinh mã camera thân thiện, duy nhất (CAM-XXXXXX)
        import secrets as _secrets
        _alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        for _ in range(20):
            candidate = 'CAM-' + ''.join(_secrets.choice(_alphabet) for _ in range(6))
            if not Camera.objects.filter(code=candidate).exists():
                code = candidate
                break
        else:
            return Response({'detail': 'Không sinh được mã camera'}, status=500)
    elif Camera.objects.filter(code=code).exists():
        return Response({'detail': 'Camera code already exists'}, status=400)

    valid_models = {value for value, _ in Camera.Model.choices}
    if model_value not in valid_models:
        return Response({'detail': 'Invalid camera_model'}, status=400)

    site = None
    if site_id:
        try:
            site = Site.objects.get(pk=site_id)
        except Site.DoesNotExist:
            return Response({'detail': 'Site not found'}, status=404)
        # Client admin chỉ được thêm camera vào site thuộc client của mình
        if not request.user.is_staff:
            membership = get_user_membership(request.user)
            if not membership or site.client_id != membership.client_id:
                return Response({'detail': 'Site không thuộc client của bạn'}, status=403)

    cam = Camera.objects.create(
        name=name,
        code=code,
        timezone=timezone_value,
        camera_model=model_value,
        site=site,
    )

    CameraDevice.objects.get_or_create(camera=cam)
    CameraSettings.objects.get_or_create(camera=cam)

    # Tự sinh credential ngay — raw_secret chỉ hiện 1 lần
    cred, raw_secret = CameraCredential.generate_credential(cam)

    # Đăng ký MQTT — ghi lại kết quả thật vào response (không bắt lỗi im lặng)
    mqtt_ok = False
    mqtt_errors = []
    try:
        from mqtt_service import device_manager
        result = device_manager.ensure_device_registered(cam)
        mqtt_ok = result['ok']
        mqtt_errors = result['errors']
    except Exception as exc:
        mqtt_errors = [str(exc)]

    broker_host = request.META.get('HTTP_HOST', 'localhost').split(':')[0] if request else 'localhost'
    server_base = request.build_absolute_uri('/').rstrip('/') if request else 'http://localhost'

    resp = camera_to_dict(cam, request)
    resp['simconfig'] = {
        'CAMERA_CODE':   cam.code,
        'MQTT_PASSWORD': cam.mqtt_password,
        'MQTT_BROKER':   broker_host,
        'MQTT_PORT':     1883,
        'SERVER_BASE':   server_base,
    }
    resp['mqtt'] = {
        'registered': mqtt_ok,
        'errors': mqtt_errors,
        'note': 'Nếu lỗi MQTT, vào camera modal → nút "Re-register MQTT" để thử lại.',
    }

    return Response(resp, status=201)


@api_view(['GET', 'PATCH', 'DELETE'])
def api_camera_detail(request, pk):
    try:
        cam = Camera.objects.select_related('site').get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if request.method == 'GET':
        if not user_can_access_camera(request.user, cam):
            return Response({'detail': 'Permission denied'}, status=403)

        try:
            dev = cam.device
        except Exception:

            dev = None
        return Response(camera_to_dict(cam, request))

    if request.method == 'PATCH':
        if not cam.is_editable_by(request.user):
            return Response({'detail': 'Permission denied'}, status=403)
        # Validate status against allowed choices
        if 'status' in request.data:
            valid_statuses = {c[0] for c in Camera.Status.choices}
            if request.data['status'] not in valid_statuses:
                return Response({'detail': f'Invalid status. Choose from: {sorted(valid_statuses)}'}, status=400)
        # Validate name length
        if 'name' in request.data and len(str(request.data.get('name', ''))) > 255:
            return Response({'detail': 'name too long (max 255)'}, status=400)
        # Validate camera_model against allowed choices
        if 'camera_model' in request.data:
            valid_models = {value for value, _ in Camera.Model.choices}
            if request.data['camera_model'] not in valid_models:
                return Response({'detail': 'Invalid camera_model'}, status=400)
        # Gán / gỡ công trình (site). 1 camera thuộc 0 hoặc 1 site.
        if 'site_id' in request.data:
            site_id = request.data.get('site_id')
            if not site_id:
                cam.site = None
            else:
                try:
                    new_site = Site.objects.get(pk=site_id)
                except Site.DoesNotExist:
                    return Response({'detail': 'Site not found'}, status=404)
                # Client admin chỉ gán vào site thuộc client của mình
                if not request.user.is_staff:
                    membership = get_user_membership(request.user)
                    if not membership or new_site.client_id != membership.client_id:
                        return Response({'detail': 'Site không thuộc client của bạn'}, status=403)
                cam.site = new_site
        # Validate code uniqueness if changing code
        if 'code' in request.data and request.data['code'] != cam.code:
            new_code = str(request.data['code']).strip()
            if not new_code:
                return Response({'detail': 'Mã camera không được để trống'}, status=400)
            if Camera.objects.filter(code=new_code).exclude(pk=cam.pk).exists():
                return Response({'detail': 'Mã camera đã tồn tại trên hệ thống'}, status=400)
            cam.code = new_code

        for field in ['name', 'mqtt_password', 'status', 'timezone', 'camera_model']:
            if field in request.data:
                setattr(cam, field, request.data[field])
        cam.save()
        return Response(camera_to_dict(cam, request))

    if request.method == 'DELETE':
        if not cam.is_editable_by(request.user):
            return Response({'detail': 'Permission denied'}, status=403)
        cam.delete()
        return Response(status=204)


@api_view(['GET'])
def api_camera_live_latest(request, pk):
    try:
        cam = Camera.objects.get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if not user_can_access_camera(request.user, cam):
        return Response({'detail': 'Permission denied'}, status=403)

    media = Media.objects.filter(camera=cam).order_by('-taken_at').first()
    today = timezone.now().date()
    today_count = Media.objects.filter(camera=cam, taken_at__date=today).count()
    total_count = Media.objects.filter(camera=cam).count()

    if not media:
        return Response({'photo': None, 'today_count': today_count, 'total_count': total_count})

    from core.utils import storage as _storage
    try:
        thumb_url = _storage.presigned_get_url(media.effective_thumb_key, expire=3600)
    except Exception:
        thumb_url = None
    try:
        view_url = _storage.presigned_get_url(media.s3_key, expire=3600)
    except Exception:
        view_url = None

    return Response({'photo': {
        'id': str(media.id),
        'taken_at': media.taken_at.isoformat(),
        'thumb_url': thumb_url,
        'view_url': view_url,
        'size_bytes': media.size_bytes,
        'width': media.width,
        'height': media.height,
    }, 'today_count': today_count, 'total_count': total_count})


@api_view(['GET'])
def api_camera_device(request, pk):
    try:
        cam = Camera.objects.get(pk=pk)
        dev = cam.device
    except (Camera.DoesNotExist, Exception):
        return Response({'detail': 'Not found'}, status=404)

    if not user_can_access_camera(request.user, cam):
        return Response({'detail': 'Permission denied'}, status=403)

    return Response(device_to_dict(dev))


@api_view(['PATCH'])
def api_camera_device_update(request, pk):
    """Update CameraDevice fields (capture_interval_sec, etc.)"""
    try:
        cam = Camera.objects.get(pk=pk)
        dev = cam.device
    except (Camera.DoesNotExist, Exception):
        return Response({'detail': 'Not found'}, status=404)

    if not cam.is_editable_by(request.user):
        return Response({'detail': 'Permission denied'}, status=403)

    updates = []
    if 'capture_interval_sec' in request.data:
        try:
            interval = int(request.data['capture_interval_sec'])
        except (TypeError, ValueError):
            return Response({'detail': 'Invalid capture_interval_sec'}, status=400)
        if not (30 <= interval <= 86400):
            return Response({'detail': 'capture_interval_sec must be between 30 and 86400'}, status=400)
        dev.capture_interval_sec = interval
        updates.append('capture_interval_sec')

    if not updates:
        return Response({'detail': 'No updatable fields provided'}, status=400)

    dev.save(update_fields=updates)

    try:
        from mqtt_service import config_publisher

        if 'capture_interval_sec' in updates:
            config_publisher.push_interval(cam.code, dev.capture_interval_sec)
    except Exception:
        pass

    return Response(device_to_dict(dev))


@api_view(['GET', 'POST'])
def api_camera_credentials(request, pk):
    """GET: xem danh sách credentials (key_id, không có secret).
    POST: tạo credential mới và trả về secret 1 lần duy nhất (admin only).
    """
    if not request.user.is_staff:
        return Response({'detail': 'Admin only'}, status=403)
    try:
        cam = Camera.objects.get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if request.method == 'POST':
        cred, raw_secret = CameraCredential.generate_credential(cam)
        return Response({
            'camera_code': cam.code,
            'device_key': cred.key_id,
            'device_secret': raw_secret,       # Chỉ hiện 1 lần!
            'mqtt_password': cam.mqtt_password,
            'mqtt_broker': 'localhost',
            'mqtt_port': 1884,
            'server_base': 'http://localhost',
            'note': 'Lưu device_secret ngay — sau này không lấy lại được.',
        }, status=201)

    # GET: liệt kê credentials hiện có (không có secret)
    creds = CameraCredential.objects.filter(camera=cam).order_by('-created_at')
    return Response({
        'camera_code': cam.code,
        'mqtt_password': cam.mqtt_password,
        'credentials': [
            {'key_id': c.key_id, 'status': c.status, 'created_at': c.created_at.isoformat()}
            for c in creds
        ],
    })


@api_view(['GET'])
def api_camera_simconfig(request, pk):
    """Trả về đầy đủ thông số sim.py theo môi trường nginx/local."""
    if not request.user.is_staff:
        return Response({'detail': 'Admin only'}, status=403)
    try:
        cam = Camera.objects.get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    host = request.META.get('HTTP_HOST', 'localhost').split(':')[0] if request else 'localhost'
    server_base = request.build_absolute_uri('/').rstrip('/') if request else 'http://localhost'

    return Response({
        'CAMERA_CODE':   cam.code,
        'MQTT_PASSWORD': cam.mqtt_password,
        'MQTT_BROKER':   host,
        'MQTT_PORT':     1883,
        'SERVER_BASE':   server_base,
    })


@api_view(['POST'])
def api_camera_power_on_cm4(request, pk):
    """Gửi lệnh MQTT 'power_on_cm4' tới ESP32-S3 để cưỡng bức bật nguồn CM4."""
    try:
        cam = Camera.objects.get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if not cam.is_editable_by(request.user):
        return Response({'detail': 'Permission denied'}, status=403)

    from mqtt_service import publisher
    try:
        publisher.publish_cmd(cam.code, "power_on_cm4", {})
        dev, _ = CameraDevice.objects.get_or_create(camera=cam)
        dev.cm4_power_state = "powering_on"
        dev.save(update_fields=["cm4_power_state", "updated_at"])
        return Response({"ok": True, "cm4_power_state": "powering_on"})
    except Exception as exc:
        return Response({"detail": f"Không thể gửi lệnh bật CM4: {exc}"}, status=500)


@api_view(['POST'])
def api_camera_power_off_cm4(request, pk):
    """Gửi lệnh MQTT 'power_off_cm4' tới ESP32-S3 để tắt CM4 và khôi phục chu kỳ tự động."""
    try:
        cam = Camera.objects.get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if not cam.is_editable_by(request.user):
        return Response({'detail': 'Permission denied'}, status=403)

    from mqtt_service import publisher
    try:
        publisher.publish_cmd(cam.code, "power_off_cm4", {})
        dev, _ = CameraDevice.objects.get_or_create(camera=cam)
        dev.cm4_power_state = "shutting_down"
        dev.save(update_fields=["cm4_power_state", "updated_at"])
        return Response({"ok": True, "cm4_power_state": "shutting_down"})
    except Exception as exc:
        return Response({"detail": f"Không thể gửi lệnh tắt CM4: {exc}"}, status=500)



@api_view(['GET', 'POST'])
def api_camera_mqtt_register(request, pk):
    """GET: kiểm tra trạng thái MQTT của camera trên broker.
    POST: đăng ký / sửa ACL trên broker (idempotent — gọi lại được).
    """
    if not request.user.is_staff:
        return Response({'detail': 'Admin only'}, status=403)
    try:
        cam = Camera.objects.get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    from mqtt_service import device_manager

    if request.method == 'GET':
        status = device_manager.get_device_status(cam.code)
        return Response({
            'camera_code': cam.code,
            'mqtt_registered': status.get('registered', False),
            'in_group': status.get('in_group', False),
            'groups': status.get('groups', []),
            'roles': status.get('roles', []),
            'ready': status.get('registered', False) and status.get('in_group', False),
        })

    # POST: ensure fully registered
    result = device_manager.ensure_device_registered(cam)
    return Response({
        'camera_code': cam.code,
        'ok': result['ok'],
        'errors': result['errors'],
        'mqtt_registered': result['status'].get('registered', False),
        'in_group': result['status'].get('in_group', False),
        'ready': result['ok'],
    }, status=200 if result['ok'] else 207)


@api_view(['GET', 'PATCH'])
def api_camera_settings(request, pk):
    """Get or update CameraSettings (ISO, aperture, etc.) for a camera."""
    try:
        cam = Camera.objects.get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if not user_can_access_camera(request.user, cam):
        return Response({'detail': 'Permission denied'}, status=403)

    settings, _ = CameraSettings.objects.get_or_create(camera=cam)

    if request.method == 'PATCH':
        if not cam.is_editable_by(request.user):
            return Response({'detail': 'Permission denied'}, status=403)
        updatable = [
            'iso', 'aperture', 'shutter_speed', 'exposure_compensation',
            'autofocus', 'focus_mode', 'image_format', 'image_size',
            'white_balance', 'capture_mode', 'capture_target',
            'high_iso_nr', 'long_exp_nr', 'liveview_af',
        ]
        changed = []
        for field in updatable:
            if field in request.data:
                setattr(settings, field, request.data[field])
                changed.append(field)
        if changed:
            settings.save(update_fields=changed + ['updated_at'] if hasattr(settings, 'updated_at') else changed)

    # Merge synced capabilities with static profile choices (fallback khi camera offline)
    from core.camera_specs import get_spec
    profile = get_spec(cam.camera_model or 'generic')
    profile_fields = profile.get('fields', {})
    synced_caps = settings.capabilities or {}

    merged_caps = {}
    for field_name, fdef in profile_fields.items():
        synced = synced_caps.get(field_name, {})
        static_choices = fdef.get('choices', [])
        merged_caps[field_name] = {
            'label': fdef.get('label', field_name),
            'type': fdef.get('type', 'RADIO'),
            'choices': synced.get('choices') or static_choices,
            'writable': synced.get('writable', not fdef.get('readonly', False)),
            'current': synced.get('current', ''),
            'readonly': fdef.get('readonly', False),
            'dynamic': fdef.get('dynamic', False),
            'note': fdef.get('note', ''),
        }
    # Also keep any live-synced fields not in static profile
    for field_name, synced in synced_caps.items():
        if field_name not in merged_caps:
            merged_caps[field_name] = synced

    return Response({
        'iso': settings.iso,
        'aperture': settings.aperture,
        'shutter_speed': settings.shutter_speed,
        'exposure_compensation': settings.exposure_compensation,
        'exposure_mode': settings.exposure_mode,
        'autofocus': settings.autofocus,
        'focus_mode': settings.focus_mode,
        'focus_switch': settings.focus_switch,
        'image_format': settings.image_format,
        'image_size': settings.image_size,
        'white_balance': settings.white_balance,
        'capture_mode': settings.capture_mode,
        'capture_target': settings.capture_target,
        'high_iso_nr': settings.high_iso_nr,
        'long_exp_nr': settings.long_exp_nr,
        'liveview_af': settings.liveview_af,
        'capabilities': merged_caps,
        'profile_settable': profile.get('settable', []),
        'profile_defaults_day': profile.get('defaults_day', {}),
        'applied': settings.applied or {},
        'in_sync': getattr(settings, 'in_sync', False),
        'last_synced_at': settings.last_synced_at.isoformat() if settings.last_synced_at else None,
    })


@api_view(['GET', 'POST'])
def api_sites(request):
    """List or create sites."""
    if request.method == 'GET':
        sites = filter_sites_for_user(request.user).order_by('name')
        return Response([{'id': str(s.id), 'name': s.name, 'description': s.description,
                          'location': getattr(s, 'location', ''),
                          'client_id': str(s.client_id) if s.client_id else None,
                          'cam_count': s.cameras.count()} for s in sites])

    # POST — chỉ superadmin hoặc client admin
    if not (request.user.is_staff or is_client_admin(request.user)):
        return Response({'detail': 'Permission denied'}, status=403)

    name = request.data.get('name', '').strip()
    if not name:
        return Response({'detail': 'name required'}, status=400)
    site = Site.objects.create(
        name=name,
        description=request.data.get('description', ''),
        location=request.data.get('location', ''),
    )
    # Client admin tạo site -> tự động gán vào client của mình
    if not request.user.is_staff:
        membership = get_user_membership(request.user)
        if membership:
            site.client = membership.client
            site.save(update_fields=['client', 'updated_at'])
    return Response({'id': str(site.id), 'name': site.name}, status=201)


@api_view(['GET', 'PATCH', 'DELETE'])
def api_site_detail(request, pk):
    """Chi tiết, cập nhật hoặc xoá 1 Site (Dự án)."""
    try:
        site = Site.objects.get(pk=pk)
    except Site.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if not (request.user.is_staff or is_client_admin(request.user)):
        return Response({'detail': 'Permission denied'}, status=403)

    if request.method == 'GET':
        return Response({
            'id': str(site.id),
            'name': site.name,
            'description': site.description,
            'location': getattr(site, 'location', ''),
            'client_id': str(site.client_id) if site.client_id else None,
            'cam_count': site.cameras.count(),
        })

    elif request.method == 'PATCH':
        if 'name' in request.data:
            site.name = str(request.data['name']).strip()
        if 'description' in request.data:
            site.description = str(request.data['description'])
        if 'location' in request.data:
            site.location = str(request.data['location'])
        site.save()
        return Response({'ok': True, 'name': site.name})

    elif request.method == 'DELETE':
        site.delete()
        return Response({'ok': True, 'detail': 'Site deleted successfully'})


# ─────────────────────────────────────────────
# Camera Access Management
# ─────────────────────────────────────────────




# ─────────────────────────────────────────────
# Media Gallery
# ─────────────────────────────────────────────

@api_view(['GET'])
def api_media_gallery(request, camera_pk):
    try:
        cam = Camera.objects.select_related('site').get(pk=camera_pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if not user_can_access_camera(request.user, cam):
        return Response({'detail': 'Permission denied'}, status=403)

    qs = Media.objects.filter(camera=cam).order_by('-taken_at')

    # Lọc theo ngày đơn (dt_from/dt_to ưu tiên hơn day)
    day    = request.query_params.get('day', '')
    dt_from = request.query_params.get('dt_from', '').strip()
    dt_to   = request.query_params.get('dt_to', '').strip()

    if dt_from or dt_to:
        from datetime import datetime as _dt
        def _parse_dt(v):
            if not v:
                return None
            try:
                d = _dt.fromisoformat(v)
            except ValueError:
                return None
            if timezone.is_naive(d):
                d = timezone.make_aware(d, timezone.get_current_timezone())
            return d
        d_from = _parse_dt(dt_from)
        d_to   = _parse_dt(dt_to)
        if d_from:
            qs = qs.filter(taken_at__gte=d_from)
        if d_to:
            qs = qs.filter(taken_at__lte=d_to)
    elif day:
        qs = qs.filter(taken_at__date=day)

    # Day stats
    day_stats = []
    for ds in MediaDayStat.objects.filter(camera=cam).order_by('-day')[:90]:
        day_stats.append({
            'day': ds.day.isoformat(),
            'date_label': ds.day.strftime('%d/%m'),
            'count': ds.count,
            'cover_thumb_url': None,
        })

    paginator = PageNumberPagination()
    paginator.page_size = 60
    page = paginator.paginate_queryset(qs, request)

    from core.utils import storage as _storage
    photos = []
    for m in page:
        photos.append({
            'id': str(m.id),
            'taken_at': m.taken_at.isoformat(),
            'size_bytes': m.size_bytes,
            'width': m.width,
            'height': m.height,
            'thumb_url': _storage.presigned_get_url_cached(m.effective_thumb_key, expire=3600) or '',
            'view_url': _storage.presigned_get_url_cached(m.s3_key, expire=3600) or '',
        })

    resp = paginator.get_paginated_response(photos)
    resp.data.update({
        'camera_name': cam.name,
        'camera_code': cam.code,
        'site_name': cam.site.name if cam.site else '',
        'total_count': Media.objects.filter(camera=cam).count(),
        'day_stats': day_stats,
    })
    return resp


# ─────────────────────────────────────────────
# Video Renders
# ─────────────────────────────────────────────

@api_view(['GET'])
def api_renders(request):
    from core.utils import storage as _st
    if request.user.is_staff:
        qs = VideoRender.objects.select_related('camera').order_by('-created_at')
    else:
        membership = get_user_membership(request.user)
        if membership:
            qs = VideoRender.objects.select_related('camera').filter(
                Q(requested_by=request.user) |
                Q(camera__site__client=membership.client)
            ).distinct().order_by('-created_at')
        else:
            qs = VideoRender.objects.select_related('camera').filter(
                requested_by=request.user
            ).order_by('-created_at')
    camera_id = request.query_params.get('camera', '')
    if camera_id:
        qs = qs.filter(camera_id=camera_id)
    paginator = PageNumberPagination()
    paginator.page_size = 50
    page = paginator.paginate_queryset(qs, request)
    data = []
    for r in page:
        download_url = None
        stream_url = None
        if r.status == 'ready' and r.output_key:
            try:
                fname = f"{r.camera.code}_{r.date_from}_{r.date_to}.mp4"
                download_url = _st.presigned_get_url(r.output_key, expire=3600, download_name=fname)
                stream_url = _st.presigned_get_url(r.output_key, expire=3600, inline_content_type='video/mp4')
            except Exception:
                download_url = None
        data.append({
            'id': str(r.id),
            'camera_id': str(r.camera_id),
            'camera_code': r.camera.code,
            'camera_name': r.camera.name,
            'status': r.status,
            'progress': r.progress or 0,
            'item_count': r.item_count or 0,
            'size_bytes': r.size_bytes,
            'download_url': download_url,
            'stream_url': stream_url,
            'error': r.error or '',
            'date_from': str(r.date_from),
            'date_to': str(r.date_to),
            'fps': r.fps,
            'resolution': r.resolution,
            'frame_interval': r.frame_interval,
            'created_at': r.created_at.isoformat(),
            'ready_at': r.ready_at.isoformat() if r.ready_at else None,
        })
    return paginator.get_paginated_response(data)


@api_view(['POST'])
def api_render_create(request, camera_pk):
    """Tạo render job qua API v1 (JSON body)."""
    try:
        cam = Camera.objects.get(pk=camera_pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not cam.is_accessible_by(request.user):
        return Response({'detail': 'Permission denied'}, status=403)

    date_from = request.data.get('date_from')
    date_to = request.data.get('date_to')
    if not date_from or not date_to:
        return Response({'detail': 'date_from và date_to là bắt buộc'}, status=400)

    # Validate date format and range limit (prevent resource exhaustion)
    from datetime import date as _date
    try:
        d_from = _date.fromisoformat(str(date_from))
        d_to   = _date.fromisoformat(str(date_to))
    except ValueError:
        return Response({'detail': 'date_from/date_to không đúng định dạng YYYY-MM-DD'}, status=400)
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
    from django.db import transaction
    from core.tasks import render_timelapse_video
    transaction.on_commit(lambda: render_timelapse_video.delay(str(vr.id)))
    return Response({'ok': True, 'render_id': str(vr.id)}, status=201)


@api_view(['DELETE'])
def api_render_detail(request, pk):
    """Xoá 1 video render (job + file output)."""
    try:
        vr = VideoRender.objects.select_related('camera').get(pk=pk)
    except VideoRender.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    # Quyền: superadmin, người tạo, hoặc quản lý được camera
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


@api_view(['POST'])
def api_archive_create(request, camera_pk):
    """Tạo job nén ZIP ảnh (theo ids hoặc khoảng ngày/giờ) qua API v1."""
    from datetime import datetime as _dt
    from core.models.media import MediaArchive
    from core.tasks import build_media_archive

    try:
        cam = Camera.objects.get(pk=camera_pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not cam.is_media_downloadable_by(request.user):
        return Response({'detail': 'Permission denied'}, status=403)

    ids = request.data.get('ids') or []
    # Giới hạn số lượng ảnh — chặn DoS/resource exhaustion
    MAX_ARCHIVE_IDS = 5000
    if len(ids) > MAX_ARCHIVE_IDS:
        return Response({'detail': f'Tối đa {MAX_ARCHIVE_IDS} ảnh mỗi lần'}, status=400)
    date_from_raw = request.data.get('date_from') or ''
    date_to_raw = request.data.get('date_to') or ''

    def _parse(v):
        if not v:
            return None
        try:
            dt = _dt.fromisoformat(str(v))
        except ValueError:
            return None
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt

    date_from = _parse(date_from_raw)
    date_to = _parse(date_to_raw)

    if not ids and not (date_from or date_to):
        return Response({'detail': 'Chọn ảnh hoặc khoảng thời gian'}, status=400)

    # 1. Tính toán số lượng ảnh thực tế khớp với bộ lọc
    qs = Media.objects.filter(camera=cam)
    if ids:
        qs = qs.filter(pk__in=ids)
    if date_from:
        qs = qs.filter(taken_at__gte=date_from)
    if date_to:
        qs = qs.filter(taken_at__lte=date_to)

    photo_count = qs.count()
    if photo_count == 0:
        return Response({'detail': 'Không tìm thấy ảnh nào khớp với khoảng thời gian đã chọn'}, status=400)

    max_items = getattr(settings, "MEDIA_ARCHIVE_MAX_ITEMS", 5000)
    if photo_count > max_items:
        return Response({
            'detail': f'Bộ lọc đã chọn chứa {photo_count} ảnh, vượt quá giới hạn tối đa {max_items} ảnh cho mỗi lần tải về. Vui lòng thu hẹp khoảng thời gian.'
        }, status=400)

    # 2. Phân loại độ ưu tiên (9: Cao, 5: Trung bình, 2: Thấp)
    if photo_count <= 100:
        priority = 9
    elif photo_count <= 1000:
        priority = 5
    else:
        priority = 2

    archive = MediaArchive.objects.create(
        requested_by=request.user, camera=cam,
        media_ids=[str(i) for i in ids],
        date_from=date_from, date_to=date_to,
        item_count=photo_count,
    )
    build_media_archive.apply_async(args=[str(archive.id)], priority=priority)
    return Response({'ok': True, 'id': str(archive.id)}, status=201)


@api_view(['DELETE'])
def api_archive_detail(request, pk):
    """Xoá 1 ZIP archive (job + file zip)."""
    from core.models.media import MediaArchive
    try:
        archive = MediaArchive.objects.select_related('camera').get(pk=pk)
    except MediaArchive.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not (request.user.is_staff or archive.requested_by_id == request.user.id
            or archive.camera.is_editable_by(request.user)):
        return Response({'detail': 'Permission denied'}, status=403)
    if getattr(archive, 'zip_key', None):
        try:
            from core.utils import storage as _st
            _st.delete_key(archive.zip_key)
        except Exception:
            pass
    archive.delete()
    return Response(status=204)


@api_view(['GET'])
def api_downloads(request):
    """Trung tâm tải xuống: gộp video renders + ZIP archives của user."""
    from core.models.media import MediaArchive
    from core.utils import storage as _st

    items = []

    if request.user.is_staff:
        renders = VideoRender.objects.select_related('camera').order_by('-created_at')[:50]
        archives = MediaArchive.objects.select_related('camera').order_by('-created_at')[:50]
    else:
        renders = VideoRender.objects.select_related('camera').filter(
            requested_by=request.user).order_by('-created_at')[:50]
        archives = MediaArchive.objects.select_related('camera').filter(
            requested_by=request.user).order_by('-created_at')[:50]

    for r in renders:
        url = None
        if r.status == 'ready' and r.output_key:
            try:
                url = _st.presigned_get_url(r.output_key, expire=3600,
                                            download_name=f"{r.camera.code}_{r.date_from}_{r.date_to}.mp4")
            except Exception:
                url = None
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
        if a.status == 'ready' and a.zip_key:
            try:
                url = _st.presigned_get_url(a.zip_key, expire=3600,
                                            download_name=f"{a.camera.code}_photos.zip")
            except Exception:
                url = None
        items.append({
            'id': str(a.id), 'kind': 'archive',
            'camera_code': a.camera.code, 'camera_name': a.camera.name,
            'title': f"ZIP {a.item_count or len(a.media_ids or [])} ảnh",
            'status': a.status, 'progress': getattr(a, 'progress', 0) or 0,
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


# ─────────────────────────────────────────────
# Clients
# ─────────────────────────────────────────────

@api_view(['GET', 'POST'])
def api_clients(request):
    membership = get_user_membership(request.user)
    if request.method == 'GET':
        # Superadmin thấy tất cả; client admin thấy client của mình; member không thấy
        if request.user.is_staff:
            clients = Client.objects.prefetch_related('projects__cameras').all()
        elif membership and membership.role == 'admin':
            clients = Client.objects.prefetch_related('projects__cameras').filter(pk=membership.client_id)
        else:
            return Response({'results': [], 'count': 0})
        data = []
        for c in clients:
            projects = []
            for p in c.projects.all():
                cams = [{'id': str(cm.id), 'code': cm.code, 'name': cm.name, 'status': cm.status}
                        for cm in p.cameras.all()]
                projects.append({'id': str(p.id), 'name': p.name, 'location': p.location,
                                 'start_date': str(p.start_date) if p.start_date else None,
                                 'end_date': str(p.end_date) if p.end_date else None,
                                 'cameras': cams, 'cam_count': len(cams)})
            data.append({
                'id': str(c.id), 'name': c.name,
                'contact_name': c.contact_name, 'contact_email': c.contact_email,
                'phone': c.phone, 'address': c.address, 'notes': c.notes,
                'projects': projects,
                'project_count': len(projects),
                'camera_count': sum(p['cam_count'] for p in projects),
                'created_at': c.created_at.isoformat(),
            })
        return Response({'results': data, 'count': len(data)})

    # POST: create client — chỉ superadmin
    if not request.user.is_staff:
        return Response({'detail': 'Permission denied'}, status=403)
    name = (request.data.get('name') or '').strip()
    if not name:
        return Response({'detail': 'name required'}, status=400)
    c = Client.objects.create(
        name=name,
        contact_name=request.data.get('contact_name', ''),
        contact_email=request.data.get('contact_email', ''),
        phone=request.data.get('phone', ''),
        address=request.data.get('address', ''),
        notes=request.data.get('notes', ''),
    )
    return Response({'id': str(c.id), 'name': c.name}, status=201)


def _can_manage_client(user, client):
    """True nếu user được quản lý client này (superadmin hoặc admin của chính client)."""
    if user.is_staff:
        return True
    m = get_user_membership(user)
    return bool(m and m.role == 'admin' and m.client_id == client.id)


@api_view(['GET', 'PATCH', 'DELETE'])
def api_client_detail(request, pk):
    try:
        c = Client.objects.prefetch_related('projects__cameras').get(pk=pk)
    except Client.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    # GET/PATCH/DELETE: chỉ superadmin hoặc user thuộc client này
    if not request.user.is_staff:
        membership = get_user_membership(request.user)
        if not membership or membership.client_id != c.id:
            return Response({'detail': 'Permission denied'}, status=403)

    if request.method == 'DELETE':
        # Xóa client chỉ superadmin
        if not request.user.is_staff:
            return Response({'detail': 'Permission denied'}, status=403)
        c.delete()
        return Response({'ok': True})

    if request.method == 'PATCH':
        if not _can_manage_client(request.user, c):
            return Response({'detail': 'Permission denied'}, status=403)
        # Validate email format
        if 'contact_email' in request.data and request.data['contact_email']:
            from django.core.validators import validate_email
            from django.core.exceptions import ValidationError as _VE
            try:
                validate_email(str(request.data['contact_email']))
            except _VE:
                return Response({'detail': 'contact_email không hợp lệ'}, status=400)
        for f in ('name', 'contact_name', 'contact_email', 'phone', 'address', 'notes'):
            if f in request.data:
                setattr(c, f, request.data[f])
        c.save()

    projects = []
    for p in c.projects.all():
        cams = [{'id': str(cm.id), 'code': cm.code, 'name': cm.name, 'status': cm.status}
                for cm in p.cameras.all()]
        projects.append({'id': str(p.id), 'name': p.name, 'location': p.location, 'cameras': cams})
    return Response({
        'id': str(c.id), 'name': c.name,
        'contact_name': c.contact_name, 'contact_email': c.contact_email,
        'phone': c.phone, 'address': c.address, 'notes': c.notes,
        'projects': projects,
    })


@api_view(['POST'])
def api_site_assign_client(request, pk):
    """Gán site (dự án) vào client. Body: {client_id: uuid|null}"""
    if not (request.user.is_staff or is_client_admin(request.user)):
        return Response({'detail': 'Permission denied'}, status=403)
    try:
        site = Site.objects.get(pk=pk)
    except Site.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    membership = get_user_membership(request.user)
    if not request.user.is_staff:
        if not membership or membership.role != 'admin':
            return Response({'detail': 'Permission denied'}, status=403)
        # Client admin chỉ được thao tác site thuộc client của mình hoặc site chưa gán.
        if site.client_id not in (None, membership.client_id):
            return Response({'detail': 'Permission denied'}, status=403)

    client_id = request.data.get('client_id')
    if client_id:
        if not request.user.is_staff and str(client_id) != str(membership.client_id):
            return Response({'detail': 'Permission denied'}, status=403)
        try:
            site.client = Client.objects.get(pk=client_id)
        except Client.DoesNotExist:
            return Response({'detail': 'Client not found'}, status=404)
    else:
        site.client = None
    site.save(update_fields=['client', 'updated_at'])
    return Response({'ok': True})


# ─────────────────────────────────────────────
# Client Membership (Phân tầng quyền — PERMISSION_ARCHITECTURE.md)
# ─────────────────────────────────────────────

def _membership_to_dict(m):
    return {
        'user_id': m.user_id,
        'username': m.user.username,
        'email': m.user.email or '',
        'full_name': getattr(getattr(m.user, 'profile', None), 'full_name', '') or '',
        'role': m.role,
        'can_download': m.can_download,
        'joined_at': m.joined_at.isoformat(),
    }


@api_view(['GET', 'POST'])
def api_client_members(request, pk):
    """
    GET  — danh sách member của client.
    POST — mời user vào client. Body: {username|user_id, role, can_download}
    """
    from core.models import ClientMembership
    try:
        client = Client.objects.get(pk=pk)
    except Client.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if not _can_manage_client(request.user, client):
        return Response({'detail': 'Permission denied'}, status=403)

    if request.method == 'GET':
        members = (
            ClientMembership.objects
            .select_related('user', 'user__profile')
            .filter(client=client)
            .order_by('-role', 'user__username')
        )
        return Response({'results': [_membership_to_dict(m) for m in members]})

    # POST — mời member
    user_id = request.data.get('user_id')
    username = (request.data.get('username') or '').strip()
    role = request.data.get('role', 'member')
    can_download = bool(request.data.get('can_download', True))

    if role not in ('admin', 'member'):
        return Response({'detail': 'role phải là admin hoặc member'}, status=400)

    # Client admin không được tạo admin khác (chỉ superadmin)
    if role == 'admin' and not request.user.is_staff:
        return Response({'detail': 'Chỉ superadmin mới cấp quyền admin'}, status=403)

    target = None
    if user_id:
        target = User.objects.filter(pk=user_id).first()
    elif username:
        target = User.objects.filter(username=username).first()
    if not target:
        return Response({'detail': 'Không tìm thấy user'}, status=404)

    if target.is_staff:
        return Response({'detail': 'Không thể gán superadmin vào client'}, status=400)

    # 1 user chỉ thuộc 1 client — chặn nếu đã thuộc client khác
    existing = ClientMembership.objects.filter(user=target).first()
    if existing and existing.client_id != client.id:
        return Response({'detail': f'User đã thuộc client khác ({existing.client.name})'}, status=400)

    m, created = ClientMembership.objects.update_or_create(
        user=target, client=client,
        defaults={'role': role, 'can_download': can_download, 'invited_by': request.user},
    )
    return Response(_membership_to_dict(m), status=201 if created else 200)


@api_view(['PATCH', 'DELETE'])
def api_client_member_detail(request, pk, user_id):
    """PATCH — đổi role/can_download. DELETE — gỡ member khỏi client."""
    from core.models import ClientMembership
    try:
        client = Client.objects.get(pk=pk)
    except Client.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if not _can_manage_client(request.user, client):
        return Response({'detail': 'Permission denied'}, status=403)

    m = (
        ClientMembership.objects
        .select_related('user', 'user__profile')
        .filter(client=client, user_id=user_id)
        .first()
    )
    if not m:
        return Response({'detail': 'Member không tồn tại'}, status=404)

    if request.method == 'DELETE':
        m.delete()
        return Response({'ok': True})

    # PATCH
    if 'role' in request.data:
        new_role = request.data['role']
        if new_role not in ('admin', 'member'):
            return Response({'detail': 'role không hợp lệ'}, status=400)
        if new_role == 'admin' and not request.user.is_staff:
            return Response({'detail': 'Chỉ superadmin mới cấp quyền admin'}, status=403)
        m.role = new_role
    if 'can_download' in request.data:
        m.can_download = bool(request.data['can_download'])
    m.save()
    return Response(_membership_to_dict(m))


# ─────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def api_users(request):
    if request.method == 'GET':
        qs = User.objects.select_related('profile').all().order_by('username')
        q = request.query_params.get('q', '')
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q))
        from core.models.permission import UserRole
        roles_map = {}
        for ur in UserRole.objects.select_related('role').filter(user__in=qs):
            roles_map.setdefault(ur.user_id, []).append({'id': str(ur.role.id), 'code': ur.role.code, 'name': ur.role.name})
        data = [{'id': u.id, 'username': u.username, 'email': u.email,
                 'full_name': getattr(getattr(u, 'profile', None), 'full_name', '') or '',
                 'is_staff': u.is_staff, 'is_active': u.is_active,
                 'roles': roles_map.get(u.id, []),
                 'date_joined': u.date_joined.isoformat()} for u in qs]
        return Response({'results': data, 'count': len(data)})

    # POST: create user
    username = (request.data.get('username') or '').strip()
    password = request.data.get('password') or ''
    if not username or not password:
        return Response({'detail': 'username và password là bắt buộc'}, status=400)
    if User.objects.filter(username=username).exists():
        return Response({'detail': 'Username đã tồn tại'}, status=400)
    if len(password) < 8:
        return Response({'detail': 'Password tối thiểu 8 ký tự'}, status=400)
    u = User.objects.create_user(
        username=username, password=password,
        email=request.data.get('email', ''),
        is_staff=bool(request.data.get('is_staff', False)),
    )
    full_name = (request.data.get('full_name') or '').strip()
    if full_name and hasattr(u, 'profile'):
        u.profile.full_name = full_name
        u.profile.save(update_fields=['full_name', 'updated_at'])
    # Gán role nếu có
    role_id = request.data.get('role_id')
    if role_id:
        from core.models.permission import Role, UserRole
        try:
            role = Role.objects.get(pk=role_id)
            UserRole.objects.get_or_create(user=u, role=role)
        except Role.DoesNotExist:
            pass
    return Response({'id': u.id, 'username': u.username}, status=201)


@api_view(['PATCH'])
@permission_classes([IsAdminUser])
def api_user_detail(request, pk):
    """Chỉnh sửa user (email, họ tên, quyền admin, đổi password, role)."""
    try:
        u = User.objects.get(pk=pk)
    except User.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if 'email' in request.data:
        email = (request.data.get('email') or '').strip()
        if email:
            from django.core.validators import validate_email
            from django.core.exceptions import ValidationError as _VE
            try:
                validate_email(email)
            except _VE:
                return Response({'detail': 'Email không hợp lệ'}, status=400)
        u.email = email
    if 'is_staff' in request.data:
        u.is_staff = bool(request.data.get('is_staff'))
    if 'is_active' in request.data:
        u.is_active = bool(request.data.get('is_active'))

    password = request.data.get('password')
    if password:
        if len(password) < 8:
            return Response({'detail': 'Password tối thiểu 8 ký tự'}, status=400)
        u.set_password(password)
    u.save()

    full_name = request.data.get('full_name')
    if full_name is not None and hasattr(u, 'profile'):
        u.profile.full_name = full_name.strip()
        u.profile.save(update_fields=['full_name', 'updated_at'])

    # Cập nhật role (thay thế toàn bộ role hiện tại bằng role_id nếu gửi lên)
    if 'role_id' in request.data:
        from core.models.permission import Role, UserRole
        UserRole.objects.filter(user=u).delete()
        role_id = request.data.get('role_id')
        if role_id:
            try:
                role = Role.objects.get(pk=role_id)
                UserRole.objects.get_or_create(user=u, role=role)
            except Role.DoesNotExist:
                pass

    return Response({'id': u.id, 'username': u.username})


@api_view(['GET'])
def api_user_search(request):
    """Search users cho autocomplete (mời member) — cần là client admin hoặc staff."""
    if not (request.user.is_staff or is_client_admin(request.user)):
        return Response({'results': []})
    q = (request.query_params.get('q') or '').strip()
    qs = User.objects.filter(is_active=True)
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q))
    qs = qs.order_by('username')[:10]
    return Response({'results': [
        {'id': u.id, 'username': u.username, 'email': u.email,
         'full_name': getattr(getattr(u, 'profile', None), 'full_name', '') or ''}
        for u in qs
    ]})


@api_view(['GET'])
def api_roles(request):
    """List roles cho user create form."""
    if not request.user.is_staff:
        return Response({'results': []})
    from core.models.permission import Role
    return Response({'results': [
        {'id': str(r.id), 'code': r.code, 'name': r.name}
        for r in Role.objects.all().order_by('name')
    ]})


@api_view(['POST'])
@permission_classes([IsAdminUser])
def api_user_toggle(request, pk):
    try:
        u = User.objects.get(pk=pk)
    except User.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    u.is_active = not u.is_active
    u.save()
    return Response({'is_active': u.is_active})


# ─────────────────────────────────────────────
# Alert Settings
# ─────────────────────────────────────────────

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
            'camera_id': str(cam.id),
            'camera_code': cam.code,
            'camera_name': cam.name,
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
    # Chỉ superadmin hoặc client admin của camera này được sửa
    if not request.user.is_staff:
        if not (is_client_admin(request.user) and user_can_access_camera(request.user, cam)):
            return Response({'detail': 'Permission denied'}, status=403)
    s, _ = AlertSettings.objects.get_or_create(camera=cam)

    # Validate notify_email
    if 'notify_email' in request.data and request.data['notify_email']:
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError as _VE
        try:
            validate_email(str(request.data['notify_email']))
        except _VE:
            return Response({'detail': 'notify_email không hợp lệ'}, status=400)

    # Validate numeric ranges trước khi setattr
    _ALERT_RANGES = {
        'battery_low_pct':      (0, 100),
        'battery_critical_pct': (0, 100),
        'signal_weak_dbm':      (-150, 0),
        'offline_minutes':      (1, 10080),   # tối đa 1 tuần
        'temperature_high_c':   (0, 100),
        'daily_photo_min':      (0, 10000),
    }
    for fname, (lo, hi) in _ALERT_RANGES.items():
        if fname in request.data:
            try:
                v = int(request.data[fname])
            except (TypeError, ValueError):
                return Response({'detail': f'{fname} phải là số nguyên'}, status=400)
            if not (lo <= v <= hi):
                return Response({'detail': f'{fname} phải trong khoảng [{lo}, {hi}]'}, status=400)

    for field in ['enabled', 'battery_low_pct', 'battery_critical_pct',
                  'signal_weak_dbm', 'offline_minutes', 'temperature_high_c',
                  'daily_photo_min', 'notify_email']:
        if field in request.data:
            setattr(s, field, request.data[field])
    s.save()
    return Response({'ok': True})
