"""Rekt.news post-mortems -> hacks_exploits (operator 2026-11-20).

WHAT THE SOURCE IS, verified before this was built (locally and FROM RAILWAY, pipeline/source_probe.py ids rekt_rss / rekt_home):
  * The RSS feed (https://www.rekt.news/rss/feed.xml) is NOT the post-mortem feed: it serves 7 opinion essays, the newest from April 2026. It is not used.
  * The HOMEPAGE (https://www.rekt.news/) lists the post-mortems as <article class="post"> blocks: title ("Kava Lend - Rekt"), the date, tags, and a 2-3 sentence excerpt that says what
    actually failed ("An accounting flaw inflated a few thousand BUSD into a 1.25 billion BUSD claim"). About one a week. This is an HTML page, not a feed: the parser is structural and a
    page that parses to nothing raises (a visible failure, never a quiet empty run), and a newest post older than STALE_DAYS raises too.

WHAT A REKT POST-MORTEM IS TO THIS PIPELINE: every post-mortem since September was about an incident DefiLlama's tracker (collectors/hacks_exploits.py) had already listed 4-10 days earlier. So Rekt adds
no new incidents -- it adds the ACCOUNT of what failed, which DefiLlama's one-word classification lacks, and that is exactly what an educational channel wants. One incident is one event:
  * the post-mortem is matched to the tracker's incident by protocol name (a tracker incident from the 45 days before the post; also a ZachXBT report of the same name);
  * if the operator already APPROVED (or edited) a card for that incident, the post-mortem is skipped -- he has that story;
  * otherwise it is a candidate of its own (category hacks_exploits, tier 2), carrying the tracker's facts when there is a match and Rekt's excerpt always. Its post attributes everything in the
    account to Rekt (pipeline/post_facts.facts_hacks_exploits) and the reproduction guard refuses copied excerpt text.
Only posts tagged "Rekt" (the post-mortem tag) or titled "... - Rekt" are used; essays, research and media pieces are not incidents.

AMOUNT: from the tracker when matched. Otherwise the FIRST dollar figure with a million/billion unit in the excerpt ("$3.87M", "$6.43 billion"), used for ranking only; the post says what Rekt's excerpt says.
No figure -> amount unknown (stored as None, scored as unknown, never as zero-loss).
"""

import html as htmllib
import logging
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from pipeline.db import dict_cursor, get_conn
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logger = logging.getLogger(__name__)

CATEGORY = "hacks_exploits"
SOURCE_NAME = "rekt_news"
SOURCE_TIER = 2
HOME_URL = "https://www.rekt.news/"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36", "Accept-Language": "en"}
MAX_AGE_DAYS = 14          # the category's freshness window
MATCH_WINDOW_DAYS = 45     # a tracker incident this long before the post-mortem is the same event
STALE_DAYS = 45            # ~1 post-mortem a week: a month and a half of nothing is a dead page, not a quiet one
HOLD_ALL_CANDIDATES = False

