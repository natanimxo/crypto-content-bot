"""Candidate-source probe (2026-11-15). For each candidate income source: does the URL answer, what does it return (JSON / RSS / HTML),
how many items, how fresh, and does it want auth -- from THIS machine.

Run locally (`python -m pipeline.source_probe`) AND from Railway: the earning cycle (scripts/collect_earning.py) runs `probe_all()` once a day
and stores the result in run_logs (job `source_probe`), because a source that works from the dev machine is not verified until it has
run there -- OKX served a different feed from Railway, Bybit refused it outright. A probe answers "reachable and shaped like an
opportunity feed"; whether it pays money, is global and is trustworthy is a separate, human judgement recorded in BACKLOG.md.

Read-only GETs (plus a couple of public GraphQL/JSON POST queries). No credentials are sent anywhere.
"""

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone

import feedparser
import requests

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
TIMEOUT = 25

B = "https://raw.githubusercontent.com"
PROBES = [
    # ---- bounty / open-source / task boards
    {"id": "superteam_earn", "dir": "bounty boards", "url": "https://earn.superteam.fun/api/listings?take=20&type=bounty"},
    {"id": "superteam_earn_projects", "dir": "bounty boards", "url": "https://earn.superteam.fun/api/listings?take=20&type=project"},
    {"id": "algora_bounties", "dir": "bounty boards", "url": "https://console.algora.io/api/trpc/bounty.list?input=%7B%22json%22%3A%7B%22limit%22%3A20%7D%7D"},
    {"id": "gitcoin_bounties_legacy", "dir": "bounty boards", "url": "https://gitcoin.co/api/v0.1/bounties/?is_open=true&limit=20"},
    {"id": "issuehunt", "dir": "bounty boards", "url": "https://issuehunt.io/"},
    # ---- bug bounty / security contests
    {"id": "bounty_targets_hackerone", "dir": "bug bounty", "url": f"{B}/arkadiyt/bounty-targets-data/main/data/hackerone_data.json"},
    {"id": "bounty_targets_bugcrowd", "dir": "bug bounty", "url": f"{B}/arkadiyt/bounty-targets-data/main/data/bugcrowd_data.json"},
    {"id": "bounty_targets_yeswehack", "dir": "bug bounty", "url": f"{B}/arkadiyt/bounty-targets-data/main/data/yeswehack_data.json"},
    {"id": "bounty_targets_intigriti", "dir": "bug bounty", "url": f"{B}/arkadiyt/bounty-targets-data/main/data/intigriti_data.json"},
    {"id": "bugcrowd_engagements", "dir": "bug bounty", "url": "https://bugcrowd.com/engagements.json?category=bug_bounty&sort_by=promoted&sort_direction=desc&page=1"},
    {"id": "yeswehack_programs", "dir": "bug bounty", "url": "https://api.yeswehack.com/programs?page=1&resultsPerPage=20"},
    {"id": "intigriti_public", "dir": "bug bounty", "url": "https://app.intigriti.com/api/core/public/programs"},
    {"id": "immunefi_explore", "dir": "bug bounty", "url": "https://immunefi.com/bug-bounty/"},
    # ---- Phase 6 startup_news candidates (2026-11-16): verified from Railway before build; crunchbase / eu-startups 403 locally, checked here for the record
    # ---- Earn Stack web candidates (operator 2026-11-17)
    {"id": "airdropalert_rss", "dir": "airdrops", "url": "https://airdropalert.com/rss-feed/"},
    {"id": "taskon_site", "dir": "quests", "url": "https://taskon.xyz/"},
    {"id": "questn_site", "dir": "quests", "url": "https://app.questn.com/"},
    {"id": "cryptorank_drophunting", "dir": "airdrops", "url": "https://cryptorank.io/drophunting"},
    {"id": "partnerstack_site", "dir": "affiliate networks", "url": "https://partnerstack.com/"},
    {"id": "impact_site", "dir": "affiliate networks", "url": "https://impact.com/"},
    {"id": "firstpromoter_site", "dir": "affiliate networks", "url": "https://firstpromoter.com/"},
    {"id": "startup_techeu_rss", "dir": "startup news", "url": "https://tech.eu/feed"},
    {"id": "startup_sifted_rss", "dir": "startup news", "url": "https://sifted.eu/feed"},
    {"id": "startup_techcrunch_venture_rss", "dir": "startup news", "url": "https://techcrunch.com/category/venture/feed/"},
    {"id": "startup_crunchbase_news_rss", "dir": "startup news", "url": "https://news.crunchbase.com/feed/"},
    {"id": "startup_eu_startups_rss", "dir": "startup news", "url": "https://www.eu-startups.com/feed/"},
    {"id": "immunefi_public_json", "dir": "bug bounty", "url": "https://immunefi.com/public-api/bounties.json"},   # keyless JSON: 218 programs, launchDate / maxBounty / rewardsToken / endDate (audit competitions)
    {"id": "hackenproof", "dir": "bug bounty", "url": "https://hackenproof.com/programs"},
    {"id": "code4rena_contests", "dir": "audit contests", "url": f"{B}/code-423n4/code4rena-org/main/_data/contests/contests.json"},
    {"id": "code4rena_site", "dir": "audit contests", "url": "https://code4rena.com/audits"},
    {"id": "sherlock_contests", "dir": "audit contests", "url": "https://mainnet-contest.sherlock.xyz/contests"},
    {"id": "cantina", "dir": "audit contests", "url": "https://cantina.xyz/opportunities"},
    {"id": "codehawks", "dir": "audit contests", "url": "https://codehawks.cyfrin.io/"},
    # ---- hackathon aggregators
    {"id": "devpost_api", "dir": "hackathons", "url": "https://devpost.com/api/hackathons?status[]=open&per_page=20"},
    {"id": "dorahacks", "dir": "hackathons", "url": "https://dorahacks.io/api/hackathon/?page=1&page_size=20&status=ongoing"},
    {"id": "devfolio", "dir": "hackathons", "url": "https://api.devfolio.co/api/hackathons?filter=application_open&page=1&limit=20"},
    {"id": "ethglobal_events", "dir": "hackathons", "url": "https://ethglobal.com/events"},
    {"id": "mlh_events", "dir": "hackathons", "url": "https://mlh.io/seasons/2026/events"},
    {"id": "hackerearth_events", "dir": "hackathons", "url": "https://www.hackerearth.com/chrome-extension/events/"},
    # ---- competition platforms with cash prizes
    {"id": "topcoder_challenges", "dir": "competitions", "url": "https://api.topcoder.com/v5/challenges?status=Active&perPage=20"},
    {"id": "kaggle_api", "dir": "competitions", "url": "https://www.kaggle.com/api/v1/competitions/list"},
    {"id": "kaggle_page", "dir": "competitions", "url": "https://www.kaggle.com/competitions"},
    {"id": "zindi_api", "dir": "competitions", "url": "https://api.zindi.africa/v1/competitions"},
    {"id": "zindi_page", "dir": "competitions", "url": "https://zindi.africa/competitions"},
    {"id": "drivendata", "dir": "competitions", "url": "https://www.drivendata.org/competitions/"},
    {"id": "aicrowd", "dir": "competitions", "url": "https://www.aicrowd.com/challenges"},
    {"id": "numerai_graphql", "dir": "competitions", "url": "https://api-tournament.numer.ai/", "method": "POST", "json": {"query": "{ rounds(tournament: 8, number: 0) { number openTime } }"}},
    # ---- exchange programs (Learn & Earn, launchpool, campaigns)
    {"id": "binance_cms_activities", "dir": "exchange programs", "url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId=93&pageNo=1&pageSize=20"},
    {"id": "binance_cms_news", "dir": "exchange programs", "url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId=49&pageNo=1&pageSize=20"},
    {"id": "okx_ann_types", "dir": "exchange programs", "url": "https://www.okx.com/api/v5/support/announcement-types"},
    {"id": "okx_ann_latest", "dir": "exchange programs", "url": "https://my.okx.com/api/v5/support/announcements?annType=announcements-latest-announcements"},
    {"id": "kucoin_activities", "dir": "exchange programs", "url": "https://api.kucoin.com/api/v3/announcements?currentPage=1&pageSize=20&annType=activities&lang=en_US"},
    {"id": "bitget_announcements", "dir": "exchange programs", "url": "https://api.bitget.com/api/v2/public/annoucements?language=en_US&annType=latest_news"},
    {"id": "coinbase_learn", "dir": "exchange programs", "url": "https://www.coinbase.com/learning-rewards"},
    {"id": "bybit_announcements", "dir": "exchange programs", "url": "https://api.bybit.com/v5/announcements/index?locale=en-US&type=latest_activities&limit=20"},
    # ---- airdrops / testnet incentives
    {"id": "airdrops_io_feed", "dir": "airdrops", "url": "https://airdrops.io/feed/"},
    {"id": "defillama_airdrops", "dir": "airdrops", "url": "https://api.llama.fi/airdrops"},
    {"id": "galxe_graphql", "dir": "airdrops", "url": "https://graphigo.prd.galaxy.eco/query", "method": "POST",
     "json": {"query": "query { campaigns(input: {first: 5, listType: Trending, statuses: [Active]}) { list { id name description endTime } } }"}},
    {"id": "layer3_quests", "dir": "airdrops", "url": "https://app.layer3.xyz/"},
    # ---- paid research / user testing / data work
    {"id": "prolific", "dir": "paid research", "url": "https://www.prolific.com/"},
    {"id": "prolific_api", "dir": "paid research", "url": "https://api.prolific.com/api/v1/studies/"},
    {"id": "usertesting", "dir": "paid research", "url": "https://www.usertesting.com/be-a-user-tester"},
    {"id": "respondent", "dir": "paid research", "url": "https://www.respondent.io/"},
    {"id": "outlier_ai", "dir": "data / AI work", "url": "https://outlier.ai/"},
    {"id": "dataannotation", "dir": "data / AI work", "url": "https://www.dataannotation.tech/"},
]

