"""
Tác vụ nền (Celery) cho media — chạy KHÔNG block request.

  - build_media_archive: nén nhiều ảnh thành 1 ZIP trên SeaweedFS.
  - cleanup_expired_archives: xoá ZIP hết hạn (vòng đời URL).
  - render_timelapse_video: dựng video timelapse từ ảnh bằng ffmpeg.
"""

import io
import os
import subprocess
import tempfile
import urllib.request
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from core.models.media import Media, MediaArchive, VideoRender
from core.utils import storage


@shared_task(bind=True, queue="archive")
def build_media_archive(self, archive_id):
    """Nén ảnh của 1 MediaArchive thành ZIP, cập nhật trạng thái + vòng đời."""
    try:
        archive = MediaArchive.objects.select_related("camera").get(pk=archive_id)
    except MediaArchive.DoesNotExist:
        return

    archive.status = MediaArchive.Status.PROCESSING
    archive.save(update_fields=["status"])

    try:
        qs = Media.objects.filter(camera=archive.camera)
        if archive.media_ids:
            qs = qs.filter(pk__in=archive.media_ids)
        if archive.date_from:
            qs = qs.filter(taken_at__gte=archive.date_from)
        if archive.date_to:
            qs = qs.filter(taken_at__lte=archive.date_to)
        qs = qs.order_by("taken_at")[: settings.MEDIA_ARCHIVE_MAX_ITEMS]

        entries = []
        for m in qs:
            ts = timezone.localtime(m.taken_at)
            name = f"{m.camera.code}/{ts:%Y%m%d_%H%M%S}_{str(m.pk)[:8]}.jpg"
            entries.append((name, m.s3_key))

        if not entries:
            archive.status = MediaArchive.Status.FAILED
            archive.error = "No photos match the criteria."
            archive.save(update_fields=["status", "error"])
            return

        zip_key = f"archives/{archive.requested_by_id}/{archive.id}.zip"
        size, count = storage.build_archive_zip(entries, zip_key)

        now = timezone.now()
        archive.zip_key = zip_key
        archive.size_bytes = size
        archive.item_count = count
        archive.status = MediaArchive.Status.READY
        archive.ready_at = now
        archive.expires_at = now + timedelta(
            hours=settings.MEDIA_ARCHIVE_TTL_HOURS
        )
        archive.save()
    except Exception as exc:  # noqa: BLE001
        archive.status = MediaArchive.Status.FAILED
        archive.error = str(exc)[:500]
        archive.save(update_fields=["status", "error"])
        raise


@shared_task(queue="default")
def cleanup_expired_archives():
    """Xoá ZIP đã quá hạn khỏi SeaweedFS + đánh dấu expired."""
    now = timezone.now()
    qs = MediaArchive.objects.filter(
        status=MediaArchive.Status.READY, expires_at__lt=now
    )
    n = 0
    for a in qs:
        if a.zip_key:
            try:
                storage.delete_key(a.zip_key)
            except Exception:  # noqa: BLE001
                pass
        a.status = MediaArchive.Status.EXPIRED
        a.zip_key = ""
        a.save(update_fields=["status", "zip_key"])
        n += 1
    return n


