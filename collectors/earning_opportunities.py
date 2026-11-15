"""earning_opportunities collector (Phase 5, 2026-11-15) -- Telegram (t.me/s/) and RSS/API sources.

What counts, the gate and both scores are in pipeline/earning.py (pure functions, no network). This module fetches,
extracts, runs the gate, looks up the checkable trust facts (RDAP domain age, optional Safe Browsing / URLhaus,
GoPlus for a token contract), works out independent corroboration, and stores the result. Scoring happens later
in pipeline/score.py from the stored payload alone.

SOURCES (live-checked 2026-11-15 from the dev machine -- NOT yet from Railway, which saw different data for two
exchanges the same day; every source here reports what it actually returned in the run log so the first Railway cycle
is the check):
  api  Epic Games Store freeGamesPromotions (official, tier 1)   api  GamerPower giveaways (tier 2, carries the steps)
  api  Devpost hackathons (tier 2)                               rss  Doctor of Credit bank bonuses (tier 2)
  rss  OzBargain / MyDealz / Dealabs freebies (tier 3, single-country)
  telegram  airdropfind, airdrops_io, incrypted_airdrops, criptinho, EpicSteamGames, FreeGamesNews, r_FreeGamesOnSteam
  corroboration only (never candidates): OKXAnnouncements, Bybit_Announcements, CoinMarketCapAnnouncements
Not built yet: Discord hub (operator sets up the server and bot first), Product Hunt (needs a token), GG.deals
(API key), GiveawayBase (403), Betalist / Startup Perks (404), MaalFreeKaa (DNS), Safe Browsing / URLhaus keys.

DEAD SOURCES FLAG THEMSELVES: a Telegram channel whose newest post is older than 30 days, or a feed/API silent for
longer than its own limit, is recorded in the run log and the operator gets one alert per week per source (6 of the 20
handles the research listed were dead; Blockworks sat dead for 311 days before the same check existed).

GATED items are stored too, held, with the reasons in payload['gate'] -- an audit trail and the count that shows what the
gate is excluding. They can never become candidates.
"""

import hashlib
import html
import logging
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from dotenv import load_dotenv

from pipeline import earning, llm_usage
from pipeline.db import dict_cursor, get_conn
from pipeline.http import get_json
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CATEGORY = "earning_opportunities"
SOURCE_NAME = "earning_sources"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36", "Accept-Language": "en"}
CALL_DELAY = 1.0

TELEGRAM_STALE_DAYS = 30
STALE_DAYS = {"gamerpower": 7, "doctorofcredit": 14, "ozbargain": 7, "mydealz": 7, "dealabs": 7, "devpost": 14}
MIN_ENGAGEMENT_RATIO = 0.2          # a Telegram post seen by under a fifth of the channel's median is dropped
MAX_AGE_HOURS_DEFAULT = 72          # items with no stated deadline stop being candidates after this (select_candidates)
CORROBORATION_LOOKBACK_DAYS = 7

RSS_FEEDS = {
    "doctorofcredit": ("https://www.doctorofcredit.com/category/bank-account-bonuses/feed/", None),
    "ozbargain": ("https://www.ozbargain.com.au/feed", re.compile(r"\bfree\b", re.IGNORECASE)),
    "mydealz": ("https://www.mydealz.de/rss/gruppe/freebies", None),
    "dealabs": ("https://www.dealabs.com/rss/groupe/gratuit", None),
}
# a single-country source says so (the offer is local) unless its own text says otherwise
SOURCE_GEO_DEFAULT = {"ozbargain": "single", "mydealz": "single", "dealabs": "single", "doctorofcredit": "single"}

# links that are an aggregator's own redirect/page, not the offer's landing page: the topic key falls back to the title
AGGREGATOR_LANDINGS = {"gamerpower.com", "doctorofcredit.com", "ozbargain.com.au", "mydealz.de", "dealabs.com", "airdrops.io"}

# posts that are discussion or housekeeping, not an offer (found on real r/FreeGamesOnSteam relays: "Weekly Discussion Thread")
_NOT_AN_OFFER_RE = re.compile(r"discussion thread|megathread|weekly thread|daily thread|read the rules|subreddit rules|\bAMA\b|poll:", re.IGNORECASE)

