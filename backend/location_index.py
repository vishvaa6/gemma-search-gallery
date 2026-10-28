"""
Location-Based Indexing & Sidecar Storage for GemmaGallery.
Manages:
1. Sidecar directory (<folder>/.gallery_index/) located right where files reside.
2. Fast SQLite index for O(1) file info, video durations, and pre-computed thumbnail paths.
3. Pre-indexed thumbnail generation and instant resolution.
"""

import os
import shutil
import sqlite3
import hashlib
import threading
from functools import lru_cache
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, Tuple

BACKEND_ROOT = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_ROOT.parent.resolve()
# The single gallery index lives at <app root>/.gallery_index. It can be relocated
# (e.g. by unit tests) with the GALLERY_INDEX_DIR environment variable before import,
# or at runtime with set_index_root().
ROOT_GALLERY_INDEX = Path(os.getenv("GALLERY_INDEX_DIR", str(ROOT_DIR / ".gallery_index"))).resolve()


def _ensure_index_dirs(index_root: Path) -> None:
    index_root.mkdir(parents=True, exist_ok=True)
    (index_root / "thumbnails").mkdir(parents=True, exist_ok=True)
    (index_root / "thumbnails" / "photos").mkdir(parents=True, exist_ok=True)


_ensure_index_dirs(ROOT_GALLERY_INDEX)

APP_DATA_ROOT = Path(os.getenv("GALLERY_DATA_DIR", str(BACKEND_ROOT / "data"))).resolve()
FALLBACK_THUMBNAILS_DIR = APP_DATA_ROOT / "thumbnails"
FALLBACK_THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
(FALLBACK_THUMBNAILS_DIR / "photos").mkdir(parents=True, exist_ok=True)

_db_connections: Dict[str, sqlite3.Connection] = {}
_db_lock = threading.RLock()
_active_folder_sidecar: Optional[Path] = None


def _migrate_external_sidecar(src: Path, dst: Path):
    """Safely migrates any external .gallery_index left in media folders into root .gallery_index."""
    try:
        if not src.is_dir() or src.resolve() == dst.resolve():
            return
        dst.mkdir(parents=True, exist_ok=True)
        src_thumbs = src / "thumbnails"
        dst_thumbs = dst / "thumbnails"
        if src_thumbs.is_dir():
            dst_thumbs.mkdir(parents=True, exist_ok=True)
            for sub in src_thumbs.iterdir():
                target_sub = dst_thumbs / sub.name
                if sub.is_dir():
                    target_sub.mkdir(parents=True, exist_ok=True)
                    for item in sub.iterdir():
                        dest_item = target_sub / item.name
                        if not dest_item.exists():
                            shutil.move(str(item), str(dest_item))
                elif not target_sub.exists():
                    shutil.move(str(sub), str(target_sub))
        for db_file in src.glob("location_index.db*"):
            dest_file = dst / db_file.name
            if not dest_file.exists():
                shutil.move(str(db_file), str(dest_file))
        shutil.rmtree(str(src), ignore_errors=True)
        print(f"[*] Migrated external gallery index from {src} to root {dst}")
    except Exception as exc:
        print(f"[!] External gallery index migration note: {exc}")


def set_active_folder(folder_path: Optional[str]) -> Optional[Path]:
    """
    Sets the primary media folder and initializes its gallery index.
    The primary gallery index (.gallery_index) is kept strictly inside the
    application root directory (ROOT_DIR), avoiding polluting external media folders.
    """
    global _active_folder_sidecar
    get_thumbnails_dir.cache_clear()
    if not folder_path:
        _active_folder_sidecar = None
        return None
    try:
        p = Path(folder_path).resolve()
        # Check and migrate any legacy external sidecar in the media folder
        old_sidecar = p / ".gallery_index"
        if old_sidecar.is_dir() and old_sidecar.resolve() != ROOT_GALLERY_INDEX:
            _migrate_external_sidecar(old_sidecar, ROOT_GALLERY_INDEX)
        sidecar = ROOT_GALLERY_INDEX

        _ensure_index_dirs(sidecar)
        _active_folder_sidecar = sidecar
        _init_db(sidecar / "location_index.db")
        return sidecar
    except Exception as exc:
        print(f"[!] Warning: Could not initialize gallery index in {folder_path}: {exc}")
    _active_folder_sidecar = ROOT_GALLERY_INDEX
    return _active_folder_sidecar


