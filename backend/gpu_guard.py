"""
GPU VRAM Budget Manager, Memory Guard & In-Memory Vector Search Index for GemmaGallery.
Ensures RTX 3050 (4 GB VRAM) runs with high performance, zero memory leaks,
active garbage collection, and strict protection against CUDA Out-Of-Memory (OOM) errors.
Lazy-loads PyTorch so module imports remain instantaneous without eager overhead.
"""
import os
import sys
import gc
import threading
import time
from typing import Dict, Any, Optional, List, Tuple

import numpy as np

_guard_lock = threading.Lock()
_last_cleanup = 0.0

MIN_FREE_VRAM_MB = 600.0
MAX_RESERVED_VRAM_MB = 3400.0


def is_torch_cuda_ready() -> bool:
    """Returns True only when PyTorch is completely initialized, has cuda support, and cuda is available."""
    try:
        mod = sys.modules.get("torch")
        if mod is None:
            return False
        spec = getattr(mod, "__spec__", None)
        if spec is not None and getattr(spec, "initializing", False):
            return False
        cuda_mod = getattr(mod, "cuda", None)
        if cuda_mod is None or not hasattr(cuda_mod, "is_available"):
            return False
        return bool(cuda_mod.is_available())
    except (AttributeError, Exception):
        return False


def _get_torch():
    """Safely retrieves fully initialized torch module, or None if not ready/available."""
    try:
        mod = sys.modules.get("torch")
        if mod is None:
            return None
        spec = getattr(mod, "__spec__", None)
        if spec is not None and getattr(spec, "initializing", False):
            return None
        cuda_mod = getattr(mod, "cuda", None)
        if cuda_mod is None or not hasattr(cuda_mod, "is_available"):
            return None
        return mod
    except (AttributeError, Exception):
        return None


def get_vram_status() -> Dict[str, Any]:
    """Returns real-time GPU VRAM telemetry in megabytes without eagerly importing torch."""
    if not is_torch_cuda_ready():
        return {
            "available": False,
            "device": "cpu",
            "total_mb": 0.0,
            "allocated_mb": 0.0,
            "reserved_mb": 0.0,
            "free_mb": 0.0,
        }
    torch = _get_torch()
    if torch is None:
        return {
            "available": False,
            "device": "cpu",
            "total_mb": 0.0,
            "allocated_mb": 0.0,
            "reserved_mb": 0.0,
            "free_mb": 0.0,
        }
    try:
        total = float(torch.cuda.get_device_properties(0).total_memory / (1024 * 1024))
        allocated = float(torch.cuda.memory_allocated(0) / (1024 * 1024))
        reserved = float(torch.cuda.memory_reserved(0) / (1024 * 1024))
        free = max(0.0, total - reserved)
        return {
            "available": True,
            "device": torch.cuda.get_device_name(0),
            "total_mb": round(total, 1),
            "allocated_mb": round(allocated, 1),
            "reserved_mb": round(reserved, 1),
            "free_mb": round(free, 1),
        }
    except Exception:
        return {
            "available": False,
            "device": "unknown",
            "total_mb": 0.0,
            "allocated_mb": 0.0,
            "reserved_mb": 0.0,
            "free_mb": 0.0,
        }


def can_use_gpu(required_mb: float = MIN_FREE_VRAM_MB) -> bool:
    """Returns True if torch is loaded on CUDA and there is sufficient safe VRAM headroom."""
    if not is_torch_cuda_ready():
        return False
    torch = _get_torch()
    if torch is None:
        return False
    status = get_vram_status()
    if not status["available"]:
        return False
    if status["free_mb"] < required_mb:
        reclaim_vram(force=True)
        status = get_vram_status()
    return status["free_mb"] >= required_mb and status["reserved_mb"] < MAX_RESERVED_VRAM_MB


def reclaim_vram(force: bool = False):
    """Actively flushes unused Python objects and PyTorch CUDA cache allocations."""
    global _last_cleanup
    now = time.monotonic()
    if not force and (now - _last_cleanup) < 1.5:
        return
    with _guard_lock:
        if not force and (now - _last_cleanup) < 1.5:
            return
        _last_cleanup = now
        gc.collect()
        if is_torch_cuda_ready():
            torch = _get_torch()
            if torch is not None:
                try:
                    torch.cuda.empty_cache()
                    if hasattr(torch.cuda, "ipc_collect"):
                        torch.cuda.ipc_collect()
                except Exception:
                    pass


def maybe_reclaim_vram():
    """Lightweight check invoked after batch operations to avoid memory bloat."""
    if not is_torch_cuda_ready():
        return
    status = get_vram_status()
    if status["available"] and status["reserved_mb"] > 2600.0:
        reclaim_vram(force=True)


