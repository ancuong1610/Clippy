"""Find the streamer's facecam automatically.

Samples frames, detects faces (OpenCV YuNet), and keeps the face that sits in the SAME place in most
frames — the webcam. Faces in gameplay / watched videos move around and get filtered out.
Returns the crop box (x, y, w, h as fractions of the frame) or None (-> use blur/square layout).
"""

from __future__ import annotations

import os
import subprocess
import urllib.request
from pathlib import Path

import numpy as np

MODEL_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
MODEL = Path(os.path.expanduser("~/.cache/clipfarm/yunet.onnx"))
STACK_ASPECT = 1080 / 640  # top panel of the stack layout


def _model() -> Path:
    if not MODEL.exists():
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, MODEL)
    return MODEL


def _frames(video: Path, n: int, width: int = 1280) -> list[np.ndarray]:
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                         "-of", "csv=p=0", str(video)]).strip())
    out = []
    for t in np.linspace(0.5, max(0.6, dur - 0.5), n):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                              "-vf", f"scale={width}:-2", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                             capture_output=True).stdout
        if raw:
            h = len(raw) // (width * 3)
            out.append(np.frombuffer(raw, np.uint8).reshape(h, width, 3))
    return out


def detect_faces(frame: np.ndarray, min_score: float = 0.75) -> list[tuple[float, float, float, float]]:
    import cv2
    h, w = frame.shape[:2]
    det = cv2.FaceDetectorYN.create(str(_model()), "", (w, h), score_threshold=min_score)
    _, faces = det.detect(frame)
    return [(f[0] / w, f[1] / h, f[2] / w, f[3] / h) for f in (faces if faces is not None else [])]


def facecam_box(faces_per_frame: list[list[tuple]], min_presence: float = 0.6
                ) -> tuple[tuple[float, float, float, float], tuple[float, float]] | None:
    """Pure part: cluster face centers across frames; the stable cluster is the webcam."""
    n = len(faces_per_frame)
    pts = [(fx + fw / 2, fy + fh / 2, fw, fh) for faces in faces_per_frame for fx, fy, fw, fh in faces]
    best = None
    for cx, cy, _, _ in pts:
        members = [p for p in pts if abs(p[0] - cx) < 0.06 and abs(p[1] - cy) < 0.08]
        frames_hit = sum(any(abs(fx + fw / 2 - cx) < 0.06 and abs(fy + fh / 2 - cy) < 0.08
                             for fx, fy, fw, fh in faces) for faces in faces_per_frame)
        if frames_hit / max(n, 1) < min_presence:
            continue
        spread = np.std([m[0] for m in members]) + np.std([m[1] for m in members])
        edge = min(cx, 1 - cx, cy, 1 - cy)  # webcams usually sit near an edge/corner
        key = (frames_hit, -spread - edge)
        if best is None or key > best[0]:
            best = (key, members)
    if not best:
        return None
    m = np.array(best[1])
    cx, cy, fw, fh = np.median(m[:, 0]), np.median(m[:, 1]), np.median(m[:, 2]), np.median(m[:, 3])
    # head-and-shoulders box in the stack panel's aspect (frame is 16:9 -> convert units)
    w = min(1.0, fw * 3.4)
    h = min(1.0, w * (16 / 9) / STACK_ASPECT)
    h = max(h, min(1.0, fh * 2.8))
    x = float(np.clip(cx - w / 2, 0, 1 - w))
    y = float(np.clip(cy - h * 0.42, 0, 1 - h))  # face slightly above center
    focus = (float((cx - x) / w), float((cy - y) / h))
    return (tuple(round(float(v), 4) for v in (x, y, w, h)), (round(float(focus[0]), 3), round(float(focus[1]), 3)))


def detect_facecam(video: Path, n: int = 10):
    frames = _frames(video, n)
    return facecam_box([detect_faces(f) for f in frames])