def set_index_root(index_root: Optional[str] = None) -> Path:
    """
    Relocates the single gallery index directory (default: <app root>/.gallery_index).
    Intended for tests and alternative deployments. Open SQLite connections to the
    previous index are closed so no file handles leak on Windows.
    """
    global ROOT_GALLERY_INDEX, _active_folder_sidecar
    new_root = Path(index_root).resolve() if index_root else (ROOT_DIR / ".gallery_index").resolve()
    with _db_lock:
        for conn in _db_connections.values():
            try:
                conn.close()
            except Exception:
                pass
        _db_connections.clear()
        ROOT_GALLERY_INDEX = new_root
        _ensure_index_dirs(ROOT_GALLERY_INDEX)
        _active_folder_sidecar = None
        get_thumbnails_dir.cache_clear()
    return ROOT_GALLERY_INDEX


def get_sidecar_dir(folder_or_file_path: Optional[str] = None) -> Path:
    """Returns the single gallery index directory (kept inside the app root)."""
    ROOT_GALLERY_INDEX.mkdir(parents=True, exist_ok=True)
    return ROOT_GALLERY_INDEX


@lru_cache(maxsize=4096)
def get_thumbnails_dir(folder_path: Optional[str] = None) -> Path:
    """Returns the pre-generated thumbnails directory (sidecar preferred)."""
    sidecar = get_sidecar_dir(folder_path)
    thumbs = sidecar / "thumbnails"
    try:
        thumbs.mkdir(parents=True, exist_ok=True)
        return thumbs
    except Exception:
        return FALLBACK_THUMBNAILS_DIR


