"""
API cho THIẾT BỊ camera (không dùng session user).

Xác thực dùng chung credential của camera:
``X-Device-Key = camera.code`` và ``X-Device-Secret = camera.mqtt_password``.

Luồng upload ảnh chụp (Capture):
  1. POST /api/device/upload/presign   → cấp presigned PUT URL (ảnh + thumb)
  2. Camera PUT original thẳng lên R2, thumbnail lên SeaweedFS
  3. POST /api/device/upload/complete  → verify object tồn tại → tạo Media

Luồng Live View (frame tạm, KHÔNG tạo Media):
  - POST /api/device/live/frame        → lưu frame mới nhất vào Redis (TTL ngắn)

Chống lạm dụng: giới hạn kích thước frame, key sinh phía server (camera không
tự chọn key), prefix key theo camera_id → camera không ghi đè dữ liệu camera khác.
"""
import json
import secrets
import uuid
from functools import wraps

from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models.camera import Camera
from core.utils import storage

# Frame Live View tối đa 512 KB (preview 640x424 thực tế ~20 KB)
LIVE_FRAME_MAX_BYTES = 512 * 1024
LIVE_FRAME_TTL = 15          # giây — frame cũ tự biến mất
LIVE_SESSION_TTL = 60        # giây — session hết hạn nếu không gia hạn
PRESIGN_EXPIRE = 600         # giây — URL upload sống 10 phút


# ── Xác thực thiết bị ────────────────────────────────────────────────────────