class GpuVectorIndex:
    """In-memory GPU vector cache for ultra-fast (20ms) full-corpus semantic search on RTX 3050."""

    def __init__(self):
        self._lock = threading.Lock()
        self.p_gpu = None
        self.p_meta = []
        self.v_gpu = None
        self.v_meta = []
        self.is_warmed = False
        self._building = False
        self.generation = 0

    def invalidate(self):
        with self._lock:
            self.is_warmed = False

    def unload(self):
        with self._lock:
            self.p_gpu = None
            self.p_meta = []
            self.v_gpu = None
            self.v_meta = []
            self.is_warmed = False
            reclaim_vram(force=True)

    def warm(self, photo_table, video_table) -> bool:
        """Loads normalized vector matrices into GPU VRAM in FP16 (~50MB total)."""
        if not is_torch_cuda_ready() or not can_use_gpu(required_mb=500.0):
            return False
        with self._lock:
            if self.is_warmed or self._building:
                return self.is_warmed
            self._building = True

        try:
            torch = _get_torch()
            if torch is None or not is_torch_cuda_ready():
                return False

            # 1. Photos
            tbl_p = photo_table.to_arrow()
            if len(tbl_p) > 0:
                p_meta = tbl_p.select(["id", "filename", "path", "size_bytes", "width", "height"]).to_pylist()
                p_flat = np.asarray(tbl_p["vector"].combine_chunks().values.to_numpy(zero_copy_only=False), dtype=np.float32).copy()
                p_gpu = torch.from_numpy(p_flat).view(len(tbl_p), 768).to("cuda", dtype=torch.float16)
                p_gpu = p_gpu / (torch.norm(p_gpu, dim=1, keepdim=True) + 1e-6)
            else:
                p_meta = []
                p_gpu = None

            # 2. Videos
            tbl_v = video_table.to_arrow()
            if len(tbl_v) > 0:
                v_meta = tbl_v.select(["id", "filename", "path", "size_bytes", "width", "height", "timestamp"]).to_pylist()
                v_flat = np.asarray(tbl_v["vector"].combine_chunks().values.to_numpy(zero_copy_only=False), dtype=np.float32).copy()
                v_gpu = torch.from_numpy(v_flat).view(len(tbl_v), 768).to("cuda", dtype=torch.float16)
                v_gpu = v_gpu / (torch.norm(v_gpu, dim=1, keepdim=True) + 1e-6)
            else:
                v_meta = []
                v_gpu = None

            with self._lock:
                self.p_gpu = p_gpu
                self.p_meta = p_meta
                self.v_gpu = v_gpu
                self.v_meta = v_meta
                self.is_warmed = True
                self.generation += 1
            print(f"[✓] GPU In-Memory Vector Index active ({len(p_meta)} photos, {len(v_meta)} video frames, ~{(len(p_meta)+len(v_meta))*768*2/(1024*1024):.1f}MB VRAM).")
            return True
        except Exception as e:
            print(f"[!] Could not warm GPU vector index: {e}")
            self.unload()
            return False
        finally:
            with self._lock:
                self._building = False

    def search(self, query_vec_768: List[float], mrl_dim: int = 768, limit: int = 30, media_type: str = "image"):
        """Instant (<20ms) vector dot-product search on Tensor Cores."""
        if not self.is_warmed or not is_torch_cuda_ready() or not can_use_gpu(required_mb=300.0):
            return None
        torch = _get_torch()
        if torch is None:
            return None

        with self._lock:
            mat = self.v_gpu if media_type == "video" else self.p_gpu
            meta = self.v_meta if media_type == "video" else self.p_meta
            if mat is None or len(meta) == 0:
                return None

            try:
                candidate_limit = min(len(meta), max(80, min(360, limit * 4 if media_type == "video" else limit * 6)))
                q_arr = np.asarray(query_vec_768[:mrl_dim], dtype=np.float32)
                q = torch.from_numpy(q_arr).to("cuda", dtype=torch.float16)
                q = q / (torch.norm(q) + 1e-6)

                if mrl_dim < 768:
                    sub_mat = mat[:, :mrl_dim]
                    sub_mat = sub_mat / (torch.norm(sub_mat, dim=1, keepdim=True) + 1e-6)
                    sims = torch.mv(sub_mat, q)
                else:
                    sims = torch.mv(mat, q)

                top_vals, top_idxs = torch.topk(sims, candidate_limit)
                raw_sims = top_vals.float().cpu().numpy()
                indices = top_idxs.cpu().numpy()
                records = [meta[i] for i in indices]
                return records, raw_sims
            except Exception:
                return None


gpu_vector_index = GpuVectorIndex()
