"""Rebuild bảng MediaDayStat từ dữ liệu Media hiện có (dùng khi seed/bulk)."""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models.media import Media, MediaDayStat


class Command(BaseCommand):
    help = "Tính lại MediaDayStat (đếm ảnh theo ngày + ảnh bìa) cho mọi camera."

    def handle(self, *args, **options):
        counts = defaultdict(int)          # (camera_id, day) -> count
        cover = {}                          # (camera_id, day) -> (taken_at, media_id)

        qs = Media.objects.values_list(
            "id", "camera_id", "taken_at"
        ).iterator(chunk_size=2000)
        total = 0
        for mid, cam_id, taken_at in qs:
            day = timezone.localtime(taken_at).date()
            key = (cam_id, day)
            counts[key] += 1
            if key not in cover or taken_at >= cover[key][0]:
                cover[key] = (taken_at, mid)
            total += 1

        with transaction.atomic():
            MediaDayStat.objects.all().delete()
            rows = [
                MediaDayStat(
                    camera_id=cam_id, day=day, count=n,
                    cover_id=cover[(cam_id, day)][1],
                )
                for (cam_id, day), n in counts.items()
            ]
            MediaDayStat.objects.bulk_create(rows, batch_size=1000)

        self.stdout.write(
            self.style.SUCCESS(
                f"Đã rebuild {len(counts)} dòng day-stat từ {total} ảnh."
            )
        )
