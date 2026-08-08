import secrets as _secrets

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from core.models import Camera, CameraCredential, CameraDevice, CameraSettings, Media, Site
from ._helpers import (
    camera_to_dict, device_to_dict, filter_cameras_for_user,
    get_user_membership, is_client_admin, user_can_access_camera,
)


@api_view(['GET', 'POST'])
def api_cameras(request):
    if request.method == 'GET':
        qs = filter_cameras_for_user(
            request.user,
            Camera.objects.select_related('site', 'site__client').prefetch_related('device'),
        ).order_by('code')
        q = request.query_params.get('q', '')
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) |
                           Q(site__name__icontains=q) | Q(site__client__name__icontains=q))
        if request.query_params.get('status'):
            qs = qs.filter(status=request.query_params['status'])
        if request.query_params.get('site'):
            qs = qs.filter(site_id=request.query_params['site'])
        pg = PageNumberPagination(); pg.page_size = 200
        page = pg.paginate_queryset(qs, request)
        return pg.get_paginated_response([camera_to_dict(c, request) for c in page])

    if not (request.user.is_staff or is_client_admin(request.user)):
        return Response({'detail': 'Permission denied'}, status=403)

    name = (request.data.get('name') or '').strip()
    code = (request.data.get('code') or '').strip()
    tz_val = (request.data.get('timezone') or 'Asia/Ho_Chi_Minh').strip() or 'Asia/Ho_Chi_Minh'
    model_val = (request.data.get('camera_model') or Camera.Model.GENERIC).strip() or Camera.Model.GENERIC
    site_id = (request.data.get('site_id') or '').strip()

    if not name:
        return Response({'detail': 'name required'}, status=400)
    if not code:
        _alpha = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        for _ in range(20):
            candidate = 'CAM-' + ''.join(_secrets.choice(_alpha) for _ in range(6))
            if not Camera.objects.filter(code=candidate).exists():
                code = candidate; break
        else:
            return Response({'detail': 'Không sinh được mã camera'}, status=500)
    elif Camera.objects.filter(code=code).exists():
        return Response({'detail': 'Camera code already exists'}, status=400)

    if model_val not in {v for v, _ in Camera.Model.choices}:
        model_val = Camera.Model.GENERIC

    site = None
    if site_id:
        try:
            site = Site.objects.get(pk=site_id)
        except Site.DoesNotExist:
            return Response({'detail': 'Site not found'}, status=404)
        if not request.user.is_staff:
            m = get_user_membership(request.user)
            if not m or site.client_id != m.client_id:
                return Response({'detail': 'Site không thuộc client của bạn'}, status=403)

    cam = Camera.objects.create(name=name, code=code, timezone=tz_val, camera_model=model_val, site=site)
    CameraDevice.objects.get_or_create(camera=cam)
    CameraSettings.objects.get_or_create(camera=cam)

    mqtt_ok, mqtt_errors = False, []
    try:
        from mqtt_service import device_manager
        result = device_manager.ensure_device_registered(cam)
        mqtt_ok, mqtt_errors = result['ok'], result['errors']
    except Exception as exc:
        mqtt_errors = [str(exc)]

    broker_host = request.META.get('HTTP_HOST', 'localhost').split(':')[0]
    resp = camera_to_dict(cam, request)
    resp['simconfig'] = {
        'CAMERA_CODE': cam.code, 'MQTT_PASSWORD': cam.mqtt_password,
        'MQTT_BROKER': broker_host, 'MQTT_PORT': 1883,
        'SERVER_BASE': request.build_absolute_uri('/').rstrip('/'),
    }
    resp['mqtt'] = {'registered': mqtt_ok, 'errors': mqtt_errors,
                    'note': 'Nếu lỗi MQTT, vào camera modal → nút "Re-register MQTT" để thử lại.'}
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
        return Response(camera_to_dict(cam, request))

    if request.method == 'PATCH':
        if not cam.is_editable_by(request.user):
            return Response({'detail': 'Permission denied'}, status=403)
        if 'status' in request.data and request.data['status'] not in {c[0] for c in Camera.Status.choices}:
            return Response({'detail': 'Invalid status'}, status=400)
        if 'name' in request.data and len(str(request.data['name'])) > 255:
            return Response({'detail': 'name too long'}, status=400)
        if 'camera_model' in request.data:
            m_val = str(request.data['camera_model']).strip()
            if m_val in {v for v, _ in Camera.Model.choices}:
                cam.camera_model = m_val
            else:
                cam.camera_model = Camera.Model.GENERIC
        if 'site_id' in request.data:
            sid = request.data.get('site_id')
            if not sid:
                cam.site = None
            else:
                try:
                    new_site = Site.objects.get(pk=sid)
                except Site.DoesNotExist:
                    return Response({'detail': 'Site not found'}, status=404)
                if not request.user.is_staff:
                    m = get_user_membership(request.user)
                    if not m or new_site.client_id != m.client_id:
                        return Response({'detail': 'Site không thuộc client của bạn'}, status=403)
                cam.site = new_site
        if 'code' in request.data and request.data['code'] != cam.code:
            new_code = str(request.data['code']).strip()
            if not new_code:
                return Response({'detail': 'Mã camera không được để trống'}, status=400)
            if Camera.objects.filter(code=new_code).exclude(pk=cam.pk).exists():
                return Response({'detail': 'Mã camera đã tồn tại'}, status=400)
            cam.code = new_code
        for f in ['name', 'mqtt_password', 'status', 'timezone']:
            if f in request.data:
                setattr(cam, f, request.data[f])
        cam.save()
        return Response(camera_to_dict(cam, request))

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

    from core.utils import storage as _st
    fname = f"{cam.code}_{media.taken_at.strftime('%Y%m%d_%H%M%S')}.jpg"
    try:
        thumb_url = _st.presigned_get_url(media.effective_thumb_key, expire=3600)
    except Exception:
        thumb_url = None
    try:
        view_url = _st.presigned_get_url(media.s3_key, expire=3600)
    except Exception:
        view_url = None
    try:
        download_url = _st.presigned_get_url(media.s3_key, expire=3600, download_name=fname, storage=media.storage)
    except Exception:
        download_url = None

    return Response({'photo': {
        'id': str(media.id), 'taken_at': media.taken_at.isoformat(),
        'thumb_url': thumb_url, 'view_url': view_url, 'download_url': download_url or view_url,
        'size_bytes': media.size_bytes, 'width': media.width, 'height': media.height,
    }, 'today_count': today_count, 'total_count': total_count})


