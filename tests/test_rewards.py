import json

from clipfarm import rewards as R


def _page(*objs) -> str:
    """A Next.js page: the data rides in self.__next_f.push([1, "<json-escaped string>"])."""
    blob = "x:" + json.dumps(list(objs))
    return f'<script>self.__next_f.push([1,{json.dumps(blob)}])</script>'


CPM = {"availableBudgetRaw": 900, "id": "c1", "title": "Lucky Latvian X GTA6", "type": "cpm", "payoutSortRaw": 2,
       "platforms": ["tiktok", "instagram", "x"], "description": "Clip Lucky Latvian playing GTA 6",
       "requiresApplication": False}
POOL = {"availableBudgetRaw": 10000, "id": "c2", "title": "Kai Cenat - $10,000 GTA 6 Pool", "type": "pool",
        "poolCents": 1000000, "platforms": ["tiktok", "youtube"], "description": "Clip Kai Cenat's GTA 6 streams"}
LOGO = {"availableBudgetRaw": 15000, "id": "c3", "title": "GTA VI Clipup Logo Campaign", "type": "cpm",
        "payoutSortRaw": 0.3, "platforms": ["youtube"], "description": "Clip any streamer posting for GTA VI and add our logo"}


def test_listing_dedupes_and_reads_pay_model():
    rows = {r["id"]: r for r in R.listing(_page(CPM, POOL, CPM))}
    assert len(rows) == 2
    assert rows["c1"]["rate_per_1k"] == 2 and rows["c1"]["pool_usd"] is None
    assert rows["c2"]["pool_usd"] == 10000 and rows["c2"]["url"].endswith("/c/campaigns/c2")


def test_detail_reads_minimums_rates_and_top_clips():
    html = _page({"budget": 1, "minClipViews": 1000, "minCreatorViews": 750000,
                  "payouts": [{"platform": "tiktok", "rateCents": 150}, {"platform": "youtube", "rateCents": None}],
                  "topClips": [{"postUrl": "a", "title": "small", "totalViews": 10, "platform": "tiktok"},
                               {"postUrl": "b", "title": "big", "totalViews": 4000000, "platform": "instagram"}]})
    d = R.detail(html)
    assert d["min_clip_views"] == 1000 and d["min_creator_views"] == 750000
    assert d["rates"] == {"tiktok": 1.5}
    assert [t["title"] for t in d["top_clips"]] == ["big", "small"]


def test_cpm_clip_minimum_comes_from_min_payout():
    d = R.detail(_page({"payouts": [{"platform": "tiktok", "rateCents": 100, "minPayoutCents": 200},
                                    {"platform": "instagram", "rateCents": 200, "minPayoutCents": 200}]}))
    assert d["min_clip_views"] == 1000  # instagram: $2 at $2/1K


def test_match_finds_our_streamer_any_gta_and_unreachable_pools():
    cfg = {"channel": [{"name": "kaicenat"}, {"name": "xqc"}],
           "campaign": [{"name": "kai", "url": "https://contentrewards.com/c/campaigns/c2"}]}
    kai = R.match({**R.listing(_page(POOL))[0], "min_creator_views": 750000}, cfg, median_views=3)
    assert kai["fit"] == "our streamer" and kai["streamers"] == ["kaicenat"] and kai["configured"]
    assert "750,000" in kai["reach"]
    assert R.match(R.listing(_page(LOGO))[0], cfg)["fit"] == "any GTA content"
    other = R.match({**R.listing(_page(CPM))[0], "min_clip_views": 1000}, cfg, median_views=3)
    assert other["fit"] == "other streamer" and other["reach"].startswith("our median 3")
