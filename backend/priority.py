"""
Priority Task Management & Thread Scheduler for GemmaGallery.

Guarantees responsive UI and playback:
1. TaskPriority.VIDEO (0): Highest Priority - Video player playback and timeline frame scrubbing.
2. TaskPriority.SEARCH (1): High Priority - Interactive search query embedding & result previews.
3. TaskPriority.GALLERY (2): Normal Priority - User-driven photo gallery scrolling & thumbnails.
4. TaskPriority.BACKGROUND (3): Low Priority - Ingestion, indexing, face scanning, warmup.

Secondary and background tasks automatically pause or yield when foreground video or search is active.
"""

import asyncio
import os
import threading
import time
from enum import IntEnum
from typing import Dict, Any, Optional


class TaskPriority(IntEnum):
    VIDEO = 0       # Highest / Critical: Video player playback & frame seeking
    SEARCH = 1      # High: Interactive search query embedding & result previews
    GALLERY = 2     # Normal: Interactive photo gallery thumbnails
    BACKGROUND = 3  # Low / Secondary: Ingestion, preheating, face indexing


class PriorityManager:
    """Tracks active foreground interactions and coordinates cooperative throttling of background tasks."""

    def __init__(self):
        self._lock = threading.Lock()
        self._video_active_until: float = 0.0
        self._search_active_until: float = 0.0

    def mark_video_active(self, duration_sec: float = 3.0):
        """Mark video player open, scrubbing, or playing (guarantees foreground priority for duration)."""
        with self._lock:
            self._video_active_until = max(self._video_active_until, time.monotonic() + duration_sec)

    def mark_search_active(self, duration_sec: float = 2.0):
        """Mark interactive search query active."""
        with self._lock:
            self._search_active_until = max(self._search_active_until, time.monotonic() + duration_sec)

    def reset(self):
        """Reset all active foreground timers."""
        with self._lock:
            self._video_active_until = 0.0
            self._search_active_until = 0.0

    def is_video_active(self) -> bool:
        return time.monotonic() < self._video_active_until

    def is_search_active(self) -> bool:
        return time.monotonic() < self._search_active_until

    def is_foreground_active(self) -> bool:
        now = time.monotonic()
        return now < self._video_active_until or now < self._search_active_until

    async def yield_if_foreground_active(self, max_wait_sec: float = 3.0, poll_interval: float = 0.08) -> bool:
        """
        Called by background tasks (indexing, face backfill, preview pre-caching).
        If the user has opened the video player or is searching, cooperatively
        pauses/sleeps so foreground tasks get 100% of CPU/GPU/decoder resources.
        Returns True if execution was paused.
        """
        if not self.is_foreground_active():
            return False

        # In synchronous mock test environments without a running event loop, yield immediately without sleep
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False

        was_paused = False
        start = time.monotonic()
        while self.is_foreground_active() and (time.monotonic() - start) < max_wait_sec:
            was_paused = True
            await asyncio.sleep(poll_interval)
        return was_paused

    def sync_yield_if_foreground_active(self, max_wait_sec: float = 2.0, poll_interval: float = 0.05) -> bool:
        """Synchronous yield helper for background worker threads."""
        if not self.is_foreground_active():
            return False
        was_paused = False
        start = time.monotonic()
        while self.is_foreground_active() and (time.monotonic() - start) < max_wait_sec:
            was_paused = True
            time.sleep(poll_interval)
        return was_paused

    def get_status(self) -> Dict[str, Any]:
        now = time.monotonic()
        return {
            "video_active": now < self._video_active_until,
            "video_remaining_sec": max(0.0, round(self._video_active_until - now, 2)),
            "search_active": now < self._search_active_until,
            "search_remaining_sec": max(0.0, round(self._search_active_until - now, 2)),
            "foreground_active": self.is_foreground_active(),
        }


priority_manager = PriorityManager()


class PriorityInferenceGate:
    """Async priority gate ensuring search queries preempt background indexing batches."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._high_priority_waiting = 0

    async def acquire(self, priority: TaskPriority = TaskPriority.SEARCH):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        if priority <= TaskPriority.SEARCH:
            self._high_priority_waiting += 1
            try:
                await self._lock.acquire()
            finally:
                self._high_priority_waiting = max(0, self._high_priority_waiting - 1)
        else:
            # Background indexing: if any search is pending or video player is active, wait
            while self._high_priority_waiting > 0 or priority_manager.is_video_active():
                await asyncio.sleep(0.05)
            await self._lock.acquire()

    def release(self):
        if self._lock.locked():
            try:
                self._lock.release()
            except (RuntimeError, ValueError):
                pass

    async def __aenter__(self):
        await self.acquire(TaskPriority.SEARCH)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()