_DATE_KEYS = re.compile(r"(date|time|created|updated|published|start|end|deadline|posted)", re.IGNORECASE)


def _parse_dt(v):
    try:
        if isinstance(v, (int, float)) and v > 1e9:
            return datetime.fromtimestamp(v / 1000 if v > 1e12 else v, tz=timezone.utc)
        if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}", v):
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def _find_items(obj, depth=0):
    """The largest list of dicts within a JSON document (depth <= 4): the 'feed'."""
    best = []
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            return obj
        for x in obj[:5]:
            r = _find_items(x, depth + 1) if depth < 4 else []
            if len(r) > len(best):
                best = r
    elif isinstance(obj, dict) and depth < 4:
        for v in obj.values():
            r = _find_items(v, depth + 1)
            if len(r) > len(best):
                best = r
    return best


def _title_of(d: dict) -> str:
    for k in ("title", "name", "annTitle", "slug", "program_name", "handle", "id"):
        if isinstance(d.get(k), (str, int)) and str(d[k]).strip():
            return str(d[k])[:70]
    return ""


def run_probe(p: dict) -> dict:
    out = {"id": p["id"], "dir": p["dir"], "url": p["url"][:110]}
    t0 = time.time()
    try:
        headers = {"User-Agent": UA, "Accept": "application/json, application/rss+xml, text/html;q=0.8, */*;q=0.5"}
        if p.get("method") == "POST":
            r = requests.post(p["url"], json=p.get("json"), headers=headers, timeout=TIMEOUT)
        else:
            r = requests.get(p["url"], headers=headers, timeout=TIMEOUT)
    except Exception as exc:
        out.update(status=type(exc).__name__, error=str(exc)[:90], secs=round(time.time() - t0, 1))
        return out
    out.update(status=r.status_code, bytes=len(r.content), secs=round(time.time() - t0, 1), ctype=(r.headers.get("content-type") or "")[:30])
    if r.status_code in (401, 403):
        out["auth_or_blocked"] = True
    if r.status_code != 200:
        out["snippet"] = r.text[:80].replace("\n", " ")
        return out
    body = r.text
    kind = "html"
    try:
        data = r.json()
        kind = "json"
    except ValueError:
        data = None
    if kind == "html" and re.search(r"<(rss|feed|rdf)\b", body[:2000], re.IGNORECASE):
        parsed = feedparser.parse(r.content)
        kind = "rss"
        entries = parsed.entries
        out["items"] = len(entries)
        ts = [datetime(*e.published_parsed[:6], tzinfo=timezone.utc) for e in entries if getattr(e, "published_parsed", None)]
        out["sample"] = [e.get("title", "")[:70] for e in entries[:3]]
        if ts:
            out["newest"] = max(ts).date().isoformat()
    elif kind == "json":
        items = _find_items(data)
        out["items"] = len(items)
        out["sample"] = [_title_of(i) for i in items[:3]]
        stamps = []
        for it in items[:60]:
            for k, v in it.items():
                if _DATE_KEYS.search(k):
                    d = _parse_dt(v)
                    if d:
                        stamps.append(d)
        if stamps:
            out["newest"] = max(stamps).date().isoformat()
    else:
        out["html_title"] = (re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I).group(1).strip()[:70] if re.search(r"<title", body, re.I) else "")
        out["login_wall"] = bool(re.search(r"log ?in|sign ?in|create an account|sign up", body[:6000], re.IGNORECASE))
    out["kind"] = kind
    return out


