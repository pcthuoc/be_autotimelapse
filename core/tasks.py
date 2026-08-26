"""
Tác vụ nền (Celery) cho media — chạy KHÔNG block request.

  - build_media_archive: nén nhiều ảnh thành 1 ZIP trên SeaweedFS.
  - cleanup_expired_archives: xoá ZIP hết hạn (vòng đời URL).
  - cleanup_expired_renders: xoá video render cũ hơn VIDEO_RENDER_TTL_DAYS ngày.
  - render_timelapse_video: dựng video timelapse từ ảnh bằng ffmpeg.
"""

import io
import logging
import os
import subprocess
import tempfile
import urllib.request
import math
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from core.models.media import Media, MediaArchive, VideoRender
from core.utils import storage
from core.utils.storage import _r2_enabled


log = logging.getLogger(__name__)


@shared_task(bind=True, queue="archive")
def build_media_archive(self, archive_id):
    """Nén ảnh của 1 MediaArchive thành ZIP, cập nhật trạng thái + vòng đời."""
    try:
        archive = MediaArchive.objects.select_related("camera").get(pk=archive_id)
    except MediaArchive.DoesNotExist:
        return

    archive.status = MediaArchive.Status.PROCESSING
    archive.processing_started_at = timezone.now()
    archive.save(update_fields=["status", "processing_started_at"])

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
            entries.append((name, m.s3_key, m.storage))

        if not entries:
            archive.status = MediaArchive.Status.FAILED
            archive.error = "No photos match the criteria."
            archive.save(update_fields=["status", "error"])
            return

        zip_key = f"archives/{archive.requested_by_id}/{archive.id}.zip"
        size, count = storage.build_archive_zip(entries, zip_key)
        out_storage = "r2" if _r2_enabled() else "seaweed"

        if count != len(entries):
            try:
                storage.delete_key(
                    zip_key,
                    storage=out_storage,
                    r2_output=(out_storage == "r2"),
                )
            except Exception:  # noqa: BLE001
                log.exception("Không xoá được ZIP thiếu dữ liệu %s", zip_key)
            raise RuntimeError(
                f"ZIP không đầy đủ: tải được {count}/{len(entries)} ảnh. Vui lòng thử lại."
            )

        now = timezone.now()
        archive.zip_key = zip_key
        archive.output_storage = out_storage
        archive.output_bucket = "output" if out_storage == "r2" else "media"
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
        deleted = not a.zip_key
        if a.zip_key:
            try:
                storage.delete_key(
                    a.zip_key,
                    storage=a.effective_output_storage,
                    r2_output=a.uses_r2_output_bucket,
                )
                deleted = True
            except Exception:  # noqa: BLE001
                log.exception("Cleanup archive %s thất bại; sẽ retry lần sau", a.pk)
        if not deleted:
            continue
        a.status = MediaArchive.Status.EXPIRED
        a.zip_key = ""
        a.save(update_fields=["status", "zip_key"])
        n += 1
    return n


@shared_task(queue="default")
def cleanup_expired_renders():
    """Xoá video render cũ hơn VIDEO_RENDER_TTL_DAYS khỏi storage."""
    ttl = getattr(settings, "VIDEO_RENDER_TTL_DAYS", 7)
    cutoff = timezone.now() - timedelta(days=ttl)
    qs = VideoRender.objects.filter(
        status=VideoRender.Status.READY,
        ready_at__lt=cutoff,
    )
    n = 0
    for vr in qs:
        deleted = not vr.output_key
        if vr.output_key:
            try:
                storage.delete_key(
                    vr.output_key,
                    storage=vr.effective_output_storage,
                    r2_output=vr.uses_r2_output_bucket,
                )
                deleted = True
            except Exception:  # noqa: BLE001
                log.exception("Cleanup render %s thất bại; sẽ retry lần sau", vr.pk)
        if not deleted:
            continue
        vr.status = VideoRender.Status.EXPIRED
        vr.output_key = ""
        vr.error = ""
        vr.save(update_fields=["status", "output_key", "error"])
        n += 1
    return n


from concurrent.futures import ThreadPoolExecutor


