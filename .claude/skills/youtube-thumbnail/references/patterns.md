# Thumbnail patterns — evidence (studied 2026-11-18)

## MrBeast (12 most recent uploads, 74M–351M views)
Titles: "Eat Everything In A Grocery Store, Win $1,000,000" (351M), "Lose 150 Lbs Together, Win $250,000" (195M), "Escape 100 Cops, Win $500,000" (130M), "7 Days Stranded in The Arctic" (149M) …

Observed:
- 1–3 faces, very large (≈40–50% of frame), extreme emotion (gritted teeth, shock, grin).
- One concept per image: the cop wall, the mansion, the ice floe, before/after split.
- Text minimal or none: "100 COPS", "DAY 201", "DAY #6", "BEFORE / AFTER" — white bold, top corner.
- Saturated, clean, studio-lit; zero UI clutter.
- Third-party analyses (not MrBeast's own docs): bold contrast, expressive faces, multiple variants tested before publishing. Sources: podcastle.ai/blog/mrbeast-thumbnails, 1of10.com/blog/how-to-make-thumbnails-like-mrbeast, buffer.com/resources/youtube-thumbnails.

## Streamer / GTA clip videos (YouTube search "GTA streamer funny moments kai cenat xqc", 12 results)
High performers:
- "xQc Reacts to There Are 2 Types of People..." — 9.4M
- "Best of GAMER RAGE (FUNNY MOMENTS)" — 6.2M
- "Jynxzi's First Time Playing GTA RP..." — 3.7M: big cut-out reaction face bottom-left over a GTA street scene.
Low performers (5–125k): raw cluttered screenshots, visible chat boxes, multiple small facecams, no focal point.

Pattern that wins: **one big reaction face sticker + one clear game moment + 2–4 bold words (yellow/white, black outline)**, e.g. "THESE GUYS ARE TOO FUNNY…", "FUNNIEST MOMENTS". Gaming still tolerates the loud "shocked face" style more than other niches (2026 trend guides).

## Shorts thumbnails
- YouTube Partner Program channels can set custom Shorts thumbnails (rollout announced 2026-07-24 by Neal Mohan; tubefilter.com). No A/B testing for Shorts thumbnails.
- Feed autoplays → thumbnail matters mainly in search, channel page, suggestions.

## Policy (support.google.com/youtube/answer/9229980, 2802167, 2802002)
- Sexually suggestive thumbnails that don't break Community Guidelines → thumbnail removed or video age-restricted (no strike, but age-restriction = no ads/limited reach).
- Pornography, sexual acts, nudity in thumbnails → prohibited.
- Factors: pose/clothing intended to sexually arouse, focus on body parts.
- Misleading thumbnails (content not in the video) can violate the policy.
- Reported test: YouTube blurring mature-content thumbnails (unconfirmed in official docs).

## Tool mapping (`clipfarm thumb`)
| Pattern | Flag |
|---|---|
| big face sticker | `--facecam x,y,w,h --face-focus x,y` |
| text opposite the face | `--face-side left|right` |
| payoff word in yellow | `--text "HE GOT *CAUGHT*"` |
| point at the thing | `--circle x,y,r` |
| right moment | `--at seconds` (default: audio peak from score) |
| A/B options | `--variants 3` |
| Shorts | `--format 9:16` or `both` |
