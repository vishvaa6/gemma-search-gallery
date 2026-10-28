"""Bounded preview generation shared by ingestion and HTTP requests."""
import asyncio
import os
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

from gpu_guard import can_use_gpu, maybe_reclaim_vram
from priority import TaskPriority, priority_manager

PREVIEW_SIZE = 400
_cpu_cores = os.cpu_count() or 4
# Dedicated pool for real-time video player frame decoding & timeline scrubbing
_video_pool = ThreadPoolExecutor(max_workers=min(8, max(4, _cpu_cores)), thread_name_prefix="video-render")
# Pool for interactive user gallery browsing and search results
_preview_pool = ThreadPoolExecutor(max_workers=min(8, max(4, _cpu_cores)), thread_name_prefix="preview-render")
# Bounded low-concurrency pool for background ingestion / preheating
_bg_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bg-preview")
_pool = _preview_pool
_pending = {}
_publish_locks = [threading.Lock() for _ in range(64)]

# Check if ffmpeg binary is present on PATH
_FFMPEG_PATH = shutil.which("ffmpeg")


def save_preview_bytes(data: bytes, target) -> Path:
    """Atomically write raw JPEG bytes to target path."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        with _publish_locks[hash(str(target)) % len(_publish_locks)]:
            if not target.is_file():
                os.replace(name, target)
    finally:
        if os.path.exists(name):
            try:
                os.unlink(name)
            except OSError:
                pass
    return target


def save_preview(image, target):
    """Publish a small, oriented JPEG atomically, including on Windows."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    preview = ImageOps.exif_transpose(image)
    preview.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE), Image.Resampling.LANCZOS)
    if preview.mode != "RGB":
        converted = preview.convert("RGB")
        preview.close()
        preview = converted
    fd, name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as output:
            preview.save(output, format="JPEG", quality=80)
        # Ingestion and a foreground request may publish the same version at
        # once. Avoid Windows replacement races and never expose partial bytes.
        with _publish_locks[hash(str(target)) % len(_publish_locks)]:
            if not target.is_file():
                os.replace(name, target)
    finally:
        preview.close()
        if os.path.exists(name):
            try:
                os.unlink(name)
            except OSError:
                pass
    return target


def _ffmpeg_fast_extract(path: str, timestamp: float, use_cuda: bool = False) -> bytes | None:
    """Uses FFmpeg container-level fast keyframe seeking to decode a 400px JPEG in ~100ms."""
    if not _FFMPEG_PATH:
        return None
    cmd = [_FFMPEG_PATH, "-y", "-loglevel", "error"]
    if use_cuda:
        cmd.extend(["-hwaccel", "cuda"])
    cmd.extend([
        "-ss", str(max(0.0, float(timestamp))),
        "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale={PREVIEW_SIZE}:-1",
        "-f", "image2",
        "-c:v", "mjpeg",
        "-q:v", "3",
        "pipe:1"
    ])
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=4.0
        )
        if proc.returncode == 0 and proc.stdout and len(proc.stdout) > 500:
            return proc.stdout
    except Exception:
        pass
    return None