def _download_single_frame(args):
    """Tải 1 ảnh từ storage về thư mục tạm, bỏ qua nếu lỗi hoặc 0 bytes."""
    idx, m, chunk_dir = args
    dst = os.path.join(chunk_dir, f"frame_{idx:06d}.jpg")
    for attempt in range(1, 4):
        try:
            storage.download_to_file(m.s3_key, dst, storage=m.storage)
            if os.path.getsize(dst) == 0:
                os.remove(dst)
                raise RuntimeError("object rỗng")
            return (idx, dst)
        except Exception:  # noqa: BLE001
            if os.path.exists(dst):
                os.remove(dst)
            if attempt == 3:
                log.exception("Không tải được frame %s sau 3 lần", m.pk)
    return None


def _parse_ffmpeg_error(stderr_bytes: bytes) -> str:
    """Lọc bỏ toàn bộ banner, header, codec info và tiến độ của ffmpeg để lấy câu lỗi thực sự."""
    text = stderr_bytes.decode(errors="replace")
    raw_lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
    filtered = []
    for s in raw_lines:
        if s.startswith("frame=") or "bitrate=" in s or "dup=" in s or "speed=" in s:
            continue
        if (
            s.startswith("ffmpeg version")
            or s.startswith("built with")
            or s.startswith("configuration:")
            or s.startswith("lib")
            or s.startswith("[")
            or s.startswith("Input #")
            or s.startswith("Output #")
            or s.startswith("Metadata:")
            or s.startswith("encoder")
            or s.startswith("Stream #")
            or s.startswith("Side data:")
            or s.startswith("cpb:")
            or s.startswith("Duration:")
            or s.startswith("Stream mapping:")
            or s.startswith("Press [q] to stop")
            or s.startswith("Hyper fast Audio and Video encoder")
        ):
            continue
        filtered.append(s)

    if filtered:
        return "\n".join(filtered[-5:])
    return "Tiến trình mã hoá FFmpeg bị gián đoạn. Vui lòng kiểm tra lại dữ liệu ảnh."


