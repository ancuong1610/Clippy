# GTA 6 Clipping Farm — Research + Workflow Design (v0, 2026-11-18)

GTA 6 launches **2026-11-19** (PS5 / Xbox Series, no PC). Big streamers (Kai Cenat, etc.) plan marathons;
YouTube runs a 24h launch stream. Expect a very high volume of clippers competing in the first 72h.

## 1. Core domain (minimal UML)

```
Channel 1──* StreamSession
StreamSession ──has── RollingBuffer   (video segments, last ~10 min)
StreamSession ──has── ChatFeed        (IRC messages, timestamped)
ChatFeed ──feeds──> SpikeDetector ──emits──> HypeEvent(t_spike, score, top_emotes)
HypeEvent ──> Clipper ──cuts RollingBuffer[t_spike-30s, t_spike+5s]──> RawClip
RawClip ──> Editor (9:16 crop, captions, title) ──> Short
Short ──> ReviewQueue (HUMAN GATE) ──> Publisher (YouTube Shorts / TikTok)
```

## 2. Pipeline

| Stage | Tool (existing, free) | Notes |
|---|---|---|
| Capture video | `streamlink` → `ffmpeg -f segment` (10s .ts segments, wall-clock named) | One process per channel; delete segments older than 10–15 min. |
| Capture chat | Twitch IRC anonymous login (`justinfan12345`), e.g. `twitchio` or raw socket | No auth needed for reading. Timestamp each message on receive. |
| Detect | Own Python detector (ideas from `slasher`, CTIC/`clipper`, `clippy`) | See §3. |
| Cut | `ffmpeg -ss … -t … -c copy` over concatenated segments | Window = spike − 30s → spike + 5s, merge overlapping events. |
| Edit | OpenShorts (MIT, faster-whisper captions, face-track crop) or AI-Youtube-Shorts-Generator; manual CapCut for gold standard | Gameplay: center/zoom crop + facecam stacked top. |
| Title/label | Small LLM call on the 60s of chat around spike (clippy pattern) | Produce title + "what happened" + keep/drop score. |
| Publish | YouTube Data API v3 `videos.insert` as **private/scheduled**, after human approval | Shorts = vertical ≤ 3 min. TikTok later. |
| Fallback | Twitch **Create Clip** API (`clips:edit`) on spike | Twitch makes the clip server-side (~30s back). Public under your account; may be restricted by streamer. Download with `yt-dlp`. |

## 3. Chat-spike detection (v0 algorithm)

- Bucket messages into **5s** bins; baseline = EWMA of msgs/5s over the last ~5 min.
- Score = `msgs_5s / baseline` with weights: unique chatters, hype tokens
  (`KEKW, OMEGALUL, LUL, LMAO, W, L, CLIP IT, +2, ???, NO WAY, Pog`), caps-lock ratio, repeated-message ratio.
- Trigger when score ≥ **3×** baseline AND unique chatters ≥ N (tune per channel; huge streams saturate chat — use *ratio*, not absolute counts; Kai-sized chats may need emote-density instead of volume).
- Cooldown 45–60s per channel; merge events < 20s apart.
- Optional 2nd signal: audio RMS spike (streamer screaming) from the buffer — confirms a moment.
- Chat reacts **after** the moment (viewer latency + reaction time) → clip starts **~30s before** the spike.

## 3b. Targets — big streamers only (decision 2026-11-18)

Target streamers who already have a large audience → proven demand + existing clip channels to learn from.

| Streamer | Platform | Why | Chat source |
|---|---|---|---|
| Kai Cenat | Twitch | #2 most-clipped (3,266 clips / 759 clipper accounts / 558M YT views in 30d, CreatorDB Aug 2026); GTA 6 marathon announced | Twitch IRC |
| IShowSpeed | **YouTube Live** | #1 most-clipped (4,835 clips / 1,087 clippers / 1.08B views) | YouTube live chat (`chat-downloader` / `pytchat`) |
| xQc / N3on / Adin Ross | Kick / Twitch | GTA history, huge chats | Kick websocket (Pusher) or Twitch IRC |

→ Detector input must be **platform-agnostic** (Twitch IRC, YouTube live chat, Kick) and capture must support `streamlink` for all three.

**Competition reality:** ~760–1,100 clipper accounts per top streamer. "The clip that lands first takes the wave" — speed of live detection is our edge, editing quality is our retention.

