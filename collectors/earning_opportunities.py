"""earning_opportunities collector (Phase 5, 2026-11-15) -- Telegram (t.me/s/) and RSS/API sources.

What counts, the gate and both scores are in pipeline/earning.py (pure functions, no network). This module fetches,
extracts, runs the gate, looks up the checkable trust facts (RDAP domain age, optional Safe Browsing / URLhaus,
GoPlus for a token contract), works out independent corroboration, and stores the result. Scoring happens later
in pipeline/score.py from the stored payload alone.

SOURCES (scope: money INTO the reader's pocket -- see pipeline/earning.py; deals, discounts and free games were REMOVED on 2026-11-15):
  api  Devpost hackathons (tier 2)          rss  Doctor of Credit bank bonuses (tier 2)
  telegram  airdropfind, airdrops_io, incrypted_airdrops, criptinho (tier 3)
  corroboration only (never candidates): OKXAnnouncements, Bybit_Announcements, CoinMarketCapAnnouncements
New sources are being researched (see BACKLOG); each must be verified from Railway, not only from the dev machine.

DEAD SOURCES FLAG THEMSELVES: a Telegram channel whose newest post is older than 30 days, or a feed/API silent for
longer than its own limit, is recorded in the run log and the operator gets one alert per week per source (6 of the 20
handles the research listed were dead; Blockworks sat dead for 311 days before the same check existed).

GATED items are stored too, held, with the reasons in payload['gate'] -- an audit trail and the count that shows what the
gate is excluding. They can never become candidates.
"""

import hashlib
import html
import logging
import os
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
STALE_DAYS = {"doctorofcredit": 14, "devpost": 14}
MIN_ENGAGEMENT_RATIO = 0.2          # a Telegram post seen by under a fifth of the channel's median is dropped
MAX_AGE_HOURS_DEFAULT = 72          # items with no stated deadline stop being candidates after this (select_candidates)
CORROBORATION_LOOKBACK_DAYS = 7

RSS_FEEDS = {
    "doctorofcredit": ("https://www.doctorofcredit.com/category/bank-account-bonuses/feed/", None),
}
# a single-country source says so (the offer is local) unless its own text says otherwise
SOURCE_GEO_DEFAULT = {"doctorofcredit": "single"}

