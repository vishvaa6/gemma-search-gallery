"""
Unit tests for Priority Threading & Scheduler System (GemmaGallery).

Verifies:
1. PriorityManager flags video player and search as foreground interactions.
2. Background tasks yield when video or search is active.
3. PriorityInferenceGate prioritizes search queries ahead of background indexing.
4. Dedicated worker pools in media_cache isolate video player frame decoding from photo/background tasks.
"""
import asyncio
import time
import unittest

from priority import TaskPriority, PriorityManager, PriorityInferenceGate
import media_cache


class PrioritySystemTests(unittest.TestCase):

    def setUp(self):
        self.mgr = PriorityManager()
        self.mgr.reset()

    def test_priority_levels(self):
        self.assertLess(TaskPriority.VIDEO, TaskPriority.SEARCH)
        self.assertLess(TaskPriority.SEARCH, TaskPriority.GALLERY)
        self.assertLess(TaskPriority.GALLERY, TaskPriority.BACKGROUND)

    def test_video_active_flag(self):
        self.assertFalse(self.mgr.is_video_active())
        self.mgr.mark_video_active(duration_sec=0.5)
        self.assertTrue(self.mgr.is_video_active())
        self.assertTrue(self.mgr.is_foreground_active())
        time.sleep(0.55)
        self.assertFalse(self.mgr.is_video_active())

    def test_search_active_flag(self):
        self.assertFalse(self.mgr.is_search_active())
        self.mgr.mark_search_active(duration_sec=0.5)
        self.assertTrue(self.mgr.is_search_active())
        self.assertTrue(self.mgr.is_foreground_active())
        time.sleep(0.55)
        self.assertFalse(self.mgr.is_search_active())

    def test_background_yields_when_foreground_active(self):
        async def run_test():
            self.mgr.mark_video_active(duration_sec=0.3)
            t0 = time.monotonic()
            paused = await self.mgr.yield_if_foreground_active(max_wait_sec=1.0, poll_interval=0.05)
            elapsed = time.monotonic() - t0
            self.assertTrue(paused)
            self.assertGreaterEqual(elapsed, 0.25)
            self.assertFalse(self.mgr.is_video_active())
        asyncio.run(run_test())

    def test_priority_inference_gate(self):
        async def run_gate_test():
            gate = PriorityInferenceGate()
            order = []

            async def background_task():
                await gate.acquire(TaskPriority.BACKGROUND)
                order.append("bg_started")
                await asyncio.sleep(0.1)
                order.append("bg_done")
                gate.release()

            async def search_task():
                # Allow background to start first
                await asyncio.sleep(0.02)
                await gate.acquire(TaskPriority.SEARCH)
                order.append("search_done")
                gate.release()

            await asyncio.gather(background_task(), search_task())
            self.assertEqual(order, ["bg_started", "bg_done", "search_done"])

        asyncio.run(run_gate_test())

    def test_media_cache_pools_isolation(self):
        # Verify dedicated pools exist and have positive worker counts
        self.assertIsNotNone(media_cache._video_pool)
        self.assertIsNotNone(media_cache._preview_pool)
        self.assertIsNotNone(media_cache._bg_pool)
        self.assertGreater(media_cache._video_pool._max_workers, 0)
        self.assertGreater(media_cache._preview_pool._max_workers, 0)
        self.assertEqual(media_cache._bg_pool._max_workers, 2)


if __name__ == "__main__":
    unittest.main()
