"""Local review-page records and upload-sized previews."""

import json
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from . import post as P

PREVIEWS = Path("review/previews_v3")
BATCHES = Path("review/batches")
VIEW_FIELDS = {"instagram": "ig_views", "tiktok": "tt_views", "youtube": "yt_views"}
JUDGE_FIELDS = ("visual", "bonus", "mood", "unison", "kind", "cutscene", "streamer_caused", "payoff", "why")


MUTED_MAX = 0.4  # Twitch mutes VOD parts with copyrighted music: >= 40 % silence = unusable clip


def muted_share(video: Path, *, runner=None) -> float:
    """Share of the clip that is digital silence (< -60 dB for >= 1 s), from ffmpeg silencedetect."""
    import re
    run = runner or subprocess.run
    err = run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(video), "-af", "silencedetect=noise=-60dB:d=1",
               "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    total = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    silent = sum(float(x) for x in re.findall(r"silence_duration: ([\d.]+)", err))
    return min(1.0, silent / total) if total else 0.0


def source_metadata(source: str | Path | None) -> dict:
    if not source:
        return {}
    path = Path(source).with_suffix(".json")
    return json.loads(path.read_text()) if path.is_file() else {}


def review_doc(item: P.PostItem, cfg: dict, state: str, batch: str, preview: Path, verdict: dict) -> dict:
    return {"title": item.title, "streamer": item.streamer, "hype": item.score or 0,
            "campaign": item.campaign, "queue": state, "reason": None, "payoff": verdict.get("payoff"),
            "video": "", "tiktok": P.social_caption(item, "tiktok", cfg),
            "instagram": P.social_caption(item, "instagram", cfg), "batch": batch,
            "judge": {k: verdict[k] for k in JUDGE_FIELDS if k in verdict}, "preview": str(preview)}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        pending = Path(tmp.name)
        try:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.close()
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


def make_preview(source: Path, output: Path) -> None:
    """Write a 480 px wide H.264 preview within the review page's 15 MB upload limit."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix=".preview-") as tmp:
        preview = Path(tmp) / "preview.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-map", "0:v:0",
                        "-map", "0:a:0?", "-vf", "scale=480:-2,setsar=1", "-c:v", "libx264", "-preset", "fast",
                        "-pix_fmt", "yuv420p", "-crf", "23", "-maxrate", "950k", "-bufsize", "1900k",
                        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(preview)], check=True)
        if preview.stat().st_size >= 15_000_000:
            raise ValueError(f"preview exceeds the 15 MB upload limit: {source}")
        preview.replace(output)