_ARTICLE_RE = re.compile(r'<article class="post[^"]*">(.*?)</article>', re.S)
_TAG_RE = re.compile(r'href="/\?tag=[^"]*">(.*?)</a>', re.S)
_AMOUNT_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?(billion|million|bn|[BM])\b", re.IGNORECASE)
_UNIT = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "m": 1e6}


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def parse_articles(page: str) -> list[dict]:
    """[{slug, title, date (aware datetime), tags, excerpt, url}] for every post block on the homepage, newest first as the page lists them."""
    out = []
    for block in _ARTICLE_RE.findall(page or ""):
        link = re.search(r'<h5[^>]*>.*?<a href="(/[^"]+)"[^>]*>(.*?)</a>', block, re.S)
        when = re.search(r"<time[^>]*>([^<]+)</time>", block)
        if not link or not when:
            continue
        try:
            date = datetime.strptime(when.group(1).strip(), "%A, %B %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        excerpt = re.search(r'post-excerpt">\s*<p>(.*?)</p>', block, re.S)
        out.append({"slug": link.group(1).strip("/"), "title": _text(link.group(2)), "date": date, "tags": [_text(t) for t in _TAG_RE.findall(block)],
                    "excerpt": _text(excerpt.group(1)) if excerpt else "", "url": "https://www.rekt.news" + link.group(1)})
    return out


def is_postmortem(a: dict) -> bool:
    return "Rekt" in a["tags"] or bool(re.search(r"\s-\s+Rekt$", a["title"]))


def protocol_name(a: dict) -> str:
    """'Kava Lend - Rekt' -> 'Kava Lend'. The title carries the name; the first tag is only a fallback (it can be 'Rekt' itself or a category)."""
    m = re.match(r"^(.*?)\s+-\s+Rekt$", a["title"])
    if m:
        return m.group(1).strip()
    generic = {"rekt", "operational security", "third party breach", "cex", "governance", "supply chain attack", "cybersecurity"}
    for t in a["tags"]:
        if t.lower() not in generic:
            return t
    return a["title"]


def first_amount(excerpt: str) -> float | None:
    m = _AMOUNT_RE.search(excerpt or "")
    return float(m.group(1).replace(",", "")) * _UNIT[m.group(2).lower()] if m else None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_sibling(name: str, when: datetime, existing: list[dict]) -> dict | None:
    """The stored incident (tracker or ZachXBT) this post-mortem is about: same protocol name, the incident dated up to MATCH_WINDOW_DAYS before the post and at most 3 days after it.
    The latest such incident wins (a protocol hit twice in a month matches the one closest to the post)."""
    n = _norm(name)
    best = None
    for e in existing:
        en = _norm(e["name"])
        if not en or not (en == n or (len(n) >= 5 and (n in en or en in n))):
            continue
        age = (when - e["when"]).total_seconds() / 86400
        if -3 <= age <= MATCH_WINDOW_DAYS and (best is None or e["when"] > best["when"]):
            best = e
    return best


def to_payload(a: dict, sibling: dict | None, tracker: dict | None) -> dict:
    name = protocol_name(a)
    amount = tracker["amount_usd"] if tracker and tracker.get("amount_usd") else first_amount(a["excerpt"])
    amount_txt = f" for ${amount:,.0f}" if amount else ""
    return {
        "title": f"{name} post-mortem{amount_txt} (per Rekt)",
        "topic_key": name.strip().lower(),
        "published_at": a["date"].isoformat(),
        "protocol_name": name, "named": True, "amount_usd": amount,
        "classification": tracker.get("classification") if tracker else None, "technique": tracker.get("technique") if tracker else None,
        "chain": tracker.get("chain") if tracker else None, "chains": tracker.get("chains", []) if tracker else [], "target_type": None, "bridge_hack": False,
        "returned_funds": tracker.get("returned_funds") if tracker else None,
        "defillama_id": tracker.get("defillama_id") if tracker else None, "protocol_tvl_usd": tracker.get("protocol_tvl_usd") if tracker else None,
        "protocol_category": tracker.get("protocol_category") if tracker else None,
        "source": "rekt", "source_tier": SOURCE_TIER, "source_url": a["url"], "claim_text": a["excerpt"], "description": a["excerpt"],
        "rekt_title": a["title"], "rekt_tags": a["tags"], "amount_from_tracker": bool(tracker and tracker.get("amount_usd")),
        "followup_of_raw_item_id": sibling["raw_item_id"] if sibling else None,
    }


def load_existing(conn) -> tuple[list[dict], dict[int, dict]]:
    """Every hacks_exploits incident stored in the last 120 days that is not itself a Rekt item, and the tracker payloads by raw id."""
    with dict_cursor(conn) as cur:
        cur.execute("""SELECT id, payload FROM raw_items WHERE category = %s AND COALESCE(payload->>'source', '') <> 'rekt' AND collected_at > now() - interval '120 days'""", (CATEGORY,))
        rows = cur.fetchall()
    existing, payloads = [], {}
    for r in rows:
        p = r["payload"] or {}
        try:
            when = datetime.fromisoformat(p["published_at"])
        except (KeyError, ValueError, TypeError):
            continue
        existing.append({"raw_item_id": r["id"], "name": p.get("protocol_name") or "", "when": when})
        payloads[r["id"]] = p
    return existing, payloads


def operator_has_this_story(conn, raw_item_id: int) -> bool:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT 1 FROM approvals WHERE raw_item_id = %s AND decision IN ('approved', 'edited') LIMIT 1", (raw_item_id,))
        return cur.fetchone() is not None


def fetch_home() -> str:
    r = requests.get(HOME_URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text


def collect(page: str | None = None, now: datetime | None = None) -> int:
    load_dotenv()
    conn = get_conn()
    try:
        with run_log(conn, "collect_rekt") as state:
            now = now or datetime.now(timezone.utc)
            articles = parse_articles(page if page is not None else fetch_home())
            if not articles:
                raise RuntimeError("rekt.news homepage parsed to no posts (the page markup changed, or it did not load)")
            newest_age_d = (now - max(a["date"] for a in articles)).days
            state["details"].update({"posts": len(articles), "newest_age_days": newest_age_d})
            if newest_age_d >= STALE_DAYS:
                raise RuntimeError(f"rekt.news is stale: newest post {newest_age_d} days old")
            existing, payloads = load_existing(conn)
            stats = {"not_postmortem": 0, "too_old": 0, "operator_already_has_it": 0, "unmatched_in_tracker": 0}
            items = []
            for a in articles:
                if not is_postmortem(a):
                    stats["not_postmortem"] += 1
                    continue
                if (now - a["date"]) > timedelta(days=MAX_AGE_DAYS):
                    stats["too_old"] += 1
                    continue
                sibling = match_sibling(protocol_name(a), a["date"], existing)
                if sibling and operator_has_this_story(conn, sibling["raw_item_id"]):
                    stats["operator_already_has_it"] += 1
                    continue
                if not sibling:
                    stats["unmatched_in_tracker"] += 1       # still a candidate: Rekt may know an incident the tracker does not list (Binance, Nov 2026)
                items.append((f"rekt:{a['slug']}", to_payload(a, sibling, payloads.get(sibling["raw_item_id"]) if sibling else None)))
            source_id = get_or_create_source(conn, CATEGORY, SOURCE_NAME, "html", {"url": HOME_URL})
            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items, held=HOLD_ALL_CANDIDATES)
            state["details"].update({**stats, "candidates": len(items), "inserted": inserted})
            logger.info("rekt: posts=%d %s candidates=%d inserted=%d", len(articles), stats, len(items), inserted)
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collect()
    sys.exit(0)
