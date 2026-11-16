"""startup_news (Phase 6, 2026-11-16) -> Hustle to Million: funding rounds WITH A STATED AMOUNT and acquisitions, from three keyless RSS feeds
(Tech.eu, Sifted, TechCrunch Venture -- all verified FROM RAILWAY before the build, see BACKLOG).

Why not @tech: the Telegram channel is general tech / AI / science news. In 123 posts over 47 days exactly one carried a funding amount; these three feeds carry 3-7 in 20 items.
"Notable launches" is NOT built: nothing in these feeds says what is notable, and a launch with no figure is a press release.

WHAT QUALIFIES (deterministic, on the title first, then the first 300 characters of the summary):
  funding      -- a raise verb (raises / secures / lands / closes / bags ...) AND a money amount stated in the text. A VC raising its own FUND ("Plural raises EUR 1bn fund") is not a startup
                  round and is excluded; so is a story with no amount.
  acquisition  -- acquires / acquired / acquisition of / to acquire / buys / merges with. The price is shown only when the story states one.
EXCLUDED before scoring: sponsored / partner content / press releases / opinion (feed categories and title markers), and Sifted+ teasers are kept (the headline is public).

AMOUNTS ARE SHOWN IN THE CURRENCY AND FORM THE OUTLET USED ("EUR 6M", "$1.1B") -- never converted, never relabelled. A dollar-equivalent is computed internally for the score only.

DEDUPE: the same raise reported by two outlets is one story (topic_key = the company, 7-day window); the first seen is kept and the others recorded in `also_covered_by`.
HIRING LINK (low priority, operator 2026-11-16): a story whose company has a startup_jobs CARD we already sent (last 90 days) carries it in payload['hiring']; the post says so. Expected to be rare
(0 matches in 164 current stories against 204 job companies) -- built strictly: exact company name, at least 4 characters, never a common word.
"""

import logging
import re
import sys
from datetime import datetime, timezone
from time import mktime

import feedparser
from dotenv import load_dotenv

from pipeline import llm_usage, prizes
from pipeline.db import dict_cursor, get_conn
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logger = logging.getLogger(__name__)

CATEGORY = "startup_news"
SOURCE_NAME = "startup_news_rss"
FEEDS = {
    "techeu": "https://tech.eu/feed",
    "sifted": "https://sifted.eu/feed",
    "techcrunch_venture": "https://techcrunch.com/category/venture/feed/",
}
SOURCE_LABEL = {"techeu": "Tech.eu", "sifted": "Sifted", "techcrunch_venture": "TechCrunch"}
MAX_AGE_HOURS = 48
STALE_FEED_DAYS = 7
DEDUP_DAYS = 7
HOLD_ALL_CANDIDATES = False
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; crypto-content-bot)"}

_TAG_RE = re.compile(r"<[^>]+>")
_NOT_NEWS_CATEGORIES = {"sponsored", "partner content", "press release", "advertorial", "opinion", "analysis", "podcast", "newsletter", "events", "event", "video"}
_NOT_NEWS_TITLE_RE = re.compile(r"^\s*(?:sponsored|partner(?:ed)? content|press release|opinion|podcast|webinar|event)\b|\bsponsored\b|\bpaid post\b|\bsummit\b|\bdisrupt\b|\bwebinar\b|\bpass rates?\b|\bnewsletter\b", re.IGNORECASE)

_RAISE_RE = re.compile(r"\b(?:raises?|raised|secures?|secured|lands?|landed|closes?|closed|bags?|nets?|snags?|gets|announces)\b", re.IGNORECASE)
_ACQ_RE = re.compile(r"\b(?:acquires?|acquired|acquisition of|to acquire|buys|bought|merges? with|snaps up)\b", re.IGNORECASE)
_FUND_RE = re.compile(r"\d\s?(?:[mkb]n?|million|billion)\s+(?:\w+\s+){0,2}fund\b|\b(?:new|fresh|second|third|debut)\s+(?:\w+\s+)?fund\b|\bfund (?:ii|iii|iv|v)\b", re.IGNORECASE)
_MONEY_RE = re.compile(
    r"(?P<all>(?P<cur>[$€£])\s?(?P<n>\d[\d.,]*)\s?(?P<u>bn|billion|b|million|mn|m|thousand|k)?(?![\w])"
    r"|(?P<n2>\d[\d.,]*)\s?(?P<u2>bn|billion|million|mn|m)\s+(?P<w>euros?|dollars?|pounds?|usd|eur|gbp))", re.IGNORECASE)
_STAGE_RE = re.compile(r"\b(pre-?seed|seed|series [a-e]|growth round|bridge round|angel round)\b", re.IGNORECASE)
_LED_RE = re.compile(r"\bled by\s+(?P<who>[^.;,]{3,80}?)(?:\s+(?:with|and|to|as|after|for)\b|[.;,]|$)", re.IGNORECASE)
_UNIT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
_WORD_CUR = {"euro": "€", "euros": "€", "dollar": "$", "dollars": "$", "pound": "£", "pounds": "£", "usd": "$", "eur": "€", "gbp": "£"}
# names too generic to link a story to a job card
_COMMON = {"the", "labs", "tech", "group", "global", "digital", "health", "cloud", "data", "pay", "ready", "claims", "chaos", "ai", "app", "hub", "one", "next", "team", "works", "studio"}


