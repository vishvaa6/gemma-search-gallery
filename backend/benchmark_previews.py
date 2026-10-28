"""Synthetic codec microbenchmark; does not measure live search or the user's media."""
import io
import json
import statistics
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from media_cache import generate_preview


def main():
    # Reproducible textured 1080p input. Real media compressibility varies.
    frame = np.random.default_rng(42).integers(0, 256, (1080, 1920, 3), dtype=np.uint8)
    _, legacy = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
    parent = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="preview-benchmark-", dir=parent) as directory:
        root = Path(directory).resolve()
        assert root.is_relative_to(parent)
        source = root / "source.jpg"
        source.write_bytes(legacy.tobytes())
        target = root / "preview.jpg"
        start = time.perf_counter()
        generate_preview(source, None, target)
        cold_ms = (time.perf_counter() - start) * 1000
        small = target.read_bytes()
        def decode_ms(data):
            samples = []
            for _ in range(15):
                start = time.perf_counter()
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                samples.append((time.perf_counter() - start) * 1000)
            return round(statistics.median(samples), 3)
        print(json.dumps({
            "fixture": "seeded random RGB 1920x1080, JPEG quality 82",
            "legacy_bytes": len(legacy), "preview_bytes": len(small),
            "transfer_reduction_percent": round(100 * (1 - len(small) / len(legacy)), 2),
            "legacy_decode_median_ms": decode_ms(legacy.tobytes()),
            "preview_decode_median_ms": decode_ms(small),
            "cold_photo_preview_ms": round(cold_ms, 3),
            "scope": "Synthetic image codec benchmark only; not end-to-end app latency",
        }, indent=2))


if __name__ == "__main__":
    main()