# links that are an aggregator's own redirect/page, not the offer's landing page: the topic key falls back to the title
AGGREGATOR_LANDINGS = {"doctorofcredit.com", "airdrops.io"}

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
    type_ = earning.classify_type(text, src["default_type"]) if src["kind"] in ("telegram", "rss") else src["default_type"]
    payout = extra.get("payout_usd")
    payout_kind = extra.get("payout_kind")
    pool_usd, unclear_usd = extra.get("pool_usd"), None
    # The payout is read from the text WITHOUT its referral clauses: "$25 for each friend you invite" is not the offer's own payout.
    base_text, _ = earning.split_referral(text)
    referral = earning.extract_referral(text)
    if payout is None and type_ != "hackathon":
        stated, kind = earning.extract_reward(base_text, type_)
        if stated is not None and kind == "pool":
            pool_usd = stated          # a total shared among participants: stated, but not what one reader receives
        elif stated is not None and kind == "unclear":
            unclear_usd = stated       # tied to N winners: per-person vs total cannot be told
        elif stated is not None:
            payout, payout_kind = stated, kind      # 'stated' (one reader) or 'max' ("up to": deliberately treated as the payout)
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
    eligibility = earning.has_eligibility(text, geo)
    return {
        "title": raw["title"] or text[:100], "description": text[:1200], "source": raw["source"], "source_name": src["name"],
        "source_tier": src["tier"], "kind": src["kind"], "published_at": raw["published_at"], "url": raw["url"],
        "landing_url": landing_norm, "landing_domain": earning.registrable_domain(host), "referral_link": earning.is_referral_link(landing_raw or ""),
        "type": type_, "payout_usd": payout, "payout_kind": payout_kind, "pool_usd": pool_usd, "unclear_usd": unclear_usd,
        "referral": referral,
        "other_amount_max": max([a for a in earning.extract_amounts_usd(base_text) if payout is None or abs(a - payout) > 0.5] or [0.0]),
        "deadline": _iso(deadline) if deadline else None, "capital_usd": capital, "effort_minutes": effort, "prereqs": prereqs, "geo": geo,
        "steps_text": extra.get("steps_text") or "",
        "terms": {"deadline": deadline is not None, "eligibility": eligibility, "payout": payout is not None or pool_usd is not None or unclear_usd is not None or earning.payout_stated(text)},
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


_WHOIS_SERVERS = {"io": "whois.nic.io", "me": "whois.nic.me"}      # ccTLDs with no RDAP server in IANA's bootstrap (checked 2026-11-15)
_WHOIS_CREATED_RE = re.compile(r"^\s*(?:Creation Date|Created On|Registered On)\s*:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE | re.MULTILINE)


def parse_whois_created(text: str):
    """The registration date (a date) from a WHOIS reply, or None."""
    m = _WHOIS_CREATED_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def whois_created(domain: str):
    """WHOIS (TCP 43) registration date for the few TLDs RDAP does not cover. None on any failure -- the age is then UNKNOWN,
    never guessed. (Outbound port 43 may be blocked on the host; unknown is the safe outcome.)"""
    import socket
    server = _WHOIS_SERVERS.get(domain.rsplit(".", 1)[-1])
    if not server:
        return None
    try:
        with socket.create_connection((server, 43), timeout=15) as s:
            s.sendall((domain + "\r\n").encode())
            chunks = []
            while True:
                b = s.recv(4096)
                if not b:
                    break
                chunks.append(b)
        return parse_whois_created(b"".join(chunks).decode("utf-8", "replace"))
    except Exception as exc:
        logger.warning("earning: WHOIS lookup failed for %s: %s", domain, exc)
        return None


def domain_age_days(conn, domain: str | None, now: datetime) -> int | None:
    """Days since registration (RDAP), cached 30 days in domain_checks. None = could not be determined."""
    if not domain:
        return None
    with dict_cursor(conn) as cur:
        cur.execute("SELECT created_on, checked_at FROM domain_checks WHERE domain = %s", (domain,))
        row = cur.fetchone()
    # A cached "no date" is trusted only when no fallback exists for the TLD: .io/.me answered nothing to RDAP before the WHOIS
    # fallback was added, and that cached nothing must not shadow it.
    retry_via_whois = bool(row and row["created_on"] is None and domain.rsplit(".", 1)[-1] in _WHOIS_SERVERS)
    if row and row["checked_at"] and (now - row["checked_at"]) < timedelta(days=30) and not retry_via_whois:
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
    if created is None:
        created = whois_created(domain)        # .io / .me have no RDAP server
    with dict_cursor(conn) as cur:
        cur.execute("""INSERT INTO domain_checks (domain, created_on, checked_at) VALUES (%s, %s, %s)
                       ON CONFLICT (domain) DO UPDATE SET created_on = EXCLUDED.created_on, checked_at = EXCLUDED.checked_at""", (domain, created, now))
    conn.commit()
    time.sleep(0.3)
    return (now.date() - created).days if created else None


def reputation_checks(urls: list[str]) -> dict[str, dict]:
    """{url: {"safe_browsing": clean|flagged|unchecked, "urlhaus": clean|flagged|unchecked}} for the landing URLs.

    Both need a key (GOOGLE_SAFE_BROWSING_API_KEY, URLHAUS_AUTH_KEY); without one the answer is 'unchecked'. ANY failure or
    unexpected response is 'unchecked' too -- a check we could not run is never coded as clean. NOT yet exercised against the
    live services (no keys at the time of writing): the response handling follows each service's documented shape and is
    covered by a mocked test only. Licensing is the operator's call: Safe Browsing v4 is "for non-commercial use only (not for
    sale or revenue generating purposes)" -- Google points commercial users to Web Risk -- and URLhaus' free community API is
    fair-use with commercial use possibly needing abuse.ch's paid API.
    """
    out = {u: {"safe_browsing": "unchecked", "urlhaus": "unchecked"} for u in urls}
    if not urls:
        return out
    sb_key = os.environ.get("GOOGLE_SAFE_BROWSING_API_KEY")
    if sb_key:
        try:
            body = {"client": {"clientId": "crypto-content-bot", "clientVersion": "1.0"},
                    "threatInfo": {"threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"],
                                   "platformTypes": ["ANY_PLATFORM"], "threatEntryTypes": ["URL"],
                                   "threatEntries": [{"url": u} for u in urls[:500]]}}
            r = requests.post("https://safebrowsing.googleapis.com/v4/threatMatches:find", params={"key": sb_key}, json=body, timeout=20)
            if r.status_code == 200 and isinstance(r.json(), dict):
                bad = {m.get("threat", {}).get("url") for m in r.json().get("matches", [])}
                for u in urls[:500]:
                    out[u]["safe_browsing"] = "flagged" if u in bad else "clean"
        except Exception as exc:
            logger.warning("earning: Safe Browsing check failed (left unchecked): %s", exc)
    uh_key = os.environ.get("URLHAUS_AUTH_KEY")
    if uh_key:
        seen: dict[str, str] = {}
        for u in urls[:60]:
            host = earning.domain_of(u)
            if not host:
                continue
            if host not in seen:
                try:
                    r = requests.post("https://urlhaus-api.abuse.ch/v1/host/", data={"host": host}, headers={"Auth-Key": uh_key}, timeout=20)
                    status = (r.json() or {}).get("query_status") if r.status_code == 200 else None
                    seen[host] = "clean" if status == "no_results" else "flagged" if status == "ok" else "unchecked"
                except Exception as exc:
                    logger.warning("earning: URLhaus check failed for %s (left unchecked): %s", host, exc)
                    seen[host] = "unchecked"
                time.sleep(0.3)
            out[u]["urlhaus"] = seen[host]
    return out


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


_AIRDROPS_IO_WEBSITE_RE = re.compile(r'Website:\s*<a href="(/visit/[^"]+)"', re.IGNORECASE)


def resolve_project_site(url: str) -> str | None:
    """Where an aggregator's OWN page for an offer points: the project's real website. Operator ruling 2026-11-15: when an
    airdrops.io post links to airdrops.io's page, scoring the aggregator's domain age "tells us nothing about the offer" --
    follow the page to the project's site so RDAP is run on THAT. airdrops.io lists it as "Website: <a href=/visit/ID/>", a
    redirect; the redirect is followed with a HEAD request and the final host must not be airdrops.io itself. None when the
    page, the link or the redirect cannot be resolved (the caller then leaves the domain facts UNKNOWN, never the
    aggregator's)."""
    host = earning.domain_of(url) or ""
    if host != "airdrops.io":
        return None
    try:
        page = requests.get(url, headers=HEADERS, timeout=25)
        m = _AIRDROPS_IO_WEBSITE_RE.search(page.text) if page.status_code == 200 else None
        if not m:
            return None
        final = requests.head("https://airdrops.io" + m.group(1), headers=HEADERS, allow_redirects=True, timeout=20).url
        time.sleep(0.3)
    except Exception as exc:
        logger.warning("earning: could not resolve the project site behind %s: %s", url, exc)
        return None
    return final if earning.domain_of(final) not in AGGREGATOR_LANDINGS else None


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
                if earning.is_roundup(raw["title"], raw["text"]):
                    reasons.append("not an offer: a digest / roundup of several projects")
                income_reason = earning.not_income_reason(payload["type"])
                if income_reason:
                    reasons.append(income_reason)
                if reasons:
                    payload["gate"] = {"excluded": True, "reasons": reasons}
                    gated.append((ext, payload))
                    continue
                items.append({"ext": ext, "payload": payload})

            # An aggregator's own page is not the offer: follow it to the project's real site so the domain facts (RDAP age) and
            # corroboration describe the PROJECT. If it cannot be resolved the domain is left unknown, never the aggregator's.
            project_sites: dict[str, str | None] = {}
            for it in items:
                p = it["payload"]
                if p.get("landing_domain") == "airdrops.io" and p.get("landing_url"):
                    if p["landing_url"] not in project_sites and len(project_sites) < 40:
                        project_sites[p["landing_url"]] = resolve_project_site(p["landing_url"])
                    site = project_sites.get(p["landing_url"])
                    p["aggregator_page"] = p["landing_url"]
                    if site:
                        p["landing_url"], p["landing_domain"] = earning.normalize_url(site), earning.registrable_domain(earning.domain_of(site))
                        p["topic_key"] = p["landing_url"]
                    else:
                        p["landing_url"], p["landing_domain"] = None, None

            # checkable trust facts, only for what survived the gate
            for it in items:
                p = it["payload"]
                p["domain_age_days"] = domain_age_days(conn, p.get("landing_domain"), now)
                p["goplus"] = goplus_for_text(conn, p["description"])
                if p["goplus"]["status"] == "flagged":
                    p["gate"] = {"excluded": True, "reasons": ["token contract flagged by GoPlus: " + "; ".join(p["goplus"].get("flags", []))[:160]]}
            # Safe Browsing / URLhaus on the landing URLs of what is left (keys optional; 'unchecked' without them, never 'clean')
            live = [it for it in items if not it["payload"]["gate"]["excluded"] and it["payload"].get("landing_url")]
            rep = reputation_checks(sorted({it["payload"]["landing_url"] for it in live}))
            for it in live:
                p = it["payload"]
                p["reputation"] = rep.get(p["landing_url"], p["reputation"])
                bad = [k for k, v in p["reputation"].items() if v == "flagged"]
                if bad:
                    p["gate"] = {"excluded": True, "reasons": ["landing URL flagged by " + " and ".join(bad)]}
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
