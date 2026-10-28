"""Idle database maintenance. Keep historical versions and source media intact."""
import json
import math
import time
from datetime import timedelta
from pathlib import Path


def maintain_tables(tables, marker_path, can_run=lambda: True):
    marker_path = Path(marker_path)
    try:
        state = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    results = {}
    for name, table in tables:
        if not can_run():
            break
        previous = state.get(name, {})
        version = table.version
        # Small indexing batches need compaction, but not after every append.
        if previous and 0 <= version - previous.get("version", 0) < 250:
            results[name] = {**previous, "status": "current", "version": version}
            continue
        started = time.perf_counter()
        count = len(table)
        if count:
            # optimize also updates existing ANN indexes with appended rows.
            # A 100-year retention window preserves all this app's history.
            table.optimize(cleanup_older_than=timedelta(days=36500))
            indexed = {column for index in table.list_indices() for column in index.columns}
            columns = set(table.schema.names)
            for column in ("path", "id", "source_id"):
                if column in columns and column not in indexed:
                    table.create_scalar_index(column, index_type="BTREE", replace=False)
            if "vector" in columns and "vector" not in indexed and count >= 1000:
                table.create_index(vector_column_name="vector", metric="l2", index_type="IVF_FLAT",
                                   num_partitions=max(16, min(256, math.isqrt(count))))
        if len(table) != count:
            raise RuntimeError(f"{name}: row count changed during maintenance; stop indexing first")
        results[name] = {"status": "optimized", "rows": count, "version": table.version,
                         "vector_index": any("vector" in index.columns for index in table.list_indices()),
                         "elapsed_seconds": round(time.perf_counter() - started, 3)}
        state[name] = results[name]
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = marker_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(marker_path)
    return results


if __name__ == "__main__":
    import os
    import lancedb
    root = Path(os.getenv("GALLERY_DATA_DIR", str(Path(__file__).parent / "data")))
    database = lancedb.connect(str(root / "lancedb"))
    names = ("photos", "video_frames", "media_catalog", "face_embeddings", "face_sources")
    print(json.dumps(maintain_tables([(name, database.open_table(name)) for name in names],
                                     root / "maintenance.json"), indent=2))
