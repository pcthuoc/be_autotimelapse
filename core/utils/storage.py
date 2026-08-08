"""
Dual-backend storage helper: SeaweedFS (hot) và Cloudflare R2 (primary/cold).

Django KHÔNG serve file. Nó chỉ:
  1. Kiểm tra quyền (object-level permission).
  2. Sinh presigned URL để browser tải trực tiếp.

Routing: dựa vào media.storage ("seaweed" | "r2").
Tất cả hàm đều nhận tham số storage=None (mặc định: "seaweed").
"""

import hashlib
from functools import lru_cache

import boto3
from botocore.client import Config
from django.conf import settings
from django.core.cache import cache


def _make_client(endpoint_url, cfg):
    """Tạo boto3 S3 client với endpoint_url chỉ định."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=cfg["ACCESS_KEY"],
        aws_secret_access_key=cfg["SECRET_KEY"],
        region_name=cfg["REGION"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": cfg.get("ADDRESSING_STYLE", "path")},
        ),
    )


# ── SeaweedFS clients ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _client():
    """boto3 S3 client trỏ tới SeaweedFS gateway nội bộ (cache 1 instance)."""
    return _make_client(settings.SEAWEED["ENDPOINT_URL"], settings.SEAWEED)


@lru_cache(maxsize=1)
def _presign_client():
    """boto3 S3 client dùng public endpoint để ký presigned URL.

    Chữ ký AWS4 bao gồm header ``host``. Nếu ký bằng endpoint nội bộ
    (seaweed-filer:8333) rồi rewrite URL, thiết bị ngoài PUT đến
    public host sẽ bị signature mismatch → Connection reset.
    Client này ký ngay với public host nên signature luôn hợp lệ.
    """
    cfg = settings.SEAWEED
    public = cfg.get("PUBLIC_ENDPOINT_URL")
    endpoint = public if public else cfg["ENDPOINT_URL"]
    return _make_client(endpoint, cfg)


def _bucket():
    return settings.SEAWEED["BUCKET"]


# ── Cloudflare R2 clients ────────────────────────────────────────────────────────

def _r2_enabled():
    return bool(getattr(settings, "R2", {}).get("ENDPOINT_URL"))


@lru_cache(maxsize=1)
def _r2_client():
    cfg = settings.R2
    r2_cfg = {**cfg, "ADDRESSING_STYLE": "path", "REGION": cfg.get("REGION", "auto")}
    return _make_client(cfg["ENDPOINT_URL"], r2_cfg)


def _r2_bucket():
    return settings.R2["BUCKET"]


def _r2_output_bucket():
    return settings.R2.get("OUTPUT_BUCKET", settings.R2["BUCKET"])


def ensure_bucket():
    """Đảm bảo bucket tồn tại (gọi khi seed / bootstrap).

    Identity API (api-tl-key) chỉ có quyền scope trên bucket ``media`` nên
    KHÔNG tạo được bucket. Bucket phải được tạo trước bằng identity ``admin``
    (xem config/s3.json). Hàm chỉ kiểm tra tồn tại, báo lỗi rõ ràng nếu thiếu.
    """
    client = _client()
    bucket = _bucket()
    existing = [b["Name"] for b in client.list_buckets().get("Buckets", [])]
    if bucket in existing:
        return bucket
    try:
        client.create_bucket(Bucket=bucket)
    except Exception as exc:  # noqa: BLE001 - thiếu quyền tạo bucket
        raise RuntimeError(
            f"Bucket '{bucket}' chưa tồn tại và identity hiện tại không có "
            f"quyền tạo. Hãy tạo bucket bằng identity 'admin' trước "
            f"(create_bucket). Chi tiết: {exc}"
        ) from exc
    return bucket


def presigned_get_url(key, expire=None, download_name=None, inline_content_type=None, storage=None, r2_output=False):
    """
    Sinh presigned URL (GET) cho 1 object.
    storage=None | "seaweed" → SeaweedFS.  storage="r2" → Cloudflare R2.
    r2_output=True → dùng R2_OUTPUT_BUCKET (atl-output) thay vì R2_BUCKET (atl-media).
    """
    if not key:
        return None
    if storage == "r2":
        bucket = _r2_output_bucket() if r2_output else _r2_bucket()
        return _presigned_get_r2(key, expire=expire,
                                  download_name=download_name,
                                  inline_content_type=inline_content_type,
                                  bucket=bucket)
    cfg = settings.SEAWEED
    params = {"Bucket": _bucket(), "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = (
            f'attachment; filename="{download_name}"'
        )
    elif inline_content_type:
        params["ResponseContentType"] = inline_content_type
        params["ResponseContentDisposition"] = "inline"
    return _presign_client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expire or cfg["PRESIGN_EXPIRE"],
    )


def _presigned_get_r2(key, expire=None, download_name=None, inline_content_type=None, bucket=None):
    """Sinh presigned GET URL trực tiếp từ R2 (hoặc public domain nếu có)."""
    cfg = settings.R2
    public = cfg.get("PUBLIC_DOMAIN")
    if public:
        return f"https://{public.rstrip('/')}/{key}"
    params = {"Bucket": bucket or _r2_bucket(), "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
    elif inline_content_type:
        params["ResponseContentType"] = inline_content_type
        params["ResponseContentDisposition"] = "inline"
    return _r2_client().generate_presigned_url(
        "get_object", Params=params, ExpiresIn=expire or cfg["PRESIGN_EXPIRE"]
    )


# ── Presigned URL cache (Redis pull-through) ──────────────────────────────────

_PRESIGN_CACHE_RATIO = 0.85  # cache 85% TTL → URL trả ra luôn còn ít nhất 9 phút hạn


def _presign_cache_key(key, ttl_bucket, download_name=None):
    """Tạo Redis key ổn định cho 1 s3 key + TTL bucket."""
    raw = f"presign:{_bucket()}:{key}:{ttl_bucket}:{download_name or ''}"
    return "psurl:" + hashlib.md5(raw.encode()).hexdigest()


def presigned_get_url_cached(key, expire=None, storage=None, **kwargs):
    """
    Sinh presigned GET URL với cache Redis (pull-through).
    storage=None | "seaweed" → SeaweedFS.  storage="r2" → Cloudflare R2.
    """
    if not key:
        return None
    # R2 public domain: trả URL tĩnh, không cần cache
    if storage == "r2" and getattr(settings, "R2", {}).get("PUBLIC_DOMAIN"):
        return _presigned_get_r2(key, **kwargs)
    ttl = expire or (settings.R2 if storage == "r2" else settings.SEAWEED)["PRESIGN_EXPIRE"]
    ttl_bucket = (ttl // 600) * 600
    c_key = _presign_cache_key(key, ttl_bucket, download_name=kwargs.get('download_name'))

    cached = cache.get(c_key)
    if cached:
        return cached

    url = presigned_get_url(key, expire=ttl, storage=storage, **kwargs)
    if url:
        cache.set(c_key, url, timeout=int(ttl * _PRESIGN_CACHE_RATIO))
    return url


def _invalidate_presign_cache_key(key, expire=None):
    """Xóa cache presigned URL của 1 s3 key (gọi khi xóa Media record)."""
    if not key:
        return
    ttl = expire or settings.SEAWEED["PRESIGN_EXPIRE"]
    ttl_bucket = (ttl // 600) * 600
    cache.delete(_presign_cache_key(key, ttl_bucket))


def presigned_put_url(key, content_type="image/jpeg", expire=None, storage=None):
    """Sinh presigned URL (PUT) để thiết bị camera upload trực tiếp.
    storage=None | "seaweed" → SeaweedFS.  storage="r2" → Cloudflare R2.
    """
    if storage == "r2":
        cfg = settings.R2
        return _r2_client().generate_presigned_url(
            "put_object",
            Params={"Bucket": _r2_bucket(), "Key": key, "ContentType": content_type},
            ExpiresIn=expire or cfg["PRESIGN_EXPIRE"],
        )
    cfg = settings.SEAWEED
    return _presign_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": _bucket(), "Key": key, "ContentType": content_type},
        ExpiresIn=expire or cfg["PRESIGN_EXPIRE"],
    )


def head_size(key, storage=None):
    """HEAD 1 object: trả size_bytes nếu tồn tại, None nếu không."""
    if not key:
        return None
    try:
        if storage == "r2":
            resp = _r2_client().head_object(Bucket=_r2_bucket(), Key=key)
        else:
            resp = _client().head_object(Bucket=_bucket(), Key=key)
        return int(resp.get("ContentLength", 0))
    except Exception:  # noqa: BLE001
        return None


def put_bytes(key, data, content_type="image/jpeg", storage=None):
    """Upload bytes trực tiếp (dùng cho seed / thumbnail sinh phía backend)."""
    if storage == "r2":
        _r2_client().put_object(Bucket=_r2_bucket(), Key=key, Body=data, ContentType=content_type)
    else:
        _client().put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=content_type)
    return key


def download_bytes(key, storage=None):
    """Dọc toàn bộ 1 object về bytes."""
    if storage == "r2":
        obj = _r2_client().get_object(Bucket=_r2_bucket(), Key=key)
    else:
        obj = _client().get_object(Bucket=_bucket(), Key=key)
    return obj["Body"].read()


def delete_key(key, storage=None):
    """Xoá 1 object (dùng khi archive hết hạn hoặc media bị xóa)."""
    if not key:
        return
    if storage == "r2":
        _r2_client().delete_object(Bucket=_r2_bucket(), Key=key)
    else:
        _client().delete_object(Bucket=_bucket(), Key=key)


def build_archive_zip(entries, zip_key, progress_cb=None):
    """
    Gói nhiều ảnh thành 1 ZIP rồi upload lên R2 output (hoặc SeaweedFS nếu chưa có R2).

    :param entries: list (arcname, s3_key, storage_type). ZIP_STORED vì JPEG đã nén sẵn.
    :param zip_key: key đích của file zip trên bucket.
    :param progress_cb: callback(i) gọi sau mỗi ảnh (để cập nhật tiến độ).
    :return: (size_bytes, item_count).
    """
    import os
    import tempfile
    import zipfile

    out_storage = "r2" if _r2_enabled() else "seaweed"
    count = 0
    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:
            for entry in entries:
                arcname, key = entry[0], entry[1]
                src_storage = entry[2] if len(entry) > 2 else None
                try:
                    data = download_bytes(key, storage=src_storage)
                except Exception:
                    continue
                zf.writestr(arcname, data)
                count += 1
                if progress_cb:
                    progress_cb(count)
        size = os.path.getsize(tmp_path)
        with open(tmp_path, "rb") as fh:
            if out_storage == "r2":
                _r2_client().upload_fileobj(
                    fh, _r2_output_bucket(), zip_key,
                    ExtraArgs={"ContentType": "application/zip"},
                )
            else:
                _client().upload_fileobj(
                    fh, _bucket(), zip_key,
                    ExtraArgs={"ContentType": "application/zip"},
                )
        return size, count
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