_AD_RE = re.compile(r"(?:^|\W)(?:#ad|#sponsored|sponsored|advertisement|\bAD\b|promo code|use code)(?:\W|$)", re.IGNORECASE)


# ---------------------------------------------------------------- helpers

def is_stale(newest: datetime | None, now: datetime, limit_days: int | None) -> bool:
    """A source whose newest item is `limit_days` or more old is dead, not quiet (same shape as the news stale-feed check).
    No limit (an official API that always has data) or no items at all is not 'stale' here -- an empty source shows up as
    zero items in the run log."""
    return bool(newest and limit_days and (now - newest).days >= limit_days)


def _get_text(url: str, retries: int = 2) -> str:
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url.split('?')[0]} failed: {str(last)[:120]}")


def _strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s or "", flags=re.IGNORECASE)
    s = re.sub(r"</(?:p|div|li)>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()
    return re.sub(r"([$€£])\s+(\d)", r"\1\2", s)           # "$ 100,000" (a currency sign in its own tag) -> "$100,000"


def _num(s: str) -> int:
    s = (s or "").strip().replace(" ", "")
    m = re.match(r"^([\d.,]+)([KkMm]?)$", s)
    if not m:
        return 0
    v = float(m.group(1).replace(",", ""))
    return int(v * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------- fetchers: each returns (raws, newest_datetime | None)
# raw = {"source","ext_id","title","text","links":[...],"url","published_at": iso, "extra": {...}}

def fetch_epic(now: datetime):
    d = get_json("https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions", params={"locale": "en-US"}, headers=HEADERS)
    raws = []
    for e in ((d.get("data") or {}).get("Catalog") or {}).get("searchStore", {}).get("elements", []):
        offers = ((e.get("promotions") or {}).get("promotionalOffers") or [])
        live = [o for blk in offers for o in blk.get("promotionalOffers", []) if (o.get("discountSetting") or {}).get("discountPercentage") == 0]
        if not live:
            continue
        o = live[0]
        slug = e.get("productSlug") or (e.get("catalogNs", {}).get("mappings") or [{}])[0].get("pageSlug") or e.get("urlSlug")
        price = ((e.get("price") or {}).get("totalPrice") or {})
        orig = (price.get("originalPrice") or 0) / (10 ** (price.get("currencyInfo", {}).get("decimals", 2) or 2))
        cur = price.get("currencyCode") or "USD"
        raws.append({
            "source": "epic_free_games", "ext_id": e.get("id"), "title": f"{e.get('title')} is free on the Epic Games Store",
            "text": f"{e.get('title')} - free to keep on the Epic Games Store until {o.get('endDate', '')[:10]}. {e.get('description') or ''}",
            "links": [f"https://store.epicgames.com/en-US/p/{slug}"] if slug else [], "url": f"https://store.epicgames.com/en-US/p/{slug}" if slug else "",
            "published_at": o.get("startDate") or _iso(now),
            "extra": {"payout_usd": round(orig, 2) if cur == "USD" and orig else None, "payout_kind": "worth", "deadline": o.get("endDate"),
                      "geo": "worldwide", "winners": None, "steps_text": "Open the store page.\nSign in to an Epic account.\nPress Get, then Place Order (the total is zero)."},
        })
    return raws, now


def fetch_gamerpower(now: datetime):
    d = get_json("https://www.gamerpower.com/api/giveaways", headers=HEADERS)
    raws, newest = [], None
    for g in d:
        if (g.get("status") or "").lower() != "active":
            continue
        pub = datetime.strptime(g["published_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if g.get("published_date") else now
        newest = max(newest, pub) if newest else pub
        worth = earning.extract_amounts_usd(g.get("worth") or "")
        end = g.get("end_date") if g.get("end_date") and g.get("end_date") != "N/A" else None
        raws.append({
            "source": "gamerpower", "ext_id": str(g["id"]), "title": g.get("title") or "", "published_at": _iso(pub),
            "text": f"{g.get('title')}. {g.get('description') or ''} {g.get('instructions') or ''}",
            "links": [g.get("open_giveaway_url")] if g.get("open_giveaway_url") else [], "url": g.get("gamerpower_url") or g.get("open_giveaway_url") or "",
            "extra": {"payout_usd": worth[0] if worth else None, "payout_kind": "worth", "deadline": (end.replace(" ", "T") + "+00:00") if end else None,
                      "geo": None, "winners": None, "steps_text": (g.get("instructions") or "").strip(), "gp_type": g.get("type"), "users": g.get("users")},
        })
    return raws, newest or now


def fetch_devpost(now: datetime):
    d = get_json("https://devpost.com/api/hackathons", params={"status[]": "open", "per_page": 40}, headers=HEADERS)
    raws = []
    for h in d.get("hackathons", []):
        prize_txt = _strip_html(h.get("prize_amount") or "")
        pool = (earning.extract_amounts_usd(prize_txt) or [None])[0]
        regs = h.get("registrations_count") or 0
        dates = h.get("submission_period_dates") or ""
        end = earning.extract_deadline("until " + dates.split("-")[-1].strip(), now) if "-" in dates else None
        themes = [t["name"] for t in h.get("themes", [])]
        loc = (h.get("displayed_location") or {}).get("location") or ""
        raws.append({
            "source": "devpost", "ext_id": str(h["id"]), "title": f"{h.get('title')} hackathon: {prize_txt} in prizes",
            "text": f"{h.get('title')} hackathon by {h.get('organization_name') or 'the organizer'}. Prize pool {prize_txt}. Submissions {dates}. {', '.join(themes)}. {loc}.",
            "links": [h.get("url")] if h.get("url") else [], "url": h.get("url") or "", "published_at": _iso(now),
            "extra": {"payout_usd": round(pool / max(regs, 10), 2) if pool else None, "payout_kind": "ev", "pool_usd": pool, "registrations": regs,
                      "deadline": _iso(end) if end else None, "geo": "worldwide" if loc.lower() == "online" else "unknown", "winners": None,
                      "steps_text": ""},
        })
    return raws, now


def fetch_rss(source_id: str, now: datetime):
    url, must = RSS_FEEDS[source_id]
    parsed = feedparser.parse(_get_text(url).encode("utf-8"))
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"{source_id}: feed did not parse")
    raws, newest = [], None
    for e in parsed.entries:
        pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc) if getattr(e, "published_parsed", None) else None
        if pub:
            newest = max(newest, pub) if newest else pub
        title = html.unescape(e.get("title") or "")
        if must and not must.search(title):
            continue
        summary = _strip_html(e.get("summary") or "")
        content = " ".join(c.get("value", "") for c in e.get("content", []) or [])
        body_links = earning.find_urls(re.sub(r'href="([^"]+)"', r" \1 ", content or e.get("summary") or ""))
        raws.append({
            "source": source_id, "ext_id": hashlib.sha1((e.get("id") or e.get("link") or title).encode()).hexdigest()[:16], "title": title,
            "text": f"{title}. {summary} {_strip_html(content)[:1500]}", "links": body_links + ([e.get("link")] if e.get("link") else []),
            "url": e.get("link") or "", "published_at": _iso(pub) if pub else _iso(now), "extra": {"geo": SOURCE_GEO_DEFAULT.get(source_id)},
        })
    return raws, newest


_MSG_SPLIT_RE = re.compile(r'<div class="tgme_widget_message_wrap')
_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*(?:<div class="tgme_widget_message_(?:footer|reactions|inline_keyboard)|</div>|<a class="tgme_widget_message_(?:date|link_preview))', re.S)
_HREF_RE = re.compile(r'<a [^>]*href="(https?://[^"]+)"', re.I)


def parse_telegram_preview(page: str, handle: str) -> list[dict]:
    """Messages from a t.me/s/<handle> page: id, time, text, outbound links, views, reaction total."""
    out = []
    for chunk in _MSG_SPLIT_RE.split(page)[1:]:
        post = re.search(r'data-post="([^"]+)"', chunk)
        when = re.search(r'<time datetime="([^"]+)"', chunk)
        if not post or not when:
            continue
        tm = _TEXT_RE.search(chunk) or re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
        text_html = tm.group(1) if tm else ""
        links = [html.unescape(l) for l in _HREF_RE.findall(text_html)]
        links += [html.unescape(l) for l in re.findall(r'class="tgme_widget_message_inline_button[^"]*"[^>]*href="(https?://[^"]+)"', chunk)]
        views = re.search(r'tgme_widget_message_views">([^<]+)<', chunk)
        reactions = sum(_num(c) for c in re.findall(r'class="tgme_reaction[^>]*>.*?</i>\s*([\d.,KkMm]+)\s*<', chunk, re.S))
        if not reactions:
            reactions = sum(_num(c) for c in re.findall(r'</(?:i|span|b)>\s*(\d[\d.,KkMm]*)\s*</span>', chunk.split("tgme_widget_message_reactions", 1)[-1][:800])) if "tgme_widget_message_reactions" in chunk else 0
        out.append({"post": post.group(1), "time": when.group(1), "text": _strip_html(text_html), "links": links,
                    "views": _num(views.group(1)) if views else 0, "reactions": reactions})
    return out


def fetch_telegram(handle: str, now: datetime):
    page = _get_text(f"https://t.me/s/{handle}")
    msgs = parse_telegram_preview(page, handle)
    if not msgs:
        raise RuntimeError(f"t.me/s/{handle} returned no messages")
    views = [m["views"] for m in msgs if m["views"]]
    median = statistics.median(views) if views else 0
    newest = max(datetime.fromisoformat(m["time"]) for m in msgs)
    raws = []
    for m in msgs:
        raws.append({
            "source": f"tg:{handle}", "ext_id": m["post"].split("/")[-1], "title": (m["text"].split("\n")[0] or "")[:110],
            "text": m["text"], "links": m["links"], "url": f"https://t.me/{m['post']}", "published_at": _iso(datetime.fromisoformat(m["time"])),
            "extra": {"views": m["views"], "reactions": m["reactions"], "channel_median_views": median,
                      "engagement_ratio": round(m["views"] / median, 2) if median else None, "geo": None},
        })
    time.sleep(CALL_DELAY)
    return raws, newest


# ---------------------------------------------------------------- payload

def build_payload(raw: dict, now: datetime) -> dict:
    src = earning.SOURCES[raw["source"]]
    extra = raw.get("extra") or {}
    text = raw["text"] or ""
    landing_raw = earning.pick_landing_url(raw["links"] or []) or None
    # trusted structured sources link out through their own redirect (GamerPower); the landing for trust purposes is that link
    landing_norm = earning.normalize_url(landing_raw) if landing_raw else None
    host = earning.domain_of(landing_raw) if landing_raw else None
    type_ = earning.classify_type(text, src["default_type"]) if src["kind"] == "telegram" or src["kind"] == "rss" else (
        src["default_type"] if raw["source"] != "gamerpower" else ("free_game" if extra.get("gp_type") in ("Game", "Loot", "Beta") else earning.classify_type(text, "giveaway")))
    payout = extra.get("payout_usd")
    payout_kind = extra.get("payout_kind")
    if payout is None and type_ != "hackathon":
        stated = earning.extract_reward_usd(text, type_)
        if stated is not None:
            payout, payout_kind = stated, "stated"
    deadline = None
    if extra.get("deadline"):
        try:
            deadline = datetime.fromisoformat(extra["deadline"].replace("Z", "+00:00"))
        except ValueError:
            deadline = None
    if deadline is None:
        deadline = earning.extract_deadline(text, now)
    prereqs = earning.extract_prereqs(text[:600] if src["kind"] == "rss" else text)   # a long article body mentions unrelated things
    capital = earning.extract_capital_usd(text, type_)
    effort = earning.estimate_effort_minutes(text, type_, prereqs)
    geo = extra.get("geo") or earning.geo_breadth(text, SOURCE_GEO_DEFAULT.get(raw["source"]))
    eligibility = geo != "unknown" or bool(re.search(r"\b(?:eligib|resident|age|18\+|21\+|must be|requires?)\b", text, re.IGNORECASE))
    return {
        "title": raw["title"] or text[:100], "description": text[:1200], "source": raw["source"], "source_name": src["name"],
        "source_tier": src["tier"], "kind": src["kind"], "published_at": raw["published_at"], "url": raw["url"],
        "landing_url": landing_norm, "landing_domain": earning.registrable_domain(host), "referral_link": earning.is_referral_link(landing_raw or ""),
        "type": type_, "payout_usd": payout, "payout_kind": payout_kind, "pool_usd": extra.get("pool_usd"),
        "deadline": _iso(deadline) if deadline else None, "capital_usd": capital, "effort_minutes": effort, "prereqs": prereqs, "geo": geo,
        "steps_text": extra.get("steps_text") or "",
        "terms": {"deadline": deadline is not None, "eligibility": eligibility, "payout": payout is not None},
        "engagement": {k: extra.get(k) for k in ("views", "reactions", "channel_median_views", "engagement_ratio") if k in extra} or None,
        "topic_key": (landing_norm if landing_norm and earning.domain_of(landing_raw) not in AGGREGATOR_LANDINGS
                      else earning.title_key(raw["title"] or text[:80], type_)),
        "gate": {"excluded": False, "reasons": []},
        "domain_age_days": None, "reputation": {"safe_browsing": "unchecked", "urlhaus": "unchecked"}, "goplus": {"status": "none"},
        "corroborators": [],
    }


# ---------------------------------------------------------------- the checkable trust facts

_RDAP_URL = "https://rdap.org/domain/{}"
_EVM_RE = re.compile(r"0x[a-fA-F0-9]{40}")


def domain_age_days(conn, domain: str | None, now: datetime) -> int | None:
    """Days since registration (RDAP), cached 30 days in domain_checks. None = could not be determined."""
    if not domain:
        return None
    with dict_cursor(conn) as cur:
        cur.execute("SELECT created_on, checked_at FROM domain_checks WHERE domain = %s", (domain,))
        row = cur.fetchone()
    if row and row["checked_at"] and (now - row["checked_at"]) < timedelta(days=30):
        return (now.date() - row["created_on"]).days if row["created_on"] else None
    created = None
    try:
        r = requests.get(_RDAP_URL.format(domain), headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/rdap+json"}, timeout=25)
        if r.status_code == 200:
            for ev in r.json().get("events", []):
                if ev.get("eventAction") == "registration":
                    created = datetime.fromisoformat(ev["eventDate"].replace("Z", "+00:00")).date()
                    break
    except Exception as exc:
        logger.warning("earning: RDAP lookup failed for %s: %s", domain, exc)
        return None
    with dict_cursor(conn) as cur:
        cur.execute("""INSERT INTO domain_checks (domain, created_on, checked_at) VALUES (%s, %s, %s)
                       ON CONFLICT (domain) DO UPDATE SET created_on = EXCLUDED.created_on, checked_at = EXCLUDED.checked_at""", (domain, created, now))
    conn.commit()
    time.sleep(0.3)
    return (now.date() - created).days if created else None


def goplus_for_text(conn, text: str) -> dict:
    """GoPlus on a token contract mentioned in the text. 'none' = no contract; 'unchecked' = a contract we could not
    get a verdict for (never coded as clean); 'flagged' = a gating red flag (the item is excluded); 'clean' otherwise."""
    from pipeline import goplus
    addrs = list(dict.fromkeys(a.lower() for a in _EVM_RE.findall(text or "")))
    if not addrs:
        return {"status": "none"}
    lower = (text or "").lower()
    chains = [c for c in goplus.CHAIN_NAME_TO_GOPLUS_ID if re.search(rf"\b{re.escape(c)}\b", lower)] or ["ethereum", "bsc", "base"]
    worst = "unchecked"
    for addr in addrs[:2]:
        for chain in chains[:3]:
            try:
                res = goplus.get_token_security(conn, chain, addr)
            except Exception as exc:
                logger.warning("earning: GoPlus failed for %s on %s: %s", addr, chain, exc)
                continue
            ev = goplus.evaluate(res)
            if ev["checked_at_all"]:
                if ev["has_red_flag"]:
                    return {"status": "flagged", "flags": ev["red_flags"], "address": addr, "chain": chain}
                worst = "clean"
                break
    return {"status": worst, "address": addrs[0]}


def resolve_redirect(url: str) -> str | None:
    """Where a link actually lands (HEAD, follow redirects). None if it cannot be resolved -- the original stands."""
    try:
        r = requests.head(url, headers=HEADERS, allow_redirects=True, timeout=12)
        time.sleep(0.2)
        return r.url
    except Exception:
        return None


def attach_corroboration(items: list[dict], known: list[dict], reference: list[dict]) -> None:
    """`corroborators` = OTHER sources carrying the same landing URL, or the same landing domain with a near-identical
    title, within this run plus the last CORROBORATION_LOOKBACK_DAYS of stored items, plus the corroboration-only
    channels (an exchange announcing the same campaign). A source counts once."""
    pool = [(i["payload"]["source"], i["payload"].get("landing_url"), i["payload"].get("landing_domain"), set(_title_words(i["payload"]["title"]))) for i in items]
    pool += [(k["source"], k.get("landing_url"), k.get("landing_domain"), set(_title_words(k.get("title") or ""))) for k in known]
    pool += [(r["source"], earning.normalize_url(r["landing"]) if r.get("landing") else None, earning.registrable_domain(earning.domain_of(r.get("landing") or "")),
              set(_title_words(r["text"][:140]))) for r in reference]
    for it in items:
        p = it["payload"]
        mine = set(_title_words(p["title"]))
        found = set()
        for src, lurl, ldom, words in pool:
            if src == p["source"] or (src.startswith("tg:") and p["source"].startswith("tg:") and src == p["source"]):
                continue
            if lurl and p.get("landing_url") and lurl == p["landing_url"]:
                found.add(src)
            elif ldom and p.get("landing_domain") and ldom == p["landing_domain"] and mine and words and len(mine & words) / len(mine | words) >= 0.5:
                found.add(src)
        p["corroborators"] = sorted(found)


def _title_words(title: str) -> list[str]:
    stop = {"the", "a", "an", "to", "of", "and", "for", "in", "on", "free", "get", "your", "with", "is", "are", "at", "by", "from", "new", "airdrop"}
    return [w for w in re.findall(r"[a-z0-9]{3,}", (title or "").lower()) if w not in stop]


# ---------------------------------------------------------------- collect

def collect() -> int:
    load_dotenv()
    conn = get_conn()
    try:
        with run_log(conn, "collect_earning_opportunities") as state:
            now = datetime.now(timezone.utc)
            source_id = get_or_create_source(conn, CATEGORY, SOURCE_NAME, "mixed", {"sources": list(earning.SOURCES)})
            diag: dict[str, dict] = {}
            raws: list[dict] = []
            reference: list[dict] = []

            def run(name, fn, stale_days):
                try:
                    rows, newest = fn()
                except Exception as exc:
                    diag[name] = {"error": str(exc)[:160]}
                    logger.warning("earning: source %s failed, continuing: %s", name, exc)
                    return []
                d = {"items": len(rows), "newest": _iso(newest) if newest else None}
                if is_stale(newest, now, stale_days):
                    d["stale_days"] = (now - newest).days
                    logger.error("earning: source %s is STALE -- newest item %d days old", name, d["stale_days"])
                    llm_usage._send_anomaly_alert(
                        conn, "earning_source_stale", name,
                        f"🎁 <b>Earning source gone quiet: {earning.SOURCES.get(name, {}).get('name', name)}</b>\nIts newest item is {d['stale_days']} "
                        f"days old (alert at {stale_days}). Remove or replace it in pipeline/earning.py SOURCES.", now, 7 * 24 * 60)
                diag[name] = d
                return rows

            raws += run("epic_free_games", lambda: fetch_epic(now), None)
            raws += run("gamerpower", lambda: fetch_gamerpower(now), STALE_DAYS["gamerpower"])
            raws += run("devpost", lambda: fetch_devpost(now), None)
            for sid in RSS_FEEDS:
                raws += run(sid, lambda sid=sid: fetch_rss(sid, now), STALE_DAYS[sid])
            for h in earning.TELEGRAM_HANDLES:
                raws += run(f"tg:{h}", lambda h=h: fetch_telegram(h, now), TELEGRAM_STALE_DAYS)
            for h in earning.CORROBORATION_ONLY_TELEGRAM:
                try:
                    rows, _ = fetch_telegram(h, now)
                    for r in rows:
                        reference.append({"source": f"ref:{h}", "text": r["text"], "landing": earning.pick_landing_url(r["links"])})
                except Exception as exc:
                    diag[f"ref:{h}"] = {"error": str(exc)[:120]}
            state["details"]["sources"] = diag
            if all("error" in d for d in diag.values() if d is not None):
                raise RuntimeError("every earning source failed")

            # existing external ids (skip work on rows we already hold) + recent stored items for corroboration
            with dict_cursor(conn) as cur:
                cur.execute("SELECT external_id FROM raw_items WHERE category = %s", (CATEGORY,))
                have = {r["external_id"] for r in cur.fetchall()}
                cur.execute("""SELECT payload FROM raw_items WHERE category = %s AND collected_at > now() - (%s || ' days')::interval""",
                            (CATEGORY, CORROBORATION_LOOKBACK_DAYS))
                known = [{"source": r["payload"].get("source"), "landing_url": r["payload"].get("landing_url"), "landing_domain": r["payload"].get("landing_domain"),
                          "title": r["payload"].get("title")} for r in cur.fetchall()]

            items, gated, dropped_spam, dropped_no_landing = [], [], 0, 0
            for raw in raws:
                ext = f"{raw['source']}:{raw['ext_id']}"
                if ext in have:
                    continue
                kind = earning.SOURCES[raw["source"]]["kind"]
                if kind == "telegram":
                    eng = (raw.get("extra") or {}).get("engagement_ratio")
                    if _AD_RE.search(raw["text"]) or _NOT_AN_OFFER_RE.search(raw["title"] or "") or (eng is not None and eng < MIN_ENGAGEMENT_RATIO):
                        dropped_spam += 1
                        continue
                    if not earning.pick_landing_url(raw["links"]):
                        dropped_no_landing += 1
                        continue
                payload = build_payload(raw, now)
                reasons = earning.gate(raw["text"] + " " + " ".join(raw["links"] or []))
                if reasons:
                    payload["gate"] = {"excluded": True, "reasons": reasons}
                    gated.append((ext, payload))
                    continue
                items.append({"ext": ext, "payload": payload})

            # GamerPower links out through its own redirect; the trust facts belong to where it actually lands
            resolved = 0
            for it in items:
                p = it["payload"]
                if p["source"] == "gamerpower" and p.get("landing_url") and resolved < 60:
                    final = resolve_redirect(p["landing_url"])
                    resolved += 1
                    if final and earning.domain_of(final) not in AGGREGATOR_LANDINGS:
                        p["landing_url"], p["landing_domain"] = earning.normalize_url(final), earning.registrable_domain(earning.domain_of(final))
                        p["topic_key"] = earning.title_key(p["title"], p["type"])

            # checkable trust facts, only for what survived the gate
            for it in items:
                p = it["payload"]
                p["domain_age_days"] = domain_age_days(conn, p.get("landing_domain"), now)
                p["goplus"] = goplus_for_text(conn, p["description"])
                if p["goplus"]["status"] == "flagged":
                    p["gate"] = {"excluded": True, "reasons": ["token contract flagged by GoPlus: " + "; ".join(p["goplus"].get("flags", []))[:160]]}
            survivors = [it for it in items if not it["payload"]["gate"]["excluded"]]
            gated += [(it["ext"], it["payload"]) for it in items if it["payload"]["gate"]["excluded"]]
            attach_corroboration(survivors, known, reference)

            ok = insert_raw_items_batch(conn, source_id, CATEGORY, [(it["ext"], it["payload"]) for it in survivors], held=False)
            held = insert_raw_items_batch(conn, source_id, CATEGORY, gated, held=True)
            state["details"].update({"fetched": len(raws), "inserted": ok, "gated_inserted_held": held, "dropped_spam": dropped_spam,
                                     "dropped_no_landing": dropped_no_landing, "gate_reasons": _count_reasons(gated)})
            logger.info("earning: fetched=%d inserted=%d gated=%d spam=%d no_landing=%d", len(raws), ok, held, dropped_spam, dropped_no_landing)
        return ok
    finally:
        conn.close()


def _count_reasons(gated):
    out: dict[str, int] = {}
    for _, p in gated:
        for r in p["gate"]["reasons"]:
            key = r.split(":")[0]
            out[key] = out.get(key, 0) + 1
    return out


if __name__ == "__main__":
    n = collect()
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