@shared_task(bind=True, queue="render")
def render_timelapse_video(self, render_id):
    """
    Render video timelapse từ ảnh của 1 VideoRender.

    Quy trình:
    1. Tải ảnh từ SeaweedFS vào thư mục tạm.
    2. Dùng ffmpeg tạo MP4 từ danh sách ảnh.
    3. Upload MP4 lên SeaweedFS.
    4. Cập nhật VideoRender status → ready.
    """
    try:
        render = VideoRender.objects.select_related("camera").get(pk=render_id)
    except VideoRender.DoesNotExist:
        return

    render.status = VideoRender.Status.PROCESSING
    render.progress = 0
    render.save(update_fields=["status", "progress"])

    try:
        # Lấy danh sách ảnh theo thứ tự thời gian
        qs = (
            Media.objects.filter(
                camera=render.camera,
                taken_at__date__gte=render.date_from,
                taken_at__date__lte=render.date_to,
                content_type__startswith="image/",
            )
            .exclude(s3_key="")
            .order_by("taken_at")
        )
        all_frames = list(qs)
        if not all_frames:
            render.status = VideoRender.Status.FAILED
            render.error = "Không có ảnh nào trong khoảng thời gian đã chọn."
            render.save(update_fields=["status", "error"])
            return

        # ── Lọc theo tần suất lấy ảnh ──────────────────────────────────────
        interval = getattr(render, "frame_interval", 0) or 0
        if interval > 0 and len(all_frames) > 1:
            # Giữ lại 1 ảnh mỗi `interval` giây tính từ ảnh đầu tiên
            sampled = [all_frames[0]]
            last_taken = all_frames[0].taken_at
            for m in all_frames[1:]:
                diff = (m.taken_at - last_taken).total_seconds()
                if diff >= interval:
                    sampled.append(m)
                    last_taken = m.taken_at
            frames = sampled
        else:
            frames = all_frames

        render.item_count = len(frames)
        render.save(update_fields=["item_count"])

        # Lấy thông số cấu hình từ Hardware Profile
        chunk_size = getattr(settings, "RENDER_CHUNK_SIZE", 150)
        ffmpeg_threads = str(getattr(settings, "FFMPEG_THREADS", 1))
        w, h = render.resolution.split("x")

        # Chia nhỏ frames thành các phân đoạn (chunks)
        chunks = [frames[i:i + chunk_size] for i in range(0, len(frames), chunk_size)]
        total_chunks = len(chunks)

        with tempfile.TemporaryDirectory() as tmpdir:
            segment_paths = []

            for c_idx, chunk_frames in enumerate(chunks):
                chunk_dir = os.path.join(tmpdir, f"chunk_{c_idx:04d}")
                os.makedirs(chunk_dir, exist_ok=True)
                list_file = os.path.join(chunk_dir, "frames.txt")
                downloaded = 0

                with open(list_file, "w") as lf:
                    for i, m in enumerate(chunk_frames):
                        try:
                            data = storage.download_bytes(m.s3_key)
                            dst = os.path.join(chunk_dir, f"frame_{i:06d}.jpg")
                            with open(dst, "wb") as fh:
                                fh.write(data)
                            lf.write(f"file '{dst}'\n")
                            lf.write(f"duration {1.0 / max(render.fps, 1):.4f}\n")
                            downloaded += 1
                        except Exception:  # noqa: BLE001
                            continue

                if downloaded == 0:
                    continue

                # Render phân đoạn này ra file MPEG-TS tạm
                seg_path = os.path.join(tmpdir, f"segment_{c_idx:04d}.ts")
                cmd = [
                    "nice", "-n", "19",
                    "ffmpeg", "-y",
                    "-threads", ffmpeg_threads,
                    "-f", "concat", "-safe", "0",
                    "-i", list_file,
                    "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "23",
                    "-pix_fmt", "yuv420p",
                    seg_path,
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=1800)  # noqa: S603
                if result.returncode != 0:
                    raise RuntimeError(f"Lỗi render segment {c_idx}: " + result.stderr.decode()[-300:])

                segment_paths.append(seg_path)

                # Dọn dẹp ảnh tạm của chunk để giải phóng đĩa cứng & RAM
                for f in os.listdir(chunk_dir):
                    os.remove(os.path.join(chunk_dir, f))
                os.rmdir(chunk_dir)

                # Cập nhật tiến độ UI (tải + render từng chunk chiếm từ 0% → 90%)
                pct = int((c_idx + 1) / total_chunks * 90)
                render.progress = pct
                render.save(update_fields=["progress"])

            if not segment_paths:
                raise RuntimeError("Không tải hoặc render được phân đoạn nào.")

            output_path = os.path.join(tmpdir, "output.mp4")

            if len(segment_paths) == 1:
                # Chỉ có 1 segment → copy thẳng ra output.mp4
                cmd_concat = [
                    "ffmpeg", "-y", "-i", segment_paths[0],
                    "-c", "copy", "-movflags", "+faststart", output_path
                ]
            else:
                # Có nhiều segments → ghép siêu tốc bằng concat protocol (0% re-encode CPU)
                concat_list = os.path.join(tmpdir, "concat_list.txt")
                with open(concat_list, "w") as cl:
                    for sp in segment_paths:
                        cl.write(f"file '{sp}'\n")

                cmd_concat = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", concat_list,
                    "-c", "copy",
                    "-movflags", "+faststart",
                    output_path,
                ]

            res_concat = subprocess.run(cmd_concat, capture_output=True, timeout=300)  # noqa: S603
            if res_concat.returncode != 0:
                raise RuntimeError("Lỗi ghép video: " + res_concat.stderr.decode()[-300:])

            render.progress = 95
            render.save(update_fields=["progress"])

            # Upload MP4 lên SeaweedFS
            output_key = (
                f"{render.camera_id}/renders/"
                f"{render.date_from}_{render.date_to}_{render.fps}fps_{render.resolution}_{render.id}.mp4"
            )
            file_size = os.path.getsize(output_path)
            with open(output_path, "rb") as fh:
                storage.put_bytes(output_key, fh.read(), content_type="video/mp4")

        render.status = VideoRender.Status.READY
        render.output_key = output_key
        render.size_bytes = file_size
        render.progress = 100
        render.ready_at = timezone.now()
        render.save(update_fields=["status", "output_key", "size_bytes", "progress", "ready_at"])

    except Exception as exc:  # noqa: BLE001
        render.status = VideoRender.Status.FAILED
        render.error = str(exc)[:1000]
        render.save(update_fields=["status", "error"])

