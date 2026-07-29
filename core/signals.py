from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import Profile


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

from core.models.media import Media, MediaDayStat  # noqa: E402


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
            Media.objects.filter(camera_id=instance.camera_id)
            .exclude(pk=instance.pk)
            .order_by("-taken_at")
            .first()
        )
        MediaDayStat.objects.filter(pk=stat.pk).update(
            cover=nxt if nxt else None
        )
