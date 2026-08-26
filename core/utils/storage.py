"""
Hybrid storage helper: R2 giữ original/output; SeaweedFS giữ thumbnail/cache.

Django KHÔNG serve file. Nó chỉ:
  1. Kiểm tra quyền (object-level permission).
  2. Sinh presigned URL để browser tải trực tiếp.

Routing: dựa vào media.storage ("seaweed" | "r2").
Tất cả hàm đều nhận tham số storage=None (mặc định: "seaweed").
"""

import hashlib
import shutil
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
    # PUBLIC_DOMAIN thường chỉ map vào bucket media. Không dùng nó cho output
    # bucket, nếu không video sẽ trỏ sang đúng key nhưng sai bucket.
    if public and not download_name and not inline_content_type and (bucket is None or bucket == _r2_bucket()):
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


def _presign_version_key(key):
    return "psver:" + hashlib.md5(key.encode()).hexdigest()


def _presign_cache_key(
    key, ttl_bucket, *, storage=None, download_name=None,
    inline_content_type=None, r2_output=False, version=0,
):
    """Cache key phân biệt đầy đủ backend, bucket và response headers."""
    backend = storage or "seaweed"
    bucket = (
        _r2_output_bucket() if backend == "r2" and r2_output
        else _r2_bucket() if backend == "r2"
        else _bucket()
    )
    raw = (
        f"presign:{backend}:{bucket}:{key}:{ttl_bucket}:"
        f"{download_name or ''}:{inline_content_type or ''}:{int(r2_output)}:{version}"
    )
    return "psurl:" + hashlib.md5(raw.encode()).hexdigest()


def presigned_get_url_cached(key, expire=None, storage=None, **kwargs):
    """
    Sinh presigned GET URL với cache Redis (pull-through).
    storage=None | "seaweed" → SeaweedFS.  storage="r2" → Cloudflare R2.
    """
    if not key:
        return None
    # R2 public domain: trả URL tĩnh, không cần cache
    if (
        storage == "r2"
        and getattr(settings, "R2", {}).get("PUBLIC_DOMAIN")
        and not kwargs.get("download_name")
        and not kwargs.get("inline_content_type")
        and not kwargs.get("r2_output")
    ):
        return _presigned_get_r2(key, **kwargs)
    ttl = expire or (settings.R2 if storage == "r2" else settings.SEAWEED)["PRESIGN_EXPIRE"]
    ttl_bucket = (ttl // 600) * 600
    version = cache.get(_presign_version_key(key), 0)
    c_key = _presign_cache_key(
        key,
        ttl_bucket,
        storage=storage,
        download_name=kwargs.get("download_name"),
        inline_content_type=kwargs.get("inline_content_type"),
        r2_output=kwargs.get("r2_output", False),
        version=version,
    )

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
    version_key = _presign_version_key(key)
    if cache.add(version_key, 1, timeout=None):
        return
    try:
        cache.incr(version_key)
    except (ValueError, TypeError):
        cache.set(version_key, 1, timeout=None)


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


def head_size(key, storage=None, r2_output=False):
    """HEAD 1 object: trả size_bytes nếu tồn tại, None nếu không."""
    if not key:
        return None
    try:
        if storage == "r2":
            bucket = _r2_output_bucket() if r2_output else _r2_bucket()
            resp = _r2_client().head_object(Bucket=bucket, Key=key)
        else:
            resp = _client().head_object(Bucket=_bucket(), Key=key)
        return int(resp.get("ContentLength", 0))
    except Exception:  # noqa: BLE001
        return None


def put_bytes(key, data, content_type="image/jpeg", storage=None, r2_output=False):
    """Upload bytes trực tiếp (dùng cho seed / thumbnail sinh phía backend)."""
    if storage == "r2":
        bucket = _r2_output_bucket() if r2_output else _r2_bucket()
        _r2_client().put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    else:
        _client().put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=content_type)
    return key


def upload_file(key, file_path, content_type="application/octet-stream", storage=None, r2_output=False):
    """Upload file từ disk bằng multipart/file streaming, không nạp toàn bộ vào RAM."""
    if storage == "r2":
        bucket = _r2_output_bucket() if r2_output else _r2_bucket()
        _r2_client().upload_file(
            file_path, bucket, key, ExtraArgs={"ContentType": content_type}
        )
    else:
        _client().upload_file(
            file_path, _bucket(), key, ExtraArgs={"ContentType": content_type}
        )
    return key


def download_bytes(key, storage=None):
    """Dọc toàn bộ 1 object về bytes."""
    if storage == "r2":
        obj = _r2_client().get_object(Bucket=_r2_bucket(), Key=key)
    else:
        obj = _client().get_object(Bucket=_bucket(), Key=key)
    return obj["Body"].read()


def download_to_file(key, file_path, storage=None, chunk_size=1024 * 1024):
    """Stream object xuống file theo block; RAM không phụ thuộc kích thước ảnh/video."""
    if storage == "r2":
        obj = _r2_client().get_object(Bucket=_r2_bucket(), Key=key)
    else:
        obj = _client().get_object(Bucket=_bucket(), Key=key)
    body = obj["Body"]
    try:
        with open(file_path, "wb") as fh:
            shutil.copyfileobj(body, fh, length=chunk_size)
    finally:
        body.close()
    return file_path


def delete_key(key, storage=None, r2_output=False):
    """Xoá 1 object (dùng khi archive hết hạn hoặc media bị xóa)."""
    if not key:
        return
    if storage == "r2":
        bucket = _r2_output_bucket() if r2_output else _r2_bucket()
        _r2_client().delete_object(Bucket=bucket, Key=key)
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
    from concurrent.futures import ThreadPoolExecutor

    out_storage = "r2" if _r2_enabled() else "seaweed"
    count = 0
    workers = max(1, int(getattr(settings, "ARCHIVE_DOWNLOAD_WORKERS", 4)))
    # Mỗi batch tối đa workers ảnh trên disk/RAM; ZIP có thể chứa hàng nghìn ảnh.
    batch_size = workers * 2

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = os.path.join(tmpdir, "archive.zip")

        def fetch(item):
            idx, entry = item
            arcname, key = entry[0], entry[1]
            src_storage = entry[2] if len(entry) > 2 else None
            staged = os.path.join(tmpdir, f"source_{idx:08d}.bin")
            try:
                download_to_file(key, staged, storage=src_storage)
                if os.path.getsize(staged) == 0:
                    os.remove(staged)
                    return None
                return arcname, staged
            except Exception:
                try:
                    os.remove(staged)
                except OSError:
                    pass
                return None

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for start in range(0, len(entries), batch_size):
                indexed = list(enumerate(entries[start:start + batch_size], start=start))
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    downloaded = list(executor.map(fetch, indexed))
                for result in downloaded:
                    if result is None:
                        continue
                    arcname, staged = result
                    zf.write(staged, arcname=arcname)
                    os.remove(staged)
                    count += 1
                    if progress_cb:
                        progress_cb(count)

        size = os.path.getsize(tmp_path)
        upload_file(
            zip_key,
            tmp_path,
            content_type="application/zip",
            storage=out_storage,
            r2_output=(out_storage == "r2"),
        )
        return size, count
