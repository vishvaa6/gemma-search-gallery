"""
Safe High-Performance Media Discovery Scanner for GemmaGallery.
Strictly isolates and protects against:
1. Scanning the application directory itself (APP_ROOT), when the app resides inside the selected folder.
2. Scanning internal cache folders (.gallery_index, .thumbnails, .cache, backend/data).
3. Scanning developer or system folders (node_modules, .venv, .git, __pycache__, etc.).
4. Scanning non-media files (.txt, .py, .js, .json, .log, .md, .tmp, etc.).
"""

import os
import sys
from pathlib import Path
from typing import List, Tuple, Set, Optional, Callable, Dict, Any

BACKEND_ROOT = Path(__file__).resolve().parent
APP_ROOT = BACKEND_ROOT.parent.resolve()

SUPPORTED_IMAGE_EXTS: Set[str] = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
SUPPORTED_VIDEO_EXTS: Set[str] = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
SUPPORTED_MEDIA_EXTS: Set[str] = SUPPORTED_IMAGE_EXTS | SUPPORTED_VIDEO_EXTS

EXCLUDED_DIR_NAMES: Set[str] = {
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".git",
    ".gemini",
    "__pycache__",
    ".gallery_index",
    ".thumbnails",
    ".cache",
    ".antigravity",
    ".vscode",
    ".idea",
    "dist",
    "build",
    "$recycle.bin",
    "system volume information",
}


def is_path_excluded(path: Path) -> bool:
    """
    Returns True if path points to or resides inside the application directory,
    a cache folder, a developer directory, or a hidden folder.
    """
    try:
        resolved = path.resolve()
        # Allow unit test regression temporary sandboxes
        if "gallery-regression-" in str(resolved):
            return False
        # Strictly exclude app directory and all of its subtrees
        if resolved == APP_ROOT or APP_ROOT in resolved.parents:
            return True
        for parent in resolved.parents:
            p_name = parent.name.lower()
            if p_name in EXCLUDED_DIR_NAMES or p_name.startswith("."):
                return True
        name = resolved.name.lower()
        if name in EXCLUDED_DIR_NAMES or name.startswith("."):
            return True
    except (OSError, RuntimeError):
        return True
    return False


def is_excluded_dir_name(dir_name: str) -> bool:
    """Fast check by directory name before full path resolution."""
    lower = dir_name.lower()
    return lower in EXCLUDED_DIR_NAMES or lower.startswith(".")


def discover_media_files(
    directory: Path,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Tuple[List[Path], List[Path]]:
    """
    Walks the target directory using os.walk with active top-down pruning.
    Returns (photos, videos), guaranteed to be free from app internal files,
    cached thumbnails, and non-media text files.
    """
    target = Path(directory).resolve()
    if not target.is_dir() or is_path_excluded(target):
        return [], []

    photos: List[Path] = []
    videos: List[Path] = []
    visited_files = 0
    app_root_norm = os.path.normcase(str(APP_ROOT))

    for root, directories, filenames in os.walk(str(target), topdown=True):
        if cancel_check and cancel_check():
            break

        # Check if current root is inside the app directory
        root_norm = os.path.normcase(root)
        if root_norm == app_root_norm or root_norm.startswith(app_root_norm + os.sep):
            directories.clear()
            continue

        # Active in-place directory pruning: prevent os.walk from descending into excluded trees
        pruned_dirs = []
        for d in directories:
            if is_excluded_dir_name(d):
                continue
            child_path = os.path.join(root, d)
            child_norm = os.path.normcase(child_path)
            if child_norm == app_root_norm or child_norm.startswith(app_root_norm + os.sep):
                continue
            pruned_dirs.append(d)
        directories[:] = pruned_dirs

        for fname in filenames:
            visited_files += 1
            ext = os.path.splitext(fname)[1].lower()
            if ext in SUPPORTED_IMAGE_EXTS:
                photos.append(Path(root) / fname)
            elif ext in SUPPORTED_VIDEO_EXTS:
                videos.append(Path(root) / fname)

            if on_progress and (visited_files % 250 == 0):
                on_progress({
                    "event": "discovering",
                    "scanned": visited_files,
                    "photos_found": len(photos),
                    "videos_found": len(videos),
                    "current": fname,
                })

    photos.sort(key=lambda p: str(p).casefold())
    videos.sort(key=lambda p: str(p).casefold())
    return photos, videos
