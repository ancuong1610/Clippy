"""Content Rewards (Whop) campaigns: find the ones we can earn from, and line up clips to submit.

discover(): reads the PUBLIC GTA VI campaign list and each campaign's public page on contentrewards.com (the same
  pages a signed-out visitor sees; robots.txt allows them, /api/ is never called). Per campaign: pay model
  (CPM rate or pool), budget left, platforms, views a clip needs to count, views a creator needs to share a pool,
  and the campaign's top clips (title + views: what wins there).
match(): which campaigns fit us — one of our streamers, "any GTA streamer" (logo campaigns), or a new streamer we
  could add — and whether our typical views can reach the clip minimum.
report(): reports/campaigns-<date>.md: new campaigns worth joining, AI-drafted [[campaign]] config for them
  (NOT applied: joining a campaign and accepting its terms is the owner's call), the clips ready to submit
  (posted, routed to a campaign, links per platform, views vs the minimum) and the top clips per campaign.
Submitting stays a human step (or Content Rewards' own auto-track for connected TikTok/Instagram accounts);
`clipfarm rewards --mark <id>` records that a clip was submitted.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

BASE = "https://contentrewards.com"
LISTING = BASE + "/c/discover/gtavi"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36"
BERLIN = ZoneInfo("Europe/Berlin")
ANY_GTA = re.compile(r"any (gta|streamer|creator)|logo|gta clipper|all streamers", re.I)


# ---------------------------------------------------------------- page parsing
def payload(html: str) -> str:
    """Next.js embeds the page data as JSON strings in self.__next_f.push([1, "..."]) calls."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.S)
    return "".join(json.loads('"' + c + '"') for c in chunks)


def _balanced(data: str, i: int) -> str | None:
    """The JSON object/array starting at data[i] ('{' or '['), by bracket matching outside strings."""
    open_, close = data[i], "}" if data[i] == "{" else "]"
    depth, inq, esc = 0, False, False
    for j in range(i, len(data)):
        ch = data[j]
        if inq:
            esc = (not esc) and ch == "\\"
            if ch == '"' and not esc:
                inq = False
            continue
        if ch == '"':
            inq = True
        elif ch == open_:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                return data[i:j + 1]
    return None


def objects(data: str, first_key: str) -> list[dict]:
    out = []
    for m in re.finditer(r'\{"' + re.escape(first_key) + r'":', data):
        raw = _balanced(data, m.start())
        try:
            out.append(json.loads(raw)) if raw else None
        except json.JSONDecodeError:
            pass
    return out


def listing(html: str) -> list[dict]:
    seen: dict[str, dict] = {}
    for o in objects(payload(html), "availableBudgetRaw"):
        seen.setdefault(o["id"], o)
    return [{"id": o["id"], "title": (o.get("title") or "").replace("$$", "$"), "brand": o.get("brand"), "type": o.get("type"),
             "rate_per_1k": o.get("payoutSortRaw") if o.get("type") == "cpm" else None,
             "pool_usd": (o.get("poolCents") or 0) / 100 or None, "budget_left": o.get("availableBudgetRaw"),
             "budget_total": o.get("budgetTotalRaw"), "platforms": o.get("platforms") or [],
             "creators": o.get("creatorCountRaw"), "submissions": o.get("submissionCountRaw"),
             "requires_application": o.get("requiresApplication"), "description": o.get("description") or "",
             "launched_ms": o.get("launchedAtMs"), "url": f"{BASE}/c/campaigns/{o['id']}"} for o in seen.values()]


def detail(html: str) -> dict:
    data = payload(html)
    num = lambda k: int(m.group(1)) if (m := re.search(rf'"{k}":\s*(\d+)', data)) else None  # noqa: E731

    def array(key: str) -> list:
        m = re.search(rf'"{key}":\s*\[', data)
        try:
            return json.loads(_balanced(data, m.end() - 1) or "[]") if m else []
        except json.JSONDecodeError:
            return []

    pay = array("payouts")
    rates = {p["platform"]: (p.get("rateCents") or 0) / 100 for p in pay if p.get("rateCents")}
    # CPM: a clip earns nothing until it reaches minPayout, i.e. minPayout / rate * 1000 views
    paid_from = [round(p["minPayoutCents"] / p["rateCents"] * 1000) for p in pay
                 if p.get("rateCents") and p.get("minPayoutCents")]
    tops, seen = [], set()
    for t in array("topClips"):
        if t.get("postUrl") not in seen:
            seen.add(t.get("postUrl"))
            tops.append({"title": (t.get("title") or "")[:140], "views": t.get("totalViews"),
                         "platform": t.get("platform"), "url": t.get("postUrl")})
    rules = re.search(r"https://docs\.google\.com/[^\s\"\\]+", data)
    return {"min_clip_views": num("minClipViews") or (min(paid_from) if paid_from else None),
            "min_creator_views": num("minCreatorViews"),
            "rates": rates, "top_clips": sorted(tops, key=lambda t: -(t["views"] or 0))[:5],
            "rules_url": rules.group(0) if rules else None}


