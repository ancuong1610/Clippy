"""Lab: lay a short voice-over line over the start of a queued Short (an original layer on top of the stream clip).

  uv run python scripts/lab_voiceover.py <item id> <voice.wav> [--at 0.15] [--duck 0.3] [--variant vo_intro]
The game audio drops to `--duck` while the voice speaks. The old video is kept as short_novo.mp4, post.json gets
`variant` so `clipfarm insights` can compare these posts with the rest. Make the wav with any TTS (we use Kokoro).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from clipfarm import post as P


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("item")
    ap.add_argument("voice", type=Path)
    ap.add_argument("--at", type=float, default=0.15)
    ap.add_argument("--duck", type=float, default=0.3)
    ap.add_argument("--variant", default="vo_intro")
    a = ap.parse_args()
    d = next(Path("queue").glob(f"*/{a.item}"))
    it = P.PostItem(**json.loads((d / "post.json").read_text()))
    src = d / "short_novo.mp4"
    if not src.exists():
        (d / "short.mp4").rename(src)
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                         "csv=p=0", str(a.voice)]).strip())
    end = a.at + dur + 0.2
    ms = int(a.at * 1000)
    graph = (f"[0:a]volume='if(between(t,{a.at},{end}),{a.duck},1)':eval=frame[g];"
             f"[1:a]adelay={ms}|{ms},volume=1.6[v];[g][v]amix=inputs=2:duration=first:normalize=0[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-i", str(a.voice), "-filter_complex", graph,
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                    str(d / "short.mp4")], check=True)
    it.variant = a.variant
    it.video_hash = P.file_hash(d / "short.mp4")
    (d / "post.json").write_text(json.dumps(asdict(it), indent=2, ensure_ascii=False))
    print(f"{it.id}: voice {dur:.1f}s at {a.at}s -> {d / 'short.mp4'} (variant {a.variant})")


if __name__ == "__main__":
    main()