@shared_task(bind=True, queue="render", soft_time_limit=6900, time_limit=7200)
def render_timelapse_video(self, render_id):
    """Render video timelapse từ ảnh của 1 VideoRender."""
    try:
        render = VideoRender.objects.select_related("camera").get(pk=render_id)
    except VideoRender.DoesNotExist:
        return

    render.status = VideoRender.Status.PROCESSING
    render.progress = 0
    render.processing_started_at = timezone.now()
    render.save(update_fields=["status", "progress", "processing_started_at"])

    try:
        # Lấy danh sách ảnh theo thứ tự thời gian
        # Dùng range thay cho taken_at__date để PostgreSQL dùng index
        # (camera, taken_at), đặc biệt quan trọng khi bảng có hàng triệu ảnh.
        from datetime import datetime, time
        try:
            camera_tz = ZoneInfo(render.camera.timezone or settings.TIME_ZONE)
        except ZoneInfoNotFoundError:
            camera_tz = ZoneInfo(settings.TIME_ZONE)
        range_start = timezone.make_aware(
            datetime.combine(render.date_from, time.min), camera_tz
        )
        range_end = timezone.make_aware(
            datetime.combine(render.date_to + timedelta(days=1), time.min), camera_tz
        )
        qs = (
            Media.objects.filter(
                camera=render.camera,
                taken_at__gte=range_start,
                taken_at__lt=range_end,
                content_type__startswith="image/",
            )
            .exclude(s3_key="")
            .order_by("taken_at")
        )
        raw_count = qs.count()
        if raw_count == 0:
            render.status = VideoRender.Status.FAILED
            render.error = "Không có ảnh nào trong khoảng thời gian đã chọn."
            render.save(update_fields=["status", "error"])
            return

        # ── Stream/lọc frame ──────────────────────────────────────────────
        # Không list(qs): chỉ giữ tối đa một chunk model trong RAM.
        interval = getattr(render, "frame_interval", 0) or 0
        chunk_size = getattr(settings, "RENDER_CHUNK_SIZE", 150)
        download_workers = max(1, min(getattr(settings, "RENDER_DOWNLOAD_WORKERS", 4), chunk_size))
        conf_threads = getattr(settings, "FFMPEG_THREADS", 1)
        ffmpeg_threads = str(max(1, conf_threads))
        w, h = render.resolution.split("x")
        is_4k = int(w) >= 3840 or int(h) >= 2160
        ffmpeg_preset = (
            getattr(settings, "FFMPEG_PRESET_4K", "veryfast")
            if is_4k else getattr(settings, "FFMPEG_PRESET", "fast")
        )

        def iter_selected_chunks():
            selected = []
            last_taken = None
            for media in qs.iterator(chunk_size=chunk_size):
                if interval > 0 and last_taken is not None:
                    if (media.taken_at - last_taken).total_seconds() < interval:
                        continue
                last_taken = media.taken_at
                selected.append(media)
                if len(selected) >= chunk_size:
                    yield selected
                    selected = []
            if selected:
                yield selected

        # Đây là upper bound khi sampling; progress vẫn đơn điệu và kết thúc ở 100%.
        total_chunks_upper = max(1, math.ceil(raw_count / chunk_size))
        rendered_frame_count = 0
        selected_frame_count = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            segment_paths = []

            for c_idx, chunk_frames in enumerate(iter_selected_chunks()):
                selected_frame_count += len(chunk_frames)
                chunk_dir = os.path.join(tmpdir, f"chunk_{c_idx:04d}")
                os.makedirs(chunk_dir, exist_ok=True)
                list_file = os.path.join(chunk_dir, "frames.txt")

                # Số worker theo hardware profile để cân bằng tốc độ, RAM và socket.
                download_tasks = [(i, m, chunk_dir) for i, m in enumerate(chunk_frames)]
                downloaded_items = []

                with ThreadPoolExecutor(max_workers=download_workers) as executor:
                    for res in executor.map(_download_single_frame, download_tasks):
                        if res is not None:
                            downloaded_items.append(res)

                downloaded_items.sort(key=lambda x: x[0])
                downloaded = len(downloaded_items)
                rendered_frame_count += downloaded
                render.item_count = rendered_frame_count

                last_dst = None
                with open(list_file, "w") as lf:
                    for _, dst in downloaded_items:
                        lf.write(f"file '{dst}'\n")
                        lf.write(f"duration {1.0 / max(render.fps, 1):.4f}\n")
                        last_dst = dst
                    if last_dst:
                        # FFmpeg concat demuxer yêu cầu lặp lại file cuối cùng không có duration
                        lf.write(f"file '{last_dst}'\n")

                if downloaded == 0:
                    continue

                # Render phân đoạn này ra file MPEG-TS tạm
                seg_path = os.path.join(tmpdir, f"segment_{c_idx:04d}.ts")
                cmd = [
                    "nice", "-n", "19",
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", list_file,
                    "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
                    "-c:v", "libx264",
                    # Output option: đặt sau encoder để giới hạn đúng x264 threads.
                    "-threads", ffmpeg_threads,
                    "-preset", ffmpeg_preset,
                    "-crf", "23",
                    "-pix_fmt", "yuv420p",
                ]
                if is_4k:
                    lookahead = getattr(settings, "FFMPEG_4K_LOOKAHEAD", 10)
                    cmd.extend(["-x264-params", f"rc-lookahead={lookahead}:ref=2"])
                cmd.append(seg_path)
                result = subprocess.run(cmd, capture_output=True, timeout=1800)  # noqa: S603
                if result.returncode != 0:
                    if result.returncode in (-9, 137):
                        raise RuntimeError(
                            "FFmpeg bị hệ thống dừng do thiếu RAM. "
                            "Hãy giảm độ phân giải hoặc tăng giới hạn RAM render."
                        )
                    err_msg = _parse_ffmpeg_error(result.stderr)
                    raise RuntimeError(f"Lỗi render segment {c_idx}: {err_msg}")

                segment_paths.append(seg_path)

                # Dọn dẹp ảnh tạm của chunk để giải phóng đĩa cứng & RAM
                for f in os.listdir(chunk_dir):
                    os.remove(os.path.join(chunk_dir, f))
                os.rmdir(chunk_dir)

                # Cập nhật tiến độ UI (tải + render từng chunk chiếm từ 0% → 90%)
                pct = min(90, int((c_idx + 1) / total_chunks_upper * 90))
                render.progress = pct
                render.save(update_fields=["progress", "item_count"])

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
                err_msg = _parse_ffmpeg_error(res_concat.stderr)
                raise RuntimeError(f"Lỗi ghép video: {err_msg}")

            render.progress = 95
            render.save(update_fields=["progress"])

            # Upload MP4 lên R2 output bucket (derived data, TTL 7 ngày).
            # upload_file stream từ disk, tránh nạp cả video vào RAM.
            out_storage = "r2" if _r2_enabled() else "seaweed"
            out_bucket = "output" if out_storage == "r2" else "media"
            output_key = (
                f"{render.camera_id}/renders/"
                f"{render.date_from}_{render.date_to}_{render.fps}fps_{render.resolution}_{render.id}.mp4"
            )
            file_size = os.path.getsize(output_path)
            storage.upload_file(
                output_key,
                output_path,
                content_type="video/mp4",
                storage=out_storage,
                r2_output=(out_bucket == "output"),
            )

        now = timezone.now()
        render.status = VideoRender.Status.READY
        render.output_key = output_key
        render.output_storage = out_storage
        render.output_bucket = out_bucket
        render.size_bytes = file_size
        render.progress = 100
        render.ready_at = now
        render.expires_at = now + timedelta(days=getattr(settings, "VIDEO_RENDER_TTL_DAYS", 7))
        skipped_frames = max(0, selected_frame_count - rendered_frame_count)
        render.error = (
            f"Cảnh báo: bỏ qua {skipped_frames} ảnh không tải/đọc được."
            if skipped_frames else ""
        )
        render.save(update_fields=[
            "status", "output_key", "output_storage", "output_bucket",
            "size_bytes", "progress", "ready_at", "expires_at", "error",
        ])

    except Exception as exc:  # noqa: BLE001
        render.status = VideoRender.Status.FAILED
        render.error = str(exc)[:1000]
        render.save(update_fields=["status", "error"])