def fetch(url: str) -> str:
    r = httpx.get(url, headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.text


def discover(pause_s: float = 1.5) -> list[dict]:
    camps = listing(fetch(LISTING))
    for c in camps:
        time.sleep(pause_s)  # a few dozen page views a day, spaced out
        try:
            c.update(detail(fetch(c["url"])))
        except httpx.HTTPError as e:
            c["error"] = str(e)
    return camps


# ---------------------------------------------------------------- fit
def match(c: dict, cfg: dict, median_views: float | None = None) -> dict:
    """How a campaign fits us: our streamer / any GTA content / a streamer we don't watch yet."""
    text = f"{c.get('title')} {c.get('description')}".lower().replace(" ", "")
    ours = [ch["name"] for ch in cfg.get("channel", []) if ch["name"].lower().rstrip("_") in text]
    configured = {x.get("url", "").rstrip("/").split("/")[-1]: x for x in cfg.get("campaign", [])}
    fit = "our streamer" if ours else "any GTA content" if ANY_GTA.search(f"{c.get('title')} {c.get('description')}") \
        else "other streamer"
    need = c.get("min_clip_views") or 1000
    pool_need = c.get("min_creator_views")
    reach = None if median_views is None else ("ok" if median_views >= need else f"our median {median_views:.0f} < {need}")
    if c.get("type") == "pool" and pool_need and pool_need > 100_000:
        reach = f"pool needs {pool_need:,} views per creator"
    platforms = sorted(set(c.get("platforms") or []) & {"instagram", "tiktok", "youtube"})
    return {"fit": fit, "streamers": ours, "configured": c["id"] in configured,
            "enabled": configured.get(c["id"], {}).get("enabled", True) if c["id"] in configured else None,
            "reach": reach, "our_platforms": platforms}


# ---------------------------------------------------------------- what to submit
def ready_to_submit(root: Path, cfg: dict) -> list[dict]:
    """Posted clips routed to a campaign, with their post links and latest views (from insights)."""
    from . import insights as I
    from . import post as P
    latest = {r["id"]: r for r in I.latest(root)}
    camps = {x["name"]: x for x in cfg.get("campaign", [])}
    done = set()
    f = root / "rewards_submitted.jsonl"
    for line in f.read_text().splitlines() if f.exists() else []:
        done.add(json.loads(line)["id"])
    out = []
    for d, it in P.items(root, "posted"):
        if not it.campaign:
            continue
        res = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else {}
        r = latest.get(it.id, {})
        links = {"youtube": (res.get("platforms", {}).get("youtube") or {}).get("url"),
                 "instagram": r.get("ig_url"), "tiktok": r.get("tt_url")}
        out.append({"id": it.id, "title": it.title, "campaign": it.campaign,
                    "campaign_url": camps.get(it.campaign, {}).get("url"), "submitted": it.id in done,
                    "links": {k: v for k, v in links.items() if v},
                    "views": {k: r.get(f"{p}_views") for k, p in (("youtube", "yt"), ("instagram", "ig"),
                                                                  ("tiktok", "tt"))}})
    return out


def mark_submitted(root: Path, item_id: str, note: str = "") -> None:
    with (root / "rewards_submitted.jsonl").open("a") as f:
        f.write(json.dumps({"id": item_id, "ts": time.time(), "note": note}) + "\n")


# ---------------------------------------------------------------- AI-drafted config for a new campaign
DRAFT = """You configure a clipping bot for a Content Rewards campaign. From the campaign below, write ONE TOML
[[campaign]] block for channels.toml with keys: name (snake_case), url, streamers (list of lowercase Twitch names the
campaign accepts, or "*" for any GTA 6 streamer), hashtags (list the captions MUST contain), and a comment line per
other rule (logo, sound, mention, length, banned content, platforms). Only rules stated below; no guessing.
Return JSON: {{"toml": "...", "rules": ["short rule", ...]}}

Title: {title}
Description: {description}
Platforms: {platforms}
URL: {url}"""


def draft_config(c: dict) -> dict:
    from .titles import _call
    return _call(DRAFT.format(**{k: c.get(k) for k in ("title", "description", "platforms", "url")}))


# ---------------------------------------------------------------- report
def _median_views(root: Path) -> float | None:
    from . import insights as I
    v = sorted(I.views(r) for r in I.latest(root))
    return v[len(v) // 2] if v else None


def report(camps: list[dict], cfg: dict, root: Path, ask_ai: bool = True) -> str:
    med = _median_views(root)
    rows = [(c, match(c, cfg, med)) for c in camps if not c.get("error")]
    pay = lambda c: (f"${c['rate_per_1k']:.2f}/1K" if c.get("rate_per_1k")  # noqa: E731
                     else f"${c['pool_usd']:,.0f} pool" if c.get("pool_usd") else "?")
    md = [f"# Content Rewards · GTA VI campaigns {datetime.now(BERLIN):%Y-%m-%d %H:%M}",
          f"{len(rows)} live campaigns · our median views per clip: {med if med is not None else '?'}", ""]
    order = {"our streamer": 0, "any GTA content": 1, "other streamer": 2}
    md += ["| fit | campaign | pays | budget left | clip needs | in config | platforms | reach |", "|---|---|---|---|---|---|---|---|"]
    for c, m in sorted(rows, key=lambda x: (order[x[1]["fit"]], -(x[0].get("budget_left") or 0))):
        cfgd = "✅" if m["configured"] and m["enabled"] is not False else "⏸" if m["configured"] else "—"
        md.append(f"| {m['fit']}{' (' + ', '.join(m['streamers']) + ')' if m['streamers'] else ''} | "
                  f"[{c['title']}]({c['url']}) | {pay(c)} | ${c.get('budget_left') or 0:,.0f} | "
                  f"{c.get('min_clip_views') or '?'} views | {cfgd} | {', '.join(m['our_platforms']) or '–'} | "
                  f"{m['reach'] or '–'} |")
    new = [(c, m) for c, m in rows if not m["configured"] and m["fit"] != "other streamer"
           and not c.get("requires_application")]
    if new and ask_ai:
        md += ["", "## Proposed config for new campaigns (NOT applied — join on Content Rewards first)"]
        for c, _ in new[:6]:
            try:
                d = draft_config(c)
                md += [f"### {c['title']}", "Rules: " + "; ".join(d.get("rules") or []), "```toml", d.get("toml", ""), "```"]
            except Exception as e:  # noqa: BLE001 — a draft is a bonus
                md.append(f"### {c['title']}: draft failed ({e})")
    others = [c for c, m in rows if m["fit"] == "other streamer" and (c.get("rate_per_1k") or 0) >= 1]
    if others:
        md += ["", "## Streamers we don't watch yet with $1+/1K campaigns (add to [[channel]] to clip them)"]
        md += [f"- [{c['title']}]({c['url']}): {pay(c)}, ${c.get('budget_left') or 0:,.0f} left" for c in others]
    subs = ready_to_submit(root, cfg)
    if subs:
        md += ["", "## Our clips for a campaign"]
        for s in subs:
            v = ", ".join(f"{k} {n}" for k, n in s["views"].items() if n is not None) or "no data yet"
            md.append(f"- {'✅ submitted' if s['submitted'] else '⬜ to submit'} · **{s['title']}** → {s['campaign']} · "
                      f"views: {v} · " + " · ".join(f"[{k}]({u})" for k, u in s["links"].items()))
    md += ["", "## What wins: top clips per campaign we can do"]
    for c, m in rows:
        if m["fit"] != "other streamer" and c.get("top_clips"):
            md.append(f"**{c['title']}**")
            md += [f"- {t['views']:,} views · {t['platform']} · {t['title'][:90]}" for t in c["top_clips"][:3]]
    return "\n".join(md) + "\n"


def run(cfg: dict, ask_ai: bool = True) -> tuple[Path, list[dict]]:
    root = Path(cfg.get("post", {}).get("queue_dir", "queue"))
    camps = discover()
    with (root / "campaigns.jsonl").open("a") as f:
        f.write(json.dumps({"ts": time.time(), "campaigns": camps}, ensure_ascii=False) + "\n")
    out = Path("reports") / f"campaigns-{datetime.now(BERLIN):%Y-%m-%d}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report(camps, cfg, root, ask_ai))
    return out, camps