def _clean(text: str) -> str:
    import html
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def parse_money(text: str) -> dict | None:
    """The FIRST amount stated in the text, as stated: {display: '€6M', amount, symbol} (amount is in the ORIGINAL currency; never converted)."""
    for m in _MONEY_RE.finditer(text or ""):
        try:
            if m.group("cur"):
                n, u, sym = m.group("n").rstrip(".,"), (m.group("u") or "").lower(), m.group("cur")
            else:
                n, u, sym = m.group("n2").rstrip(".,"), (m.group("u2") or "").lower(), _WORD_CUR.get(m.group("w").lower(), "$")
            amount = float(n.replace(",", "")) * _UNIT.get(u, 1)
        except ValueError:
            continue
        if amount < 100_000:
            continue                                     # a figure under 100k is not a funding round (a price, a user count)
        return {"display": re.sub(r"\s+", " ", m.group("all")).strip(), "amount": amount, "symbol": sym}
    return None


def company_from_title(title: str, verb_re: re.Pattern) -> str | None:
    """The company in '<descriptor> <Company> raises ...': the trailing run of capitalised words before the verb ('AI inference startup Daisytuner' -> 'Daisytuner')."""
    title = re.sub(r"^\s*(?:exclusive|breaking|just in|update)\s*:\s*", "", title or "", flags=re.IGNORECASE)
    m = verb_re.search(title or "")
    if not m:
        return None
    words = re.sub(r"[“”\"']", "", title[:m.start()]).split()
    out = []
    for w in reversed(words):
        w = w.strip(",:;")
        if not w or not (w[0].isupper() or w[0].isdigit()):
            break
        out.append(w)
    name = " ".join(reversed(out)).strip()
    return name if 2 <= len(name) <= 40 else None


# An acquisition's PRICE only when the text says it is the price ("for $8 billion", "valued at", "a deal worth"): a figure in the same sentence is often something else
# ("WeRoad acquires Swiss TravelTribe as it targets EUR 70M in M&A-driven growth" -- a target, not a price).
_PRICE_RE = re.compile(r"\b(?:for|valued at|worth|deal of)\s+(?:about |around |roughly |an estimated |up to |nearly |over )?(?=[$€£])", re.IGNORECASE)


def same_story(a: dict, b: dict) -> bool:
    """The same raise reported by two outlets: equal dollar-equivalent amount (within 1%) AND company names that overlap (one contains the other, or they share a word of 4+ letters)."""
    ua, ub = a.get("amount_usd_equiv"), b.get("amount_usd_equiv")
    if not ua or not ub or abs(ua - ub) > 0.01 * max(ua, ub):
        return False
    na, nb = (a.get("company") or "").lower(), (b.get("company") or "").lower()
    if not na or not nb:
        return False
    return na in nb or nb in na or bool(set(re.findall(r"[a-z0-9]{4,}", na)) & set(re.findall(r"[a-z0-9]{4,}", nb)))


def classify(title: str, summary: str, categories: list[str] | None = None, link: str = "") -> dict | None:
    """{story_type, company, money, stage, led_by} for a qualifying story, else None. See the module docstring for what qualifies."""
    cats = {c.lower() for c in categories or []}
    if cats & _NOT_NEWS_CATEGORIES or _NOT_NEWS_TITLE_RE.search(title or "") or re.search(r"/(?:sponsored|partner-content|press-release)/", link or ""):
        return None
    head = f"{title}. {(summary or '')[:300]}"
    if _ACQ_RE.search(title):
        company = company_from_title(title, _ACQ_RE)
        pm = _PRICE_RE.search(head)
        return {"story_type": "acquisition", "company": company, "money": parse_money(head[pm.start():]) if pm else None, "stage": None, "led_by": None} if company else None
    if _RAISE_RE.search(title) and not _FUND_RE.search(title):
        money = parse_money(head)
        if not money:
            return None
        company = company_from_title(title, _RAISE_RE)
        if not company:
            return None
        led = _LED_RE.search(head)
        stage = _STAGE_RE.search(head)
        return {"story_type": "funding", "company": company, "money": money, "stage": stage.group(1).title() if stage else None, "led_by": led.group("who").strip() if led else None}
    return None


def normalise_company(name: str | None) -> str | None:
    n = re.sub(r"\b(?:inc|ltd|gmbh|ag|sa|plc|llc|co|corp|limited)\b\.?", "", (name or "").lower())
    n = re.sub(r"[^a-z0-9 ]", "", n).strip()
    return n if len(n) >= 4 and n not in _COMMON else None