def device_auth(view):
    """Xác thực camera: X-Device-Key = camera.code, X-Device-Secret = mqtt_password."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        key = request.headers.get("X-Device-Key", "")
        secret = request.headers.get("X-Device-Secret", "")
        if not key or not secret:
            return JsonResponse({"error": "Missing credentials"}, status=401)
        try:
            cam = Camera.objects.get(code=key)
            if cam.mqtt_password and secrets.compare_digest(cam.mqtt_password, secret):
                request.camera = cam
                return view(request, *args, **kwargs)
        except Camera.DoesNotExist:
            pass
        return JsonResponse({"error": "Invalid credentials"}, status=401)
    return wrapper


def _json_body(request):
    try:
        return json.loads(request.body.decode())
    except (ValueError, UnicodeDecodeError):
        return None


# ── Upload ảnh Capture ───────────────────────────────────────────────────────

@csrf_exempt
@require_POST
@device_auth
def upload_presign(request):
    """Cấp presigned PUT URL. Key do SERVER sinh — camera không tự chọn.

    Body: {"content_type": "image/jpeg", "taken_at": "...", "with_thumb": true}
    """
    body = _json_body(request)
    if body is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    content_type = body.get("content_type", "image/jpeg")
    if content_type not in ("image/jpeg", "image/x-nikon-nef"):
        return JsonResponse({"error": "Unsupported content_type"}, status=400)

    taken_at = _parse_taken_at(body.get("taken_at")) or timezone.now()
    media_id = uuid.uuid4()
    ext = "jpg" if content_type == "image/jpeg" else "nef"
    prefix = f"{request.camera.id}/{taken_at:%Y/%m/%d}"
    key = f"{prefix}/{media_id}.{ext}"

    from django.conf import settings as _s
    storage_backend = "r2" if getattr(_s, "R2", {}).get("ENDPOINT_URL") else "seaweed"

    data = {
        "media_id": str(media_id),
        "key": key,
        "storage": storage_backend,
        "url": storage.presigned_put_url(key, content_type, expire=PRESIGN_EXPIRE, storage=storage_backend),
    }
    if body.get("with_thumb"):
        thumb_key = f"{prefix}/{media_id}_thumb.jpg"
        data["thumb_key"] = thumb_key
        # Thumbnail luôn vào SeaweedFS (VPS), bất kể ảnh gốc đi R2 hay Seaweed
        data["thumb_url"] = storage.presigned_put_url(
            thumb_key, "image/jpeg", expire=PRESIGN_EXPIRE, storage="seaweed"
        )
    # Lưu storage backend vào Redis — upload_complete tự lookup, camera không cần gửi lại
    cache.set(f"presign_storage:{media_id}", storage_backend, timeout=PRESIGN_EXPIRE + 60)
    return JsonResponse(data)


@csrf_exempt
@require_POST
@device_auth
def upload_complete(request):
    """Camera báo upload xong → server verify object → tạo Media.

    Body: {"media_id", "key", "thumb_key", "taken_at", "width", "height",
           "content_type"}
    Idempotent theo media_id: gọi lại lần 2 trả về bản ghi cũ.
    """
    from core.models.media import Media

    body = _json_body(request)
    if body is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    media_id = body.get("media_id")
    key = body.get("key") or ""
    if not media_id or not key:
        return JsonResponse({"error": "media_id and key required"}, status=400)

    # Key phải thuộc prefix của camera này (chặn ghi metadata chéo camera)
    if not key.startswith(f"{request.camera.id}/"):
        return JsonResponse({"error": "Key not owned by this camera"}, status=403)

    existing = Media.objects.filter(pk=media_id).first()
    if existing:
        return JsonResponse({"ok": True, "media_id": str(existing.pk),
                             "duplicate": True})

    # Ưu tiên: lấy storage từ Redis (server đã lưu khi presign)
    # Fallback: client gửi (firmware mới) → fallback cuối: "seaweed" (firmware cũ)
    storage_backend = (
        cache.get(f"presign_storage:{media_id}")
        or body.get("storage", "seaweed")
    )
    if storage_backend not in ("seaweed", "r2"):
        storage_backend = "seaweed"

    size = storage.head_size(key, storage=storage_backend)
    if size is None:
        return JsonResponse({"error": "Object not found on storage"}, status=400)

    thumb_key = body.get("thumb_key") or ""
    if thumb_key:
        if not thumb_key.startswith(f"{request.camera.id}/"):
            return JsonResponse({"error": "thumb_key not owned"}, status=403)
        # Thumbnail luôn kiểm tra trên SeaweedFS
        if storage.head_size(thumb_key, storage="seaweed") is None:
            thumb_key = ""

    taken_at = _parse_taken_at(body.get("taken_at")) or timezone.now()
    media = Media.objects.create(
        pk=media_id,
        camera=request.camera,
        s3_key=key,
        thumb_key=thumb_key,
        taken_at=taken_at,
        content_type=body.get("content_type", "image/jpeg"),
        size_bytes=size,
        width=int(body.get("width") or 0),
        height=int(body.get("height") or 0),
        storage=storage_backend,
    )
    return JsonResponse({"ok": True, "media_id": str(media.pk)})


def _parse_taken_at(value):
    if not value:
        return None
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


# ── Live View: nhận frame ────────────────────────────────────────────────────

@csrf_exempt
@require_POST
@device_auth
def live_frame(request):
    """Camera PUSH 1 frame JPEG (body = raw bytes). Chỉ giữ frame MỚI NHẤT.

    Headers: X-Live-Session, X-Frame-Seq. Từ chối nếu session không active
    (server đã stop hoặc hết TTL) → camera biết đường dừng gửi.
    """
    session_id = request.headers.get("X-Live-Session", "")
    cam_id = str(request.camera.id)

    active = cache.get(f"live:{cam_id}:session")
    if not active or (session_id and active != session_id):
        return JsonResponse({"error": "no_active_session"}, status=409)

    frame = request.body
    if not frame or len(frame) > LIVE_FRAME_MAX_BYTES:
        return JsonResponse({"error": "frame_empty_or_too_large"}, status=400)

    try:
        seq = int(request.headers.get("X-Frame-Seq", "0"))
    except ValueError:
        seq = 0
    meta = cache.get(f"live:{cam_id}:meta") or {}
    if seq and meta.get("seq") and seq <= meta["seq"]:
        return JsonResponse({"ok": True, "skipped": "stale"})

    cache.set(f"live:{cam_id}:frame", frame, LIVE_FRAME_TTL)
    cache.set(f"live:{cam_id}:meta", {
        "seq": seq,
        "at": timezone.now().isoformat(),
        "size": len(frame),
    }, LIVE_FRAME_TTL)
    # Gia hạn session khi còn frame đổ về
    cache.set(f"live:{cam_id}:session", active, LIVE_SESSION_TTL)
    return JsonResponse({"ok": True, "seq": seq})


# ── Device Config: CM4 pull cấu hình & trạng thái cưỡng bức bật ──────────────

@csrf_exempt
@device_auth
def device_config(request):
    """Camera CM4 chủ động PULL cấu hình vận hành và trạng thái cưỡng bức bật.

    Trả về:
      - force_power_on: bool (true nếu Web UI đang cưỡng bức bật -> CM4 không tự tắt)
      - cm4_power_state: str (off / powering_on / running / shutting_down)
      - capture_interval_sec: int
      - schedule_enabled: bool
      - work_start_time, work_end_time: str
      - schedules: list khung giờ chụp
      - server_time: ISO timestamp
    """
    from core.models.camera import CameraDevice
    dev, _ = CameraDevice.objects.get_or_create(camera=request.camera)
    schedules = [s.to_dict() for s in request.camera.schedules.filter(is_enabled=True)]

    return JsonResponse({
        "ok": True,
        "camera_code": request.camera.code,
        "force_power_on": bool(dev.force_power_on),
        "cm4_power_state": dev.cm4_power_state,
        "capture_interval_sec": dev.capture_interval_sec,
        "schedule_enabled": dev.schedule_enabled,
        "work_start_time": dev.work_start_time,
        "work_end_time": dev.work_end_time,
        "schedules": schedules,
        "server_time": timezone.now().isoformat(),
    })