@api_view(['POST'])
def api_camera_live_start(request, pk):
    from core.views.camera import live_view_start
    return live_view_start(request._request, pk)


@api_view(['POST'])
def api_camera_live_stop(request, pk):
    from core.views.camera import live_view_stop
    return live_view_stop(request._request, pk)


@api_view(['GET'])
def api_camera_live_frame(request, pk):
    from core.views.camera import live_view_frame
    return live_view_frame(request._request, pk)


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
            return Response({'detail': 'capture_interval_sec must be 30–86400'}, status=400)
        dev.capture_interval_sec = interval
        updates.append('capture_interval_sec')

    if not updates:
        return Response({'detail': 'No updatable fields'}, status=400)
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
    if not request.user.is_staff:
        return Response({'detail': 'Admin only'}, status=403)
    try:
        cam = Camera.objects.get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)

    if request.method == 'POST':
        cred, raw_secret = CameraCredential.generate_credential(cam)
        return Response({
            'camera_code': cam.code, 'device_key': cred.key_id,
            'device_secret': raw_secret, 'mqtt_password': cam.mqtt_password,
            'note': 'Lưu device_secret ngay — sau này không lấy lại được.',
        }, status=201)

    creds = CameraCredential.objects.filter(camera=cam).order_by('-created_at')
    return Response({'camera_code': cam.code, 'mqtt_password': cam.mqtt_password,
                     'credentials': [{'key_id': c.key_id, 'status': c.status,
                                      'created_at': c.created_at.isoformat()} for c in creds]})


@api_view(['GET'])
def api_camera_simconfig(request, pk):
    if not request.user.is_staff:
        return Response({'detail': 'Admin only'}, status=403)
    try:
        cam = Camera.objects.get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    return Response({
        'CAMERA_CODE': cam.code, 'MQTT_PASSWORD': cam.mqtt_password,
        'MQTT_BROKER': request.META.get('HTTP_HOST', 'localhost').split(':')[0],
        'MQTT_PORT': 1883,
        'SERVER_BASE': request.build_absolute_uri('/').rstrip('/'),
    })