def _init_db(db_path: Path):
    with _db_lock:
        key = str(db_path.resolve())
        if key in _db_connections:
            return
        conn = sqlite3.connect(key, check_same_thread=False, timeout=10.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS media_files (
                path TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                folder TEXT NOT NULL,
                media_type TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                duration REAL DEFAULT 0.0,
                thumb_path TEXT,
                indexed_at REAL NOT NULL
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_folder ON media_files(folder);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_type ON media_files(media_type);")
        conn.commit()
        _db_connections[key] = conn


def _get_connection(media_path: Optional[str] = None) -> sqlite3.Connection:
    with _db_lock:
        target_db = None
        if _active_folder_sidecar and _active_folder_sidecar.is_dir():
            target_db = _active_folder_sidecar / "location_index.db"

        if target_db is None:
            target_db = ROOT_GALLERY_INDEX / "location_index.db"

        key = str(target_db.resolve())
        if key not in _db_connections:
            target_db.parent.mkdir(parents=True, exist_ok=True)
            _init_db(target_db)
        return _db_connections[key]


def get_thumb_target_path(media_path: str, timestamp: Optional[float] = None) -> Tuple[Path, Optional[Path]]:
    """
    Returns (preferred_thumb_path, existing_cached_path).
    Checks both the folder's root .gallery_index/thumbnails and fallback backend/data/thumbnails.
    """
    normalized_path = os.path.normcase(os.path.abspath(media_path))
    stat = os.stat(media_path)
    # Include source identity and output format revision. Old path-only caches can
    # contain changed media or full-resolution frames, so never reuse those bytes.
    revision = f"preview-v2:{stat.st_mtime_ns}:{stat.st_size}"
    if timestamp is not None and timestamp >= 0.0:
        ts_key = f"{revision}:{normalized_path}:{float(timestamp):.3f}"
        hash_key = hashlib.sha1(ts_key.encode("utf-8")).hexdigest()
        filename = f"{hash_key}.jpg"
        rel_sub = ""
    else:
        hash_key = hashlib.sha1(f"{revision}:{normalized_path}".encode("utf-8")).hexdigest()
        filename = f"{hash_key}.jpg"
        rel_sub = "photos"

    # 1. Preferred target in root directory .gallery_index (or active test sidecar)
    sidecar_thumbs = get_thumbnails_dir(media_path)
    target_dir = sidecar_thumbs / rel_sub if rel_sub else sidecar_thumbs
    target_dir.mkdir(parents=True, exist_ok=True)
    preferred_path = target_dir / filename

    # Check if preferred exists
    if preferred_path.is_file():
        return preferred_path, preferred_path

    # Check fallback in ROOT_GALLERY_INDEX if different
    root_thumbs = ROOT_GALLERY_INDEX / "thumbnails"
    root_target_dir = root_thumbs / rel_sub if rel_sub else root_thumbs
    root_path = root_target_dir / filename
    if root_path.is_file():
        return preferred_path, root_path

    # Check fallback in backend/data/thumbnails
    fallback_dir = FALLBACK_THUMBNAILS_DIR / rel_sub if rel_sub else FALLBACK_THUMBNAILS_DIR
    fallback_path = fallback_dir / filename
    if fallback_path.is_file():
        return preferred_path, fallback_path

    return preferred_path, None


def get_cached_duration(media_path: str) -> Optional[float]:
    """Instant O(1) video duration lookup from SQLite location index."""
    try:
        conn = _get_connection(media_path)
        with _db_lock:
            cur = conn.cursor()
            cur.execute("SELECT duration FROM media_files WHERE path = ? LIMIT 1", (str(media_path),))
            row = cur.fetchone()
            if row and row[0] is not None and row[0] > 0.0:
                return float(row[0])
    except Exception:
        pass
    return None


def record_file_metadata(
    path: str,
    filename: str,
    folder: str,
    media_type: str,
    mtime_ns: int,
    size_bytes: int,
    width: int = 0,
    height: int = 0,
    duration: float = 0.0,
    thumb_path: Optional[str] = None,
    indexed_at: float = 0.0,
):
    """Upserts a media file record in the location index."""
    try:
        conn = _get_connection(path)
        with _db_lock:
            conn.execute("""
                INSERT INTO media_files (path, filename, folder, media_type, mtime_ns, size_bytes, width, height, duration, thumb_path, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    mtime_ns = excluded.mtime_ns,
                    size_bytes = excluded.size_bytes,
                    width = excluded.width,
                    height = excluded.height,
                    duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE media_files.duration END,
                    thumb_path = CASE WHEN excluded.thumb_path IS NOT NULL THEN excluded.thumb_path ELSE media_files.thumb_path END,
                    indexed_at = excluded.indexed_at;
            """, (path, filename, folder, media_type, mtime_ns, size_bytes, width, height, duration, thumb_path, indexed_at))
            conn.commit()
    except Exception as exc:
        print(f"[!] location_index record error: {exc}")


def batch_record_files(records: List[Dict[str, Any]]):
    """Batch upsert for indexing phases (efficient single-transaction commit)."""
    if not records:
        return
    try:
        sample_path = records[0].get("path", "")
        conn = _get_connection(sample_path)
        data = [
            (
                r["path"], r["filename"], r["folder"], r["media_type"],
                r.get("mtime_ns", 0), r.get("size_bytes", 0),
                r.get("width", 0), r.get("height", 0),
                r.get("duration", 0.0), r.get("thumb_path"),
                r.get("indexed_at", 0.0)
            )
            for r in records
        ]
        with _db_lock:
            conn.executemany("""
                INSERT INTO media_files (path, filename, folder, media_type, mtime_ns, size_bytes, width, height, duration, thumb_path, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    mtime_ns = excluded.mtime_ns,
                    size_bytes = excluded.size_bytes,
                    width = excluded.width,
                    height = excluded.height,
                    duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE media_files.duration END,
                    thumb_path = CASE WHEN excluded.thumb_path IS NOT NULL THEN excluded.thumb_path ELSE media_files.thumb_path END,
                    indexed_at = excluded.indexed_at;
            """, data)
            conn.commit()
    except Exception as exc:
        print(f"[!] batch_record_files error: {exc}")
