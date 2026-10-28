"""Offline regressions: real image/video codecs; isolated in-memory DB double.

Run: backend\\.venv\\Scripts\\python.exe -m unittest discover -s backend -p test_performance.py -v
The DB double avoids mutating the user's library and does not benchmark LanceDB.
"""
import asyncio
import importlib
import os
import sys
import sqlite3
import tempfile
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import numpy as np
import cv2

TEST_ROOT = tempfile.TemporaryDirectory(prefix="gallery-regression-", dir=Path(__file__).resolve().parent)
assert Path(TEST_ROOT.name).resolve().is_relative_to(Path(__file__).resolve().parent)
os.environ["GALLERY_DATA_DIR"] = TEST_ROOT.name


class Query:
    def __init__(self, table):
        self.table = table
        self.columns = None
        self.maximum = None
        self.start = 0
        self.filter = None

    def where(self, value):
        self.table.last_filter = value
        self.filter = value
        return self

    def select(self, columns):
        self.columns = columns
        return self

    def limit(self, count):
        self.maximum = count
        return self

    def offset(self, count):
        self.start = count
        return self

    def to_list(self):
        rows = self.table.rows
        if self.filter and rows:
            # Evaluate actual SQL equality/escaping semantics rather than merely
            # accepting a mocked .where() call.
            columns = sorted({key for row in rows for key in row})
            with sqlite3.connect(":memory:") as connection:
                connection.execute("CREATE TABLE records (" + ",".join('"' + key + '"' for key in columns) + ")")
                connection.executemany("INSERT INTO records VALUES (" + ",".join("?" for _ in columns) + ")",
                                       [[str(row.get(key)) if isinstance(row.get(key), list) else row.get(key) for key in columns] for row in rows])
                ids = [item[0] - 1 for item in connection.execute("SELECT rowid FROM records WHERE " + self.filter)]
                rows = [rows[index] for index in ids]
        rows = rows[self.start:]
        if self.maximum is not None:
            rows = rows[:self.maximum]
        return [{k: v for k, v in r.items() if self.columns is None or k in self.columns} for r in rows]


class Table:
    def __init__(self, schema):
        self.schema = schema
        self.rows = []
        self.version = 1
        self.searches = 0
        self.indexes = []

    def __len__(self):
        return len(self.rows)

    def search(self, *args):
        self.searches += 1
        return Query(self)

    def add(self, rows):
        self.rows.extend(rows)
        self.version += 1

    def delete(self, predicate):
        matches = self.search().where(predicate).to_list()
        self.rows = [row for row in self.rows if row not in matches]
        self.version += 1

    def list_indices(self):
        return self.indexes

    def optimize(self, **kwargs):
        assert kwargs['cleanup_older_than'].days == 36500
        self.version += 1

    def create_scalar_index(self, column, **kwargs):
        self.indexes.append(types.SimpleNamespace(columns=[column]))
        self.version += 1


class Database:
    def create_table(self, name, schema, **kwargs):
        return Table(schema)


fake_lancedb = types.ModuleType("lancedb")
fake_lancedb.connect = lambda *args, **kwargs: Database()
with patch.dict(sys.modules, {"lancedb": fake_lancedb}):
    app = importlib.import_module("main")

import media_cache
import location_index
from index_maintenance import maintain_tables


async def immediate_thread(function, *args, **kwargs):
    return function(*args, **kwargs)


