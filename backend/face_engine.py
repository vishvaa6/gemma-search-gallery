"""Local YuNet/SFace high-recall multi-scale face detection and embedding helpers."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import numpy as np
from PIL import Image, ImageOps


MODEL_VERSION = "yunet-2023mar-sface-2021dec-v2"
MODEL_URLS = {
    "yunet": "https://github.com/opencv/opencv_zoo/raw/refs/heads/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "sface": "https://github.com/opencv/opencv_zoo/raw/refs/heads/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}


def _box_iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    """Compute IoU between two [x, y, w, h] boxes."""
    ax1, ay1, aw, ah = float(box_a[0]), float(box_a[1]), float(box_a[2]), float(box_a[3])
    bx1, by1, bw, bh = float(box_b[0]), float(box_b[1]), float(box_b[2]), float(box_b[3])
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area <= 0.0:
        return 0.0
    union_area = max(1e-6, aw * ah + bw * bh - inter_area)
    return inter_area / union_area


def _is_same_face_candidate(det_a: np.ndarray, det_b: np.ndarray) -> bool:
    """Check if two detections from different pyramid scales refer to the same physical face."""
    iou = _box_iou(det_a[:4], det_b[:4])
    if iou >= 0.36:
        return True
    # Also check if one box is mostly contained inside the other with close centers
    ax, ay, aw, ah = [float(v) for v in det_a[:4]]
    bx, by, bw, bh = [float(v) for v in det_b[:4]]
    acx, acy = ax + aw * 0.5, ay + ah * 0.5
    bcx, bcy = bx + bw * 0.5, by + bh * 0.5
    center_dist = ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5
    min_side = max(1.0, min(aw, ah, bw, bh))
    if center_dist < min_side * 0.42 and iou >= 0.22:
        return True
    return False


def _validate_face_geometry(det: np.ndarray, img_w: int, img_h: int) -> Tuple[bool, float]:
    """
    Validates YuNet 15-value detection [x, y, w, h, re_x, re_y, le_x, le_y, nt_x, nt_y, rcm_x, rcm_y, lcm_x, lcm_y, score]
    using 5-point facial landmark geometry so real faces (including angled/side-profile/small faces)
    pass while non-face background false positives are rejected.
    Returns (is_valid, geometry_quality_bonus).
    """
    if det.shape[0] < 15 or not np.isfinite(det).all():
        return False, 0.0

    x, y, w, h = float(det[0]), float(det[1]), float(det[2]), float(det[3])
    score = float(det[14])

    # Clip box to image boundaries to check visible dimensions
    x1 = max(0.0, x)
    y1 = max(0.0, y)
    x2 = min(float(img_w), x + w)
    y2 = min(float(img_h), y + h)
    vis_w = x2 - x1
    vis_h = y2 - y1

    if vis_w < 16.0 or vis_h < 16.0 or w < 16.0 or h < 16.0:
        return False, 0.0

    aspect = w / max(1.0, h)
    if aspect < 0.38 or aspect > 1.75:
        return False, 0.0

    re_x, re_y = float(det[4]), float(det[5])
    le_x, le_y = float(det[6]), float(det[7])
    nt_x, nt_y = float(det[8]), float(det[9])
    rcm_x, rcm_y = float(det[10]), float(det[11])
    lcm_x, lcm_y = float(det[12]), float(det[13])

    # Landmarks should lie within a generous margin around the bounding box
    pad_x, pad_y = w * 0.35, h * 0.35
    for lx, ly in ((re_x, re_y), (le_x, le_y), (nt_x, nt_y), (rcm_x, rcm_y), (lcm_x, lcm_y)):
        if not (x - pad_x <= lx <= x + w + pad_x and y - pad_y <= ly <= y + h + pad_y):
            return False, 0.0

    eye_mid_y = 0.5 * (re_y + le_y)
    mouth_mid_y = 0.5 * (rcm_y + lcm_y)
    eye_to_mouth_y = mouth_mid_y - eye_mid_y

    # Eyes must be above the mouth in upright/moderately tilted faces
    if eye_to_mouth_y < h * 0.10:
        return False, 0.0

    # Nose should vertically sit near or between the eye line and mouth line (with pitch tolerance)
    if nt_y < eye_mid_y - h * 0.15 or nt_y > mouth_mid_y + h * 0.18:
        return False, 0.0

    inter_eye_dist = ((le_x - re_x) ** 2 + (le_y - re_y) ** 2) ** 0.5
    mouth_width = ((lcm_x - rcm_x) ** 2 + (lcm_y - rcm_y) ** 2) ** 0.5

    # Compute landmark symmetry / frontalness bonus
    eye_tilt = abs(le_y - re_y) / max(1.0, inter_eye_dist)
    symmetry_bonus = 0.0
    if inter_eye_dist >= w * 0.16 and mouth_width >= w * 0.12 and eye_tilt < 0.45:
        symmetry_bonus = 0.06
    elif inter_eye_dist >= w * 0.08:
        symmetry_bonus = 0.03

    return True, symmetry_bonus


class FaceEngine:
    """Lazy-load YuNet/SFace models with multi-scale pyramid detection & TTA flip embeddings."""

    def __init__(self, cv2_module, model_dir: Path):
        self.cv2 = cv2_module
        self.model_dir = Path(model_dir)
        self.detector = None
        self.recognizer = None
        self._lock = threading.RLock()

    def _ensure_file(self, name: str, progress: Callable[[str, int, int], None] | None = None) -> Path:
        destination = self.model_dir / ("face_detection_yunet_2023mar.onnx" if name == "yunet" else "face_recognition_sface_2021dec.onnx")
        minimum_size = 20_000 if name == "yunet" else 1_000_000
        if destination.is_file() and destination.stat().st_size >= minimum_size:
            return destination

        import requests

        self.model_dir.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".download")
        downloaded = 0
        total = 0
        try:
            with requests.get(MODEL_URLS[name], stream=True, timeout=(15, 120)) as response:
                response.raise_for_status()
                try:
                    total = int(response.headers.get("Content-Length", 0))
                except (TypeError, ValueError):
                    total = 0
                with open(temporary, "wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        output.write(chunk)
                        downloaded += len(chunk)
                        if progress:
                            progress(name, downloaded, total)
            if downloaded < minimum_size:
                raise RuntimeError(f"Downloaded {name} model is incomplete ({downloaded} bytes). Check the network and retry.")
            os.replace(temporary, destination)
            return destination
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def ensure_ready(self, progress: Callable[[str, int, int], None] | None = None):
        with self._lock:
            if self.detector is not None and self.recognizer is not None:
                return
            if self.cv2 is None:
                raise RuntimeError("Face matching requires OpenCV contrib. Run install.bat, then restart the app.")

            detector_path = self._ensure_file("yunet", progress)
            recognizer_path = self._ensure_file("sface", progress)
            detector_factory = getattr(self.cv2, "FaceDetectorYN", None)
            recognizer_factory = getattr(self.cv2, "FaceRecognizerSF", None)
            if detector_factory is None or recognizer_factory is None:
                raise RuntimeError("The installed OpenCV build does not include YuNet/SFace. Run install.bat and restart the app.")

            # Candidate score threshold 0.55 captures angled, small, and dimly lit faces;
            # false positives are filtered out by multi-scale consensus + 5-landmark validation.
            self.detector = detector_factory.create(
                str(detector_path), "", (320, 320), 0.55, 0.35, 5000
            )
            self.recognizer = recognizer_factory.create(str(recognizer_path), "")

    def _detect_at_scale(self, bgr_img: np.ndarray, target_max_side: int) -> List[np.ndarray]:
        """Run YuNet at a specific pyramid scale and map coordinates back to original image space."""
        orig_h, orig_w = bgr_img.shape[:2]
        max_side = max(orig_w, orig_h)
        if max_side <= 0:
            return []

        scale = float(target_max_side) / float(max_side)
        scaled_w = max(64, int(round(orig_w * scale)))
        scaled_h = max(64, int(round(orig_h * scale)))

        if abs(scale - 1.0) > 0.03:
            interp = self.cv2.INTER_CUBIC if scale > 1.0 else self.cv2.INTER_AREA
            resized = self.cv2.resize(bgr_img, (scaled_w, scaled_h), interpolation=interp)
        else:
            resized = bgr_img
            scale = 1.0

        self.detector.setInputSize((scaled_w, scaled_h))
        _, raw_faces = self.detector.detect(resized)
        if raw_faces is None or len(raw_faces) == 0:
            return []

        inv_scale = 1.0 / scale
        mapped: List[np.ndarray] = []
        for det in raw_faces:
            row = np.asarray(det, dtype=np.float32).copy()
            # Map bbox (0..3) and 5 landmarks (4..13) back to original image pixels
            row[:14] *= inv_scale
            mapped.append(row)
        return mapped

    def _enhance_low_light_bgr(self, bgr_img: np.ndarray) -> np.ndarray:
        """Apply LAB-space CLAHE luminance enhancement to recover faces in shadows or backlighting."""
        lab = self.cv2.cvtColor(bgr_img, self.cv2.COLOR_BGR2LAB)
        l_chan, a_chan, b_chan = self.cv2.split(lab)
        clahe = self.cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
        l_eq = clahe.apply(l_chan)
        merged = self.cv2.merge((l_eq, a_chan, b_chan))
        return self.cv2.cvtColor(merged, self.cv2.COLOR_LAB2BGR)

    def _detect_multiscale_faces(self, bgr: np.ndarray) -> List[np.ndarray]:
        """
        Multi-scale pyramid + CLAHE low-light face detector:
        - Scale 640px: catches large close-up / selfie portraits that exceed YuNet's anchor stride at 4K
        - Scale 1280px: balanced resolution for medium & standard faces
        - Scale 1920px (or 1.6x upscaled for small images): catches small/distant faces in group shots
        - CLAHE 1280px pass: catches faces in dark indoor lighting, night shots, or strong backlighting
        """
        orig_h, orig_w = bgr.shape[:2]
        max_side = max(orig_w, orig_h)

        # Build adaptive scale pyramid based on source resolution
        if max_side < 700:
            pyramid_scales = [max_side, min(1440, int(round(max_side * 1.65))), 640]
        elif max_side <= 1400:
            pyramid_scales = [640, max_side, min(1920, int(round(max_side * 1.35)))]
        else:
            pyramid_scales = [640, 1120, 1760]

        # Deduplicate nearly identical target sizes
        unique_scales: List[int] = []
        for s in pyramid_scales:
            if not any(abs(s - existing) < 90 for existing in unique_scales):
                unique_scales.append(s)

        raw_candidates: List[Tuple[np.ndarray, str]] = []
        for target_side in unique_scales:
            for det in self._detect_at_scale(bgr, target_side):
                raw_candidates.append((det, f"scale_{target_side}"))

        # Run CLAHE shadow/low-light pass to ensure dark/backlit faces are never missed
        try:
            enhanced_bgr = self._enhance_low_light_bgr(bgr)
            clahe_side = min(1280, max(640, max_side))
            for det in self._detect_at_scale(enhanced_bgr, clahe_side):
                raw_candidates.append((det, "clahe"))
        except Exception:
            pass

        # Validate geometry on all candidates
        validated: List[Dict] = []
        for det, source_tag in raw_candidates:
            ok, geom_bonus = _validate_face_geometry(det, orig_w, orig_h)
            if not ok:
                continue
            raw_score = float(det[14])
            validated.append({
                "det": det,
                "raw_score": raw_score,
                "geom_bonus": geom_bonus,
                "source_tag": source_tag,
            })

        if not validated:
            return []

        # Group overlapping detections across scales and count multi-scale consensus
        validated.sort(key=lambda item: item["raw_score"] + item["geom_bonus"], reverse=True)
        clusters: List[Dict] = []
        for cand in validated:
            matched_cluster = None
            for cluster in clusters:
                if _is_same_face_candidate(cand["det"], cluster["best_det"]):
                    matched_cluster = cluster
                    break
            if matched_cluster is None:
                clusters.append({
                    "best_det": cand["det"].copy(),
                    "max_raw_score": cand["raw_score"],
                    "geom_bonus": cand["geom_bonus"],
                    "sources": {cand["source_tag"]},
                    "members": [cand["det"]],
                })
            else:
                matched_cluster["sources"].add(cand["source_tag"])
                matched_cluster["members"].append(cand["det"])
                if cand["raw_score"] > matched_cluster["max_raw_score"]:
                    matched_cluster["max_raw_score"] = cand["raw_score"]
                    matched_cluster["best_det"] = cand["det"].copy()
                matched_cluster["geom_bonus"] = max(matched_cluster["geom_bonus"], cand["geom_bonus"])

        # Filter by consensus-aware confidence floor:
        # - Detected at >= 2 pyramid scales: accept down to 0.56
        # - Strong landmark symmetry: accept down to 0.60
        # - Single scale detection: accept >= 0.65
        accepted_detections: List[np.ndarray] = []
        for cluster in clusters:
            support = len(cluster["sources"])
            max_score = cluster["max_raw_score"]
            geom_bonus = cluster["geom_bonus"]
            if support >= 2 and max_score >= 0.55:
                accepted = True
            elif geom_bonus >= 0.05 and max_score >= 0.59:
                accepted = True
            elif max_score >= 0.65:
                accepted = True
            else:
                accepted = False

            if accepted:
                det_out = cluster["best_det"].copy()
                # Store effective confidence in slot 14
                det_out[14] = min(0.999, max_score + (0.04 if support >= 2 else 0.0) + geom_bonus * 0.5)
                accepted_detections.append(det_out)

        # Sort faces deterministically: largest & highest confidence first, then left-to-right
        accepted_detections.sort(
            key=lambda d: (round(float(d[14]), 2), float(d[2] * d[3]), -float(d[0])),
            reverse=True,
        )
        return accepted_detections

    def _extract_face_embedding(self, bgr: np.ndarray, enhanced_bgr: np.ndarray | None, detection: np.ndarray) -> np.ndarray | None:
        """
        Compute a pose-robust 128d SFace descriptor using Test-Time Augmentation (TTA):
        averages the L2-normalized embedding of the aligned face crop with its horizontal mirror
        (and blends a low-light CLAHE crop if the image is very dark).
        """
        aligned = self.recognizer.alignCrop(bgr, detection)
        if aligned is None or aligned.size == 0:
            return None

        descriptors: List[Tuple[np.ndarray, float]] = []

        # 1. Primary aligned crop
        vec_orig = np.asarray(self.recognizer.feature(aligned), dtype=np.float32).reshape(-1)
        if vec_orig.shape == (128,) and np.isfinite(vec_orig).all():
            n_orig = float(np.linalg.norm(vec_orig))
            if n_orig > 1e-12:
                descriptors.append((vec_orig / n_orig, 0.65))

        # 2. Horizontal flip TTA crop for pose invariance (left vs right head turn)
        flipped = self.cv2.flip(aligned, 1)
        vec_flip = np.asarray(self.recognizer.feature(flipped), dtype=np.float32).reshape(-1)
        if vec_flip.shape == (128,) and np.isfinite(vec_flip).all():
            n_flip = float(np.linalg.norm(vec_flip))
            if n_flip > 1e-12:
                descriptors.append((vec_flip / n_flip, 0.35))

        # 3. If the face crop is very dark (mean luminance < 55), blend CLAHE-equalized crop
        if enhanced_bgr is not None and float(np.mean(aligned)) < 55.0:
            try:
                aligned_eq = self.recognizer.alignCrop(enhanced_bgr, detection)
                vec_eq = np.asarray(self.recognizer.feature(aligned_eq), dtype=np.float32).reshape(-1)
                if vec_eq.shape == (128,) and np.isfinite(vec_eq).all():
                    n_eq = float(np.linalg.norm(vec_eq))
                    if n_eq > 1e-12:
                        descriptors.append((vec_eq / n_eq, 0.25))
            except Exception:
                pass

        if not descriptors:
            return None

        combined = np.zeros(128, dtype=np.float32)
        for vec, weight in descriptors:
            combined += vec * float(weight)

        norm = float(np.linalg.norm(combined))
        if norm <= 1e-12:
            return None
        return combined / norm

    def analyze(self, image: Image.Image, progress: Callable[[str, int, int], None] | None = None) -> List[Dict]:
        """Return all detected face boxes and normalized 128d descriptors for an RGB PIL image."""
        with self._lock:
            self.ensure_ready(progress)
            # Respect EXIF orientation if present on PIL Image
            try:
                upright = ImageOps.exif_transpose(image)
            except Exception:
                upright = image
            rgb = np.asarray(upright.convert("RGB"), dtype=np.uint8)
            bgr = self.cv2.cvtColor(rgb, self.cv2.COLOR_RGB2BGR)
            height, width = bgr.shape[:2]

            detections = self._detect_multiscale_faces(bgr)
            if not detections:
                return []

            enhanced_bgr = None
            if float(np.mean(bgr)) < 75.0:
                try:
                    enhanced_bgr = self._enhance_low_light_bgr(bgr)
                except Exception:
                    enhanced_bgr = None

            output = []
            for detection in detections:
                x, y, face_width, face_height = [float(value) for value in detection[:4]]
                # Clamp bbox neatly inside image bounds
                x1 = max(0.0, min(float(width - 1), x))
                y1 = max(0.0, min(float(height - 1), y))
                x2 = max(x1 + 1.0, min(float(width), x + face_width))
                y2 = max(y1 + 1.0, min(float(height), y + face_height))

                vector = self._extract_face_embedding(bgr, enhanced_bgr, detection)
                if vector is None or vector.shape != (128,) or not np.isfinite(vector).all():
                    continue

                face_index = len(output)
                output.append({
                    "face_index": face_index,
                    "bbox": [round(x1, 2), round(y1, 2), round(x2 - x1, 2), round(y2 - y1, 2)],
                    "score": round(float(detection[14]), 4),
                    "vector": vector.tolist(),
                })
            return output
