from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from ..imaging import image_to_display_rgb


@dataclass(frozen=True)
class ImageFeatures:
    path: Path
    width: int
    height: int
    keypoints: np.ndarray
    descriptors: Optional[np.ndarray]

    @property
    def keypoint_count(self) -> int:
        return int(len(self.keypoints))


@dataclass(frozen=True)
class MatchScore:
    score: float
    inliers: int
    good_matches: int
    avg_distance: float


@dataclass(frozen=True)
class SearchResult:
    path: Path
    score: float
    inliers: int
    good_matches: int
    avg_distance: float
    thumbnail: Optional[bytes] = None

    @property
    def confidence(self) -> str:
        """How much the geometric check supports the match: "high", "medium" or "low"."""
        if self.inliers >= 20:
            return "high"
        if self.inliers >= 8:
            return "medium"
        return "low"


def read_image(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return cv2.cvtColor(np.asarray(image_to_display_rgb(image)), cv2.COLOR_RGB2BGR)


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def crop_query_content(gray: np.ndarray) -> np.ndarray:
    mask = (gray < 245) & (gray > 10)
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return gray
    pad = 8
    x0 = max(0, int(xs.min()) - pad)
    x1 = min(gray.shape[1], int(xs.max()) + pad + 1)
    y0 = max(0, int(ys.min()) - pad)
    y1 = min(gray.shape[0], int(ys.max()) + pad + 1)
    return gray[y0:y1, x0:x1]


def extract_features(path: Path, *, query: bool = False, nfeatures: int = 2500) -> ImageFeatures:
    image = read_image(path)
    gray = to_gray(image)
    if query:
        gray = crop_query_content(gray)
    orb = cv2.ORB.create(nfeatures=nfeatures)
    keypoints, descriptors = orb.detectAndCompute(gray, np.empty((0, 0), dtype=np.uint8))
    points = _keypoints_to_array(keypoints)
    if descriptors is not None:
        descriptors = np.asarray(descriptors, dtype=np.uint8)
    return ImageFeatures(
        path=path,
        width=int(gray.shape[1]),
        height=int(gray.shape[0]),
        keypoints=points,
        descriptors=descriptors,
    )


def match_features(query: ImageFeatures, candidate: ImageFeatures) -> MatchScore:
    if (
        query.descriptors is None
        or candidate.descriptors is None
        or len(query.descriptors) < 4
        or len(candidate.descriptors) < 4
        or len(query.keypoints) < 4
        or len(candidate.keypoints) < 4
    ):
        return MatchScore(score=0.0, inliers=0, good_matches=0, avg_distance=999.0)

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(query.descriptors, candidate.descriptors, k=2)
    good = []
    for pair in pairs:
        if len(pair) < 2:
            continue
        first, second = pair
        if first.distance < 0.78 * second.distance:
            good.append(first)

    if not good:
        return MatchScore(score=0.0, inliers=0, good_matches=0, avg_distance=999.0)

    good.sort(key=lambda item: item.distance)
    avg_distance = float(np.mean([item.distance for item in good[:30]]))
    inliers = 0
    if len(good) >= 4:
        source = np.asarray([query.keypoints[item.queryIdx] for item in good], dtype=np.float32).reshape(-1, 1, 2)
        target = np.asarray([candidate.keypoints[item.trainIdx] for item in good], dtype=np.float32).reshape(-1, 1, 2)
        _, mask = cv2.findHomography(source, target, cv2.RANSAC, 5.0)
        if mask is not None:
            inliers = int(mask.sum())

    score = float(inliers * 1000 + len(good) * 10 - avg_distance)
    return MatchScore(
        score=score,
        inliers=inliers,
        good_matches=len(good),
        avg_distance=avg_distance,
    )


def serialize_features(features: ImageFeatures) -> tuple[bytes, Optional[bytes]]:
    keypoint_buffer = BytesIO()
    np.save(keypoint_buffer, features.keypoints.astype(np.float32), allow_pickle=False)
    descriptor_bytes = None
    if features.descriptors is not None:
        descriptor_buffer = BytesIO()
        np.save(descriptor_buffer, features.descriptors.astype(np.uint8), allow_pickle=False)
        descriptor_bytes = descriptor_buffer.getvalue()
    return keypoint_buffer.getvalue(), descriptor_bytes


def deserialize_features(
    path: Path,
    width: int,
    height: int,
    keypoints_blob: bytes,
    descriptors_blob: Optional[bytes],
) -> ImageFeatures:
    keypoints = np.load(BytesIO(keypoints_blob), allow_pickle=False).astype(np.float32)
    descriptors = None
    if descriptors_blob:
        descriptors = np.load(BytesIO(descriptors_blob), allow_pickle=False).astype(np.uint8)
    return ImageFeatures(
        path=path,
        width=int(width),
        height=int(height),
        keypoints=keypoints,
        descriptors=descriptors,
    )


def make_thumbnail(path: Path, *, size: int = 128) -> Optional[bytes]:
    try:
        image = read_image(path)
    except (OSError, ValueError):
        return None
    display = image
    height, width = display.shape[:2]
    scale = min(size / width, size / height, 1.0)
    resized = cv2.resize(
        display,
        (max(1, int(width * scale)), max(1, int(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    ok, encoded = cv2.imencode(".png", resized)
    if not ok:
        return None
    return bytes(encoded)


def _keypoints_to_array(keypoints: Optional[Sequence[cv2.KeyPoint]]) -> np.ndarray:
    if not keypoints:
        return np.empty((0, 2), dtype=np.float32)
    return np.asarray([point.pt for point in keypoints], dtype=np.float32)
