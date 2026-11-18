# Account setup for posting (do this yourself — passwords/2FA never go to Claude or into the repo)

Order matters: create the 3 platform accounts first, then connect them to Upload-Post, then Whop.

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

## 4. Upload-Post (does the actual posting)
1. Sign up at https://www.upload-post.com (brand email).
2. Dashboard → **Profiles → Create profile** named e.g. `clipfarm` → connect YouTube, TikTok, Instagram (each opens that platform's own login/permission screen — approve it there).
3. Plan: Free = 10 uploads/month, **no TikTok**; Basic ≈ $24/mo adds TikTok + unlimited. Decide before launch day.
4. Dashboard → **API Keys → create** → copy it.
5. On the Mac (not in chat):
   ```bash
   cd /Users/cuong/Documents/Clipping && open -e .env
   ```
   add the line `UPLOAD_POST_API_KEY=<paste>` and save. Then set `user = "clipfarm"` in `channels.toml` under `[post]`.
6. Test without posting: `uv run clipfarm post --dry-run`

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
- [ ] Upload-Post profile with all 3 connected, plan chosen
- [ ] API key in `.env`, `[post] user` set, dry run OK
- [ ] Whop signed up, campaigns joined, payout set up