def generate_preview(path, timestamp, target, cv2_module=None):
    if Path(target).is_file():
        return Path(target)
    if timestamp is None:
        with Image.open(path) as image:
            image.draft("RGB", (PREVIEW_SIZE, PREVIEW_SIZE))
            return save_preview(image, target)

    # 1. First attempt: Fast FFmpeg input seek (with GPU NVDEC if safe VRAM headroom exists)
    gpu_safe = can_use_gpu(required_mb=650.0)
    if gpu_safe:
        raw_jpeg = _ffmpeg_fast_extract(str(path), timestamp, use_cuda=True)
        if raw_jpeg:
            return save_preview_bytes(raw_jpeg, target)

    # 2. Second attempt: Fast FFmpeg input seek on CPU demuxer (~100ms)
    raw_jpeg = _ffmpeg_fast_extract(str(path), timestamp, use_cuda=False)
    if raw_jpeg:
        return save_preview_bytes(raw_jpeg, target)

    # 3. Third attempt: Robust OpenCV fallback with step-back keyframe tolerance
    if cv2_module is None:
        raise RuntimeError("Video previews require OpenCV or FFmpeg")
    cv = cv2_module
    capture = cv.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError("Could not open video")

        ok = False
        frame = None
        if timestamp > 0:
            capture.set(cv.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
        else:
            ok, frame = capture.read()

        if not ok or frame is None:
            fps = float(capture.get(cv.CAP_PROP_FPS) or 0)
            if fps > 0 and timestamp > 0:
                capture.set(cv.CAP_PROP_POS_FRAMES, int(round(timestamp * fps)))
                ok, frame = capture.read()

        # Step-back keyframe tolerance for long videos or sparse keyframes
        if (not ok or frame is None) and timestamp > 0:
            fps = float(capture.get(cv.CAP_PROP_FPS) or 0)
            for delta in (-1.0, -2.0, -3.0, -5.0, 1.0, 2.0):
                adj_ts = timestamp + delta
                if adj_ts < 0:
                    continue
                capture.set(cv.CAP_PROP_POS_MSEC, adj_ts * 1000)
                ok, frame = capture.read()
                if ok and frame is not None:
                    break
                if fps > 0:
                    capture.set(cv.CAP_PROP_POS_FRAMES, int(round(adj_ts * fps)))
                    ok, frame = capture.read()
                    if ok and frame is not None:
                        break

        # Resilient fallback to first available frame rather than failing with 422
        if not ok or frame is None:
            capture.set(cv.CAP_PROP_POS_FRAMES, 0)
            ok, frame = capture.read()

        # If OpenCV still failed, attempt FFmpeg keyframe at 0.0s so thumbnails never fail
        if not ok or frame is None:
            raw_zero = _ffmpeg_fast_extract(str(path), 0.0, use_cuda=False)
            if raw_zero:
                return save_preview_bytes(raw_zero, target)
            raise ValueError(f"Could not decode any video frame for {path} at timestamp {timestamp}")

        height, width = frame.shape[:2]
        if max(height, width) > PREVIEW_SIZE:
            scale = PREVIEW_SIZE / max(height, width)
            frame = cv.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv.INTER_AREA)
        with Image.fromarray(cv.cvtColor(frame, cv.COLOR_BGR2RGB)) as image:
            return save_preview(image, target)
    finally:
        capture.release()


async def ensure_preview(path, timestamp, target, cv2_module=None, priority: TaskPriority = TaskPriority.GALLERY):
    """Coalesce duplicate requests with priority-based worker routing.

    Video player frames are routed to a dedicated _video_pool to guarantee immediate
    playback/scrubbing without waiting behind photo thumbnails. Search previews are prioritized
    before gallery thumbnails, and background previews yield when foreground is active.
    """
    if timestamp is not None and priority > TaskPriority.VIDEO:
        priority = TaskPriority.VIDEO

    if priority == TaskPriority.VIDEO:
        priority_manager.mark_video_active(3.0)
        target_pool = _video_pool
    elif priority == TaskPriority.SEARCH:
        priority_manager.mark_search_active(2.0)
        target_pool = _preview_pool
    elif priority == TaskPriority.BACKGROUND:
        await priority_manager.yield_if_foreground_active(max_wait_sec=1.5)
        target_pool = _bg_pool
    else:
        target_pool = _preview_pool

    key = str(target)
    task = _pending.get(key)
    if task is None:
        async def run():
            try:
                return await asyncio.get_running_loop().run_in_executor(
                    target_pool, generate_preview, path, timestamp, target, cv2_module
                )
            finally:
                _pending.pop(key, None)
                maybe_reclaim_vram()
        task = asyncio.create_task(run())
        _pending[key] = task
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
    return await asyncio.shield(task)