@shared_task(queue="default")
def recover_stale_jobs():
    """Đưa job bị worker bỏ dở ra khỏi PROCESSING để UI không kẹt vĩnh viễn."""
    cutoff = timezone.now() - timedelta(hours=3)
    from django.db.models import Q

    render_count = VideoRender.objects.filter(
        status=VideoRender.Status.PROCESSING,
    ).filter(
        Q(processing_started_at__lt=cutoff)
        | Q(processing_started_at__isnull=True, created_at__lt=cutoff)
    ).update(
        status=VideoRender.Status.FAILED,
        error="Worker bị gián đoạn hoặc job quá thời gian. Hãy tạo lại render.",
    )
    archive_count = MediaArchive.objects.filter(
        status=MediaArchive.Status.PROCESSING,
    ).filter(
        Q(processing_started_at__lt=cutoff)
        | Q(processing_started_at__isnull=True, created_at__lt=cutoff)
    ).update(
        status=MediaArchive.Status.FAILED,
        error="Worker bị gián đoạn hoặc job quá thời gian. Hãy tạo lại ZIP.",
    )

    # Đưa các CameraDevice bị kẹt powering_on / shutting_down quá 60s về off
    from core.models.camera import CameraDevice
    cutoff_cm4 = timezone.now() - timedelta(seconds=60)
    cm4_reset_count = CameraDevice.objects.filter(
        cm4_power_state__in=[CameraDevice.CM4State.POWERING_ON, CameraDevice.CM4State.SHUTTING_DOWN],
        updated_at__lt=cutoff_cm4,
    ).update(cm4_power_state=CameraDevice.CM4State.OFF)

    return {"renders": render_count, "archives": archive_count, "cm4_resets": cm4_reset_count}


@shared_task(queue="default")
def sync_postgres_backups_to_r2():
    """Đồng bộ tối đa 7 pg_dump local gần nhất sang R2 output bucket."""
    backup_dir = "/backup"
    if not _r2_enabled() or not os.path.isdir(backup_dir):
        return 0
    uploaded = 0
    paths = sorted(
        (
            os.path.join(backup_dir, name)
            for name in os.listdir(backup_dir)
            if name.endswith(".dump")
        ),
        key=os.path.getmtime,
        reverse=True,
    )[:7]
    for path in paths:
        key = f"backups/postgres/{os.path.basename(path)}"
        size = os.path.getsize(path)
        if storage.head_size(key, storage="r2", r2_output=True) == size:
            continue
        storage.upload_file(
            key,
            path,
            content_type="application/octet-stream",
            storage="r2",
            r2_output=True,
        )
        uploaded += 1
    return uploaded