**Learn from existing clippers (study, don't copy files):** For each target, pull the top 20 Shorts of the top clip channels (e.g. "Kai Cenat Live", 16.3M subs) with `yt-dlp --dump-json` (metadata only) and log: clip length, time-to-hook (first 1–2s), caption style, title pattern, facecam layout, upload delay after the live moment. Turn that into a **style spec** our editor follows. Re-uploading their edited clips = instant reused-content/copyright strike — never do that.

## 3c. Tools evaluated (user-supplied, 2026-11-18)

| Tool | Role in pipeline | Verdict |
|---|---|---|
| **TwitchDownloader** (lay295, MIT) — CLI cross-platform, chat JSON + chat render | Post-stream mode: download VOD segment + chat JSON for offline detection & high-quality re-cut; render chat overlay for clips | **Use** (CLI). macOS: only Intel tested → verify on Apple Silicon first |
| **SupoClip** (AGPL-3.0, Docker, BYO LLM) — Twitch/Kick VOD input, face reframe 9:16, word captions, virality score, YT/TikTok scheduling | Edit stage candidate (alternative to OpenShorts) | **Trial** vs OpenShorts; AGPL fine for personal use |
| **Cutawan** (MIT desktop, OpenRouter supported, local Whisper) | Human gold-standard editor step | **Use** for manual review/editing day 1 |
| **OpenShot** (GPLv3 GUI editor) | Manual editing fallback | Low priority — no headless automation API documented; ffmpeg covers automation |
| **OpenRouter** | LLM for chat-context labeling, titles, keep/drop scoring | **Use**; key goes in local `.env` (`OPENROUTER_API_KEY`), never in chat/code |

## 4. Hard constraints / risks (read before monetizing)

1. **YouTube reused-content policy**: raw re-uploads of someone else's stream are not monetizable even *with* permission; a few violations can demonetize the whole channel. Need real editing value (captions, cuts, context titles, commentary/zooms).
2. **Copyright**: the stream belongs to the streamer (and game footage to Rockstar/Take-Two). Unauthorized clips can get claims/strikes. Safest money path = **official clipping campaigns** (Whop Content Rewards, streamers' own programs, Kick's program): paid per 1k approved views (~$0.20–$6, ~$1 typical; 10% fee / payout holds reported).
3. **Spam policy**: mass-uploading near-identical clips across many channels → channel termination risk. Keep a human approval gate and quality bar.
4. Twitch side: reading chat + watching stream is fine; don't bot-spam clip creation (global rate limit on Create Clip).

## 5. MVP for tomorrow (GTA 6 launch)

1. Tonight: one Python service — 3 channels × (streamlink buffer + IRC chat) + detector + ffmpeg cutter → `clips/<channel>/<time>_<score>.mp4` + `events.jsonl` (chat context).
2. Day 1: you review the clip list, hand-edit 3–5 best into Shorts (gold standard), post manually; join the GTA-related Whop campaigns for those streamers.
3. Day 2+: add auto-edit (OpenShorts) + LLM titles + YouTube upload-as-private.

## 6. Sources

- Chat-spike backdating technique: https://www.opus.pro/mcp/inspiration/clip-the-moments-your-live-chat-exploded
- Twitch Clips API: https://dev.twitch.tv/docs/api/clips/ · Clip-from-VOD beta: https://discuss.dev.twitch.com/t/introducing-clip-api-improvements-and-clip-from-vod-in-open-beta/64492
- OSS detectors: https://github.com/nirvagold/stream-clipper · https://github.com/hamzaskewl/clippy · https://github.com/supersonichub1/slasher · https://github.com/hougesen/twitch-highlight-finder
- OSS shorts editors: https://github.com/mutonby/openshorts · https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator
- n8n template: https://www.n8n.cloud/workflows/3521-twitch-auto-clip-generator-fetch-from-streamers-clip-and-edit-on-autopilot
- Commercial: Eklipse (live auto-detect), OpusClip, StreamLadder, Klypra — https://eklipse.gg/compare/ (vendor-written)
- Money: https://www.opus.pro/blog/whop-content-rewards · https://contentrewards.com/articles/get-paid-clip-streamers
- Policy: https://vidiq.com/blog/post/youtube-reused-content-policy-guide/
- User-supplied tools: https://github.com/lay295/TwitchDownloader · https://www.supoclip.com (https://github.com/FujiwaraChoki/supoclip) · https://cutawan.xyz · https://github.com/OpenShot/openshot-qt
- Big-streamer clip economy: https://creatordb.app/creator-news/the-rise-of-the-clip-economy/ · https://contentrewards.com/articles/how-to-run-a-gta-6-clipping-campaign · https://clipspeed.ai/gta6/streamers/kai-cenat-gta-6.html · https://clipspeed.ai/gta6/streamers/ishowspeed-gta-6.html (vendor)
- GTA 6 launch + streamers: https://sportskeeda.com/gta/news-streamers-like-kai-cenat-await-gta-6-release-all-need-know
