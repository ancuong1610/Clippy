# clipfarm — chat-spike auto-clipper (GTA 6 launch MVP)

When something crazy happens on stream, chat explodes. clipfarm watches chat, detects the spike
relative to that channel's own normal (adaptive baseline, works for 50 or 5,000 msg/min), and cuts
the video from **30 s before** the spike to **8 s after** (chat reacts late).

Design + research: [clipping-farm-design.md](clipping-farm-design.md)

## Two modes

| Mode | Command | Speed | Use when |
|---|---|---|---|
| **Live** | `uv run clipfarm run` | clip ready ~20 s after the moment | GTA 6 launch marathons: be first to post |
| **VOD** | `uv run clipfarm analyze chat.json --vod <url>` | after the stream | catch-up on streams you missed |

### Live
1. Edit [channels.toml](channels.toml) (Twitch or YouTube Live channels).
2. `uv run clipfarm run` — per channel: streamlink → rolling 15‑min buffer, anonymous chat reader, detector.
3. Output: `clips/<channel>/<time>_r<ratio>.mp4` + `.json` (chat around the moment, label) and `clips/events.jsonl`.
4. Pipeline check without waiting for a moment: `uv run clipfarm run --test-trigger 120`.

### VOD (same idea as the "chat tells you" video)
```bash
tools/TwitchDownloaderCLI chatdownload -u https://www.twitch.tv/videos/<id> -o chat.json
uv run clipfarm analyze chat.json --vod https://www.twitch.tv/videos/<id> --top 10
```
Only the spike sections are downloaded (yt-dlp `--download-sections`), not the whole VOD.

## Moment score (0–100)
Every clip gets a score = **chat** (max 60: spike ratio vs normal + share of hype messages) +
**audio** (max 40: loudest moment near the spike vs the clip's normal loudness — screams, explosions).
File names carry it (`<time>_s72.mp4`). Below `min_score` (default 35) clips go to `clips/<ch>/low/`.
Clip length: `length = "auto" | "15-30" | "30-60"` in channels.toml (or `analyze --length`).

## Edit → Short (local, free, no API key)
```bash
uv run clipfarm edit clips/xqc/<clip>.mp4                                   # blur layout, karaoke captions
uv run clipfarm edit clips/xqc/<clip>.mp4 --layout square --captions hormozi
uv run clipfarm edit clips/xqc/<clip>.mp4 --layout stack --facecam 0,0.69,0.22,0.31 --title "..."
```
Layouts: `blur` (whole frame), `square` (center square — crops out corner facecams), `stack` (facecam top).
Captions: `karaoke` (2 words, active word green — default), `hormozi` (2 words, active word on yellow pill),
`classic` (3 words, yellow). Transcript is cached next to the clip (`<clip>.words.json`), so re-renders are fast.
1080×1920, word-by-word captions (local Whisper large-v3-turbo on Apple Silicon, ~30–60 s per clip after
first model download), active word in yellow, optional title box. `--facecam x,y,w,h` = facecam position as
fractions of the 16:9 frame (streamers move it — check per streamer). Title defaults to the AI label if set.
OpenShorts/SupoClip remain options for AI reframing but need Docker + a Gemini key.
Upload stays a human decision (reused-content + copyright rules — see design doc §4).

## Optional AI labels (OpenRouter)
Create `.env` (git-ignored) with `OPENROUTER_API_KEY=...` and optionally `OPENROUTER_MODEL=...`.
Each clip's JSON then gets `what_happened`, `title`, `keep_score` (0–10) inferred from chat.

## Tuning (`[detector]` in channels.toml)
- `ratio` (3.0): lower = more clips. In a 20‑min xQc test, ordinary chat swings stayed ≤ 1.9×.
- `cooldown_s` (45): min gap between clips per channel.
- `chat_video_offset_s` (0): if clips start too late/early, shift here.

## Setup (done on this Mac)
`brew install ffmpeg`, `uv sync`, `tools/TwitchDownloaderCLI` (MacOSArm64 release 1.56.5, MIT). Tests: `uv run pytest`.

## Verified 2026‑11‑18
- Unit: 18 tests pass (detector, moment score incl. synthetic loud/quiet clips, length presets, caption presets).
- Live: xQc stream, 4 min → buffer + chat (≈600 msg/min) + forced clip = 38.0 s, 720p60, audio, 379 chat lines.
- VOD: xQc VOD chat (8,458 msgs/20 min) → spikes ranked → 2 × 38.0 s 1080p sections cut.
- Not yet verified: a real live spike auto-firing end-to-end; YouTube Live chat path; OpenRouter labels (no key yet).

Known: `chat-downloader` can no longer fetch Twitch **VOD** chat (Twitch API change) → use TwitchDownloaderCLI.
