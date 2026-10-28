"""
FastAPI Server for Multimodal Photo Search Gallery.
Provides real-time GPU-accelerated semantic search, visual similarity search,
adjustable Matryoshka Representation Learning (MRL default 768d), instant folder
inspection with time estimates, pause/resume indexing across app restarts,
and live photo streaming.
"""

import os
import sys
import time
import json
import shutil
import tempfile
import mimetypes
import asyncio
import threading
import queue
import io
import gc
import atexit
import signal
import string
import random
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable

# Silence verbose libavformat / FFmpeg warnings (e.g. partial file / offset warnings on seeking)
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
os.environ["AV_LOG_FORCE_NOCOLOR"] = "1"

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        # Prevent Windows Proactor event loop from dumping tracebacks on client socket disconnects during streaming
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _orig_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost

        def _silent_call_connection_lost(self, exc):
            try:
                _orig_call_connection_lost(self, exc)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass
            except OSError as os_err:
                if getattr(os_err, "winerror", None) in (10053, 10054):
                    pass
                else:
                    raise

        _ProactorBasePipeTransport._call_connection_lost = _silent_call_connection_lost
    except Exception:
        pass

from fastapi import FastAPI, UploadFile, File, Form, Query, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel, Field
import pyarrow as pa
import lancedb
from PIL import Image, ImageOps
import numpy as np
import subprocess
import hashlib
try:
    import cv2
    if cv2 is not None:
        try:
            cv2.setLogLevel(0)
        except Exception:
            pass
except ImportError:
    cv2 = None

from engine import MultimodalEngine
from face_engine import FaceEngine, MODEL_VERSION as FACE_MODEL_VERSION

BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scanner import (
    discover_media_files,
    is_path_excluded,
    SUPPORTED_IMAGE_EXTS,
    SUPPORTED_VIDEO_EXTS,
    SUPPORTED_MEDIA_EXTS,
)

# Standard media extension sets
image_exts = SUPPORTED_IMAGE_EXTS
video_exts = SUPPORTED_VIDEO_EXTS
from location_index import (
    set_active_folder,
    get_thumb_target_path,
    get_cached_duration,
    record_file_metadata,
    batch_record_files,
    get_thumbnails_dir,
    APP_DATA_ROOT,
)

from media_cache import ensure_preview, save_preview
from index_maintenance import maintain_tables
from gpu_guard import can_use_gpu, reclaim_vram, maybe_reclaim_vram, get_vram_status, gpu_vector_index
from priority import TaskPriority, priority_manager, PriorityInferenceGate

_inference_gate = PriorityInferenceGate()
_inference_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")

async def _infer(method, *args, request=None, **kwargs):
    # Check disconnect immediately so abandoned searches never wait for GPU slots
    if request is not None and await request.is_disconnected():
        raise HTTPException(status_code=499, detail="Search cancelled")
    priority = kwargs.get("priority", TaskPriority.SEARCH if method in (engine.embed_text, engine.embed_image) else TaskPriority.BACKGROUND)
    await _inference_gate.acquire(priority)
    try:
        if request is not None and await request.is_disconnected():
            raise HTTPException(status_code=499, detail="Search cancelled")
        work = asyncio.get_running_loop().run_in_executor(_inference_pool, method, *args)
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # A thread cannot be interrupted safely. Retain ownership of its PIL
            # inputs and GPU slot until it finishes, then propagate cancellation.
            await asyncio.shield(work)
            raise
    finally:
        _inference_gate.release()


