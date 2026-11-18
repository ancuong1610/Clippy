---
name: youtube-thumbnail
description: Create click-worthy YouTube thumbnails (16:9 for long-form/compilations, 9:16 for Shorts) from clipfarm stream clips using `uv run clipfarm thumb`, following MrBeast and top streamer-clip-channel patterns for GTA 6 content. Use this whenever the user wants a thumbnail, cover image, "thumb", title+thumbnail package, A/B thumbnail variants, or is about to upload a clip/compilation to YouTube — even if they only say "make it clickable" or "prep this for upload".
---

# YouTube thumbnails for clipfarm clips

The tool does the pixels; your job is the **decisions**: which moment, which face, which 1–4 words. Those decide CTR far more than any styling.

## Workflow

1. **Know the clip.** Read the clip's `.json` (next to the `.mp4`): `score.peak_at_s` (loudest moment), `event.top_tokens`, the `chat` lines, and `label.what_happened` / `label.title` if present. Chat tells you what viewers reacted to — that's the hook.
2. **Pick the moment.** Default is the audio peak. If the peak is a scream but the face is mid-blink, pass `--at <seconds>` a bit earlier/later. Look at a frame first if unsure:
   `ffmpeg -v error -ss <t> -i clip.mp4 -frames:v 1 /tmp/f.jpg` then view it.
3. **Find the facecam.** Grab one frame, locate the streamer's camera, express it as `x,y,w,h` fractions of the frame (e.g. xQc bottom-left `0,0.69,0.22,0.31`). Streamers move cameras between streams — check each time. Set `--face-focus x,y` (where the face sits inside that box) so the sticker is face, not room. No facecam (gameplay-only)? Omit `--facecam`.
4. **Write the text** (see rules below). Mark the one payoff word with `*asterisks*` → yellow.
5. **Render 3 variants:**
   ```bash
   uv run clipfarm thumb clips/kaicenat/<clip>.mp4 --text "HE GOT *CAUGHT*" \
     --facecam 0.74,0.02,0.25,0.33 --face-focus 0.5,0.35 --format both
   ```
   Outputs `<clip>_thumb_16x9_v1..3.jpg` (+ `9x16`). Variants = different sharp frames near the moment.
6. **Look at every output** (Read the JPGs) and run the checklist. Re-render with a different `--at`, `--face-focus`, `--face-side`, or text if any check fails. Hand the user the best 1–3 for YouTube's "Test & compare" (long-form only; Shorts can't A/B test).

## Text rules (why: thumbnails are seen at ~200 px wide on phones)

- **1–4 words, never a sentence.** It must read in under a second. MrBeast uses 0–3 ("100 COPS", "DAY 201").
- **Don't repeat the title.** Title says what happens; thumbnail text adds emotion or stakes. Title "Kai Cenat Gets Arrested In GTA 6" → text "*WORST* DAY" or "HE'S DONE".
- **Curiosity, not spoilers.** Show the setup or the reaction, not the punchline.
- **Must be true.** A thumbnail promising something the clip doesn't contain violates YouTube's thumbnail policy and kills retention anyway.
- Good GTA patterns: stakes ("5 STARS"), outcome tease ("NO WAY OUT"), reaction label ("HE SNAPPED"), number hooks ("$10M HEIST").

## Visual checklist

- [ ] **One face, big, with emotion** — face reads clearly at phone size; shocked/laughing/raging beats neutral. Neutral face → pick another `--at`.
- [ ] **One focal action** — the GTA moment (car, cop, explosion) is visible behind the text; no chat box or alert overlays.
- [ ] **Text doesn't cover the face or the action.** Swap `--face-side` if it does.
- [ ] **Contrast** — text readable on its background; bright, saturated image (the tool boosts it).
- [ ] Red circle (`--circle x,y,r`) only when there's a specific thing to look at; never on empty space.
- [ ] Squint test: shrink mentally to a phone feed — can you tell what's happening and who it is?

## Edgy vs. sexual — the line

GTA content is edgy by nature (crime, cops, chaos, suggestive NPCs) and that's fine. But YouTube's thumbnail policy: **sexually suggestive thumbnails get removed or the video age-restricted** (age-restricted = no ads, little reach — fatal for a monetized clip channel), and nudity/sexual acts in thumbnails are banned outright. So:

- OK: danger, violence-implied action, cops, explosions, shocked faces, provocative *words* that are true ("SHE BETRAYED HIM").
- Not OK: framing that focuses on bodies/poses meant to arouse, crop-ins on NPC bodies, nudity (even in-game), implying sexual content the clip doesn't have.
If the user asks for sexual thumbnails, explain this trade-off briefly and offer the strongest *edgy-but-compliant* version instead.

## Shorts vs long-form

- **Shorts (9:16):** the feed autoplays the video, so the thumbnail only shows in search, the channel page and suggestions — still worth setting, lower stakes. The first frame of the Short matters more.
- **Long-form compilations (16:9)** ("Kai Cenat GTA 6 Funniest Moments"): thumbnail is the #1 CTR lever. Use the strongest face + stakes text; consider 2 faces (two streamers) for crossover compilations by making two stickers manually or picking a frame where both are visible.

## More detail

`references/patterns.md` — what the reference thumbnails (MrBeast uploads, top GTA/streamer clip videos) actually do, with view counts, plus policy sources. Read it when choosing a style or explaining choices to the user.
