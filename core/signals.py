import logging

from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import Profile


log = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Tự tạo Profile khi User mới được tạo."""
    if created:
        Profile.objects.create(
            user=instance,
            full_name=f"{instance.first_name} {instance.last_name}".strip(),
        )


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """Đảm bảo Profile luôn được save khi User save."""
    if hasattr(instance, "profile"):
        instance.profile.save()


# ── Rollup MediaDayStat: cập nhật đếm theo ngày khi ảnh thêm/xoá ──────────────
from django.db.models import F  # noqa: E402
from django.db.models.signals import post_delete  # noqa: E402
from django.utils import timezone  # noqa: E402

from core.models.media import Media, MediaArchive, MediaDayStat, VideoRender  # noqa: E402


def _local_day(dt):
    return timezone.localtime(dt).date()


@receiver(post_save, sender=Media)
def media_stat_inc(sender, instance, created, **kwargs):
    if not created:
        return
    day = _local_day(instance.taken_at)
    stat, made = MediaDayStat.objects.get_or_create(
        camera_id=instance.camera_id, day=day,
        defaults={"count": 1, "cover": instance},
    )
    if made:
        return
    MediaDayStat.objects.filter(pk=stat.pk).update(count=F("count") + 1)
    # cover = ảnh mới nhất trong ngày
    if stat.cover_id is None or instance.taken_at >= stat.cover.taken_at:
        MediaDayStat.objects.filter(pk=stat.pk).update(cover=instance)


@receiver(post_delete, sender=Media)
def media_stat_dec(sender, instance, **kwargs):
    day = _local_day(instance.taken_at)
    try:
        stat = MediaDayStat.objects.get(camera_id=instance.camera_id, day=day)
    except MediaDayStat.DoesNotExist:
        return
    if stat.count <= 1:
        stat.delete()
        return
    MediaDayStat.objects.filter(pk=stat.pk).update(count=F("count") - 1)
    if stat.cover_id == instance.id:
        nxt = (
            Media.objects.filter(camera_id=instance.camera_id, taken_at__date=day)
            .exclude(pk=instance.pk)
            .order_by("-taken_at")
            .first()
        )
        MediaDayStat.objects.filter(pk=stat.pk).update(
            cover=nxt if nxt else None
        )


# ── Presigned URL cache invalidation khi xoá Media ───────────────────────────

@receiver(post_delete, sender=Media)
def media_storage_cleanup(sender, instance, **kwargs):
    """Xóa file trên object storage và cache presigned URL khi Media bị xóa."""
    from core.utils import storage as _storage
    from core.utils.storage import _invalidate_presign_cache_key
    _backend = instance.storage
    try:
        _storage.delete_key(instance.s3_key, storage=_backend)
    except Exception:  # noqa: BLE001
        log.exception("Không xóa được original %s khỏi %s", instance.s3_key, _backend)
    try:
        if instance.thumb_key:
            # Thumbnail luôn nằm ở SeaweedFS, original ưu tiên R2.
            _storage.delete_key(instance.thumb_key, storage="seaweed")
    except Exception:  # noqa: BLE001
        log.exception("Không xóa được thumbnail %s", instance.thumb_key)
    try:
        _invalidate_presign_cache_key(instance.s3_key)
        if instance.thumb_key:
            _invalidate_presign_cache_key(instance.thumb_key)
    except Exception:  # noqa: BLE001
        log.exception("Không invalidate được presigned cache cho media %s", instance.pk)


@receiver(post_delete, sender=VideoRender)
def video_render_storage_cleanup(sender, instance, **kwargs):
    """Không để orphan MP4 khi render bị xóa bởi API hoặc cascade DB."""
    if not instance.output_key:
        return
    from core.utils import storage as _storage
    try:
        _storage.delete_key(
            instance.output_key,
            storage=instance.effective_output_storage,
            r2_output=instance.uses_r2_output_bucket,
        )
    except Exception:  # noqa: BLE001
        log.exception("Không xóa được output của render %s", instance.pk)


@receiver(post_delete, sender=MediaArchive)
def media_archive_storage_cleanup(sender, instance, **kwargs):
    """Không để orphan ZIP khi archive bị xóa bởi API hoặc cascade DB."""
    if not instance.zip_key:
        return
    from core.utils import storage as _storage
    try:
        _storage.delete_key(
            instance.zip_key,
            storage=instance.effective_output_storage,
            r2_output=instance.uses_r2_output_bucket,
        )
    except Exception:  # noqa: BLE001
        log.exception("Không xóa được output của archive %s", instance.pk)