# ---- Telegram channels the operator asked for (2026-11-16). Verified FROM RAILWAY before anything is built on them: does t.me/s/<handle> serve posts,
# how fresh, how many a day, how engaged (median views), how much of it is an ad / price-bot / promotion. Handles were found by trying the plausible names
# locally; several candidates are dead, hijacked or preview-less and are listed so the answer is on record.
TELEGRAM_CANDIDATES = {
    "whale_alert_io": "Whale Alert (official)", "wublockchainenglish": "Wu Blockchain (English)", "cointelegraph": "Cointelegraph",
    "cryptopolitanofficial": "Cryptopolitan (official)", "CoinMarketCapNews": "CoinMarketCap News (candidate)", "toncoin": "TON (candidate)",
    "tonblockchain": "TON Blockchain (candidate)", "investigations": "ZachXBT", "CertiKCommunity": "CertiK Community", "certikalert": "CertiK Alert (candidate)",
    "binance_announcements": "Binance Announcements", "icodrops": "ICO Drops", "CryptoRankNews": "CryptoRank", "cryptoquant_official": "CryptoQuant",
    "tech": "@tech Startups & Ventures",
    # ---- Earn Stack candidates (operator 2026-11-17; third-party research, expect dead / renamed ones): verified FROM RAILWAY before anything is built
    "airdropinspector": "airdropinspector (earn candidate)",
    "AirdropDetective": "AirdropDetective (earn candidate)",
    "airdropking_io": "airdropking_io (earn candidate)",
    "LegitAirdrops": "LegitAirdrops (earn candidate)",
    "AirdropOasis": "AirdropOasis (earn candidate)",
    "AirdropNinja": "AirdropNinja (earn candidate)",
    "cryptoairdrops": "cryptoairdrops (earn candidate)",
    "airdropalert": "airdropalert (earn candidate)",
    "bulka_crypto": "bulka_crypto (earn candidate)",
    "top2airdrop": "top2airdrop (earn candidate)",
    "easyprofitcrypto": "easyprofitcrypto (earn candidate)",
    "unfud_crypto": "unfud_crypto (earn candidate)",
    "moni_talks": "moni_talks (earn candidate)",
    "CoinMarketCapAirdrops": "CoinMarketCapAirdrops (earn candidate)",
    "OKXOfficial_English": "OKXOfficial_English (earn candidate)",
    "Galxe": "Galxe (earn candidate)",
}
_ADISH_RE = re.compile(r"join @|bitcoin_priceupdatebot|promo|sponsored|#ad\b|advertis|referral|use code|sign up (?:now|today)|trade (?:now|with)|signals?\b|vip\b", re.IGNORECASE)


