"""
API cho THIẾT BỊ camera (không dùng session user).

Xác thực: header ``X-Device-Key`` (key_id) + ``X-Device-Secret`` (raw secret)
đối chiếu CameraCredential (secret chỉ lưu hash — CODING_RULES §9).

Luồng upload ảnh chụp (Capture):
  1. POST /api/device/upload/presign   → cấp presigned PUT URL (ảnh + thumb)
  2. Camera PUT file thẳng lên SeaweedFS (Django không đụng byte ảnh)
  3. POST /api/device/upload/complete  → verify object tồn tại → tạo Media

Luồng Live View (frame tạm, KHÔNG tạo Media):
  - POST /api/device/live/frame        → lưu frame mới nhất vào Redis (TTL ngắn)

Chống lạm dụng: giới hạn kích thước frame, key sinh phía server (camera không
tự chọn key), prefix key theo camera_id → camera không ghi đè dữ liệu camera khác.
"""
import json
import uuid
from functools import wraps

from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models.camera import Camera, CameraCredential
from core.utils import storage

# Frame Live View tối đa 512 KB (preview 640x424 thực tế ~20 KB)
LIVE_FRAME_MAX_BYTES = 512 * 1024
LIVE_FRAME_TTL = 15          # giây — frame cũ tự biến mất
LIVE_SESSION_TTL = 60        # giây — session hết hạn nếu không gia hạn
PRESIGN_EXPIRE = 600         # giây — URL upload sống 10 phút


# ── Xác thực thiết bị ────────────────────────────────────────────────────────

def device_auth(view):
    """Decorator xác thực camera qua 2 cách (ưu tiên đơn giản trước):

    1. X-Device-Key = camera.code  +  X-Device-Secret = camera.mqtt_password
       → Đơn giản nhất, cùng thông số với MQTT, không hết hạn.

    2. X-Device-Key = CameraCredential.key_id  +  X-Device-Secret = raw secret
       → Legacy / cho phép rotate key riêng biệt.
    """
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        key = request.headers.get("X-Device-Key", "")
        secret = request.headers.get("X-Device-Secret", "")
        if not key or not secret:
            return JsonResponse({"error": "Missing credentials"}, status=401)

        # ── Cách 1: camera_code + mqtt_password (đơn giản, không hết hạn) ──
        try:
            cam = Camera.objects.get(code=key)
            if cam.mqtt_password and cam.mqtt_password == secret:
                request.camera = cam
                return view(request, *args, **kwargs)
        except Camera.DoesNotExist:
            pass

        # ── Cách 2: CameraCredential key_id + hashed secret (legacy) ──
        try:
            cred = CameraCredential.objects.select_related("camera").get(
                key_id=key, status=CameraCredential.Status.ACTIVE
            )
            if check_password(secret, cred.secret_hash):
                request.camera = cred.camera
                return view(request, *args, **kwargs)
        except CameraCredential.DoesNotExist:
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

    data = {
        "media_id": str(media_id),
        "key": key,
        "url": storage.presigned_put_url(key, content_type, expire=PRESIGN_EXPIRE),
    }
    if body.get("with_thumb"):
        thumb_key = f"{prefix}/{media_id}_thumb.jpg"
        data["thumb_key"] = thumb_key
        data["thumb_url"] = storage.presigned_put_url(
            thumb_key, "image/jpeg", expire=PRESIGN_EXPIRE
        )
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

    size = storage.head_size(key)
    if size is None:
        return JsonResponse({"error": "Object not found on storage"}, status=400)

    thumb_key = body.get("thumb_key") or ""
    if thumb_key:
        if not thumb_key.startswith(f"{request.camera.id}/"):
            return JsonResponse({"error": "thumb_key not owned"}, status=403)
        if storage.head_size(thumb_key) is None:
            thumb_key = ""          # thumb chưa lên → fallback ảnh gốc

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