@api_view(['POST'])
def api_camera_power_on_cm4(request, pk):
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
        return Response({"detail": str(exc)}, status=500)


@api_view(['POST'])
def api_camera_power_off_cm4(request, pk):
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
        return Response({"detail": str(exc)}, status=500)


@api_view(['GET', 'POST'])
def api_camera_mqtt_register(request, pk):
    if not request.user.is_staff:
        return Response({'detail': 'Admin only'}, status=403)
    try:
        cam = Camera.objects.get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    from mqtt_service import device_manager
    if request.method == 'GET':
        s = device_manager.get_device_status(cam.code)
        return Response({'camera_code': cam.code, 'mqtt_registered': s.get('registered', False),
                         'in_group': s.get('in_group', False), 'groups': s.get('groups', []),
                         'roles': s.get('roles', []), 'ready': s.get('registered') and s.get('in_group')})
    result = device_manager.ensure_device_registered(cam)
    return Response({'camera_code': cam.code, 'ok': result['ok'], 'errors': result['errors'],
                     'mqtt_registered': result['status'].get('registered', False),
                     'in_group': result['status'].get('in_group', False), 'ready': result['ok']},
                    status=200 if result['ok'] else 207)


@api_view(['GET', 'PATCH'])
def api_camera_settings(request, pk):
    try:
        cam = Camera.objects.get(pk=pk)
    except Camera.DoesNotExist:
        return Response({'detail': 'Not found'}, status=404)
    if not user_can_access_camera(request.user, cam):
        return Response({'detail': 'Permission denied'}, status=403)

    cam_settings, _ = CameraSettings.objects.get_or_create(camera=cam)
    if request.method == 'PATCH':
        if not cam.is_editable_by(request.user):
            return Response({'detail': 'Permission denied'}, status=403)
        updatable = ['iso', 'aperture', 'shutter_speed', 'exposure_compensation', 'autofocus',
                     'focus_mode', 'image_format', 'image_size', 'white_balance', 'capture_mode',
                     'capture_target', 'high_iso_nr', 'long_exp_nr', 'liveview_af']
        changed = [f for f in updatable if f in request.data and not setattr(cam_settings, f, request.data[f])]
        if changed:
            cam_settings.save(update_fields=changed + (['updated_at'] if hasattr(cam_settings, 'updated_at') else []))

    from core.camera_specs import get_spec
    profile = get_spec(cam.camera_model or 'generic')
    synced_caps = cam_settings.capabilities or {}
    merged_caps = {}
    for fname, fdef in profile.get('fields', {}).items():
        synced = synced_caps.get(fname, {})
        merged_caps[fname] = {
            'label': fdef.get('label', fname), 'type': fdef.get('type', 'RADIO'),
            'choices': synced.get('choices') or fdef.get('choices', []),
            'writable': synced.get('writable', not fdef.get('readonly', False)),
            'current': synced.get('current', ''), 'readonly': fdef.get('readonly', False),
            'dynamic': fdef.get('dynamic', False), 'note': fdef.get('note', ''),
        }
    for fname, synced in synced_caps.items():
        if fname not in merged_caps:
            merged_caps[fname] = synced

    return Response({
        'iso': cam_settings.iso, 'aperture': cam_settings.aperture,
        'shutter_speed': cam_settings.shutter_speed, 'exposure_compensation': cam_settings.exposure_compensation,
        'exposure_mode': cam_settings.exposure_mode, 'autofocus': cam_settings.autofocus,
        'focus_mode': cam_settings.focus_mode, 'focus_switch': cam_settings.focus_switch,
        'image_format': cam_settings.image_format, 'image_size': cam_settings.image_size,
        'white_balance': cam_settings.white_balance, 'capture_mode': cam_settings.capture_mode,
        'capture_target': cam_settings.capture_target, 'high_iso_nr': cam_settings.high_iso_nr,
        'long_exp_nr': cam_settings.long_exp_nr, 'liveview_af': cam_settings.liveview_af,
        'capabilities': merged_caps,
        'profile_settable': profile.get('settable', []),
        'profile_defaults_day': profile.get('defaults_day', {}),
        'applied': cam_settings.applied or {}, 'in_sync': getattr(cam_settings, 'in_sync', False),
        'last_synced_at': cam_settings.last_synced_at.isoformat() if cam_settings.last_synced_at else None,
    })