def probe_telegram(handle: str) -> dict:
    """What t.me/s/<handle> actually serves from THIS machine. Read-only."""
    import statistics
    from collectors.earning_opportunities import parse_telegram_preview
    out = {"id": f"tg:{handle}", "label": TELEGRAM_CANDIDATES.get(handle, handle)}
    t0 = time.time()
    try:
        r = requests.get(f"https://t.me/s/{handle}", headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=25)
    except Exception as exc:
        return {**out, "status": None, "error": str(exc)[:100]}
    out["status"], out["secs"] = r.status_code, round(time.time() - t0, 1)
    posts = parse_telegram_preview(r.text, handle) if r.status_code == 200 else []
    title = re.search(r'<meta property="og:title" content="([^"]*)"', r.text)
    out["page_title"] = (title.group(1) if title else "")[:50]
    out["posts"] = len(posts)
    if not posts:
        out["note"] = "no public preview (a group, a private channel, or a different page)"
        return out
    ts = sorted(datetime.fromisoformat(p["time"]) for p in posts)
    now = datetime.now(timezone.utc)
    span = max((ts[-1] - ts[0]).total_seconds() / 86400, 0.05)
    views = [p["views"] for p in posts if p["views"]]
    out.update({"newest": ts[-1].isoformat(), "newest_age_h": round((now - ts[-1]).total_seconds() / 3600, 1), "oldest": ts[0].date().isoformat(),
                "per_day": round(len(posts) / span, 1), "median_views": int(statistics.median(views)) if views else 0,
                "adish_share": round(sum(1 for p in posts if _ADISH_RE.search(p["text"] or "")) / len(posts), 2),
                "sample": [(p["text"] or "").replace("\n", " ")[:90] for p in posts[-3:]]})
    return out


def probe_telegram_all() -> list[dict]:
    res = []
    for h in TELEGRAM_CANDIDATES:
        res.append(probe_telegram(h))
        time.sleep(1.0)
    return res


def probe_all(only: list[str] | None = None) -> list[dict]:
    results = []
    for p in PROBES:
        if only and p["id"] not in only:
            continue
        results.append(run_probe(p))
        time.sleep(0.4)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    res = probe_all(sys.argv[1:] or None)
    for r in res:
        line = f"{r['id']:26} {str(r.get('status')):>6} {r.get('kind', ''):5} items={r.get('items', '-')!s:>4} newest={r.get('newest', '-'):10} {r.get('secs', '')}s"
        extra = r.get("sample") or r.get("html_title") or r.get("snippet") or r.get("error") or ""
        print(line, "|", str(extra)[:100].replace("\n", " "), "| LOGIN?" if r.get("login_wall") else "")
