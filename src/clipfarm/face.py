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


def snap_to_cam(frames: list[np.ndarray], box: tuple, face: tuple) -> tuple:
    """Shrink the crop to the webcam rectangle (e.g. stop above Kai's chat overlay under his cam).
    A cam border is a straight edge that runs across (nearly) the whole crop and sits in the same place in
    every frame; room edges inside the cam are partial lines. Searched only at least ~half a face away from
    the face (head and shoulders stay in). Box/face are fractions (x, y, w, h)."""
    if not frames:
        return box
    g = np.stack([f.mean(axis=2) for f in frames]).astype(np.float32)  # (n, H, W) gray
    _, H, W = g.shape
    x0, y0, w, h = box
    X0, X1, Y0, Y1 = int(x0 * W), int((x0 + w) * W), int(y0 * H), int((y0 + h) * H)
    fx, fy, fw, fh = face
    FX0, FX1, FY0, FY1 = int(fx * W), int((fx + fw) * W), int(fy * H), int((fy + fh) * H)
    FWp, FHp = max(FX1 - FX0, 1), max(FY1 - FY0, 1)
    # fraction of the line (across the crop) that is a strong edge in the median frame-to-frame sense
    dy = np.median(np.abs(np.diff(g[:, :, X0:X1], axis=1)), axis=0)  # (H-1, cw)
    dx = np.median(np.abs(np.diff(g[:, Y0:Y1, :], axis=2)), axis=0)  # (ch, W-1)
    row_line = (dy > 12).mean(axis=1)
    col_line = (dx > 12).mean(axis=0)

    def first_line(prof, rng, need=0.7):
        for i in rng:
            if 0 <= i < len(prof) and prof[i] >= need:
                return i
        return None

    b = first_line(row_line, range(FY1 + FHp // 4, min(Y1, H - 1)))
    t = first_line(row_line, range(FY0 - FHp // 3, max(Y0, 0), -1))
    r = first_line(col_line, range(FX1 + FWp // 2, min(X1, W - 1)))
    lft = first_line(col_line, range(FX0 - FWp // 2, max(X0, 0), -1))
    NY1 = b if b else Y1
    NY0 = t + 2 if t is not None else Y0
    NX1 = r if r else X1
    NX0 = lft + 2 if lft is not None else X0
    if (NX0, NY0, NX1, NY1) == (X0, Y0, X1, Y1):
        return box
    # keep the stack panel's aspect inside the new limits, face centred / slightly above centre
    target = STACK_ASPECT * H / W  # width/height in pixels of this frame
    cw, ch = NX1 - NX0, NY1 - NY0
    if cw / ch > target:
        cw = int(ch * target)
        nx = int(np.clip((FX0 + FX1) // 2 - cw // 2, NX0, NX1 - cw))
        NX0, NX1 = nx, nx + cw
    else:
        ch = int(cw / target)
        ny = int(np.clip((FY0 + FY1) // 2 - int(ch * 0.42), NY0, NY1 - ch))
        NY0, NY1 = ny, ny + ch
    if (NX1 - NX0) < 2.2 * FWp:  # a crop barely wider than the face is a wrong snap: keep the original
        return box
    return tuple(round(v, 4) for v in (NX0 / W, NY0 / H, (NX1 - NX0) / W, (NY1 - NY0) / H))


def detect_facecam(video: Path, n: int = 10):
    frames = _frames(video, n)
    faces = [detect_faces(f) for f in frames]
    found = facecam_box(faces)
    if not found:
        return None
    box, focus = found
    # the face itself (median of the faces inside the box), to know where the border search starts
    inside = [f for fs in faces for f in fs
              if box[0] <= f[0] + f[2] / 2 <= box[0] + box[2] and box[1] <= f[1] + f[3] / 2 <= box[1] + box[3]]
    if not inside:
        return found
    face = tuple(float(np.median([f[i] for f in inside])) for i in range(4))
    snapped = snap_to_cam(frames, box, face)
    if snapped == box:
        return found
    x, y, w, h = snapped
    cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
    return snapped, (round((cx - x) / w, 3), round((cy - y) / h, 3))
