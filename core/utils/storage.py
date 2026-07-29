"""
Helper kết nối SeaweedFS qua giao thức S3 (boto3).

Django KHÔNG serve file. Nó chỉ:
  1. Kiểm tra quyền (object-level permission).
  2. Sinh presigned URL để browser tải trực tiếp từ SeaweedFS.

Tách riêng ở đây để sau này đổi sang Cloudflare R2 chỉ cần sửa 1 chỗ.
"""

from functools import lru_cache

import boto3
from botocore.client import Config
from django.conf import settings


def _make_client(endpoint_url):
    """Tạo boto3 S3 client với endpoint_url chỉ định."""
    cfg = settings.SEAWEED
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


@lru_cache(maxsize=1)
def _client():
    """boto3 S3 client trỏ tới SeaweedFS gateway nội bộ (cache 1 instance)."""
    return _make_client(settings.SEAWEED["ENDPOINT_URL"])


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
    return _make_client(endpoint)


def _bucket():
    return settings.SEAWEED["BUCKET"]


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


def presigned_get_url(key, expire=None, download_name=None, inline_content_type=None):
    """
    Sinh presigned URL (GET) cho 1 object.

    :param key: key trong bucket.
    :param expire: số giây hết hạn (mặc định lấy từ settings).
    :param download_name: nếu set → ép trình duyệt tải về với tên file này.
    :param inline_content_type: nếu set → ép Content-Type (vd 'video/mp4') và
        Content-Disposition=inline để phát trực tiếp trên trình duyệt.
    """
    if not key:
        return None
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


def presigned_put_url(key, content_type="image/jpeg", expire=None):
    """Sinh presigned URL (PUT) để thiết bị camera upload trực tiếp."""
    cfg = settings.SEAWEED
    return _presign_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": _bucket(), "Key": key, "ContentType": content_type},
        ExpiresIn=expire or cfg["PRESIGN_EXPIRE"],
    )


def head_size(key):
    """HEAD 1 object: trả size_bytes nếu tồn tại, None nếu không."""
    if not key:
        return None
    try:
        resp = _client().head_object(Bucket=_bucket(), Key=key)
        return int(resp.get("ContentLength", 0))
    except Exception:  # noqa: BLE001 - not found / lỗi mạng
        return None


def put_bytes(key, data, content_type="image/jpeg"):
    """Upload bytes trực tiếp (dùng cho seed / thumbnail sinh phía backend)."""
    _client().put_object(
        Bucket=_bucket(), Key=key, Body=data, ContentType=content_type
    )
    return key


def download_bytes(key):
    """Đọc toàn bộ 1 object từ SeaweedFS về bytes."""
    obj = _client().get_object(Bucket=_bucket(), Key=key)
    return obj["Body"].read()


def delete_key(key):
    """Xoá 1 object (dùng khi archive hết hạn)."""
    if key:
        _client().delete_object(Bucket=_bucket(), Key=key)


def build_archive_zip(entries, zip_key, progress_cb=None):
    """
    Gói nhiều ảnh thành 1 ZIP rồi upload lại SeaweedFS.

    :param entries: list (arcname, s3_key). ZIP_STORED vì JPEG đã nén sẵn
                    (không nén lại → nhanh, ít CPU).
    :param zip_key: key đích của file zip trên bucket.
    :param progress_cb: callback(i) gọi sau mỗi ảnh (để cập nhật tiến độ).
    :return: (size_bytes, item_count).
    """
    import os
    import tempfile
    import zipfile

    count = 0
    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:
            for i, (arcname, key) in enumerate(entries):
                try:
                    data = download_bytes(key)
                except Exception:
                    continue  # bỏ qua ảnh lỗi, không làm sập cả gói
                zf.writestr(arcname, data)
                count += 1
                if progress_cb:
                    progress_cb(count)
        size = os.path.getsize(tmp_path)
        with open(tmp_path, "rb") as fh:
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