app = FastAPI(
    title="EmbeddingGemma 2 Multimodal Gallery API",
    description="Real-Time GPU-Accelerated Photo Search with Adjustable MRL (Default 768d)",
    version=(BACKEND_ROOT.parent / "VERSION").read_text(encoding="utf-8").strip()
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

PICKER_SCRIPT = Path(__file__).parent / "pick_folder.py"

def prompt_windows_folder_picker() -> Optional[str]:
    """Runs standalone pick_folder.py in an isolated process with its own message pump and topmost focus."""
    try:
        res = subprocess.run(
            [sys.executable, str(PICKER_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=180
        )
        folder = res.stdout.strip()
        if folder and os.path.isdir(folder):
            return str(Path(folder).resolve())
    except Exception as e:
        print(f"[!] Error invoking pick_folder script: {e}")
    return None

# Initialize Multimodal Engine on GPU with default 768d
engine = MultimodalEngine(canonical_dim=768, default_target_dim=768, initialize_model=False)

# Setup LanceDB with canonical 768-dimensional vector schema
BACKEND_ROOT = Path(__file__).resolve().parent
DATA_DIR = (APP_DATA_ROOT / "lancedb").resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
db = lancedb.connect(str(DATA_DIR))

schema = pa.schema([
    pa.field("id", pa.string()),
    pa.field("filename", pa.string()),
    pa.field("path", pa.string()),
    pa.field("size_bytes", pa.int64()),
    pa.field("width", pa.int32()),
    pa.field("height", pa.int32()),
    pa.field("vector", pa.list_(pa.float32(), 768)),
])

table = db.create_table("photos", schema=schema, exist_ok=True)
video_schema = schema.append(pa.field("timestamp", pa.float64()))
video_table = db.create_table("video_frames", schema=video_schema, exist_ok=True)
catalog_schema = pa.schema([
    pa.field("id", pa.string()), pa.field("media_type", pa.string()),
    pa.field("filename", pa.string()), pa.field("path", pa.string()),
    pa.field("size_bytes", pa.int64()), pa.field("width", pa.int32()),
    pa.field("height", pa.int32()), pa.field("timestamp", pa.float64()),
])
catalog_table = db.create_table("media_catalog", schema=catalog_schema, exist_ok=True)
face_schema = pa.schema([
    pa.field("id", pa.string()), pa.field("source_id", pa.string()),
    pa.field("filename", pa.string()), pa.field("path", pa.string()),
    pa.field("media_type", pa.string()), pa.field("size_bytes", pa.int64()),
    pa.field("width", pa.int32()), pa.field("height", pa.int32()),
    pa.field("timestamp", pa.float64()), pa.field("face_index", pa.int32()),
    pa.field("bbox", pa.list_(pa.float32(), 4)),
    pa.field("vector", pa.list_(pa.float32(), 128)),
])
face_table = db.create_table("face_embeddings", schema=face_schema, exist_ok=True)
face_source_schema = pa.schema([
    pa.field("source_id", pa.string()), pa.field("media_type", pa.string()),
    pa.field("path", pa.string()), pa.field("timestamp", pa.float64()),
    pa.field("model_version", pa.string()), pa.field("face_count", pa.int32()),
    pa.field("indexed_at", pa.float64()),
])
face_source_table = db.create_table("face_sources", schema=face_source_schema, exist_ok=True)
face_engine = FaceEngine(cv2, APP_DATA_ROOT / "face_models")
SETTINGS_FILE = (APP_DATA_ROOT / "app_settings.json").resolve()
FACE_STATE_FILE = (APP_DATA_ROOT / "face_index_state.json").resolve()
_settings_lock = threading.RLock()
_face_table_lock = threading.RLock()
_face_state_lock = threading.RLock()
_face_generation = {"photos": 0, "videos": 0}
_face_tasks = {"photos": None, "videos": None}
_face_task_generation = {"photos": None, "videos": None}
_face_enabled_field = {"photos": "face_search_photos_enabled", "videos": "face_search_videos_enabled"}


def _load_json(path, defaults):
    try:
        with open(path, "r", encoding="utf-8") as source:
            data = json.load(source)
        return {**defaults, **data} if isinstance(data, dict) else dict(defaults)
    except (OSError, ValueError, TypeError):
        return dict(defaults)


app_settings = _load_json(SETTINGS_FILE, {
    "show_filenames": False,
    "face_search_photos_enabled": False,
    "face_search_videos_enabled": False,
})
face_index_state = _load_json(FACE_STATE_FILE, {
    "photos": {"status": "disabled", "processed": 0, "total": 0, "percent": 0.0, "current": None, "error": None},
    "videos": {"status": "disabled", "processed": 0, "total": 0, "percent": 0.0, "current": None, "error": None},
})
for _kind in ("photos", "videos"):
    face_index_state.setdefault(_kind, {"status": "disabled", "processed": 0, "total": 0, "percent": 0.0, "current": None, "error": None})
    if app_settings.get(_face_enabled_field[_kind]):
        if face_index_state[_kind].get("status") in {"in_progress", "queued", "loading_models"}:
            face_index_state[_kind]["status"] = "queued"
    else:
        face_index_state[_kind].update({"status": "disabled", "current": None, "error": None})


def _save_json_atomic(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8") as output:
        json.dump(data, output, indent=2, ensure_ascii=True)
    os.replace(temp, path)


def _persist_settings():
    with _settings_lock:
        _save_json_atomic(SETTINGS_FILE, app_settings)


def _update_face_state(kind: str, **updates):
    with _face_state_lock:
        face_index_state[kind].update(updates)
        try:
            _save_json_atomic(FACE_STATE_FILE, face_index_state)
        except OSError as exc:
            print(f"[!] Could not persist face-index progress: {exc}")


_catalog_lock = threading.Lock()
_index_write_lock = threading.Lock()
_catalog_marker = DATA_DIR / "media_catalog_v1.ready"
_catalog_backfilled = _catalog_marker.exists() and (len(catalog_table) > 0 or (len(table) == 0 and len(video_table) == 0))
_ann_index_status = {"photos": "not_started", "video_frames": "not_started"}
_ann_generation = 0
_maintenance_status = {"status": "waiting", "tables": {}}


async def _maintain_index_when_idle():
    # Keep the first gallery paint and accelerator warmup ahead of disk work.
    await asyncio.sleep(30)
    while session_manager.active_session.get("status") == "in_progress" or engine.model_status in {"loading", "not_started"}:
        await asyncio.sleep(5)
    def work():
        # Serialize against commits/clear. HTTP reads remain available.
        with _index_write_lock, _face_table_lock:
            if session_manager.active_session.get("status") == "in_progress":
                return {}
            return maintain_tables(
                [("photos", table), ("video_frames", video_table), ("media_catalog", catalog_table),
                 ("face_embeddings", face_table), ("face_sources", face_source_table)],
                APP_DATA_ROOT / "maintenance.json",
                lambda: session_manager.active_session.get("status") != "in_progress",
            )
    _maintenance_status["status"] = "running"
    try:
        _maintenance_status["tables"] = await asyncio.to_thread(work)
        for name in ("photos", "video_frames"):
            result = _maintenance_status["tables"].get(name)
            if result:
                _ann_index_status[name] = "ready" if result.get("vector_index") else "exact_scan_small_library"
        _maintenance_status["status"] = "complete"
    except Exception as exc:
        _maintenance_status.update(status="error", error=str(exc))
        print(f"[!] Database maintenance: {exc}")


def _metadata_page(source_table, columns, offset=0, limit=1000):
    """Read a bounded metadata-only page; never materialize embedding vectors."""
    if len(source_table) == 0:
        return []
    return source_table.search().select(columns).limit(limit).offset(offset).to_list()


def _metadata_rows(source_table, columns, page_size=5000):
    """Read all requested metadata in bounded pages, projecting before retrieval."""
    rows = []
    total = len(source_table)
    for offset in range(0, total, page_size):
        rows.extend(_metadata_page(source_table, columns, offset, page_size))
    return rows


def _catalog_record(row, media_type, timestamp=None):
    return {
        "id": str(row["id"]), "media_type": media_type,
        "filename": str(row["filename"]), "path": str(row["path"]),
        "size_bytes": int(row.get("size_bytes", 0) or 0),
        "width": int(row.get("width", 0) or 0), "height": int(row.get("height", 0) or 0),
        "timestamp": float(timestamp if timestamp is not None else row.get("timestamp", 0.0) or 0.0),
    }


def _backfill_catalog():
    """One-time migration for libraries created before the metadata catalog existed."""
    global _catalog_backfilled, catalog_table
    if _catalog_backfilled:
        return
    with _catalog_lock:
        if _catalog_backfilled:
            return
        # No ready marker means a prior migration may have been interrupted or only
        # partially populated. Rebuild this derived metadata table from vector rows.
        try:
            catalog_table = db.create_table("media_catalog", schema=catalog_schema, mode="overwrite")
        except Exception:
            try:
                db.drop_table("media_catalog")
            except Exception:
                pass
            catalog_table = db.create_table("media_catalog", schema=catalog_schema, exist_ok=True)
        rows = []
        for offset in range(0, len(table), 1000):
            for row in _metadata_page(table, ["id", "filename", "path", "size_bytes", "width", "height"], offset, 1000):
                rows.append(_catalog_record(row, "image"))
        earliest = {}
        for offset in range(0, len(video_table), 1000):
            for row in _metadata_page(video_table, ["id", "filename", "path", "size_bytes", "width", "height", "timestamp"], offset, 1000):
                current = earliest.get(row["path"])
                if current is None or float(row.get("timestamp", 0.0)) < float(current.get("timestamp", 0.0)):
                    earliest[row["path"]] = row
        rows.extend(_catalog_record(row, "video") for row in earliest.values())
        for start in range(0, len(rows), 1000):
            catalog_table.add(rows[start:start + 1000])
        _catalog_marker.touch()
        _catalog_backfilled = True
        _invalidate_catalog_cache()


_catalog_cache_lock = threading.Lock()
_catalog_cache: Optional[List[Dict[str, Any]]] = None
_catalog_cache_len: int = -1


def _invalidate_catalog_cache():
    global _catalog_cache, _catalog_cache_len
    with _catalog_cache_lock:
        _catalog_cache = None
        _catalog_cache_len = -1


def _get_catalog_items() -> List[Dict[str, Any]]:
    global _catalog_cache, _catalog_cache_len
    with _catalog_cache_lock:
        current_len = len(catalog_table) if "catalog_table" in globals() else 0
        if _catalog_cache is None or _catalog_cache_len != current_len:
            _backfill_catalog()
            current_len = len(catalog_table)
            if current_len == 0:
                _catalog_cache = []
            else:
                try:
                    arrow_tbl = catalog_table.to_arrow()
                    _catalog_cache = arrow_tbl.to_pylist()
                except Exception:
                    _catalog_cache = catalog_table.search().select(
                        ["id", "media_type", "filename", "path", "size_bytes", "width", "height", "timestamp"]
                    ).to_list()
            _catalog_cache_len = current_len
        return list(_catalog_cache)


def _remove_catalog_path(path):
    try:
        catalog_table.delete("path = '" + path.replace("'", "''") + "'")
        _invalidate_catalog_cache()
    except Exception:
        pass


async def _build_ann_indexes():
    """Build IVF-Flat vector indexes for large tables off the API startup path."""
    generation = _ann_generation
    for name, source_table in (("photos", table), ("video_frames", video_table)):
        if generation != _ann_generation:
            return
        if _ann_index_status.get(name) in {"ready", "building"}:
            continue
        if len(source_table) < 1000:
            _ann_index_status[name] = "exact_scan_small_library"
            continue
        try:
            existing = list(source_table.list_indices())
            if any("vector" in getattr(index, "columns", []) for index in existing):
                _ann_index_status[name] = "ready"
                continue
        except Exception:
            pass
        _ann_index_status[name] = "building"
        try:
            await asyncio.to_thread(
                source_table.create_index,
                vector_column_name="vector", metric="l2",
                num_partitions=max(16, min(256, int(len(source_table) ** 0.5))),
                index_type="IVF_FLAT",
            )
            if generation != _ann_generation:
                return
            _ann_index_status[name] = "ready"
        except Exception as exc:
            if generation != _ann_generation:
                return
            # LanceDB versions differ in index signatures; vector search still works without an index.
            _ann_index_status[name] = "unavailable"
            print(f"[!] Could not build {name} ANN index; using LanceDB scan search: {exc}")


def _scan_watched_folder_for_new_media() -> Dict[str, Any]:
    """
    Fast delta scan of the remembered/completed folder to detect any newly added
    or modified photos and videos without re-indexing already completed files.
    """
    state = dict(session_manager.active_session)
    current_status = state.get("status")
    if current_status in ("in_progress", "paused"):
        return {
            "status": current_status,
            "new_files_detected": 0,
            "new_images_detected": 0,
            "new_videos_detected": 0,
            "directory": state.get("directory") or state.get("last_selected_directory"),
        }

    watched_dir = state.get("directory") or state.get("last_selected_directory")
    if not watched_dir:
        return {"status": "no_folder", "new_files_detected": 0, "new_images_detected": 0, "new_videos_detected": 0, "directory": None}

    p = Path(watched_dir)
    if not p.exists() or not p.is_dir() or is_path_excluded(p):
        return {"status": "unavailable", "new_files_detected": 0, "new_images_detected": 0, "new_videos_detected": 0, "directory": watched_dir}

    normalized_dir = str(p.resolve())
    try:
        photos, videos = discover_media_files(p)
        all_files = photos + videos
    except Exception as exc:
        return {"status": "error", "error": str(exc), "new_files_detected": 0, "directory": normalized_dir}

    total_files = len(all_files)
    if total_files == 0:
        return {"status": "empty", "new_files_detected": 0, "new_images_detected": 0, "new_videos_detected": 0, "directory": normalized_dir}

    image_rows = _metadata_rows(table, ["id", "path"])
    image_ids_by_norm_path: Dict[str, set] = {}
    for row in image_rows:
        if row.get("path"):
            image_ids_by_norm_path.setdefault(os.path.normcase(str(row["path"])), set()).add(row["id"])

    video_rows = _metadata_rows(video_table, ["id", "path"])
    video_ids_by_norm_path: Dict[str, set] = {}
    for row in video_rows:
        if row.get("path"):
            video_ids_by_norm_path.setdefault(os.path.normcase(str(row["path"])), set()).add(row["id"])

    failed_norm = {
        os.path.normcase(str(fp))
        for fp in (state.get("failed_paths") or [])
        if fp
    }
    saved_frame_rate = float(state.get("frame_rate") or (1.0 / 30.0))

    def _is_vid_done(v_path: str, ids: set) -> bool:
        if not ids:
            return False
        dur = get_video_duration(v_path)
        if dur <= 0:
            return len(ids) >= 4
        if dur < 5.0:
            return len(ids) >= 1
        if len(ids) < 4:
            return False
        max_ts = max(
            (int(vid.split("_")[-1]) for vid in ids if vid.split("_")[-1].isdigit()),
            default=0,
        ) / 1000.0
        user_interval_sec = 1.0 / max(0.01, saved_frame_rate)
        tolerance = max(15.0, user_interval_sec * 2.5)
        return max_ts >= max(0.0, dur - tolerance)

    new_images = 0
    new_videos = 0
    for f in all_files:
        norm_p = os.path.normcase(str(f.resolve()))
        if norm_p in failed_norm:
            continue
        ext = f.suffix.lower()
        if ext in image_exts:
            existing_ids = image_ids_by_norm_path.get(norm_p)
            if not existing_ids:
                new_images += 1
            else:
                try:
                    stat = f.stat()
                    expected_id = f"{f.name}_{stat.st_mtime_ns}"
                    if expected_id not in existing_ids:
                        new_images += 1
                except OSError:
                    pass
        else:
            existing_vids = video_ids_by_norm_path.get(norm_p, set())
            if not existing_vids:
                new_videos += 1
            else:
                try:
                    resolved_str = str(f.resolve())
                    stat = f.stat()
                    prefix = hashlib.sha1(f"{resolved_str}:{stat.st_mtime_ns}".encode()).hexdigest()[:20]
                    matching_ids = {v for v in existing_vids if v.startswith(f"video_{prefix}_")}
                    if not _is_vid_done(resolved_str, matching_ids):
                        new_videos += 1
                except OSError:
                    pass

    new_total = new_images + new_videos
    already_done = max(0, total_files - new_total)

    with _index_write_lock:
        live_status = session_manager.active_session.get("status")
        if live_status not in ("in_progress", "paused"):
            prev_pending = bool(session_manager.active_session.get("auto_sync_pending"))
            prev_new = int(session_manager.active_session.get("new_files_detected", 0) or 0)
            if new_total > 0:
                if not prev_pending or prev_new != new_total:
                    print(
                        f"[*] Auto-Watcher detected {new_total} new/unindexed media file(s) in {normalized_dir} "
                        f"({new_images} photos, {new_videos} videos)."
                    )
                session_manager.save_state({
                    "directory": normalized_dir,
                    "last_selected_directory": normalized_dir,
                    "total_files": total_files,
                    "processed_files": already_done,
                    "indexed_count": already_done,
                    "percent": round((already_done / max(1, total_files)) * 100.0, 1),
                    "new_files_detected": new_total,
                    "new_images_detected": new_images,
                    "new_videos_detected": new_videos,
                    "auto_sync_pending": True,
                })
            elif prev_pending or prev_new > 0:
                session_manager.save_state({
                    "new_files_detected": 0,
                    "new_images_detected": 0,
                    "new_videos_detected": 0,
                    "auto_sync_pending": False,
                })

    return {
        "status": "new_media_detected" if new_total > 0 else "up_to_date",
        "directory": normalized_dir,
        "total_files": total_files,
        "already_indexed": already_done,
        "new_files_detected": new_total,
        "new_images_detected": new_images,
        "new_videos_detected": new_videos,
    }


_backfill_progress = {"status": "idle", "processed": 0, "total": 0, "created": 0}


async def _background_thumbnail_backfill_loop():
    """Warm gallery covers gently through the same bounded preview service."""
    await asyncio.sleep(15)
    try:
        await asyncio.to_thread(_backfill_catalog)
        total = await asyncio.to_thread(len, catalog_table)
        _backfill_progress.update(status="running", total=total)
        for offset in range(0, total, 100):
            rows = await asyncio.to_thread(_metadata_page, catalog_table, ["path", "media_type", "timestamp"], offset, 100)
            for row in rows:
                while session_manager.active_session.get("status") == "in_progress":
                    await asyncio.sleep(2)
                await priority_manager.yield_if_foreground_active(max_wait_sec=2.0)
                try:
                    path = row["path"]
                    timestamp = float(row.get("timestamp") or 0) if row["media_type"] == "video" else None
                    preferred, cached = await asyncio.to_thread(get_thumb_target_path, path, timestamp)
                    if cached is None:
                        await ensure_preview(path, timestamp, preferred, cv2, priority=TaskPriority.BACKGROUND)
                        _backfill_progress["created"] += 1
                except (OSError, ValueError, RuntimeError):
                    pass
                _backfill_progress["processed"] += 1
                await asyncio.sleep(0.05)
        _backfill_progress["status"] = "complete"
    except Exception as exc:
        _backfill_progress["status"] = "error"
        print(f"[!] Preview backfill failed: {exc}")


async def _background_folder_watcher_loop():
    """Periodically checks the watched folder for newly added photos and videos."""
    await asyncio.sleep(45.0)
    while True:
        try:
            current_status = session_manager.active_session.get("status")
            if current_status not in ("in_progress", "paused"):
                await asyncio.to_thread(_scan_watched_folder_for_new_media)
        except Exception as exc:
            print(f"[!] Background folder watcher error: {exc}")
        await asyncio.sleep(300.0)


@app.on_event("startup")
async def start_background_initialization():
    saved_dir = session_manager.active_session.get("directory") or session_manager.active_session.get("last_selected_directory")
    if saved_dir:
        await asyncio.to_thread(set_active_folder, saved_dir)

    asyncio.create_task(asyncio.to_thread(engine.initialize))
    asyncio.create_task(_background_thumbnail_backfill_loop())

    async def _delayed_startup_tasks():
        # Wait gracefully for model initialization before warming GPU in-memory index
        for _ in range(45):
            if getattr(engine, "model_status", "") in ("ready", "fallback", "error"):
                break
            await asyncio.sleep(1.0)
        if getattr(engine, "model_status", "") == "ready":
            asyncio.create_task(asyncio.to_thread(gpu_vector_index.warm, table, video_table))
        asyncio.create_task(_maintain_index_when_idle())
        asyncio.create_task(_background_folder_watcher_loop())
        for kind in ("photos", "videos"):
            if app_settings.get(_face_enabled_field[kind]):
                needs_rescan = (
                    face_index_state[kind].get("status") != "complete"
                    or face_index_state[kind].get("model_version") != FACE_MODEL_VERSION
                    or int(face_index_state[kind].get("total", 0) or 0) != len(_face_source_table(kind))
                )
                if needs_rescan:
                    _schedule_face_backfill(kind)

    asyncio.create_task(_delayed_startup_tasks())


@app.on_event("shutdown")
def on_app_shutdown():
    session_manager.on_shutdown()


def _face_kind_label(kind: str) -> str:
    return "image" if kind == "photos" else "video"


def _face_source_table(kind: str):
    return table if kind == "photos" else video_table


def _face_source_columns(kind: str):
    columns = ["id", "filename", "path", "size_bytes", "width", "height"]
    if kind == "videos":
        columns.append("timestamp")
    return columns


def _face_kind_enabled(kind: str) -> bool:
    with _settings_lock:
        return bool(app_settings.get(_face_enabled_field[kind], False))


def _quote_sql(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _read_face_source_image(row: Dict[str, Any], kind: str) -> Image.Image:
    source_path = Path(row["path"])
    if not source_path.is_file():
        raise FileNotFoundError(f"Media file is missing: {source_path}")
    if kind == "photos":
        with Image.open(source_path) as source:
            try:
                upright = ImageOps.exif_transpose(source)
            except Exception:
                upright = source
            return upright.convert("RGB")
    if cv2 is None:
        raise RuntimeError("Video face matching requires OpenCV contrib. Run install.bat and restart.")
    capture = cv2.VideoCapture(str(source_path))
    try:
        if not capture.isOpened():
            raise RuntimeError(f"Video could not be opened: {source_path}")
        target_ts = max(0.0, float(row.get("timestamp", 0.0) or 0.0))
        capture.set(cv2.CAP_PROP_POS_MSEC, target_ts * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            if fps > 0:
                capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(target_ts * fps))))
                ok, frame = capture.read()
        if not ok or frame is None:
            raise RuntimeError(f"Could not read sampled video frame at {target_ts} seconds")
        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        capture.release()


def _face_model_download_progress(kind: str, model: str, downloaded: int, total: int):
    updates = {"status": "downloading_models", "current": f"Downloading {model} face model"}
    if total > 0:
        updates["model_percent"] = round(downloaded * 100.0 / total, 1)
        updates["model_downloaded_bytes"] = downloaded
        updates["model_total_bytes"] = total
    _update_face_state(kind, **updates)


def _index_face_source_sync(kind: str, source: Dict[str, Any], generation: Optional[int] = None) -> int:
    """Idempotently index one already-Gemma-indexed image or sampled video frame."""
    if not _face_kind_enabled(kind):
        return 0
    if generation is not None and generation != _face_generation[kind]:
        return 0

    source_id = str(source["id"])
    media_type = _face_kind_label(kind)
    escaped_source_id = source_id.replace("'", "''")
    marker_filter = (
        f"source_id = '{escaped_source_id}' AND media_type = '{media_type}' "
        f"AND model_version = '{FACE_MODEL_VERSION}'"
    )
    if face_source_table.search().where(marker_filter).limit(1).to_list():
        return 0

    _update_face_state(kind, status="in_progress", current=source.get("filename") or source_id, error=None)
    image = _read_face_source_image(source, kind)
    faces = face_engine.analyze(
        image,
        progress=lambda name, downloaded, total: _face_model_download_progress(kind, name, downloaded, total),
    )

    face_rows = []
    for detected in faces:
        vector = np.asarray(detected["vector"], dtype=np.float32)
        if vector.shape != (128,) or not np.isfinite(vector).all():
            continue
        norm = float(np.linalg.norm(vector))
        if not 0.98 <= norm <= 1.02:
            continue
        face_index = int(detected["face_index"])
        face_id = "face_" + hashlib.sha1(
            f"{media_type}:{source_id}:{face_index}:{FACE_MODEL_VERSION}".encode("utf-8")
        ).hexdigest()
        face_rows.append({
            "id": face_id,
            "source_id": source_id,
            "filename": str(source.get("filename", Path(source["path"]).name)),
            "path": str(source["path"]),
            "media_type": media_type,
            "size_bytes": int(source.get("size_bytes", 0) or 0),
            "width": int(image.width or source.get("width", 0) or 0),
            "height": int(image.height or source.get("height", 0) or 0),
            "timestamp": float(source.get("timestamp", 0.0) or 0.0),
            "face_index": face_index,
            "bbox": [float(value) for value in detected["bbox"]],
            "vector": vector.tolist(),
        })

    with _face_table_lock:
        if not _face_kind_enabled(kind) or (generation is not None and generation != _face_generation[kind]):
            return 0
        # A parallel backfill or a newly indexed item may have completed this source first.
        if face_source_table.search().where(marker_filter).limit(1).to_list():
            return 0
        face_table.delete(
            f"source_id = '{escaped_source_id}' AND media_type = '{media_type}'"
        )
        face_source_table.delete(
            f"source_id = '{escaped_source_id}' AND media_type = '{media_type}'"
        )
        if face_rows:
            face_table.add(face_rows)
        face_source_table.add([{
            "source_id": source_id,
            "media_type": media_type,
            "path": str(source["path"]),
            "timestamp": float(source.get("timestamp", 0.0) or 0.0),
            "model_version": FACE_MODEL_VERSION,
            "face_count": len(face_rows),
            "indexed_at": time.time(),
        }])
    return len(face_rows)


def _run_face_backfill(kind: str, generation: int):
    source_table = _face_source_table(kind)
    total = len(source_table)
    _update_face_state(kind, status="loading_models", processed=0, total=total, percent=0.0,
                       current="Preparing local face models", error=None, failed_count=0,
                       model_percent=None, model_version=FACE_MODEL_VERSION)
    try:
        face_engine.ensure_ready(
            progress=lambda name, downloaded, expected: _face_model_download_progress(kind, name, downloaded, expected)
        )
        # Clean up any stale face_source_table markers from older model versions so they get re-scanned
        media_type = _face_kind_label(kind)
        with _face_table_lock:
            try:
                face_source_table.delete(f"media_type = '{media_type}' AND model_version != '{FACE_MODEL_VERSION}'")
            except Exception:
                pass
        _update_face_state(kind, status="in_progress", current="Scanning faces (multi-scale high-recall mode)", model_percent=None)
        processed = 0
        failed = 0
        last_error = None
        columns = _face_source_columns(kind)
        for offset in range(0, total, 128):
            rows = _metadata_page(source_table, columns, offset, 128)
            for row in rows:
                if generation != _face_generation[kind] or not _face_kind_enabled(kind):
                    return
                priority_manager.sync_yield_if_foreground_active(max_wait_sec=2.0)
                try:
                    _index_face_source_sync(kind, row, generation)
                except Exception as exc:
                    failed += 1
                    last_error = f"{row.get('filename') or row['path']}: {exc}"
                    print(f"[!] Face index skipped {row.get('path')}: {exc}")
                processed += 1
                _update_face_state(
                    kind,
                    status="in_progress",
                    processed=processed,
                    total=total,
                    percent=round(processed * 100.0 / max(1, total), 1),
                    current=row.get("filename") or row["path"],
                    failed_count=failed,
                    error=last_error,
                    model_version=FACE_MODEL_VERSION,
                )
        if generation == _face_generation[kind] and _face_kind_enabled(kind):
            _update_face_state(kind, status="complete", processed=total, total=total, percent=100.0,
                               current=None, failed_count=failed, error=last_error, model_version=FACE_MODEL_VERSION)
    except Exception as exc:
        print(f"[!] Face {kind} indexing failed: {exc}")
        if generation == _face_generation[kind] and _face_kind_enabled(kind):
            _update_face_state(kind, status="error", current=None, error=str(exc))


def _schedule_face_backfill(kind: str):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = _face_tasks.get(kind)
    if task is not None and not task.done() and _face_task_generation.get(kind) == _face_generation[kind]:
        return
    _face_generation[kind] += 1
    generation = _face_generation[kind]
    _update_face_state(kind, status="queued", total=len(_face_source_table(kind)), current="Waiting for local face index", error=None)
    _face_tasks[kind] = loop.create_task(asyncio.to_thread(_run_face_backfill, kind, generation))
    _face_task_generation[kind] = generation


async def _index_new_face_source(kind: str, source: Dict[str, Any]):
    if not _face_kind_enabled(kind):
        return
    generation = _face_generation[kind]
    try:
        await asyncio.to_thread(_index_face_source_sync, kind, source, generation)
        task = _face_tasks.get(kind)
        if task is None or task.done():
            total = len(_face_source_table(kind))
            with _face_state_lock:
                processed = min(total, int(face_index_state[kind].get("processed", 0) or 0) + 1)
            _update_face_state(kind, status="complete" if processed >= total else "in_progress",
                               processed=processed, total=total,
                               percent=round(processed * 100.0 / max(1, total), 1), current=None, error=None)
    except Exception as exc:
        print(f"[!] Could not index face data for {source.get('path')}: {exc}")
        if _face_kind_enabled(kind):
            _update_face_state(kind, error=str(exc))


def _schedule_new_face_sources(kind: str, rows: List[Dict[str, Any]]):
    if not _face_kind_enabled(kind):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for row in rows:
        loop.create_task(_index_new_face_source(kind, row))

# -------------------------------------------------------------------
# Persistent Indexing Session Manager & Newly Added Media Tracker
# -------------------------------------------------------------------

STATE_FILE = (APP_DATA_ROOT / "indexing_state.json").resolve()
NEWLY_ADDED_FILE = (APP_DATA_ROOT / "newly_added.json").resolve()
_newly_added_lock = threading.Lock()
_newly_added_cache: List[Dict[str, Any]] = []

def _load_newly_added() -> List[Dict[str, Any]]:
    global _newly_added_cache
    with _newly_added_lock:
        if not _newly_added_cache and NEWLY_ADDED_FILE.is_file():
            try:
                with open(NEWLY_ADDED_FILE, "r", encoding="utf-8") as f:
                    _newly_added_cache = json.load(f)
            except Exception:
                _newly_added_cache = []
        return list(_newly_added_cache)

def _record_newly_added(items: List[Dict[str, Any]]):
    global _newly_added_cache
    if not items:
        return
    with _newly_added_lock:
        existing_paths = {it.get("path") for it in _newly_added_cache}
        fresh = []
        for it in items:
            p = it.get("path")
            if p and p not in existing_paths:
                existing_paths.add(p)
                fresh.append(it)
        if fresh:
            _newly_added_cache = (fresh + _newly_added_cache)[:500]
            try:
                with open(NEWLY_ADDED_FILE, "w", encoding="utf-8") as f:
                    json.dump(_newly_added_cache, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

def _clear_newly_added():
    global _newly_added_cache
    with _newly_added_lock:
        _newly_added_cache = []
        try:
            if NEWLY_ADDED_FILE.is_file():
                NEWLY_ADDED_FILE.unlink()
        except Exception:
            pass

class IndexingSessionManager:
    def __init__(self):
        self._state_lock = threading.RLock()
        self.active_session: Dict[str, Any] = {
            "status": "idle",
            "directory": None,
            "last_selected_directory": None,
            "total_files": 0,
            "indexed_count": 0,
            "current_file": None,
            "speed_fps": 0.0,
            "percent": 0.0,
            "eta_seconds": 0.0,
            "is_paused": False,
        }
        self.is_paused: bool = False
        self.is_cancelled: bool = False
        self.generation: int = 0
        self.pause_event = asyncio.Event()
        self.pause_event.set()
        self._load_persisted_state()

    def _load_persisted_state(self):
        """Loads saved session state from disk. Marks interrupted jobs as paused for resume."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("status") in ("in_progress", "paused"):
                        data["status"] = "paused"
                        data["is_paused"] = True
                        self.active_session = data
                        self.is_paused = True
                        self.pause_event.clear()
                        self.save_state({"status": "paused", "is_paused": True})
                        print(f"[*] Recovered indexing session for resume: {data.get('directory')} (processed {data.get('processed_files', data.get('indexed_count', 0))}/{data.get('total_files')})")
                    else:
                        self.active_session = data
                    self.active_session.setdefault(
                        "last_selected_directory", self.active_session.get("directory")
                    )
            except Exception as e:
                print(f"[!] Error loading indexing state from disk: {e}")

    def on_shutdown(self):
        """Called on application exit or shutdown signal to guarantee checkpoint persistence."""
        current_status = self.active_session.get("status")
        if current_status in ("in_progress", "paused"):
            self.is_paused = True
            self.save_state({"status": "paused", "is_paused": True})
            print(f"[*] Persisted indexing checkpoint on shutdown: {self.active_session.get('processed_files', 0)}/{self.active_session.get('total_files', 0)} files.")

    def save_state(self, updates: Dict[str, Any]):
        with self._state_lock:
            self._save_state_locked(updates)

    def _save_state_locked(self, updates: Dict[str, Any]):
        self.active_session.update(updates)
        self.active_session["updated_at"] = time.time()
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range(3):
                try:
                    temp_file = STATE_FILE.with_name(f"indexing_state_tmp_{os.getpid()}.json")
                    with open(temp_file, "w", encoding="utf-8") as f:
                        json.dump(self.active_session, f, indent=2)
                    try:
                        os.replace(temp_file, STATE_FILE)
                        return
                    except OSError:
                        # On Windows, replace can transiently fail if file is held open; fallback to direct write
                        with open(STATE_FILE, "w", encoding="utf-8") as f:
                            json.dump(self.active_session, f, indent=2)
                        try:
                            if temp_file.exists():
                                temp_file.unlink()
                        except Exception:
                            pass
                        return
                except Exception as inner_e:
                    if attempt == 2:
                        raise inner_e
                    time.sleep(0.05)
        except Exception as e:
            print(f"[!] Error saving indexing state: {e}")

    def remember_folder(self, directory: str):
        """Persist the user's latest folder independently from indexing status."""
        self.save_state({"last_selected_directory": directory})

    def pause(self):
        self.is_paused = True
        self.pause_event.clear()
        self.save_state({"status": "paused", "is_paused": True})

    def resume(self):
        self.is_paused = False
        self.pause_event.set()
        self.save_state({"status": "in_progress", "is_paused": False})

    def cancel(self):
        self.generation += 1
        self.is_cancelled = True
        self.is_paused = False
        self.pause_event.set()
        self.save_state({
            "status": "idle",
            "directory": None,
            "total_files": 0,
            "indexed_count": 0,
            "percent": 0.0,
            "is_paused": False
        })

    def reset(self):
        """Stops indexing and forgets its saved checkpoint without touching source media."""
        self.generation += 1
        self.is_cancelled = True
        self.is_paused = False
        self.pause_event.set()
        last_selected_directory = self.active_session.get("last_selected_directory")
        self.active_session = {
            "status": "idle", "directory": None, "total_files": 0,
            "last_selected_directory": last_selected_directory,
            "indexed_count": 0, "indexed_frames": 0, "current_file": None,
            "speed_fps": 0.0, "percent": 0.0, "eta_seconds": 0.0,
            "is_paused": False,
        }
        self.save_state({})

session_manager = IndexingSessionManager()
atexit.register(session_manager.on_shutdown)

_win32_ctrl_handler_ref = None
if os.name == "nt":
    try:
        import ctypes
        import signal as _sig
        if hasattr(_sig, "SIGBREAK"):
            _sig.signal(_sig.SIGBREAK, lambda *_: session_manager.on_shutdown())

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
        def _win32_console_ctrl_handler(ctrl_type):
            # 0=CTRL_C_EVENT, 1=CTRL_BREAK_EVENT, 2=CTRL_CLOSE_EVENT, 5=CTRL_LOGOFF_EVENT, 6=CTRL_SHUTDOWN_EVENT
            if ctrl_type in (0, 1, 2, 5, 6):
                try:
                    session_manager.on_shutdown()
                except Exception:
                    pass
            return False

        _win32_ctrl_handler_ref = _win32_console_ctrl_handler
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_win32_ctrl_handler_ref, True)
    except Exception:
        pass

# -------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------

STOPWORDS = {"a", "an", "the", "at", "in", "on", "of", "for", "to", "with", "and", "or", "by", "photo", "image", "picture"}

def calculate_accuracy(
    similarity: float,
    search_mode: str = "text",
    z_score: float = 0.0
) -> Tuple[float, float, str]:
    """
    Converts cosine similarity into a ranking guidance score and tier, not a probability.
    The display score combines similarity and relative candidate separation. It is a ranking aid,
    not a calibrated probability; candidate search means its relative component is local to this result set.
    """
    sim_clamped = max(0.0, min(1.0, float(similarity)))

    if search_mode == "image":
        # Image-to-image cosine baseline is ~0.60, self/near-duplicate is 0.85-1.00
        if sim_clamped >= 0.995:
            accuracy_percent = 99.9
        else:
            norm_img = (sim_clamped - 0.55) / 0.40
            accuracy_percent = round(float(np.clip(50.0 + norm_img * 49.0, 15.0, 99.5)), 1)
    else:
        # Blend an absolute similarity signal with separation from the candidate-pool mean.
        abs_score = 100.0 / (1.0 + np.exp(-28.0 * (sim_clamped - 0.575)))
        z_score_pct = 100.0 / (1.0 + np.exp(-1.35 * (z_score - 0.25)))
        blended = 0.55 * abs_score + 0.45 * z_score_pct
        accuracy_percent = round(float(np.clip(blended, 12.0, 99.4)), 1)

    if accuracy_percent >= 85.0:
        tier = "exceptional"
    elif accuracy_percent >= 72.0:
        tier = "high"
    elif accuracy_percent >= 58.0:
        tier = "moderate"
    else:
        tier = "loose"

    return round(sim_clamped, 4), accuracy_percent, tier


_query_embed_cache: Dict[Tuple[str, int], List[float]] = {}
_query_embed_lock = threading.Lock()


def execute_mrl_search(
    query_vec_768: List[float],
    mrl_dim: int = 768,
    limit: int = 30,
    search_mode: str = "text",
    query_text: str = "",
    source_table=None,
    media_type: str = "image",
) -> List[Dict[str, Any]]:
    """
    Retrieve candidates in canonical space, then rerank their truncated vectors.
    Reduced dimensions are approximate candidate reranking, not an exhaustive
    search of the truncated corpus.
    """
    # LanceTable truthiness depends on its row count. Preserve an explicitly supplied
    # empty video table instead of accidentally searching the photo table twice.
    if source_table is None:
        source_table = table
    if len(source_table) == 0:
        return []

    if mrl_dim not in [128, 256, 512, 768]:
        mrl_dim = 768

    # 1. Primary: Instant GPU Tensor-Core vector dot-product search (<20ms) if index is warmed
    records = None
    raw_sims = None
    if source_table in (None, table, video_table):
        gpu_res = gpu_vector_index.search(query_vec_768, mrl_dim=mrl_dim, limit=limit, media_type=media_type)
        if gpu_res is not None:
            records, raw_sims = gpu_res

    # 2. Fallback to LanceDB PyArrow scanner
    if records is None:
        candidate_limit = min(len(source_table), max(80, min(360, limit * 4 if media_type == "video" else limit * 6)))
        query_full = np.asarray(query_vec_768[:768], dtype=np.float32)
        query_full /= np.linalg.norm(query_full) + 1e-12

        # Include _distance to eliminate the LanceDB scanner deprecation warning
        columns = ["id", "filename", "path", "size_bytes", "width", "height", "vector", "_distance"]
        if media_type == "video":
            columns.append("timestamp")

        mat_normed = None
        try:
            arrow_tbl = source_table.search(query_full.tolist()).select(columns).distance_type("l2").limit(candidate_limit).to_arrow()
            if len(arrow_tbl) == 0:
                return []
            chunked = arrow_tbl["vector"].combine_chunks()
            flat_vals = np.asarray(chunked.values.to_numpy(zero_copy_only=False), dtype=np.float32)
            mat_full = flat_vals.reshape(len(arrow_tbl), -1)[:, :mrl_dim]
            mat_norms = np.linalg.norm(mat_full, axis=1, keepdims=True) + 1e-12
            mat_normed = mat_full / mat_norms
            meta_cols = [c for c in arrow_tbl.column_names if c not in ("vector", "_distance")]
            records = arrow_tbl.select(meta_cols).to_pylist()
        except Exception:
            try:
                records = source_table.search(query_full.tolist()).distance_type("l2").limit(candidate_limit).to_list()
            except (AttributeError, TypeError):
                records = source_table.search(query_full.tolist()).limit(candidate_limit).to_list()
            if not records:
                return []
            mat_full = np.asarray([r["vector"][:mrl_dim] for r in records], dtype=np.float32)
            mat_norms = np.linalg.norm(mat_full, axis=1, keepdims=True) + 1e-12
            mat_normed = mat_full / mat_norms

        # Slice and L2-normalize query vector to matching mrl_dim
        q_sub = np.array(query_vec_768[:mrl_dim], dtype=np.float32)
        q_normed = q_sub / (np.linalg.norm(q_sub) + 1e-12)

        # Exact cosine similarity across ANN candidates (GPU accelerated for large pools).
        if can_use_gpu(required_mb=300.0) and len(records) >= 300:
            try:
                import torch as _torch
                q_t = _torch.from_numpy(q_normed).to("cuda", non_blocking=True)
                mat_t = _torch.from_numpy(mat_normed).to("cuda", non_blocking=True)
                raw_sims = _torch.mv(mat_t, q_t).cpu().numpy()
            except Exception:
                raw_sims = (mat_normed @ q_normed).astype(np.float32)
        else:
            raw_sims = (mat_normed @ q_normed).astype(np.float32)

    # Optional lexical filename token boost for text queries
    query_tokens = [
        tok for tok in query_text.lower().replace("_", " ").replace("-", " ").split()
        if len(tok) >= 2 and tok not in STOPWORDS
    ]
    if query_tokens and search_mode == "text":
        for idx, r in enumerate(records):
            fname_clean = r["filename"].lower().replace("_", " ").replace("-", " ").replace(".", " ")
            matched = sum(1 for tok in query_tokens if tok in fname_clean)
            if matched > 0:
                raw_sims[idx] += min(0.08, matched * 0.045)

    # Compute corpus distribution statistics for relative z-score calibration
    mean_sim = float(np.mean(raw_sims)) if len(raw_sims) > 1 else 0.20
    std_sim = float(np.std(raw_sims)) if len(raw_sims) > 1 else 0.05
    std_safe = max(std_sim, 0.025)

    scored = []
    for idx, r in enumerate(records):
        sim_val = float(raw_sims[idx])
        dist_val = max(0.0, 1.0 - sim_val)
        z_val = (sim_val - mean_sim) / std_safe if len(raw_sims) > 2 else 1.5
        sim, acc_pct, tier = calculate_accuracy(sim_val, search_mode=search_mode, z_score=z_val)

        is_video_result = media_type == "video" and Path(r["path"]).suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
        scored.append({
            "id": r["id"],
            "filename": r["filename"],
            "path": r["path"],
            "size_bytes": int(r.get("size_bytes", 0)),
            "width": int(r.get("width", 0)),
            "height": int(r.get("height", 0)),
            "media_type": "video" if is_video_result else "image",
            "timestamp": float(r.get("timestamp", 0.0)) if is_video_result else None,
            "similarity": sim,
            "raw_distance": round(dist_val, 4),
            "accuracy_percent": acc_pct,
            "accuracy_tier": tier,
        })

    scored.sort(key=lambda x: (x["similarity"], x["accuracy_percent"]), reverse=True)
    return scored[:limit]


VIDEO_DURATIONS_FILE = (APP_DATA_ROOT / "video_durations.json").resolve()
_durations_lock = threading.Lock()

def _load_video_durations() -> Dict[str, float]:
    if not VIDEO_DURATIONS_FILE.exists():
        return {}
    try:
        with open(VIDEO_DURATIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except Exception:
        return {}

def _save_video_durations(cache: Dict[str, float]) -> None:
    try:
        VIDEO_DURATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(VIDEO_DURATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass

_video_duration_cache: Dict[str, float] = _load_video_durations()

def get_video_duration(video_path: str, cached_only: bool = False) -> float:
    if not video_path:
        return 0.0
    with _durations_lock:
        if video_path in _video_duration_cache:
            return _video_duration_cache[video_path]
    sqlite_dur = get_cached_duration(video_path)
    if sqlite_dur is not None and sqlite_dur > 0.0:
        with _durations_lock:
            _video_duration_cache[video_path] = sqlite_dur
        return sqlite_dur
    if cached_only:
        return 0.0
    dur = 0.0
    if cv2 is not None:
        try:
            p = Path(video_path)
            if p.is_file():
                cap = cv2.VideoCapture(str(p))
                try:
                    if cap.isOpened():
                        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                        cnt = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
                        dur = round(cnt / fps, 2) if fps > 0 else 0.0
                finally:
                    cap.release()
        except Exception:
            dur = 0.0
    with _durations_lock:
        _video_duration_cache[video_path] = dur
    if dur > 0.0:
        try:
            p = Path(video_path)
            record_file_metadata(
                path=video_path, filename=p.name, folder=str(p.parent),
                media_type="video", mtime_ns=p.stat().st_mtime_ns if p.is_file() else 0,
                size_bytes=p.stat().st_size if p.is_file() else 0, duration=dur
            )
        except Exception:
            pass
    return dur


def collapse_video_matches(results: List[Dict[str, Any]], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Keep one strongest sampled-frame match per video in visible search results."""
    visible = {}
    for row in results:
        if row.get("media_type") != "video":
            visible[("image", row.get("id"))] = row
            continue
        key = ("video", row.get("path"))
        current = visible.get(key)
        if current is None or float(row.get("similarity", 0.0)) > float(current.get("similarity", 0.0)):
            matched = dict(row)
            matched["matched_timestamp"] = float(row.get("timestamp", 0.0) or 0.0)
            matched["duration"] = get_video_duration(row.get("path"), cached_only=True)
            matched["result_label"] = "Best sampled frame match"
            visible[key] = matched
    ordered = sorted(visible.values(), key=lambda item: (item.get("similarity", 0.0), item.get("accuracy_percent", 0.0)), reverse=True)
    return ordered[:limit] if limit is not None else ordered

# -------------------------------------------------------------------
# Request / Response Schemas
# -------------------------------------------------------------------

class TextSearchQuery(BaseModel):
    query: str = Field(..., max_length=2000, description="Natural language search prompt")
    mrl_dim: int = Field(768, description="Matryoshka representation dimension (default 768 for max accuracy)")
    limit: int = Field(30, ge=1, le=200, description="Max number of results to return")

class SimilarQuery(BaseModel):
    photo_id: str = Field(..., description="ID of photo to find similar matches for")
    mrl_dim: int = Field(768, description="Matryoshka representation dimension (default 768)")
    limit: int = Field(12, ge=1, le=200, description="Max number of recommendations")


class SettingsUpdate(BaseModel):
    show_filenames: Optional[bool] = None
    face_search_photos_enabled: Optional[bool] = None
    face_search_videos_enabled: Optional[bool] = None


class FaceSearchQuery(BaseModel):
    media_id: str = Field(..., min_length=1)
    media_type: str = Field(..., pattern="^(image|video)$")
    face_index: int = Field(..., ge=0)
    limit: int = Field(40, ge=1, le=200)


class OpenWithRequest(BaseModel):
    media_id: str = Field(..., min_length=1)
    media_type: str = Field(..., pattern="^(image|video)$")

class FolderInspectRequest(BaseModel):
    directory: str = Field(..., description="Directory path on local disk to inspect")
    frame_rate: float = Field(0.5, ge=0.01, le=5.0, description="Video frames sampled per second (e.g. 0.0333 for 1 frame every 30s)")
    adaptive_sampling: bool = Field(True, description="Enable smart scene-change adaptive frame sampling")

# -------------------------------------------------------------------
# REST API Endpoints
# -------------------------------------------------------------------

@app.get("/api/system/status")
def system_status():
    """Live GPU telemetry, VRAM stats, and indexed photo counts."""
    telemetry = engine.get_device_info()
    telemetry.update({
        "indexed_count": len(table),
        "indexed_video_frames": len(video_table),
        "indexed_media_count": len(catalog_table),
        "indexed_faces": len(face_table),
        "video_processing_available": cv2 is not None,
        "model_id": engine.model_id,
        "default_mrl_dim": 768,
        "available_mrl_dims": [128, 256, 512, 768],
        "active_indexing": session_manager.active_session,
        "model_status": engine.model_status,
        "model_stage": engine.model_stage,
        "model_error": engine.model_error,
        "vector_indexes": dict(_ann_index_status),
        "maintenance": dict(_maintenance_status),
        "thumbnail_backfill": dict(_backfill_progress),
        "face_index": {kind: dict(state) for kind, state in face_index_state.items()},
        "priority": priority_manager.get_status(),
        "gpu_vector_index_warmed": gpu_vector_index.is_warmed,
    })
    return telemetry


@app.get("/api/system/priority")
def get_system_priority():
    """Live scheduler priority status and active foreground state."""
    return priority_manager.get_status()


def _require_model_ready():
    if engine.model_status != "ready" or not engine.model_loaded:
        raise HTTPException(status_code=503, detail="Semantic search requires the real model. Check model status and retry when ready.")


@app.get("/api/settings")
def get_settings():
    with _settings_lock, _face_state_lock:
        return {"settings": dict(app_settings), "face_index": {kind: dict(state) for kind, state in face_index_state.items()}}


@app.patch("/api/settings")
async def update_settings(payload: SettingsUpdate):
    changes = payload.model_dump(exclude_none=True)
    turning_off = []
    turning_on = []
    with _settings_lock:
        for key, value in changes.items():
            old_value = bool(app_settings.get(key, False))
            app_settings[key] = bool(value)
            if key == "face_search_photos_enabled" and old_value != bool(value):
                (turning_on if value else turning_off).append("photos")
            elif key == "face_search_videos_enabled" and old_value != bool(value):
                (turning_on if value else turning_off).append("videos")
        _persist_settings()

    for kind in turning_off:
        _face_generation[kind] += 1
        media_type = _face_kind_label(kind)
        with _face_table_lock:
            face_table.delete(f"media_type = '{media_type}'")
            face_source_table.delete(f"media_type = '{media_type}'")
        _update_face_state(kind, status="disabled", processed=0, total=0, percent=0.0,
                           current=None, error=None, model_percent=None, failed_count=0)
    for kind in turning_on:
        _update_face_state(kind, status="queued", processed=0, total=len(_face_source_table(kind)),
                           percent=0.0, current="Waiting for local face index", error=None,
                           failed_count=0, model_percent=None)
        _schedule_face_backfill(kind)

    with _settings_lock, _face_state_lock:
        return {"settings": dict(app_settings), "face_index": {kind: dict(state) for kind, state in face_index_state.items()}}

@app.get("/api/system/audit")
def audit_image_processing(sample_size: int = Query(8, ge=1, le=32)):
    """Re-embeds a small indexed-photo sample and validates vector shape and normalization."""
    rows = table.to_arrow().slice(0, sample_size).to_pylist() if len(table) else []
    items = []
    for row in rows:
        item = {"filename": row["filename"], "ok": False, "reason": None}
        try:
            with Image.open(row["path"]) as source:
                image = source.convert("RGB")
            vector = np.asarray(engine.embed_image(image, 768), dtype=np.float32)
            stored = np.asarray(row["vector"], dtype=np.float32)
            valid = vector.shape == (768,) and stored.shape == (768,) and np.isfinite(vector).all() and np.isfinite(stored).all()
            norm = float(np.linalg.norm(vector)) if valid else 0.0
            stored_norm = float(np.linalg.norm(stored)) if valid else 0.0
            cosine = float(np.dot(vector, stored) / max(1e-12, norm * stored_norm)) if valid and stored_norm > 0 else 0.0
            valid = valid and 0.98 <= norm <= 1.02 and 0.98 <= stored_norm <= 1.02 and cosine >= 0.95
            item.update({"ok": bool(valid), "embedding_dim": int(vector.size), "norm": round(norm, 5), "stored_cosine": round(cosine, 5)})
            if not valid:
                item["reason"] = "Embedding failed dimension, finite-value, or normalization checks"
        except Exception as exc:
            item["reason"] = str(exc)
        items.append(item)
    return {"model_loaded": engine.model_loaded, "is_synthetic": engine.is_synthetic, "model_id": engine.model_id,
            "sampled": len(items), "passed": sum(1 for item in items if item["ok"]), "items": items}

def _inspect_folder_work(directory: str, frame_rate: float, progress: Optional[Callable[[Dict[str, Any]], None]] = None, adaptive_sampling: bool = True):
    clean_dir = (directory or "").strip().strip('"').strip("'")
    p = Path(clean_dir)
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail="Directory not found or inaccessible")
    session_manager.remember_folder(str(p.resolve()))

    valid_exts = SUPPORTED_IMAGE_EXTS
    video_exts = SUPPORTED_VIDEO_EXTS
    supported_exts = valid_exts | video_exts

    photos, videos = discover_media_files(p, on_progress=progress)
    all_files = photos + videos
    total_files = len(all_files)
    visited = total_files
    if progress:
        progress({"event": "progress", "phase": "checking", "scanned": visited,
                  "checked": 0, "total": total_files, "percent": 0.0,
                  "current_file": "Comparing files with the index"})

    image_rows = _metadata_rows(table, ["id", "path"])
    image_ids_by_path: Dict[str, set] = {}
    for row in image_rows:
        image_ids_by_path.setdefault(row["path"], set()).add(row["id"])
    video_rows = _metadata_rows(video_table, ["id", "path"])
    video_ids_by_path = {}
    for row in video_rows:
        video_ids_by_path.setdefault(row["path"], set()).add(row["id"])

    unindexed_count = 0
    partially_indexed_count = 0
    sample_files = []
    image_count = 0
    video_count = 0
    for index, file_path in enumerate(all_files, 1):
        try:
            stat = file_path.stat()
            resolved = str(file_path.resolve())
            is_image = file_path.suffix.lower() in valid_exts
            if is_image:
                image_count += 1
                current_id = f"{file_path.name}_{stat.st_mtime_ns}"
                is_indexed = current_id in image_ids_by_path.get(resolved, set())
            else:
                video_count += 1
                prefix = hashlib.sha1(f"{resolved}:{stat.st_mtime_ns}".encode()).hexdigest()[:20]
                existing_video_ids = {
                    value for value in video_ids_by_path.get(resolved, set())
                    if value.startswith(f"video_{prefix}_")
                }
                is_indexed = False
                if cv2 is not None and existing_video_ids:
                    capture = cv2.VideoCapture(str(file_path))
                    try:
                        source_fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
                        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
                        if capture.isOpened() and source_fps > 0 and frame_count > 0:
                            duration = frame_count / source_fps
                            interval = 1.0 / frame_rate
                            matching_timestamps = [
                                int(vid.split("_")[-1]) for vid in existing_video_ids
                            ]
                            if matching_timestamps:
                                max_ts = max(matching_timestamps) / 1000.0
                                # A video is complete if its frames reach near duration
                                is_indexed = max_ts >= max(0.0, duration - max(8.0, interval * 3))
                                if not is_indexed:
                                    partially_indexed_count += 1
                    finally:
                        capture.release()
            if not is_indexed:
                unindexed_count += 1
            if len(sample_files) < 4:
                sample_files.append({"name": file_path.name, "path": resolved, "size_bytes": stat.st_size})
        except Exception as exc:
            if progress:
                progress({"event": "progress", "phase": "checking", "checked": index,
                          "total": total_files, "percent": round(index * 100.0 / max(1, total_files), 1),
                          "current_file": file_path.name, "warning": str(exc)})
            continue
        if progress and (index % 10 == 0 or index == total_files):
            progress({"event": "progress", "phase": "checking", "checked": index,
                      "total": total_files, "percent": round(index * 100.0 / max(1, total_files), 1),
                      "current_file": file_path.name})

    already_indexed = total_files - unindexed_count
    return {
        "status": "success", "directory": str(p.resolve()), "total_files": total_files,
        "already_indexed": already_indexed, "unindexed_files": unindexed_count,
        "partially_indexed": partially_indexed_count, "estimated_seconds": 0.0,
        "estimated_time_str": "Already indexed" if unindexed_count == 0 else "Measured ETA appears after indexing starts",
        "estimated_fps": 0, "samples": sample_files,
        "image_files": image_count, "video_files": video_count,
    }


@app.post("/api/folder/inspect")
def inspect_folder(payload: FolderInspectRequest):
    return _inspect_folder_work(payload.directory, payload.frame_rate, adaptive_sampling=payload.adaptive_sampling)


@app.get("/api/folder/inspect/stream")
async def inspect_folder_stream(
    directory: str = Query(...),
    frame_rate: float = Query(0.5, ge=0.01, le=5.0, description="Video frames sampled per second (e.g. 0.0333 for 1 frame every 30s)"),
    adaptive_sampling: bool = Query(True, description="Enable smart scene-change adaptive frame sampling"),
):
    clean_dir = (directory or "").strip().strip('"').strip("'")
    p = Path(clean_dir)
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail="Directory not found or inaccessible")
    event_queue = queue.Queue()

    def worker():
        try:
            result = _inspect_folder_work(clean_dir, frame_rate, event_queue.put, adaptive_sampling=adaptive_sampling)
            event_queue.put({"event": "complete", "result": result})
        except HTTPException as exc:
            event_queue.put({"event": "error", "message": str(exc.detail)})
        except Exception as exc:
            event_queue.put({"event": "error", "message": str(exc)})

    task = asyncio.create_task(asyncio.to_thread(worker))

    async def event_generator():
        while True:
            try:
                event = await asyncio.to_thread(event_queue.get, True, 0.25)
            except queue.Empty:
                if task.done():
                    break
                continue
            yield f"data: {json.dumps(event, ensure_ascii=True)}\n\n"
            if event.get("event") in {"complete", "error"}:
                break
        await task

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

@app.get("/api/index/state")
def get_indexing_state():
    """Returns current active or persisted indexing state (supports resume on app restart)."""
    state = dict(session_manager.active_session)
    saved_directory = state.get("last_selected_directory") or state.get("directory")
    active_directory = state.get("directory")
    state["last_selected_directory_available"] = bool(saved_directory and Path(saved_directory).is_dir())
    state["directory_available"] = bool(active_directory and Path(active_directory).is_dir())
    return state


@app.post("/api/index/check-new")
def check_new_media():
    """Scans the remembered folder for newly added or modified photos and videos."""
    result = _scan_watched_folder_for_new_media()
    result["state"] = get_indexing_state()
    return result

@app.post("/api/index/pause")
def pause_indexing():
    """Pauses the active indexing process immediately."""
    with _index_write_lock:
        session_manager.pause()
    return {"status": "success", "message": "Indexing paused", "state": session_manager.active_session}

@app.post("/api/index/resume")
def resume_indexing():
    """Resumes paused indexing process."""
    if _maintenance_status["status"] == "running":
        raise HTTPException(status_code=409, detail="Database optimization is running. Retry resume shortly.")
    with _index_write_lock:
        session_manager.resume()
    return {"status": "success", "message": "Indexing resumed", "state": session_manager.active_session}

@app.post("/api/index/cancel")
def cancel_indexing():
    """Cancels and clears the current indexing job."""
    with _index_write_lock:
        session_manager.cancel()
    return {"status": "success", "message": "Indexing cancelled"}

@app.post("/api/index/clear")
@app.delete("/api/index/clear")
def clear_all_indexing():
    """Clears all indexed photos from LanceDB and resets any saved indexing session state."""
    global table, video_table, catalog_table, face_table, face_source_table, _catalog_backfilled, _ann_generation
    try:
        # Serialize clearing against in-flight batch writes so a cancelled batch
        # cannot repopulate freshly cleared tables.
        with _index_write_lock, _catalog_lock, _face_table_lock:
            session_manager.reset()
            for kind in ("photos", "videos"):
                _face_generation[kind] += 1
            try:
                table = db.create_table("photos", schema=schema, mode="overwrite")
            except Exception:
                try:
                    db.drop_table("photos")
                except Exception:
                    pass
                table = db.create_table("photos", schema=schema, exist_ok=True)

            try:
                video_table = db.create_table("video_frames", schema=video_schema, mode="overwrite")
            except Exception:
                try:
                    db.drop_table("video_frames")
                except Exception:
                    pass
                video_table = db.create_table("video_frames", schema=video_schema, exist_ok=True)

            try:
                catalog_table = db.create_table("media_catalog", schema=catalog_schema, mode="overwrite")
            except Exception:
                try:
                    db.drop_table("media_catalog")
                except Exception:
                    pass
                catalog_table = db.create_table("media_catalog", schema=catalog_schema, exist_ok=True)
            try:
                face_table = db.create_table("face_embeddings", schema=face_schema, mode="overwrite")
            except Exception:
                try:
                    db.drop_table("face_embeddings")
                except Exception:
                    pass
                face_table = db.create_table("face_embeddings", schema=face_schema, exist_ok=True)
            try:
                face_source_table = db.create_table("face_sources", schema=face_source_schema, mode="overwrite")
            except Exception:
                try:
                    db.drop_table("face_sources")
                except Exception:
                    pass
                face_source_table = db.create_table("face_sources", schema=face_source_schema, exist_ok=True)
            for kind in ("photos", "videos"):
                enabled = _face_kind_enabled(kind)
                _update_face_state(kind, status="complete" if enabled else "disabled", processed=0, total=0,
                                   percent=100.0 if enabled else 0.0, current=None, error=None)
            _catalog_backfilled = True
            _catalog_marker.touch()
            _invalidate_media_caches()
            _invalidate_catalog_cache()
            _ann_generation += 1
            _ann_index_status.update({"photos": "not_started", "video_frames": "not_started"})
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "status": "success",
        "message": "All indexed photos and sessions have been cleared.",
        "indexed_count": len(table),
        "indexed_video_frames": len(video_table),
        "indexed_media_count": len(catalog_table),
        "indexed_faces": len(face_table),
    }

def _delete_rows_for_path(source_table, path: str):
    """Deletes stale embeddings for one changed source file, safely quoting its path."""
    escaped_path = path.replace("'", "''")
    source_table.delete("path = '" + escaped_path + "'")
    if source_table is table or source_table is video_table:
        _remove_catalog_path(path)
        escaped_path = path.replace("'", "''")
        with _face_table_lock:
            face_table.delete(f"path = '{escaped_path}'")
            face_source_table.delete(f"path = '{escaped_path}'")

def _delete_rows_for_ids(source_table, ids, path: Optional[str] = None):
    """Removes selected stale vector rows in bounded SQL batches."""
    ids = list(ids)
    for start in range(0, len(ids), 500):
        quoted = ["'" + value.replace("'", "''") + "'" for value in ids[start:start + 500]]
        if quoted:
            where = "id IN (" + ",".join(quoted) + ")"
            if path is not None:
                where += " AND path = '" + path.replace("'", "''") + "'"
            source_table.delete(where)
    if path is not None and (source_table is table or source_table is video_table):
        escaped_path = path.replace("'", "''")
        with _face_table_lock:
            face_table.delete(f"path = '{escaped_path}'")
            face_source_table.delete(f"path = '{escaped_path}'")

@app.post("/api/search/text")
async def search_text(payload: TextSearchQuery, request: Request):
    """Real-time semantic text search across photos and sampled video frames."""
    if not payload.query.strip():
        return {"results": [], "latency_ms": 0.0, "mrl_dim": payload.mrl_dim}

    t0 = time.perf_counter()
    _require_model_ready()
    priority_manager.mark_search_active(2.5)

    t_start_embed = time.perf_counter()
    cache_key = (payload.query.strip().lower(), 768)
    with _query_embed_lock:
        query_vec_768 = _query_embed_cache.get(cache_key)
    from_cache = query_vec_768 is not None
    if query_vec_768 is None:
        query_vec_768 = await _infer(engine.embed_text, payload.query, 768, request=request, priority=TaskPriority.SEARCH)
        with _query_embed_lock:
            if len(_query_embed_cache) > 256:
                _query_embed_cache.pop(next(iter(_query_embed_cache)))
            _query_embed_cache[cache_key] = query_vec_768
    t_after_embed = time.perf_counter()

    results_task = asyncio.to_thread(
        execute_mrl_search,
        query_vec_768,
        mrl_dim=payload.mrl_dim,
        limit=payload.limit,
        search_mode="text",
        query_text=payload.query,
    )
    video_results_task = asyncio.to_thread(
        execute_mrl_search,
        query_vec_768,
        payload.mrl_dim,
        min(120, payload.limit * 4),
        "text",
        payload.query,
        video_table,
        "video",
    )
    results, video_results = await asyncio.gather(results_task, video_results_task)
    t_after_search = time.perf_counter()

    semantic_results = await asyncio.to_thread(collapse_video_matches, results + video_results, payload.limit)
    t_after_collapse = time.perf_counter()
    results = semantic_results

    # Pre-warm video frame previews in background with SEARCH priority so thumbnails appear instantly
    for r in results:
        if r.get("media_type") == "video" and r.get("timestamp") is not None:
            try:
                p_str = r["path"]
                ts_val = float(r["timestamp"])
                pref, cached = get_thumb_target_path(p_str, ts_val)
                if cached is None:
                    asyncio.create_task(ensure_preview(p_str, ts_val, pref, cv2, priority=TaskPriority.SEARCH))
            except Exception:
                pass

    embed_ms = round((t_after_embed - t_start_embed) * 1000, 1)
    search_ms = round((t_after_search - t_after_embed) * 1000, 1)
    collapse_ms = round((t_after_collapse - t_after_search) * 1000, 1)
    print(f"[*] Search timing '{payload.query}': embed={embed_ms}ms (cache={from_cache}), search={search_ms}ms, collapse={collapse_ms}ms")

    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    return {
        "results": results,

        "latency_ms": latency_ms,
        "mrl_dim": payload.mrl_dim,
        "query": payload.query,
    }

@app.post("/api/search/image")
async def search_image(
    request: Request,
    file: UploadFile = File(...),
    mrl_dim: int = Form(768),
    limit: int = Form(30, ge=1, le=200)
):
    """Real-time visual similarity search via image file upload."""
    _require_model_ready()
    t0 = time.perf_counter()
    priority_manager.mark_search_active(2.5)
    ext = Path(file.filename or "upload.jpg").suffix or ".jpg"

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp_buf:
        temp_path = Path(temp_buf.name)
        await asyncio.to_thread(shutil.copyfileobj, file.file, temp_buf)

    try:
        query_vec_768 = await _infer(engine.embed_image, str(temp_path), 768, request=request, priority=TaskPriority.SEARCH)
        results_task = asyncio.to_thread(
            execute_mrl_search,
            query_vec_768,
            mrl_dim=mrl_dim,
            limit=limit,
            search_mode="image",
        )
        video_results_task = asyncio.to_thread(
            execute_mrl_search,
            query_vec_768,
            mrl_dim,
            min(120, limit * 4),
            "image",
            "",
            video_table,
            "video",
        )
        results, video_results = await asyncio.gather(results_task, video_results_task)
        results = await asyncio.to_thread(collapse_video_matches, results + video_results, limit)

        # Pre-warm video frame previews in background with SEARCH priority
        for r in results:
            if r.get("media_type") == "video" and r.get("timestamp") is not None:
                try:
                    p_str = r["path"]
                    ts_val = float(r["timestamp"])
                    pref, cached = get_thumb_target_path(p_str, ts_val)
                    if cached is None:
                        asyncio.create_task(ensure_preview(p_str, ts_val, pref, cv2, priority=TaskPriority.SEARCH))
                except Exception:
                    pass

        latency_ms = round((time.perf_counter() - t0) * 1000, 2)

        return {
            "results": results,
            "latency_ms": latency_ms,
            "mrl_dim": mrl_dim,
            "filename": file.filename,
        }
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass

@app.post("/api/photo/similar")
async def find_similar(payload: SimilarQuery):
    """'Related Photos' recommendation endpoint."""
    _require_model_ready()
    t0 = time.perf_counter()
    records = await asyncio.to_thread(lambda: table.search().where(f"id = {_quote_sql(payload.photo_id)}").limit(1).to_list())
    if not records:
        records = await asyncio.to_thread(lambda: video_table.search().where(f"id = {_quote_sql(payload.photo_id)}").limit(1).to_list())
        if not records:
            raise HTTPException(status_code=404, detail="Media item not found")

    target_vec = records[0]["vector"]
    raw_results_task = asyncio.to_thread(
        execute_mrl_search,
        target_vec,
        mrl_dim=payload.mrl_dim,
        limit=payload.limit + 1,
        search_mode="image",
    )
    video_results_task = asyncio.to_thread(
        execute_mrl_search,
        target_vec,
        mrl_dim=payload.mrl_dim,
        limit=min(200, payload.limit * 8),
        search_mode="image",
        query_text="",
        source_table=video_table,
        media_type="video",
    )
    raw_results, video_results = await asyncio.gather(raw_results_task, video_results_task)
    filtered = await asyncio.to_thread(collapse_video_matches,
        [row for row in raw_results + video_results if row["id"] != payload.photo_id], payload.limit
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    return {
        "results": filtered,
        "latency_ms": latency_ms,
        "photo_id": payload.photo_id,
        "mrl_dim": payload.mrl_dim,
    }

@app.get("/api/photos")
def list_photos(
    offset: int = 0,
    limit: int = 50,
    shuffle: bool = True,
    seed: Optional[int] = None,
):
    """Returns a metadata-only page from the compact per-media catalog, with optional seed-based shuffling."""
    offset, limit = max(0, offset), max(1, min(200, limit))
    items = _get_catalog_items()
    total = len(items)

    if total == 0:
        return {"photos": [], "total": 0, "offset": offset, "limit": limit, "has_more": False, "seed": seed}

    if shuffle:
        actual_seed = seed if seed is not None else random.randint(1, 10000000)
        rng = np.random.default_rng(actual_seed)
        perm = rng.permutation(total)
        page_indices = perm[offset : offset + limit]
        records = [items[i] for i in page_indices]
    else:
        actual_seed = seed
        records = items[offset : offset + limit]

    for r in records:
        if r.get("media_type") == "video" and "duration" not in r:
            r["duration"] = get_video_duration(r.get("path"), cached_only=True)

    return {
        "photos": records,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(records)) < total,
        "seed": actual_seed,
    }


@app.get("/api/albums")
def get_albums(
    folder: Optional[str] = Query(None, description="Target folder to browse, or None for root level"),
    include_subfolders: bool = Query(False, description="Include media from all nested subfolders"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    sort_by: str = Query("name", description="Sort by: 'name', 'date', 'size', 'type'"),
    sort_order: str = Query("asc", description="'asc' or 'desc'"),
):
    """
    Returns folder-level album hierarchy and media files for Explorer-style album browsing.
    """
    folder = folder if isinstance(folder, str) else None
    include_subfolders = bool(include_subfolders) if isinstance(include_subfolders, (bool, int)) else False
    offset = max(0, int(offset) if isinstance(offset, (int, str)) and str(offset).isdigit() else 0)
    limit = max(1, min(500, int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 100))
    sort_by = str(sort_by) if isinstance(sort_by, str) else "name"
    sort_order = str(sort_order) if isinstance(sort_order, str) else "asc"
    items = _get_catalog_items()
    if not items:
        return {
            "common_root": "",
            "current_path": "",
            "parent_path": None,
            "breadcrumbs": [{"name": "Albums", "path": ""}],
            "subfolders": [],
            "media": [],
            "media_total": 0,
            "media_offset": offset,
            "media_limit": limit,
            "media_has_more": False,
            "stats": {"photo_count": 0, "video_count": 0, "total_count": 0, "total_bytes": 0},
            "all_albums": [],
        }

    # Group all catalog items by normalized folder path
    by_folder: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for it in items:
        f_dir = os.path.normpath(os.path.dirname(it.get("path", "")))
        by_folder[f_dir].append(it)

    folders_list = sorted(list(by_folder.keys()))
    common_root = os.path.commonpath(folders_list) if folders_list else ""

    # Build flat summary of all albums (distinct folders)
    all_albums = []
    for f_path, m_list in by_folder.items():
        p_count = sum(1 for m in m_list if m.get("media_type") != "video")
        v_count = len(m_list) - p_count
        sample = m_list[0] if m_list else None
        if sample and sample.get("media_type") == "video" and "duration" not in sample:
            sample = dict(sample)
            sample["duration"] = get_video_duration(sample.get("path"), cached_only=True)
        all_albums.append({
            "name": os.path.basename(f_path) or f_path,
            "path": f_path,
            "photo_count": p_count,
            "video_count": v_count,
            "total_count": len(m_list),
            "cover_media": sample,
        })
    all_albums.sort(key=lambda a: a["total_count"], reverse=True)

    # Determine current browsing path
    if folder and folder.strip():
        current_path = os.path.normpath(folder.strip())
    else:
        current_path = common_root

    # Compute breadcrumbs
    breadcrumbs = [{"name": "Albums", "path": common_root}]
    if current_path and common_root and current_path != common_root:
        try:
            rel = os.path.relpath(current_path, common_root)
            if not rel.startswith(".."):
                accum = common_root
                for part in rel.split(os.sep):
                    if part and part != ".":
                        accum = os.path.join(accum, part)
                        breadcrumbs.append({"name": part, "path": accum})
            else:
                breadcrumbs.append({"name": os.path.basename(current_path) or current_path, "path": current_path})
        except Exception:
            breadcrumbs.append({"name": os.path.basename(current_path) or current_path, "path": current_path})

    # Compute parent path
    parent_path = None
    if current_path and common_root and current_path != common_root:
        parent_candidate = os.path.dirname(current_path)
        try:
            rel_parent = os.path.relpath(parent_candidate, common_root)
            if not rel_parent.startswith(".."):
                parent_path = parent_candidate
        except Exception:
            pass

    # Find immediate subfolders under current_path
    subfolders_map = {}
    for f_path in folders_list:
        if f_path == current_path:
            continue
        try:
            rel = os.path.relpath(f_path, current_path)
            if rel.startswith(".."):
                continue
            parts = rel.split(os.sep)
            imm_name = parts[0]
            imm_path = os.path.normpath(os.path.join(current_path, imm_name))
            if imm_path not in subfolders_map:
                subfolders_map[imm_path] = {
                    "name": imm_name,
                    "path": imm_path,
                    "photo_count": 0,
                    "video_count": 0,
                    "total_count": 0,
                    "has_subfolders": False,
                    "cover_media": None,
                }
            f_items = by_folder[f_path]
            for it in f_items:
                if it.get("media_type") == "video":
                    subfolders_map[imm_path]["video_count"] += 1
                else:
                    subfolders_map[imm_path]["photo_count"] += 1
                subfolders_map[imm_path]["total_count"] += 1
                if subfolders_map[imm_path]["cover_media"] is None:
                    cov = dict(it)
                    if cov.get("media_type") == "video" and "duration" not in cov:
                        cov["duration"] = get_video_duration(cov.get("path"), cached_only=True)
                    subfolders_map[imm_path]["cover_media"] = cov
            if len(parts) > 1:
                subfolders_map[imm_path]["has_subfolders"] = True
        except Exception:
            pass
    subfolders = sorted(list(subfolders_map.values()), key=lambda s: s["name"].lower())

    # Collect media for current_path
    if include_subfolders:
        media_pool = []
        for f_path, m_list in by_folder.items():
            try:
                rel = os.path.relpath(f_path, current_path)
                if not rel.startswith(".."):
                    media_pool.extend(m_list)
            except Exception:
                pass
    else:
        media_pool = list(by_folder.get(current_path, []))

    # Calculate stats
    total_bytes = sum(int(m.get("size_bytes") or 0) for m in media_pool)
    photos_count = sum(1 for m in media_pool if m.get("media_type") != "video")
    videos_count = len(media_pool) - photos_count

    # Sort media
    reverse_sort = (sort_order.lower() == "desc")
    if sort_by == "date" or sort_by == "timestamp":
        media_pool.sort(key=lambda m: float(m.get("timestamp") or 0.0), reverse=reverse_sort)
    elif sort_by == "size":
        media_pool.sort(key=lambda m: int(m.get("size_bytes") or 0), reverse=reverse_sort)
    elif sort_by == "type":
        media_pool.sort(key=lambda m: (0 if m.get("media_type") != "video" else 1, (m.get("filename") or "").lower()), reverse=reverse_sort)
    else:  # name
        media_pool.sort(key=lambda m: (m.get("filename") or "").lower(), reverse=reverse_sort)

    # Paginate media
    paginated_media = media_pool[offset : offset + limit]
    for m in paginated_media:
        if m.get("media_type") == "video" and "duration" not in m:
            m["duration"] = get_video_duration(m.get("path"), cached_only=True)

    return {
        "common_root": common_root,
        "current_path": current_path,
        "parent_path": parent_path,
        "breadcrumbs": breadcrumbs,
        "subfolders": subfolders,
        "media": paginated_media,
        "media_total": len(media_pool),
        "media_offset": offset,
        "media_limit": limit,
        "media_has_more": (offset + len(paginated_media)) < len(media_pool),
        "stats": {
            "photo_count": photos_count,
            "video_count": videos_count,
            "total_count": len(media_pool),
            "total_bytes": total_bytes,
        },
        "all_albums": all_albums,
    }


@app.post("/api/system/open-explorer")
def open_in_explorer(path: str = Query(...)):
    """Opens Windows File Explorer with the specified folder or file selected."""
    if sys.platform == "win32":
        try:
            clean_path = (path or "").strip().strip('"').strip("'")
            norm = os.path.normpath(clean_path)
            if os.path.exists(norm):
                import subprocess
                if os.path.isfile(norm):
                    subprocess.Popen(["explorer", f"/select,{norm}"])
                else:
                    subprocess.Popen(["explorer", norm])
                return {"status": "opened", "path": norm}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "not_supported"}


@app.get("/api/media/newly-added")
def get_newly_added(limit: int = Query(200, ge=1, le=1000)):
    """Returns newly indexed files from the current and recent indexing sessions."""
    items = _load_newly_added()
    for it in items:
        if it.get("media_type") == "video" and "duration" not in it:
            it["duration"] = get_video_duration(it.get("path"), cached_only=True)
    return {
        "items": items[:limit],
        "total": len(items),
    }


@app.post("/api/media/newly-added/clear")
def clear_newly_added():
    """Clears the newly added files list."""
    _clear_newly_added()
    return {"status": "cleared"}

THUMBNAILS_DIR = (APP_DATA_ROOT / "thumbnails").resolve()
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
_PHOTO_THUMBNAILS_DIR = (THUMBNAILS_DIR / "photos").resolve()
_PHOTO_THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

_media_access_cache = OrderedDict()
_media_cache_lock = threading.RLock()
_video_rows_cache = OrderedDict()


def _invalidate_media_caches():
    with _media_cache_lock:
        _media_access_cache.clear()
        _video_rows_cache.clear()
    priority_manager.reset()


def _authorized_media(path, kind):
    target = Path(path)
    extensions = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"} if kind == "video" else {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
    if target.suffix.lower() not in extensions:
        raise HTTPException(status_code=400, detail="Unsupported media format")
    try:
        target = target.resolve(strict=True)
        stat = target.stat()
        if not target.is_file():
            raise OSError("Not a file")
    except (OSError, RuntimeError):
        raise HTTPException(status_code=404, detail="Media not found on disk")
    source = video_table if kind == "video" else table
    # The table version invalidates authorization after clears, deletes or writes.
    key = (kind, str(target), stat.st_mtime_ns, stat.st_size, source.version)
    with _media_cache_lock:
        if key in _media_access_cache:
            _media_access_cache.move_to_end(key)
            return target
    rows = source.search().where(f"path = {_quote_sql(str(target))}").select(["path"]).limit(1).to_list()
    if not rows:
        raise HTTPException(status_code=404, detail="Media is not indexed")
    with _media_cache_lock:
        _media_access_cache[key] = True
        while len(_media_access_cache) > 4096:
            _media_access_cache.popitem(last=False)
    return target


def _photo_target(path, media_id):
    if not path and media_id:
        rows = table.search().where(f"id = {_quote_sql(media_id)}").select(["path"]).limit(1).to_list()
        path = rows[0]["path"] if rows else None
    if not path:
        raise HTTPException(status_code=404, detail="Photo not found")
    return _authorized_media(path, "image")


def _preview_response(path, request):
    # URLs contain paths/timestamps, not source versions.
    # max-age=3600 prevents the browser from spamming 304 revalidations on every scroll
    # while still honoring If-None-Match when the client revalidates.
    stat = Path(path).stat()
    mtime_val = stat.st_mtime_ns if hasattr(stat, "st_mtime_ns") else stat.st_mtime
    etag = '"' + hashlib.md5(f"{mtime_val}-{stat.st_size}".encode(), usedforsecurity=False).hexdigest() + '"'
    headers = {
        "Cache-Control": "private, max-age=3600, stale-while-revalidate=86400",
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(str(path), media_type="image/jpeg", headers=headers, stat_result=stat)


@app.get("/api/photo")
async def get_photo(request: Request, path: Optional[str] = None, id: Optional[str] = None, thumb: bool = False):
    target = await asyncio.to_thread(_photo_target, path, id)
    if not thumb:
        return FileResponse(str(target), headers={"Cache-Control": "private, no-cache"})
    preferred, cached = await asyncio.to_thread(get_thumb_target_path, str(target))
    if cached is None:
        try:
            cached = await ensure_preview(str(target), None, preferred, priority=TaskPriority.GALLERY)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Could not create photo preview") from exc
    return await asyncio.to_thread(_preview_response, cached, request)


@app.get("/api/video")
def get_video(path: str = Query(...)):
    priority_manager.mark_video_active(4.0)
    target = _authorized_media(path, "video")
    return FileResponse(str(target), media_type=mimetypes.guess_type(str(target))[0] or "video/mp4",
                        headers={"Cache-Control": "private, no-cache"})


def _indexed_video_path(path):
    target = _authorized_media(path, "video")
    key = (str(target), video_table.version)
    with _media_cache_lock:
        cached = _video_rows_cache.get(key)
        if cached is not None:
            _video_rows_cache.move_to_end(key)
            return target, cached
    rows = video_table.search().where(f"path = {_quote_sql(str(target))}").select(["timestamp"]).limit(max(1, len(video_table))).to_list()
    timestamps = sorted({float(row["timestamp"]) for row in rows})
    with _media_cache_lock:
        _video_rows_cache[key] = timestamps
        while len(_video_rows_cache) > 64:
            _video_rows_cache.popitem(last=False)
    return target, timestamps


@app.get("/api/video/frames")
def get_sampled_video_frames(path: str = Query(...), offset: int = Query(0, ge=0), limit: int = Query(60, ge=1, le=2000)):
    priority_manager.mark_video_active(3.0)
    target, timestamps = _indexed_video_path(path)
    page = timestamps[offset:offset + limit]
    return {"path": str(target), "frames": page, "offset": offset, "limit": limit,
            "total": len(timestamps), "has_more": offset + len(page) < len(timestamps),
            "source_fps": 0, "duration": get_video_duration(str(target), cached_only=True)}


@app.get("/api/video/frame")
async def get_sampled_video_frame(request: Request, path: str = Query(...), timestamp: float = Query(..., ge=0.0, allow_inf_nan=False)):
    priority_manager.mark_video_active(3.0)
    # Authorize before reading any disk cache. Exact preview requests never load
    # every frame record just to render one thumbnail.
    target = await asyncio.to_thread(_authorized_media, path, "video")
    preferred, cached = await asyncio.to_thread(get_thumb_target_path, str(target), timestamp)
    if cached is None:
        if cv2 is None:
            raise HTTPException(status_code=503, detail="Video previews require OpenCV")
        try:
            cached = await ensure_preview(str(target), timestamp, preferred, cv2, priority=TaskPriority.VIDEO)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Could not decode the requested frame") from exc
    return await asyncio.to_thread(_preview_response, cached, request)


def _face_record(media_id: str, media_type: str, face_index: Optional[int] = None):
    escaped_id = media_id.replace("'", "''")
    where = f"source_id = '{escaped_id}' AND media_type = '{media_type}'"
    if face_index is not None:
        where += f" AND face_index = {int(face_index)}"
    return face_table.search().where(where).select(
        ["id", "source_id", "filename", "path", "media_type", "size_bytes", "width", "height", "timestamp", "face_index", "bbox", "vector"]
    ).limit(500).to_list()


@app.get("/api/faces/media")
def get_media_faces(media_id: str = Query(...), media_type: str = Query(..., pattern="^(image|video)$")):
    kind = "photos" if media_type == "image" else "videos"
    if not _face_kind_enabled(kind):
        raise HTTPException(status_code=409, detail=f"Face matching is disabled for {media_type}s in Settings")
    rows = _face_record(media_id, media_type)
    return {"media_id": media_id, "media_type": media_type,
            "faces": [{"face_index": int(row["face_index"]), "bbox": row["bbox"]} for row in sorted(rows, key=lambda item: item["face_index"]) ]}


@app.get("/api/faces/preview")
def get_face_preview(media_id: str = Query(...), media_type: str = Query(..., pattern="^(image|video)$"), face_index: int = Query(..., ge=0)):
    rows = _face_record(media_id, media_type, face_index)
    if not rows:
        raise HTTPException(status_code=404, detail="Indexed face not found")
    row = rows[0]
    kind = "photos" if media_type == "image" else "videos"
    image = None
    crop = None
    try:
        image = _read_face_source_image(row, kind)
        x, y, width, height = [float(value) for value in row["bbox"]]
        # Compute a balanced square portrait crop centered slightly above the nose so forehead/hair & chin fit cleanly
        cx = x + width * 0.5
        cy = y + height * 0.46
        side = max(width, height) * 1.65
        side = min(side, float(min(image.width, image.height)))
        half = side * 0.5

        left = cx - half
        top = cy - half
        right = cx + half
        bottom = cy + half

        # Shift window inside image bounds so the crop stays square whenever possible
        if left < 0:
            right = min(float(image.width), right - left)
            left = 0.0
        if top < 0:
            bottom = min(float(image.height), bottom - top)
            top = 0.0
        if right > image.width:
            left = max(0.0, left - (right - image.width))
            right = float(image.width)
        if bottom > image.height:
            top = max(0.0, top - (bottom - image.height))
            bottom = float(image.height)

        left_i, top_i, right_i, bottom_i = int(round(left)), int(round(top)), int(round(right)), int(round(bottom))
        if right_i <= left_i or bottom_i <= top_i:
            raise ValueError("Detected face box is outside the media frame")
        crop = image.crop((left_i, top_i, right_i, bottom_i))
        if max(crop.size) > 256:
            crop.thumbnail((256, 256), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        crop.save(output, format="JPEG", quality=90)
        output.seek(0)
        return StreamingResponse(output, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not prepare face preview: {exc}") from exc
    finally:
        if crop is not None:
            try:
                crop.close()
            except Exception:
                pass
        if image is not None:
            try:
                image.close()
            except Exception:
                pass


@app.post("/api/faces/search")
def search_similar_faces(payload: FaceSearchQuery):
    source_kind = "photos" if payload.media_type == "image" else "videos"
    if not _face_kind_enabled(source_kind):
        raise HTTPException(status_code=409, detail=f"Face matching is disabled for {payload.media_type}s in Settings")
    reference = _face_record(payload.media_id, payload.media_type, payload.face_index)
    if not reference:
        raise HTTPException(status_code=404, detail="No indexed face found for the selected media")
    query_vector = np.asarray(reference[0]["vector"], dtype=np.float32)
    query_vector /= np.linalg.norm(query_vector) + 1e-12
    candidate_limit = min(len(face_table), max(256, payload.limit * 40))
    if candidate_limit <= 0:
        return {"results": [], "media_id": payload.media_id}
    candidates = face_table.search(query_vector.tolist()).distance_type("l2").limit(candidate_limit).to_list()
    active_types = set()
    if _face_kind_enabled("photos"):
        active_types.add("image")
    if _face_kind_enabled("videos"):
        active_types.add("video")
    matches = {}
    for row in candidates:
        if row.get("media_type") not in active_types:
            continue
        vector = np.asarray(row["vector"], dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if vector.shape != (128,) or not np.isfinite(vector).all() or norm <= 1e-12:
            continue
        cosine = float(np.dot(query_vector, vector / norm))
        if cosine < 0.363:
            continue
        key = (row["media_type"], row["path"])
        if key not in matches or cosine > matches[key]["similarity"]:
            score = max(0.0, min(1.0, cosine))
            is_vid = row["media_type"] == "video"
            matches[key] = {
                "id": row["source_id"], "filename": row["filename"], "path": row["path"],
                "media_type": row["media_type"], "size_bytes": int(row.get("size_bytes", 0) or 0),
                "width": int(row.get("width", 0) or 0), "height": int(row.get("height", 0) or 0),
                "timestamp": float(row.get("timestamp", 0.0) or 0.0) if is_vid else None,
                "matched_timestamp": float(row.get("timestamp", 0.0) or 0.0) if is_vid else None,
                "duration": get_video_duration(row["path"]) if is_vid else None,
                "matched_face_index": int(row["face_index"]), "similarity": round(score, 4),
                "accuracy_percent": round(score * 100.0, 1), "accuracy_tier": "face match",
            }
    results = sorted(matches.values(), key=lambda item: item["similarity"], reverse=True)[:payload.limit]
    return {"results": results, "media_id": payload.media_id, "face_index": payload.face_index}


@app.post("/api/media/open-with")
def open_media_with(request: OpenWithRequest):
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="The Windows Open With chooser is only available on Windows")
    source_table = table if request.media_type == "image" else video_table
    escaped = request.media_id.replace("'", "''")
    rows = source_table.search().where(f"id = '{escaped}'").select(["path"]).limit(1).to_list()
    if not rows:
        raise HTTPException(status_code=404, detail="Indexed media not found")
    try:
        target = Path(rows[0]["path"]).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="Media file is not available on disk") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Media file is not available on disk")
    try:
        subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(target)], shell=False)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open Windows app chooser: {exc}") from exc
    return {"status": "success", "message": "Windows Open With chooser opened"}

@app.get("/api/system/browse-dir")
def browse_directory(path: Optional[str] = Query(None)):
    """
    In-app visual directory browser.
    If path is None or empty: returns available system drives and detected photo folders.
    If path is provided: returns subdirectories and counts image files in that folder.
    """
    valid_exts = SUPPORTED_IMAGE_EXTS

    if not path or not path.strip():
        import string
        drives = []
        for d in string.ascii_uppercase:
            drive_path = f"{d}:\\"
            if os.path.exists(drive_path):
                drives.append(drive_path)

        presets = []
        candidates = []
        last_folder = session_manager.active_session.get("last_selected_directory")
        if last_folder:
            candidates.append(("Last selected", Path(last_folder), "Previously used"))
        sample_dir = Path(__file__).resolve().parent.parent / "media" / "sample_photos"
        if sample_dir.is_dir():
            candidates.append(("Sample Photos", sample_dir, "Demo collection"))
        pictures_dir = Path.home() / "Pictures"
        if pictures_dir.is_dir():
            candidates.append(("Pictures", pictures_dir, "User library"))
        seen_paths = set()
        for name, candidate, tag in candidates:
            try:
                normalized = str(candidate.resolve(strict=True))
            except (OSError, RuntimeError):
                continue
            key = os.path.normcase(normalized)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            presets.append({"name": name, "path": normalized, "tag": tag})

        return {
            "current_path": "",
            "parent_path": None,
            "drives": drives,
            "presets": presets,
            "subdirectories": [],
            "image_count": 0,
        }

    clean_path = (path or "").strip().strip('"').strip("'")
    p = Path(clean_path)
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail="Invalid directory path")

    subdirs = []
    try:
        for item in p.iterdir():
            if item.is_dir() and not item.name.startswith(".") and not is_path_excluded(item):
                subdirs.append(item.name)
    except Exception:
        pass

    subdirs.sort()

    image_count = 0
    try:
        image_count = sum(1 for f in p.iterdir() if f.is_file() and f.suffix.lower() in valid_exts)
    except Exception:
        pass

    parent = str(p.parent.resolve()) if p.parent != p else None

    return {
        "current_path": str(p.resolve()),
        "parent_path": parent,
        "drives": [],
        "presets": [],
        "subdirectories": subdirs[:100],
        "image_count": image_count,
    }

@app.post("/api/system/pick-folder")
def pick_folder():
    """Opens native Windows folder picker dialog and returns the selected path."""
    folder = prompt_windows_folder_picker()
    if not folder:
        return {"status": "cancelled", "folder": None}
    return {"status": "success", "folder": folder}

@app.get("/api/index/stream")
async def index_stream(
    directory: str = Query(..., description="Directory path to index"),
    resume: bool = Query(False, description="Resume existing paused session"),
    frame_rate: float = Query(0.5, ge=0.01, le=5.0, description="Video frames sampled per second (e.g. 0.0333 for 1 frame every 30s)"),
    adaptive_sampling: bool = Query(True, description="Enable smart scene-change adaptive frame sampling"),
):
    """Indexes supported photos and videos (always indexing all photos FIRST, then videos AFTER)."""
    _require_model_ready()
    clean_dir = (directory or "").strip().strip('"').strip("'")
    p = Path(clean_dir)
    if not p.exists() or not p.is_dir() or is_path_excluded(p):
        raise HTTPException(status_code=400, detail="Invalid directory path")
    normalized_directory = str(p.resolve())
    await asyncio.to_thread(_backfill_catalog)
    session_manager.remember_folder(normalized_directory)
    image_exts = SUPPORTED_IMAGE_EXTS
    video_exts = SUPPORTED_VIDEO_EXTS
    # Always discover files with APP_ROOT & cache isolation, sorting Photos FIRST and Videos SECOND
    photos, videos = await asyncio.to_thread(discover_media_files, p)
    all_files = photos + videos
    total_files = len(all_files)
    if _maintenance_status["status"] == "running":
        raise HTTPException(status_code=409, detail="Database optimization is running. Browse/search remain available; retry indexing shortly.")
    with _index_write_lock:
        if session_manager.active_session.get("status") == "in_progress" and not resume:
            raise HTTPException(status_code=409, detail="Another folder is currently being indexed")
        session_manager.generation += 1
        session_generation = session_manager.generation
        session_manager.is_cancelled = False
        session_manager.is_paused = False
        session_manager.pause_event.set()

    image_rows = await asyncio.to_thread(_metadata_rows, table, ["id", "path"])
    image_ids = {row["id"] for row in image_rows}
    image_ids_by_path: Dict[str, set] = {}
    for row in image_rows:
        image_ids_by_path.setdefault(row["path"], set()).add(row["id"])
    video_rows = await asyncio.to_thread(_metadata_rows, video_table, ["id", "path"])
    video_ids = {row["id"] for row in video_rows}
    video_ids_by_path: Dict[str, set] = {}
    for row in video_rows:
        video_ids_by_path.setdefault(row["path"], set()).add(row["id"])

    def _is_video_complete(v_path: str, ids: set) -> bool:
        if not ids:
            return False
        dur = get_video_duration(v_path)
        if dur <= 0:
            return len(ids) >= 4
        if dur < 5.0:
            return len(ids) >= 1
        if len(ids) < 4:
            return False
        max_ts = max(
            (int(vid.split("_")[-1]) for vid in ids if vid.split("_")[-1].isdigit()),
            default=0,
        ) / 1000.0
        user_interval_sec = 1.0 / max(0.01, float(frame_rate))
        tolerance = max(15.0, user_interval_sec * 2.5)
        return max_ts >= max(0.0, dur - tolerance)

    # Normalized in-memory lookup sets of already-completed files
    def current_completed_sources():
        photos_done, videos_done = set(), set()
        for source in all_files:
            try:
                resolved = str(source.resolve())
                stat = source.stat()
                if source.suffix.lower() in image_exts:
                    if f"{source.name}_{stat.st_mtime_ns}" in image_ids_by_path.get(resolved, set()):
                        photos_done.add(os.path.normcase(resolved))
                else:
                    prefix = hashlib.sha1(f"{resolved}:{stat.st_mtime_ns}".encode()).hexdigest()[:20]
                    current_ids = {value for value in video_ids_by_path.get(resolved, set()) if value.startswith(f"video_{prefix}_")}
                    if _is_video_complete(resolved, current_ids):
                        videos_done.add(os.path.normcase(resolved))
            except OSError:
                continue
        return photos_done, videos_done
    done_image_norm, done_video_norm = await asyncio.to_thread(current_completed_sources)
    done_all_norm = done_image_norm | done_video_norm

    # Check if there is an active/saved checkpoint for this directory that can be resumed
    saved_session = dict(session_manager.active_session)
    saved_dir = saved_session.get("directory")
    is_matching_dir = (
        saved_dir is not None and
        os.path.normcase(os.path.normpath(saved_dir)) == os.path.normcase(os.path.normpath(normalized_directory))
    )
    saved_processed = int(saved_session.get("processed_files", 0) or 0)
    saved_indexed = int(saved_session.get("indexed_count", 0) or 0)
    saved_frames = int(saved_session.get("indexed_frames", saved_session.get("total_frames_indexed", len(video_ids))) or len(video_ids))
    saved_failed = int(saved_session.get("failed_count", 0) or 0)

    # Active resume / delta-sync condition: user requested resume OR folder matches a saved/completed session with prior progress
    is_resume_active = (resume or is_matching_dir) and is_matching_dir and (0 < saved_processed <= total_files or len(done_all_norm) > 0)
    failed_paths_set = {
        os.path.normcase(str(fp))
        for fp in (saved_session.get("failed_paths") or [])
        if fp and is_matching_dir
    }

    if is_resume_active:
        # Filter out already-indexed files in RAM (~4ms for 16k files, zero disk stat calls)
        # while preserving Photos-First, Videos-Second ordering!
        files_to_process = [
            f for f in all_files
            if os.path.normcase(str(f.resolve())) not in done_all_norm
            and os.path.normcase(str(f.resolve())) not in failed_paths_set
        ]
        already_done = total_files - len(files_to_process)
        processed_files = min(total_files, already_done)
        indexed = max(0, already_done - len(failed_paths_set))
        indexed_frames = max(saved_frames, len(video_ids))
        failed = len(failed_paths_set)
        completed_paths = set()
        print(
            f"[*] Fast-forwarding resume/sync for {normalized_directory} (Photos First -> Videos After): "
            f"skipping {already_done} completed files in RAM, syncing {len(files_to_process)} remaining file(s) ({processed_files}/{total_files})"
        )
    else:
        failed_paths_set = set()
        already_done = 0
        completed_paths = set()
        for file_path in all_files:
            if session_manager.is_cancelled or session_manager.generation != session_generation:
                raise HTTPException(status_code=409, detail="Indexing was cancelled while checking the library")
            try:
                stat = file_path.stat()
                source_path = str(file_path.resolve())
                if file_path.suffix.lower() in image_exts:
                    current_id = f"{file_path.name}_{stat.st_mtime_ns}"
                    existing_ids = image_ids_by_path.get(source_path, set())
                    is_complete = current_id in existing_ids
                    stale_ids = existing_ids - {current_id}
                    if stale_ids:
                        with _index_write_lock:
                            if session_manager.is_cancelled or session_manager.generation != session_generation:
                                raise HTTPException(status_code=409, detail="Indexing was cancelled while checking the library")
                            _delete_rows_for_ids(table, stale_ids, source_path)
                            image_ids.difference_update(stale_ids)
                            image_ids_by_path[source_path] = ({current_id} if is_complete else set())
                            _remove_catalog_path(source_path)
                else:
                    stored_ids = video_ids_by_path.get(source_path, set())
                    prefix = hashlib.sha1(f"{source_path}:{stat.st_mtime_ns}".encode()).hexdigest()[:20]
                    current_ids = {value for value in stored_ids if value.startswith(f"video_{prefix}_")}
                    stale_ids = stored_ids - current_ids
                    if stale_ids:
                        with _index_write_lock:
                            if session_manager.is_cancelled or session_manager.generation != session_generation:
                                raise HTTPException(status_code=409, detail="Indexing was cancelled while checking the library")
                            _delete_rows_for_ids(video_table, stale_ids, source_path)
                            video_ids.difference_update(stale_ids)
                            video_ids_by_path[source_path] = current_ids
                            _remove_catalog_path(source_path)
                    is_complete = _is_video_complete(source_path, current_ids)
                if is_complete:
                    completed_paths.add(source_path)
                    already_done += 1
            except OSError:
                continue
        processed_files = already_done
        indexed = already_done
        indexed_frames = len(video_ids)
        failed = 0
        files_to_process = [f for f in all_files if str(f.resolve()) not in completed_paths]

    catalog_paths = {row["path"] for row in await asyncio.to_thread(_metadata_rows, catalog_table, ["path"])}
    if _maintenance_status["status"] == "running":
        raise HTTPException(status_code=409, detail="Database optimization started. Retry indexing shortly.")
    with _index_write_lock:
        if session_manager.is_cancelled or session_manager.generation != session_generation:
            raise HTTPException(status_code=409, detail="Indexing was cancelled before processing started")
        session_manager.save_state({
            "status": "in_progress",
            "directory": normalized_directory,
            "total_files": total_files,
            "indexed_count": indexed,
            "percent": round(processed_files / max(1, total_files) * 100, 1),
            "processed_files": processed_files,
            "indexed_frames": indexed_frames,
            "failed_count": failed,
            "failed_paths": list(failed_paths_set),
            "is_paused": False,
            "frame_rate": frame_rate,
            "adaptive_sampling": adaptive_sampling,
            "new_files_detected": 0,
            "new_images_detected": 0,
            "new_videos_detected": 0,
            "auto_sync_pending": False,
        })

    async def event_generator():
        nonlocal indexed, indexed_frames, failed, processed_files
        yield f"data: {json.dumps({'event': 'start', 'total': total_files, 'directory': normalized_directory, 'already_indexed': indexed, 'already_processed_files': processed_files, 'already_indexed_frames': indexed_frames, 'frame_rate': frame_rate, 'adaptive_sampling': adaptive_sampling}, ensure_ascii=True)}\n\n"
        if is_resume_active:
            yield f"data: {json.dumps({'event': 'progress', 'indexed': indexed, 'indexed_frames': indexed_frames, 'failed': failed, 'total': total_files, 'processed_files': processed_files, 'current_file': f'Resumed ({processed_files} completed, photos first)', 'speed_fps': 0.0, 'eta_seconds': 0.0, 'percent': round(processed_files / max(1, total_files) * 100, 1)}, ensure_ascii=True)}\n\n"
        embedded_images = 0
        embedded_video_frames = 0
        batches_since_gc = 0
        session_start_processed = processed_files
        started = time.perf_counter()
        image_batch = []
        device_info = engine.get_device_info()
        # Scale batch size dynamically while preserving user's 500MB-1GB VRAM headroom
        image_batch_size = min(4, engine.get_optimal_batch_size(min_headroom_mb=650.0)) if engine.device == "cuda" else 2
        max_decode_batch_bytes = 384 * 1024 * 1024 if engine.device == "cuda" else 192 * 1024 * 1024
        image_batch_bytes = 0
        print(f"[*] Indexing (Photos First -> Videos After) with batch size {image_batch_size} on {device_info.get('gpu_name', engine.device)}")

        def session_cancelled():
            return session_manager.is_cancelled or session_manager.generation != session_generation

        def emit(event):
            return f"data: {json.dumps(event, ensure_ascii=True)}\n\n"

        def maybe_cleanup_memory():
            nonlocal batches_since_gc
            batches_since_gc += 1
            if batches_since_gc >= 10:
                batches_since_gc = 0
                reclaim_vram(force=True)
            else:
                maybe_reclaim_vram()

        async def flush_images():
            nonlocal indexed, failed, image_batch, image_batch_bytes, embedded_images, processed_files
            if not image_batch:
                return []
            pending, image_batch = image_batch, []
            image_batch_bytes = 0

            # Use the same versioned, atomic small preview format as HTTP retrieval.
            def cache_image_batch():
                for item in pending:
                    try:
                        target, cached = get_thumb_target_path(item["meta"]["path"])
                        if cached is None:
                            save_preview(item["image"], target)
                    except OSError:
                        pass
            await asyncio.to_thread(cache_image_batch)

            await priority_manager.yield_if_foreground_active(max_wait_sec=2.5)
            pil_imgs = [item["image"] for item in pending]
            try:
                vectors = await _infer(engine.embed_image_batch, pil_imgs, 768)
            except Exception as exc:
                failed += len(pending)
                processed_files += len(pending)
                print(f"[!] Image embedding batch failed: {exc}")
                return []
            finally:
                for im in pil_imgs:
                    try:
                        im.close()
                    except Exception:
                        pass
            if len(vectors) < len(pending):
                failed += len(pending) - len(vectors)
            if session_cancelled():
                return []
            rows, added_photos = [], []
            for item, vector in zip(pending, vectors):
                vec = np.asarray(vector, dtype=np.float32)
                if vec.shape != (768,) or not np.isfinite(vec).all() or not 0.98 <= float(np.linalg.norm(vec)) <= 1.02:
                    failed += 1
                    continue
                vector_row = {key: value for key, value in item["meta"].items() if key in schema.names}
                vector_row["vector"] = vec.tolist()
                rows.append(vector_row)
                added_photos.append(item["meta"])
            processed_files += len(pending)
            if rows:
                def commit_images():
                    nonlocal indexed, embedded_images
                    with _index_write_lock:
                        if session_cancelled():
                            return False
                        table.add(rows)
                        gpu_vector_index.invalidate()
                        catalog_rows = [_catalog_record(row, "image") for row in added_photos]
                        if catalog_rows:
                            catalog_table.add(catalog_rows)
                            _record_newly_added(catalog_rows)
                            _invalidate_catalog_cache()
                        indexed += len(rows)
                        embedded_images += len(rows)
                        image_ids.update(row["id"] for row in rows)
                        for row in rows:
                            image_ids_by_path.setdefault(row["path"], set()).add(row["id"])
                            done_image_norm.add(os.path.normcase(row["path"]))
                        # Immediately persist checkpoint after every committed photo batch
                        session_manager.save_state({
                            "indexed_count": indexed,
                            "indexed_frames": indexed_frames,
                            "failed_count": failed,
                            "processed_files": processed_files,
                            "total_frames_indexed": indexed_frames,
                            "current_file": rows[-1]["filename"],
                            "percent": round(processed_files / max(1, total_files) * 100, 1),
                        })
                    return True
                if not await asyncio.to_thread(commit_images):
                    return []
                _schedule_new_face_sources("photos", rows)
            await asyncio.to_thread(maybe_cleanup_memory)
            return [{"event": "photos_added", "photos": added_photos}] if added_photos else []

        def sample_video(path):
            if cv2 is None:
                raise RuntimeError("Video processing requires OpenCV. Run install.bat to install the added dependency.")
            capture = cv2.VideoCapture(str(path))
            source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
            duration = frame_count / source_fps if source_fps > 0 else 0.0
            if not capture.isOpened() or source_fps <= 0 or duration <= 0:
                capture.release()
                raise ValueError("Video could not be opened or has no valid frame rate")

            # Ensure every video gets thorough multi-frame coverage (at least 6-15 frames for short/medium clips)
            # while still honoring the user's interval cap on long videos.
            user_interval_sec = 1.0 / max(0.01, float(frame_rate))
            min_target_frames = max(6, min(15, int(duration // 3) + 1)) if duration >= 4.0 else max(2, int(duration) + 1)
            effective_interval_sec = min(user_interval_sec, max(0.75, duration / max(2, min_target_frames)))
            interval_frames = max(1, int(round(source_fps * effective_interval_sec)))

            batch = []
            batch_bytes = 0
            file_stat = path.stat()
            resolved_path = str(path.resolve())
            _video_duration_cache[resolved_path] = round(duration, 2)
            prefix = hashlib.sha1(f"{resolved_path}:{file_stat.st_mtime_ns}".encode()).hexdigest()[:20]
            curr_frame = 0
            last_indexed_time = -999.0
            prev_thumb = None

            def process_candidate_frame(frame, curr_f):
                nonlocal prev_thumb, last_indexed_time, batch, batch_bytes
                timestamp = round(curr_f / source_fps, 3)
                frame_id = f"video_{prefix}_{int(timestamp * 1000)}"
                if frame_id in video_ids:
                    return None

                # Fast 32x32 luminance scene-change filter (only skips near-identical frames within a tight window)
                thumb = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (32, 32))
                if adaptive_sampling and prev_thumb is not None and effective_interval_sec < 2.5:
                    diff = float(np.mean(np.abs(thumb.astype(np.float32) - prev_thumb.astype(np.float32))) / 255.0)
                    if diff < 0.02 and (timestamp - last_indexed_time) < min(3.0, effective_interval_sec * 1.5):
                        return None

                prev_thumb = thumb
                last_indexed_time = timestamp
                orig_h, orig_w = frame.shape[:2]

                # Fast in-OpenCV pre-scale before converting to PIL
                if max(orig_h, orig_w) > 768:
                    scale = 768.0 / max(orig_h, orig_w)
                    frame = cv2.resize(frame, (int(orig_w * scale), int(orig_h * scale)), interpolation=cv2.INTER_AREA)

                # Pre-cache frame thumbnail on disk in location_index so gallery/viewer never needs to re-decode video
                thumb_target, exist_thumb = get_thumb_target_path(resolved_path, timestamp)
                if not exist_thumb and not thumb_target.is_file():
                    try:
                        with Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)) as preview:
                            save_preview(preview, thumb_target)
                    except Exception:
                        pass

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = Image.fromarray(rgb)
                batch.append({"id": frame_id, "filename": path.name, "path": resolved_path,
                              "size_bytes": file_stat.st_size, "width": orig_w, "height": orig_h,
                              "timestamp": float(timestamp), "image": image})
                batch_bytes += image.width * image.height * 3
                if len(batch) >= image_batch_size or batch_bytes >= max_decode_batch_bytes:
                    ready = batch
                    batch = []
                    batch_bytes = 0
                    return ready
                return None

            try:
                curr_ts = 0.0
                while curr_ts < duration:
                    if session_cancelled():
                        break

                    candidate_id = f"video_{prefix}_{int(round(curr_ts * 1000))}"
                    # Fast-forward already-indexed frames in RAM without disk seeks
                    if candidate_id in video_ids:
                        last_indexed_time = curr_ts
                        curr_ts += effective_interval_sec
                        continue

                    target_frame = int(round(curr_ts * source_fps))
                    target_msec = curr_ts * 1000.0

                    capture.set(cv2.CAP_PROP_POS_MSEC, target_msec)
                    ok, frame = capture.read()

                    if not ok or frame is None:
                        capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                        ok, frame = capture.read()

                    # Step-back keyframe tolerance for long videos or sparse keyframe intervals
                    if not ok or frame is None:
                        for delta in (-1.0, -2.0, -3.0, 1.0, 2.0):
                            adj_ts = max(0.0, curr_ts + delta)
                            capture.set(cv2.CAP_PROP_POS_MSEC, adj_ts * 1000.0)
                            ok, frame = capture.read()
                            if ok and frame is not None:
                                break

                    # If this specific second failed (e.g. keyframe gap), skip to next timestamp rather than aborting the video!
                    if not ok or frame is None:
                        curr_ts += effective_interval_sec
                        continue

                    ready = process_candidate_frame(frame, target_frame)
                    if ready:
                        yield ready
                    curr_ts += effective_interval_sec

                if batch:
                    yield batch
            finally:
                capture.release()

        async def report_progress(filename):
            elapsed = max(0.001, time.perf_counter() - started)
            embedded_count = embedded_images + embedded_video_frames
            speed = round(embedded_count / elapsed, 2)
            percent = round(processed_files / max(1, total_files) * 100, 1)
            files_this_session = max(0, processed_files - session_start_processed)
            files_per_second = files_this_session / elapsed if files_this_session > 0 else 0.0
            eta = round(max(0, total_files - processed_files) / max(0.1, files_per_second), 1) if files_per_second > 0 else 0.0
            with _index_write_lock:
                if not session_cancelled():
                    session_manager.save_state({"indexed_count": indexed, "indexed_frames": indexed_frames, "failed_count": failed,
                                                "current_file": filename, "speed_fps": speed, "percent": percent, "eta_seconds": eta,
                                                "processed_files": processed_files, "total_frames_indexed": indexed_frames})
            return {"event": "progress", "indexed": indexed, "indexed_frames": indexed_frames, "failed": failed,
                    "total": total_files, "processed_files": processed_files, "current_file": filename,
                    "speed_fps": speed, "eta_seconds": eta, "percent": percent}

        for file_path in files_to_process:
            if os.path.normcase(str(file_path)) in done_all_norm or str(file_path.resolve()) in completed_paths:
                continue
            if session_cancelled():
                yield f"data: {json.dumps({'event': 'cancelled', 'indexed': indexed, 'indexed_frames': indexed_frames, 'failed': failed, 'total': total_files})}\n\n"
                break
            if session_manager.is_paused:
                yield f"data: {json.dumps({'event': 'paused', 'indexed': indexed, 'indexed_frames': indexed_frames, 'failed': failed, 'total': total_files, 'processed_files': processed_files})}\n\n"
                await session_manager.pause_event.wait()
                if session_cancelled():
                    yield f"data: {json.dumps({'event': 'cancelled', 'indexed': indexed, 'indexed_frames': indexed_frames, 'failed': failed, 'total': total_files, 'processed_files': processed_files})}\n\n"
                    break
                yield f"data: {json.dumps({'event': 'resumed', 'indexed': indexed, 'indexed_frames': indexed_frames, 'failed': failed, 'total': total_files, 'processed_files': processed_files})}\n\n"
            try:
                stat = file_path.stat()
                photo_id = f"{file_path.name}_{stat.st_mtime_ns}"
                if file_path.suffix.lower() in image_exts:
                    image_path = str(file_path.resolve())
                    existing_image_ids = image_ids_by_path.get(image_path, set())
                    if photo_id in existing_image_ids:
                        done_image_norm.add(os.path.normcase(image_path))
                        continue
                    if existing_image_ids:
                        cancelled_before_delete = False
                        with _index_write_lock:
                            cancelled_before_delete = session_cancelled()
                            if not cancelled_before_delete:
                                _delete_rows_for_path(table, image_path)
                        if cancelled_before_delete:
                            yield f"data: {json.dumps({'event': 'cancelled', 'indexed': indexed, 'indexed_frames': indexed_frames, 'failed': failed, 'total': total_files})}\n\n"
                            break
                        image_ids.difference_update(existing_image_ids)
                        image_ids_by_path[image_path] = set()
                    def decode_image():
                        with Image.open(file_path) as img:
                            orig_w, orig_h = img.size
                            img.draft("RGB", (768, 768))
                            oriented = ImageOps.exif_transpose(img)
                            image = oriented.convert("RGB")
                            oriented.close()
                            image.thumbnail((768, 768), Image.Resampling.LANCZOS)
                            return image, orig_w, orig_h
                    image, orig_w, orig_h = await asyncio.to_thread(decode_image)
                    meta = {"id": photo_id, "filename": file_path.name, "path": image_path,
                            "size_bytes": stat.st_size, "width": orig_w, "height": orig_h, "media_type": "image"}
                    image_batch.append({"image": image, "meta": meta})
                    image_batch_bytes += image.width * image.height * 3
                    if len(image_batch) < image_batch_size and image_batch_bytes < max_decode_batch_bytes:
                        continue
                    for event in await flush_images():
                        yield emit(event)
                    yield emit(await report_progress(file_path.name))
                    continue
                else:
                    # Before starting the first video, ensure any remaining photo batch is flushed first!
                    if image_batch:
                        for event in await flush_images():
                            yield emit(event)
                    video_path = str(file_path.resolve())
                    stat = file_path.stat()
                    prefix = hashlib.sha1(f"{video_path}:{stat.st_mtime_ns}".encode()).hexdigest()[:20]
                    current_video_ids = video_ids_by_path.get(video_path, set())
                    matching_ids = {value for value in current_video_ids if value.startswith(f"video_{prefix}_")}
                    had_prior_frames = len(matching_ids) > 0
                    if _is_video_complete(video_path, matching_ids):
                        done_video_norm.add(os.path.normcase(video_path))
                        continue
                    stale_video_ids = current_video_ids - matching_ids
                    video_interrupted = False
                    if stale_video_ids:
                        with _index_write_lock:
                            video_interrupted = session_cancelled()
                            if not video_interrupted:
                                _delete_rows_for_ids(video_table, stale_video_ids, video_path)
                                _remove_catalog_path(video_path)
                                catalog_paths.discard(video_path)
                                video_ids.difference_update(stale_video_ids)
                                video_ids_by_path[video_path] = matching_ids
                                indexed_frames = max(0, indexed_frames - len(stale_video_ids))
                    if video_interrupted:
                        yield f"data: {json.dumps({'event': 'cancelled', 'indexed': indexed, 'indexed_frames': indexed_frames, 'failed': failed, 'total': total_files})}\n\n"
                        break
                    frames_added = 0
                    found_frame = had_prior_frames
                    frame_iterator = sample_video(file_path)
                    while True:
                        # Yield if user is actively watching/scrubbing a video in the player or searching
                        await priority_manager.yield_if_foreground_active(max_wait_sec=2.5)
                        # Advancing this generator seeks/decodes many frames. Never
                        # execute next() on the API event loop.
                        frames = await asyncio.to_thread(next, frame_iterator, None)
                        if frames is None:
                            break
                        if session_cancelled():
                            video_interrupted = True
                            for f_item in frames:
                                try:
                                    f_item["image"].close()
                                except Exception:
                                    pass
                            break
                        if session_manager.is_paused:
                            yield f"data: {json.dumps({'event': 'paused', 'indexed': indexed, 'indexed_frames': indexed_frames, 'total': total_files, 'processed_files': processed_files})}\n\n"
                            await session_manager.pause_event.wait()
                            if session_cancelled():
                                video_interrupted = True
                                for f_item in frames:
                                    try:
                                        f_item["image"].close()
                                    except Exception:
                                        pass
                                break
                            yield f"data: {json.dumps({'event': 'resumed', 'indexed': indexed, 'indexed_frames': indexed_frames, 'total': total_files, 'processed_files': processed_files})}\n\n"
                        await priority_manager.yield_if_foreground_active(max_wait_sec=2.5)
                        frame_imgs = [frame["image"] for frame in frames]
                        try:
                            vectors = await _infer(engine.embed_image_batch, frame_imgs, 768)
                        finally:
                            for im in frame_imgs:
                                try:
                                    im.close()
                                except Exception:
                                    pass
                        if len(vectors) < len(frames):
                            failed += len(frames) - len(vectors)
                        rows = []
                        if session_cancelled():
                            video_interrupted = True
                            break
                        for frame, vector in zip(frames, vectors):
                            vec = np.asarray(vector, dtype=np.float32)
                            if vec.shape != (768,) or not np.isfinite(vec).all() or not 0.98 <= float(np.linalg.norm(vec)) <= 1.02:
                                failed += 1
                                continue
                            row = {key: value for key, value in frame.items() if key != "image"}
                            row["vector"] = vec.tolist()
                            rows.append(row)
                            video_ids.add(row["id"])
                        if rows:
                            found_frame = True
                            def commit_frames():
                                nonlocal indexed_frames, embedded_video_frames, frames_added
                                with _index_write_lock:
                                    if session_cancelled():
                                        return False
                                    video_table.add(rows)
                                    gpu_vector_index.invalidate()
                                    if video_path not in catalog_paths:
                                        first_frame = min(rows, key=lambda row: float(row.get("timestamp", 0.0)))
                                        v_rec = _catalog_record(first_frame, "video")
                                        catalog_table.add([v_rec])
                                        catalog_paths.add(video_path)
                                        _record_newly_added([v_rec])
                                        _invalidate_catalog_cache()
                                    indexed_frames += len(rows)
                                    embedded_video_frames += len(rows)
                                    frames_added += len(rows)
                                    video_ids_by_path.setdefault(video_path, set()).update(row["id"] for row in rows)
                                    session_manager.save_state({
                                        "indexed_count": indexed,
                                        "indexed_frames": indexed_frames,
                                        "failed_count": failed,
                                        "processed_files": processed_files,
                                        "total_frames_indexed": indexed_frames,
                                        "current_file": file_path.name,
                                    })
                                return True
                            if not await asyncio.to_thread(commit_frames):
                                video_interrupted = True
                                break
                            _schedule_new_face_sources("videos", rows)
                            await asyncio.to_thread(maybe_cleanup_memory)
                            thumbnail = {"id": rows[0]["id"], "filename": rows[0]["filename"], "path": rows[0]["path"],
                                         "timestamp": rows[0]["timestamp"], "media_type": "video"}
                            yield emit({"event": "video_frame_added", "video": thumbnail})
                            yield emit(await report_progress(file_path.name))
                    await asyncio.to_thread(frame_iterator.close)
                    if video_interrupted:
                        yield f"data: {json.dumps({'event': 'cancelled', 'indexed': indexed, 'indexed_frames': indexed_frames, 'failed': failed, 'total': total_files})}\n\n"
                        break
                    if found_frame and not had_prior_frames:
                        indexed += 1
                    done_video_norm.add(os.path.normcase(video_path))
                processed_files += 1
                yield emit(await report_progress(file_path.name))
            except Exception as exc:
                failed += 1
                processed_files += 1
                try:
                    failed_paths_set.add(os.path.normcase(str(file_path.resolve())))
                except Exception:
                    pass
                print(f"[!] Error indexing {file_path}: {exc}")
                yield emit({"event": "file_error", "filename": file_path.name, "message": str(exc)})
                yield emit(await report_progress(file_path.name))

        for event in await flush_images():
            yield emit(event)
        if image_batch == [] and processed_files:
            yield emit(await report_progress("Finishing image batch"))
        completed = False
        with _index_write_lock:
            if not session_cancelled():
                elapsed = round(time.perf_counter() - started, 2)
                final_percent = round(processed_files / max(1, total_files) * 100, 1)
                session_manager.save_state({
                    "status": "completed",
                    "indexed_count": indexed,
                    "indexed_frames": indexed_frames,
                    "failed_count": failed,
                    "failed_paths": list(failed_paths_set),
                    "processed_files": processed_files,
                    "percent": 100.0 if total_files == 0 or processed_files >= total_files else final_percent,
                    "is_paused": False,
                    "current_file": None,
                    "new_files_detected": 0,
                    "new_images_detected": 0,
                    "new_videos_detected": 0,
                    "auto_sync_pending": False,
                })
                completed = True
        if completed:
            if len(table) >= 1000 or len(video_table) >= 1000:
                asyncio.create_task(_build_ann_indexes())
            done = {"event": "complete", "indexed": indexed, "indexed_frames": indexed_frames, "failed": failed,
                    "total": total_files, "elapsed_seconds": elapsed}
            yield f"data: {json.dumps(done)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# Mount built frontend dist for direct browsing on http://localhost:8000
from fastapi.staticfiles import StaticFiles
FRONTEND_DIST = (Path(__file__).parent.parent / "frontend" / "dist").resolve()
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