def hiring_links(conn) -> dict[str, dict]:
    """{normalised company: {company, position, apply_url, notified_at}} for startup_jobs cards we SENT in the last 90 days (newest wins)."""
    out: dict[str, dict] = {}
    with dict_cursor(conn) as cur:
        cur.execute("""SELECT r.payload, n.sent_at FROM notifications n JOIN raw_items r ON r.id = ANY(n.candidate_raw_item_ids)
                       WHERE r.category = 'startup_jobs' AND n.kind = 'candidate' AND n.sent_at > now() - interval '90 days' ORDER BY n.sent_at""")
        for row in cur.fetchall():
            p = row["payload"] or {}
            key = normalise_company(p.get("company"))
            if key:
                out[key] = {"company": p.get("company"), "position": p.get("position") or p.get("title"), "apply_url": p.get("apply_url"), "notified_at": row["sent_at"].isoformat()}
    return out


def to_payload(c: dict, source: str, entry: dict, published: datetime | None, hiring: dict | None) -> dict:
    money = c["money"]
    usd = round(prizes.to_usd(money["amount"], money["symbol"]), 2) if money else None
    payload = {
        "title": entry["title"], "topic_key": (c["company"] or entry["title"]).lower(), "source": source, "source_name": SOURCE_LABEL[source], "link": entry["link"],
        "description": entry["summary"][:600], "published_at": published.isoformat() if published else None,
        "story_type": c["story_type"], "company": c["company"], "stage": c["stage"], "led_by": c["led_by"],
        "amount_display": money["display"] if money else None, "amount_usd_equiv": usd,       # equivalent is INTERNAL (the score); only amount_display is ever shown
        "also_covered_by": [],
    }
    if hiring:
        payload["hiring"] = hiring
    return payload


def _entry_dt(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc) if parsed else None


def collect() -> int:
    load_dotenv()
    conn = get_conn()
    try:
        with run_log(conn, "collect_startup_news") as state:
            now = datetime.now(timezone.utc)
            diag, cands = {}, []
            for source, url in FEEDS.items():
                st = diag[source] = {"entries": 0, "qualifying": 0, "too_old": 0}
                try:
                    parsed = feedparser.parse(url, request_headers=HEADERS)
                except Exception as exc:
                    st["error"] = str(exc)[:100]; continue
                if parsed.bozo and not parsed.entries:
                    st["error"] = "feed did not parse"; continue
                st["entries"] = len(parsed.entries)
                newest = max((d for d in (_entry_dt(e) for e in parsed.entries) if d), default=None)
                if newest and (now - newest).days >= STALE_FEED_DAYS:
                    llm_usage._send_anomaly_alert(conn, "feed_stale", source, f"🚀 <b>Startup news feed gone quiet: {source}</b> -- newest item {(now - newest).days} days old.", now, 7 * 24 * 60)
                for e in parsed.entries:
                    published = _entry_dt(e)
                    if published and (now - published).total_seconds() > MAX_AGE_HOURS * 3600:
                        st["too_old"] += 1; continue
                    title = _clean(e.get("title"))
                    summary = _clean(e.get("summary"))
                    cats = [t.term for t in e.get("tags", []) if getattr(t, "term", None)]
                    c = classify(title, summary, cats, e.get("link") or "")
                    if not c or not e.get("link"):
                        continue
                    st["qualifying"] += 1
                    cands.append((source, {"title": title, "summary": summary, "link": e["link"], "id": e.get("id") or e["link"]}, published, c))
            state["details"]["feeds"] = diag
            if all("error" in d for d in diag.values()):
                raise RuntimeError("every startup_news feed failed")

            with dict_cursor(conn) as cur:
                cur.execute("SELECT DISTINCT payload->>'topic_key' k FROM raw_items WHERE category = %s AND collected_at > now() - (%s || ' days')::interval", (CATEGORY, DEDUP_DAYS))
                seen = {r["k"] for r in cur.fetchall()}
            jobs = hiring_links(conn)
            items, merged = [], {}
            for source, entry, published, c in sorted(cands, key=lambda x: x[2] or now):
                key = (c["company"] or entry["title"]).lower()
                link = jobs.get(normalise_company(c["company"]))
                payload = to_payload(c, source, entry, published, link)
                twin = next((p for p in merged.values() if same_story(p, payload)), None)
                if key in seen or twin is not None:
                    holder = merged.get(key) or twin
                    if holder is not None and SOURCE_LABEL[source] not in holder["also_covered_by"] and SOURCE_LABEL[source] != holder["source_name"]:
                        holder["also_covered_by"].append(SOURCE_LABEL[source])
                    continue
                seen.add(key)
                merged[key] = payload
                items.append((f"{source}:{entry['id']}", payload))
            source_id = get_or_create_source(conn, CATEGORY, SOURCE_NAME, "rss", {"feeds": list(FEEDS)})
            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items, held=HOLD_ALL_CANDIDATES)
            state["details"].update({"candidates": len(items), "inserted": inserted, "linked_to_job_cards": sum(1 for _, p in items if p.get("hiring"))})
            logger.info("startup_news: qualifying=%d new=%d inserted=%d", len(cands), len(items), inserted)
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collect()
    sys.exit(0)
