"""Spotting photos that are on their side or upside down.

A camera records which way up it was held, and Simple-Photos already
follows that. This is for photos that lost it — scans, edited copies,
files from old cameras or messaging apps that strip metadata.

The cue is faces: a face detector only finds faces that are roughly
upright, so the photo is tried at all four quarter turns, and if faces
turn up only when it's turned, that's the way up it should be. To keep
a pattern of leaves or coins from being taken for a face, a face only
counts when two different detectors agree on it and eyes are found in
its top half. Photos without people give no clear answer and are left
as they are.

Uses OpenCV's bundled Haar cascades; no network, no models to fetch.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

from simple_photos.core.thumbnails import load_image

DETECT_SIZE = 800  # px, longest side the photo is checked at

# One set per thread: OpenCV doesn't promise a classifier can be used
# from several threads at once, and Auto-Rotate checks photos in parallel.
_local = threading.local()


def _load_cascades():
    cascades = getattr(_local, "cascades", None)
    if cascades is None:
        import cv2

        folder = Path(cv2.data.haarcascades)
        cascades = tuple(
            cv2.CascadeClassifier(str(folder / name))
            for name in (
                "haarcascade_frontalface_default.xml",
                "haarcascade_frontalface_alt2.xml",
                "haarcascade_eye.xml",
            )
        )
        if any(c.empty() for c in cascades):
            raise RuntimeError(f"OpenCV's face detector files are missing from {folder}")
        _local.cascades = cascades
    return cascades


def available() -> bool:
    try:
        _load_cascades()
    except Exception:
        return False
    return True


def _overlap(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / float(aw * ah + bw * bh - inter)


def count_faces(gray) -> int:
    """Faces in a greyscale numpy image that both face detectors agree on
    and that have an eye in their top half."""
    face_a, face_b, eyes = _load_cascades()
    min_side = max(24, min(gray.shape) // 14)
    first = face_a.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_side, min_side))
    if len(first) == 0:
        return 0
    second = face_b.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=3, minSize=(min_side, min_side))
    found = 0
    for face in first:
        if not any(_overlap(face, other) > 0.4 for other in second):
            continue
        x, y, w, h = face
        top_half = gray[y : y + h // 2 + h // 8, x : x + w]
        eye_side = max(5, w // 8)
        if len(eyes.detectMultiScale(top_half, scaleFactor=1.1, minNeighbors=3, minSize=(eye_side, eye_side))):
            found += 1
    return found


def suggested_turns(path: str | Path) -> Optional[int]:
    """Quarter turns clockwise that would put the photo the right way up
    (1, 2 or 3), or None if it already looks right or there's no clear
    answer. Raises OSError if the photo can't be read."""
    import numpy as np

    image = load_image(path, DETECT_SIZE).convert("L")
    gray = np.asarray(image)
    if count_faces(gray) > 0:
        return None  # faces the way it is: it's fine
    counts = {turns: count_faces(np.ascontiguousarray(np.rot90(gray, -turns))) for turns in (1, 2, 3)}
    best = max(counts, key=counts.get)
    if counts[best] == 0 or list(counts.values()).count(counts[best]) > 1:
        return None
    return best


def worker_count() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))