def resolve(coroutine):
    """Drive coroutines whose worker boundaries are mocked with immediate results."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()
    raise AssertionError("Test unexpectedly requires a real event loop")


class PerformanceRegressionTests(unittest.TestCase):
    def test_api_version_matches_release(self):
        release = (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(app.app.version, release)

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(dir=TEST_ROOT.name)
        self.root = Path(self.work.name)
        self.photo = self.root / "photo.jpg"
        Image.new("RGB", (2400, 1600), "red").save(self.photo)
        location_index.set_active_folder(str(self.root))
        app.table.rows = [{"id": "photo", "path": str(self.photo.resolve())}]
        app.video_table.rows = []
        app.table.version += 1
        app.video_table.version += 1
        app._invalidate_media_caches()

    def tearDown(self):
        # SQLite sidecar connections hold Windows file handles.
        with location_index._db_lock:
            for connection in location_index._db_connections.values():
                connection.close()
            location_index._db_connections.clear()
        location_index.set_active_folder(None)
        self.assertTrue(self.root.resolve().is_relative_to(Path(TEST_ROOT.name).resolve()))
        self.work.cleanup()

    def test_engine_import_does_not_import_torch(self):
        self.assertNotIn("torch", sys.modules)
        info = app.engine.get_device_info()
        self.assertEqual(info["model_status"], "not_started")

    def test_photo_preview_is_small_and_source_is_preserved(self):
        before = self.photo.read_bytes()
        target, cached = location_index.get_thumb_target_path(str(self.photo))
        self.assertIsNone(cached)
        media_cache.generate_preview(self.photo, None, target)
        with Image.open(target) as preview:
            self.assertEqual(preview.size, (400, 267))
        self.assertEqual(self.photo.read_bytes(), before)
        self.assertLess(target.stat().st_size, self.photo.stat().st_size)

    def test_source_change_invalidates_preview(self):
        target, _ = location_index.get_thumb_target_path(str(self.photo))
        media_cache.generate_preview(self.photo, None, target)
        Image.new("RGB", (1000, 1000), "blue").save(self.photo)
        updated, cached = location_index.get_thumb_target_path(str(self.photo))
        self.assertNotEqual(updated, target)
        self.assertIsNone(cached)

    def test_atomic_parallel_writers(self):
        target = self.root / "shared.jpg"
        def write(_):
            with Image.new("RGB", (800, 600), "blue") as image:
                media_cache.save_preview(image, target)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(12)))
        with Image.open(target) as preview:
            preview.load()
            self.assertEqual(preview.size, (400, 300))
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_video_preview_dimensions_and_timestamp(self):
        path = self.root / "clip.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (1280, 720))
        self.assertTrue(writer.isOpened())
        try:
            for number in range(20):
                frame = np.zeros((720, 1280, 3), dtype=np.uint8)
                frame[:, :, 2 if number < 10 else 0] = 255
                writer.write(frame)
        finally:
            writer.release()
        target = self.root / "frame.jpg"
        media_cache.generate_preview(path, 1.5, target, cv2)
        with Image.open(target) as image:
            self.assertEqual(image.size, (400, 225))
            red, green, blue = image.getpixel((100, 100))
            self.assertGreater(blue, red + 100)

    def test_failed_frame_is_not_cached_as_frame_zero(self):
        capture = types.SimpleNamespace(isOpened=lambda: True, set=lambda *a: True,
                                        read=lambda: (False, None), get=lambda *a: 30, release=lambda: None)
        fake = types.SimpleNamespace(VideoCapture=lambda *a: capture, CAP_PROP_POS_MSEC=0,
                                     CAP_PROP_FPS=1, CAP_PROP_POS_FRAMES=2)
        target = self.root / "missing.jpg"
        with self.assertRaises(ValueError):
            media_cache.generate_preview("video.mp4", 90, target, fake)
        self.assertFalse(target.exists())

    def test_clearing_table_revokes_cached_authorization(self):
        # Canonicalization is a platform boundary, stubbed alongside the DB.
        with patch.object(Path, "resolve", return_value=self.photo.absolute()):
            app._authorized_media(str(self.photo), "image")
            app.table.rows = []
            app.table.version += 1
            with self.assertRaises(app.HTTPException) as error:
                app._authorized_media(str(self.photo), "image")
        self.assertEqual(error.exception.status_code, 404)

    def test_frame_metadata_cache_updates_after_append(self):
        video = self.root / "video.mp4"
        video.touch()
        app.video_table.add([{"path": str(video.resolve()), "timestamp": 0.0}])
        with patch.object(Path, "resolve", return_value=video.absolute()):
            self.assertEqual(app._indexed_video_path(str(video))[1], [0.0])
            app.video_table.add([{"path": str(video), "timestamp": 2.0}])
            self.assertEqual(app._indexed_video_path(str(video))[1], [0.0, 2.0])

    def test_cached_frame_never_loads_frame_list(self):
        video = self.root / "video.mp4"
        video.touch()
        app.video_table.add([{"path": str(video.resolve()), "timestamp": 2.0}])
        target, _ = location_index.get_thumb_target_path(str(video), 2)
        Image.new("RGB", (400, 225)).save(target)
        with patch.object(app.asyncio, "to_thread", immediate_thread), patch.object(app, "_indexed_video_path", side_effect=AssertionError("Loaded all frames")), patch.object(Path, "resolve", return_value=video.absolute()):
            response = resolve(app.get_sampled_video_frame(types.SimpleNamespace(headers={}), str(video), 2.0))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.path, str(target))

    def test_unindexed_path_is_rejected(self):
        other = self.root / "other.jpg"
        Image.new("RGB", (20, 20)).save(other)
        with patch.object(Path, "resolve", return_value=other.absolute()):
            with self.assertRaises(app.HTTPException) as error:
                app._authorized_media(str(other), "image")
        self.assertEqual(error.exception.status_code, 404)

    def test_quoted_photo_id_lookup(self):
        app.table.rows[0]["id"] = "it's a photo"
        with patch.object(Path, "resolve", return_value=self.photo.absolute()):
            target = app._photo_target(None, "it's a photo")
        self.assertEqual(target, self.photo)

    def test_indexing_photo_and_video_commits_and_caches_previews(self):
        app.table.rows = []
        app.catalog_table.rows = []
        app._catalog_backfilled = True
        app.session_manager.reset()
        video = self.root / "sample.avi"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 64))
        try:
            for _ in range(20):
                writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
        finally:
            writer.release()
        async def infer(method, images, dim):
            return [[1.0] + [0.0] * 767 for _ in images]
        with patch.object(app.asyncio, "to_thread", immediate_thread), patch.object(app, "_infer", infer), patch.object(app.engine, "model_status", "ready"), patch.object(app.engine, "model_loaded", True), patch.object(app, "discover_media_files", return_value=([self.photo], [video])):
            response = resolve(app.index_stream(str(self.root), False, 0.5, False))
            events = []
            iterator = response.body_iterator
            while True:
                try:
                    events.append(resolve(iterator.__anext__()))
                except StopAsyncIteration:
                    break
        self.assertEqual(len(app.table), 1)
        self.assertGreaterEqual(len(app.video_table), 2)
        self.assertEqual(len(app.catalog_table), 2)
        self.assertTrue(any('"event": "complete"' in event for event in events))
        for row in app.video_table.rows:
            _, cached = location_index.get_thumb_target_path(row["path"], row["timestamp"])
            self.assertIsNotNone(cached)

    def test_preview_etag_returns_not_modified(self):
        first = app._preview_response(self.photo, types.SimpleNamespace(headers={}))
        second = app._preview_response(self.photo, types.SimpleNamespace(headers={"if-none-match": first.headers["etag"]}))
        self.assertEqual(second.status_code, 304)

    def test_resume_reindexes_changed_source(self):
        app.table.rows = [{"id": "photo.jpg_1", "path": str(self.photo), "filename": self.photo.name}]
        app.catalog_table.rows = []
        app._catalog_backfilled = True
        app.session_manager.reset()
        app.session_manager.save_state({"status": "completed", "directory": str(self.root), "processed_files": 1})
        async def infer(method, images, dim):
            return [[1.0] + [0.0] * 767 for _ in images]
        with patch.object(app.asyncio, "to_thread", immediate_thread), patch.object(app, "_infer", infer), patch.object(app.engine, "model_status", "ready"), patch.object(app.engine, "model_loaded", True), patch.object(app, "discover_media_files", return_value=([self.photo], [])):
            response = resolve(app.index_stream(str(self.root), True, 0.5, False))
            while True:
                try:
                    resolve(response.body_iterator.__anext__())
                except StopAsyncIteration:
                    break
        self.assertEqual(len(app.table), 1)
        self.assertEqual(app.table.rows[0]["id"], f"photo.jpg_{self.photo.stat().st_mtime_ns}")

    def test_gallery_duration_does_not_decode_video(self):
        with patch.object(app.cv2, "VideoCapture", side_effect=AssertionError("Unexpected video decode")), patch.object(app, "get_cached_duration", return_value=None):
            self.assertEqual(app.get_video_duration("uncached.mp4", cached_only=True), 0)

    def test_synthetic_embeddings_cannot_pollute_index(self):
        with patch.object(app.engine, "model_status", "fallback"), patch.object(app.engine, "model_loaded", False):
            with self.assertRaises(app.HTTPException):
                app._require_model_ready()

    def test_disconnected_search_skips_inference(self):
        async def disconnected():
            return True
        request = types.SimpleNamespace(is_disconnected=disconnected)
        with self.assertRaises(app.HTTPException) as error:
            resolve(app._infer(lambda: self.fail("Inference should not run"), request=request))
        self.assertEqual(error.exception.status_code, 499)

    def test_maintenance_preserves_rows_and_skips_unchanged_tables(self):
        marker = self.root / "maintenance.json"
        count = len(app.table)
        first = maintain_tables([("photos", app.table)], marker)
        self.assertEqual(first["photos"]["status"], "optimized")
        self.assertEqual(len(app.table), count)
        self.assertEqual({column for index in app.table.indexes for column in index.columns}, {"path", "id"})
        second = maintain_tables([("photos", app.table)], marker)
        self.assertEqual(second["photos"]["status"], "current")


if __name__ == "__main__":
    unittest.main()
