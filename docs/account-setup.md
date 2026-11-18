# Account setup for posting (do this yourself — passwords/2FA never go to Claude or into the repo)

Order: create the 3 platform accounts, connect YouTube to Composio, then Whop.

## 0. Before you start
- One **dedicated email** for the clip brand (e.g. a new Gmail). Keeps it separate from your personal accounts.
- Pick a **brand name** usable on all three, e.g. `GTA6 Moments`, `Stream Chaos Clips`. Check the handle is free on TikTok, YouTube and Instagram.
- A phone for verification codes. Turn on 2FA everywhere.

## 1. YouTube channel
1. Sign in to YouTube with the brand Google account → profile icon → **Create a channel** (use a custom name = brand).
2. YouTube Studio → **Settings → Channel → Basic info**: country = Germany; keywords: GTA 6, streamer clips.
3. Verify the channel (phone) → unlocks custom thumbnails and videos > 15 min.
4. Later, for money: YouTube Studio → **Earn** (YPP opens at 1k subs + 10M Shorts views / 90 days).

## 2. TikTok
1. Sign up with the brand email in the app. Keep it a **Personal** account — Creator Rewards does not work with Business accounts.
2. Profile: same name + picture as YouTube; bio "GTA 6 stream highlights 🎮 credit to the streamers".
3. Settings → Privacy: account **public**. 2FA on.

## 3. Instagram
1. Create the account with the brand email (same handle).
2. Settings → **Account type and tools → Switch to professional account → Creator** (category: Gaming video creator).
3. Create a **Facebook Page** for the brand and link it: Instagram → Settings → Accounts Center / Linked accounts → Facebook Page. (Required for API posting.)

## 4. Composio (posts to YouTube, free tier)
1. Sign up at https://composio.dev → **Settings → API keys** → create a project key (starts with `ak_`).
2. Put it in `.env` yourself (copy `.env.example`): `COMPOSIO_API_KEY=ak_...`. Never paste keys in chat or code.
3. Connect the channel: `uv run python scripts/composio_youtube.py connect` → open the printed link → approve
   with the brand Google account.
4. Test without posting: `uv run clipfarm post --dry-run`.

TikTok and Instagram are not posted automatically (their APIs only allow private posts for unaudited apps);
`clipfarm post` writes `manual_tiktok.txt` / `manual_instagram.txt` captions next to each video to paste in the app.

## 5. Whop (campaign money)
1. Sign up at https://whop.com (brand email) → **Content Rewards** / campaigns.
2. Join GTA 6 / streamer campaigns. Read each campaign's rules: allowed platforms, required tags/sounds, whether reposting the same clip elsewhere is allowed, payout per 1k views and caps.
3. Payouts need Stripe-supported identity/bank — set up early, approvals can take days.

## 6. First real post (with Claude)
1. `uv run clipfarm queue add <short.mp4> --title "..." --thumb <thumb.jpg>`
2. `uv run clipfarm queue approve <id>`
3. `uv run clipfarm post` → check the post on each platform, then `uv run clipfarm queue list`.

Checklist
- [ ] Brand email + name chosen
- [ ] YouTube channel created + verified
- [ ] TikTok (personal, public, 2FA)
- [ ] Instagram Creator + Facebook Page linked
- [ ] Composio key in `.env`, YouTube connected, dry run OK
- [ ] Whop signed up, campaigns joined, payout set up
