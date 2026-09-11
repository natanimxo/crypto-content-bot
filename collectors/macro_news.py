"""Macro news collector (Phase 3, 2026-09-12) -- Hustle to Million, alongside
tool_launches/startup_jobs. Reuses news.py's entire infrastructure unchanged
(operator direction): entity fingerprinting (pipeline/entities.py), same-cycle
cross-outlet dedup, the opinion/sponsored gate shape, per-topic cycle cap. What
changes is the feed list, the relevance gate, and (in pipeline/write_post.py)
the editorial brief and the write prompt.

EDITORIAL PREMISE (operator direction, deliberately narrow): this is NOT
"business news" and NOT a general news feed. It's macro developments that
plausibly affect a reader's economic situation -- major geopolitical events,
AI capability/industry shifts, significant regulation and policy changes, big
monetary/economic moves. Not company funding rounds (tool_launches already
covers the builder-facing side), not general tech commentary, not personal
finance advice, not individual stock-picking. The relevance bar below is
deliberately high -- a cycle with zero qualifying candidates is the expected,
correct outcome on a quiet news day, not a bug.

FEEDS: every candidate was fetched, parsed, and inspected for REAL item
structure (title/tags/description content, not just HTTP 200) before being
added or dropped -- same discipline as news.py's RSS feeds. Full reasoning
per feed:

  KEPT:
  - BBC World, BBC Business: no tags at all on either feed (live-checked) --
    real geopolitical/rate/inflation signal exists but is mixed with
    celebrity/crime/royal-family noise; the keyword gate below does all the
    filtering work here.
  - NPR Economy: strong econ-desk signal (Fed decisions, tariffs, trade war,
    oil prices). NPR Business dropped as redundant -- live-checked 2026-09-12,
    its top stories were identical to Economy's.
  - CNBC Economy (id=20910258, NOT the general "Top News" feed): live-checked
    content is CPI/Fed-hike-odds/import-bans, nearly 100% on-brief. The
    general CNBC feed was dropped -- it's dominated by individual stock-pick
    commentary ("Dell stock jumps on RBC initiation"), exactly what this
    category excludes.
  - Federal Reserve press releases: live-checked tag distribution across 20
    real releases -- 3/20 tagged "Monetary Policy" (FOMC minutes/statements,
    discount-rate minutes) are exactly on-brief; the other 17/20 are
    "Enforcement Actions" (single-bank penalties) or "Orders on Banking
    Applications" (single-bank M&A approvals) -- administrivia, not macro
    policy. Gated on the "Monetary Policy" tag alone, not the whole feed.
  - Axios: real hits (diesel-price inflation, an AI-warning-to-Congress
    story) mixed with pure politics/crime noise. No dedicated macro/business
    section feed is reachable -- every api.axios.com/feed/{business,markets,
    macro,ai-plus,...} guess 404'd live. Kept as the general feed; the
    keyword gate does the filtering.
  - Ars Technica, The Verge, Wired: weakest density of the set but the only
    real AI-capability/tech-policy coverage available. Each carries genuine
    real tags (unlike BBC/NPR/CNBC/Axios) -- live-checked distributions:
    Ars Technica's "Policy" tag caught a coal-plant-ruling story and an
    Oracle/Stargate energy-policy story alongside pure Science/Space/Health
    noise; The Verge's "AI"/"Policy" tags caught real AI-regulation coverage;
    Wired caught a federal-investigation story buried in mostly Culture/TV
    content. Tags are used as a cheap pre-filter (drop untagged-as-relevant
    items before running keyword matching at all), not a substitute for the
    keyword gate -- a Policy tag alone still isn't specific enough (see
    _passes_tag_prefilter's docstring).

  DROPPED, WITH LIVE EVIDENCE:
  - MarketWatch: live content is almost entirely individual-stock investment
    advice ("this investment pays 4.7%", "should I cash out my inherited
    IRA") -- advice content, not macro news, and not something this category
    or any other in this system covers.
  - TechCrunch (main feed AND /category/artificial-intelligence/, both
    checked live): dominated by funding rounds and conference promotion --
    exactly what "not company funding rounds" excludes.
  - Investing.com: live content is executive stock-sale SEC filings, not
    news.
  - Yahoo Finance: general stock-picking/consumer-finance clickbait.
  - Reuters, AP, Politico: blocked (403/404) across every URL tried,
    including an AP mirror via rsshub.app -- same "documented unreachable,
    not silently skipped" treatment as CryptoPanic/Product Hunt.

POLITICAL NEUTRALITY: this collector does no neutrality enforcement itself --
that's a write-time concern (pipeline/write_post.py's
_find_neutrality_violations, checked against what the LLM actually writes).
Nothing here should be read as a claim that source SELECTION makes this
category neutral; see that function's docstring for what the check does and
does NOT catch.
"""

import logging
import re
import sys
from datetime import datetime, timezone
from time import mktime

import feedparser
from dotenv import load_dotenv

from pipeline import entities as entity_lib
from pipeline.db import get_conn
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CATEGORY = "macro_news"

FEEDS = {
    "bbc_world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "bbc_business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "npr_economy": "https://feeds.npr.org/1017/rss.xml",
    "cnbc_economy": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    "federal_reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "axios": "https://api.axios.com/feed/",
    "ars_technica": "https://feeds.arstechnica.com/arstechnica/index",
    "the_verge": "https://www.theverge.com/rss/index.xml",
    "wired": "https://www.wired.com/feed/rss",
}

MAX_AGE_HOURS = 48

# Federal Reserve's own tag taxonomy (live-checked, see module docstring) --
# only this tag is treated as on-brief; the feed's other real tag values
# ("Enforcement Actions", "Orders on Banking Applications", "Banking and
# Consumer Regulatory Policy") are single-institution administrivia, not
# macro policy. Feeds that carry no tags at all (BBC/NPR/CNBC/Axios) skip
# this pre-filter entirely and go straight to the keyword gate below.
FEED_TAG_ALLOWLIST = {
    "federal_reserve": {"monetary policy"},
    # Ars Technica / The Verge / Wired: cheap pre-filter only -- drops
    # obviously-irrelevant tagged content (Culture, TV, Streaming, Gaming,
    # Space, pure Science, Health) before it ever reaches the keyword gate.
    # NOT a substitute for that gate -- a "Policy" or "AI" tag is necessary
    # but nowhere near sufficient (see _passes_tag_prefilter's docstring):
    # plenty of real Policy/AI-tagged stories (an FCC/media dispute, a
    # bankruptcy-data-sale privacy suit, a single-lawyer sanctions case)
    # still correctly fail the keyword gate for not being macro-economic.
    "ars_technica": {"policy", "ai", "business", "economy", "energy"},
    "the_verge": {"policy", "ai", "business", "economy"},
    "wired": {"policy", "ai", "business", "economy", "security"},
}

# Keyword taxonomy -- the actual relevance bar (operator direction: "propose
# what makes something qualify... be willing to make the bar high enough
# that quiet days are normal"). Each bucket maps to one thing named in the
# brief; deliberately does NOT include general tech/AI-product coverage or
# company-level funding news (tool_launches' territory) or bare "war"/
# "conflict" (too broad on its own -- see _CONFLICT_RE below, gated on
# co-occurring economic-spillover language instead of firing alone).
_RATES_RE = re.compile(
    r"\b(federal reserve|the fed\b|fomc|interest rates?|rate hikes?|rate cuts?|"
    r"monetary policy|discount rate|central banks?|\becb\b|bank of england|bank of japan)\b",
    re.IGNORECASE,
)
_INFLATION_RE = re.compile(
    r"\b(inflation|\bcpi\b|\bppi\b|consumer price index|producer price index|cost of living)\b",
    re.IGNORECASE,
)
_TRADE_POLICY_RE = re.compile(
    r"\b(tariffs?|trade wars?|sanctions?|export bans?|export controls?|import bans?)\b",
    re.IGNORECASE,
)
_CONFLICT_RE = re.compile(
    r"\b(war|invasion|military strikes?|air ?strikes?|drone strikes?|ceasefire|conflict|"
    r"offensive|attacks?|seized|strikes?)\b",
    re.IGNORECASE,
)
_ECON_SPILLOVER_RE = re.compile(
    r"\b(oil prices?|crude oil|\bopec\b|gas prices?|diesel prices?|energy suppl(?:y|ies)|"
    r"energy costs?|shipping lanes?|supply chains?|markets?|red sea|strait of hormuz|suez canal)\b",
    re.IGNORECASE,
)
_AI_POLICY_RE = re.compile(
    r"\b(ai regulation|artificial intelligence regulation|ai executive order|"
    r"export controls? on (?:ai|chips|semiconductors)|chip export|ai safety (?:law|regulation|policy)|"
    r"frontier model)\b",
    re.IGNORECASE,
)
_REGULATORY_RE = re.compile(
    r"\b(antitrust|doj lawsuit|ftc lawsuit|sec (?:charges|lawsuit|enforcement)|\bcftc\b|"
    r"regulatory crackdown|landmark ruling|supreme court ruling|investigations?|regulations?)\b",
    re.IGNORECASE,
)


def _matched_taxonomy_buckets(text: str) -> list[str]:
    """Which taxonomy bucket(s) this title cleared -- stored on the payload
    (see collect() below) so pipeline/score.py's impact component can weight
    a Fed rate decision (broad, universal reach) differently from a single
    regulatory action (real, but narrower) without re-running these regexes
    at scoring time. Order matters only for readability; a title can clear
    more than one bucket."""
    buckets = []
    if _RATES_RE.search(text):
        buckets.append("rates")
    if _INFLATION_RE.search(text):
        buckets.append("inflation")
    if _TRADE_POLICY_RE.search(text):
        buckets.append("trade_policy")
    if _CONFLICT_RE.search(text) and _ECON_SPILLOVER_RE.search(text):
        buckets.append("conflict_spillover")
    if _AI_POLICY_RE.search(text):
        buckets.append("ai_policy")
    if _REGULATORY_RE.search(text):
        buckets.append("regulatory")
    return buckets


def _matches_macro_taxonomy(text: str) -> bool:
    return bool(_matched_taxonomy_buckets(text))


# Exclude patterns -- checked BEFORE the taxonomy gate, and win regardless of
# a taxonomy match, since these are the specific noise shapes live-verified
# in the candidate feeds (BBC Business's "I asked my husband to pay into my
# pension" personal-finance column; CNBC/MarketWatch-style individual stock
# picks that could otherwise slip through by incidentally naming the Fed or
# a tariff in passing).
_PERSONAL_FINANCE_RE = re.compile(
    r"\b(should i|can (?:we|i) cash out|my (?:husband|wife|siblings?|parents?|mother|father)|"
    r"inherited (?:an? )?ira|dear (?:abby|penny))\b",
    re.IGNORECASE,
)
_STOCK_PICKING_RE = re.compile(
    r"\b(stocks? (?:jumps?|rises?|falls?|surges?|slides?)\b|shares? (?:jump|rise|fall|surge)|"
    r"stocks making the biggest moves|price targets?|buy ratings?|sell ratings?|"
    r"stock up nearly|now up nearly)\b",
    re.IGNORECASE,
)


def _is_excluded(title: str) -> bool:
    if title.strip().startswith(("I ", "I've", "I'm")):
        return True
    return bool(_PERSONAL_FINANCE_RE.search(title) or _STOCK_PICKING_RE.search(title))


def _passes_tag_prefilter(source_name: str, tags: list[str]) -> bool:
    """Cheap pre-filter only -- see FEED_TAG_ALLOWLIST's comment. A feed with
    no tags at all (BBC/NPR/CNBC/Axios) always passes this stage; a feed
    with a tag allowlist configured must have at least one tag intersect it.
    This exists purely to avoid running keyword matching against Wired's
    Culture/TV/Streaming firehose, not to decide relevance on its own."""
    allowlist = FEED_TAG_ALLOWLIST.get(source_name)
    if not allowlist or not tags:
        return True
    lowered = {t.lower() for t in tags if t}
    return bool(lowered & allowlist)


def _is_relevant(source_name: str, title: str, tags: list[str]) -> bool:
    """Matched against TITLE ONLY, deliberately -- live bug, first real check
    2026-09-12: several feeds' `description` field is full article body text,
    not a short teaser (Axios ran 1000-3400+ characters per item, live-
    measured), and matching the taxonomy/exclude regexes against that much
    running text produced real false positives purely from incidental body
    mentions -- "Vance: MAGA's millennial messenger for the midterms" and
    "Sports' grip on America sparks summer of political warfare" both
    cleared the gate only because their long bodies happened to say "war"
    or "trade" somewhere, not because the STORY was about either. A
    headline is already what a wire editor judged worth leading with; if
    the macro angle isn't there, the body text having it in passing isn't
    the same thing. (Different reasoning from, but the same instinct as,
    pipeline/entities.py's title-vs-description split -- know which field
    is reliable for which purpose.)"""
    if _is_excluded(title):
        return False
    if not _passes_tag_prefilter(source_name, tags):
        return False
    return _matches_macro_taxonomy(title)


# Same-cycle cross-outlet dedup -- identical mechanism/threshold to news.py's
# (pipeline/entities.py's fingerprint_overlap), reused unchanged per operator
# direction. Not re-tuned for this category yet -- no real-cycle evidence
# either way; revisit alongside news.py's own DEDUP_MERGE_THRESHOLD if this
# category's real cycles show it over- or under-merging.
DEDUP_WINDOW_HOURS = 12
DEDUP_MERGE_THRESHOLD = 0.5

# Same reasoning as news.py's NEWS_PER_TOPIC_CYCLE_CAP -- a live-updating
# wire story (e.g. a developing Fed-decision article) re-emitted on every
# update would otherwise fill a cycle's per-topic slots on its own.
MACRO_NEWS_PER_TOPIC_CYCLE_CAP = 2

# Operator direction 2026-09-12 -- hold everything until the Hetzner
# approval-poller host is verified, same as every other collector right now.
HOLD_ALL_CANDIDATES = True


def _entry_datetime(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)


def fetch_entries(source_name: str, url: str) -> list[dict]:
    parsed = feedparser.parse(url)
    if parsed.bozo and not parsed.entries:
        logger.warning("macro_news: %s failed to parse: %s", source_name, parsed.get("bozo_exception"))
        return []
    return parsed.entries


def collect() -> int:
    load_dotenv()
    conn = get_conn()
    inserted = 0
    try:
        with run_log(conn, "collect_macro_news") as state:
            now = datetime.now(timezone.utc)
            candidates = []
            fetched_total = 0
            too_old_filtered = 0
            not_relevant_filtered = 0

            for source_name, url in FEEDS.items():
                try:
                    entries = fetch_entries(source_name, url)
                except Exception:
                    logger.exception("macro_news: failed to fetch %s", source_name)
                    continue
                fetched_total += len(entries)

                for entry in entries:
                    published_at = _entry_datetime(entry)
                    if published_at and (now - published_at).total_seconds() > MAX_AGE_HOURS * 3600:
                        too_old_filtered += 1
                        continue

                    title = (entry.get("title") or "").strip()
                    description = (entry.get("summary") or "").strip()
                    link = entry.get("link") or ""
                    external_id = entry.get("id") or link
                    if not title or not link or not external_id:
                        continue

                    tags = [t.term for t in entry.get("tags", []) if getattr(t, "term", None)]
                    if not _is_relevant(source_name, title, tags):
                        not_relevant_filtered += 1
                        continue

                    ent = entity_lib.extract_entities(title, description)
                    candidates.append({
                        "source_name": source_name,
                        "external_id": external_id,
                        "title": title,
                        "description": description,
                        "link": link,
                        "author": entry.get("author") or "",
                        "tags": tags,
                        "published_at": published_at,
                        "entities": ent,
                        "matched_buckets": _matched_taxonomy_buckets(title),
                    })

            state["details"]["fetched"] = fetched_total
            state["details"]["too_old_filtered"] = too_old_filtered
            state["details"]["not_relevant_filtered"] = not_relevant_filtered

            # Same-cycle cross-outlet dedup -- identical to news.py, see that
            # module's comment for the full greedy-clustering rationale.
            merged_away = 0
            assigned = [False] * len(candidates)
            for i, c in enumerate(candidates):
                if assigned[i]:
                    continue
                c["also_covered_by"] = []
                for j in range(i + 1, len(candidates)):
                    if assigned[j] or candidates[j]["source_name"] == c["source_name"]:
                        continue
                    other = candidates[j]
                    if c["published_at"] and other["published_at"]:
                        gap_hours = abs((c["published_at"] - other["published_at"]).total_seconds()) / 3600
                        if gap_hours > DEDUP_WINDOW_HOURS:
                            continue
                    overlap = entity_lib.fingerprint_overlap(c["entities"], other["entities"])
                    if overlap >= DEDUP_MERGE_THRESHOLD:
                        assigned[j] = True
                        merged_away += 1
                        c["also_covered_by"].append(other["source_name"])
            state["details"]["merged_away"] = merged_away

            # Per-topic-per-cycle cap -- identical mechanism to news.py.
            topic_counts: dict[str, int] = {}
            candidates_by_recency = sorted(
                (c for i, c in enumerate(candidates) if not assigned[i]),
                key=lambda c: c["published_at"] or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            topic_capped = 0
            kept_candidates = []
            for c in candidates_by_recency:
                ent = c["entities"]
                topic_key = entity_lib.primary_entity(ent) or c["external_id"]
                c["topic_key"] = topic_key
                if topic_key in topic_counts and topic_counts[topic_key] >= MACRO_NEWS_PER_TOPIC_CYCLE_CAP:
                    topic_capped += 1
                    continue
                topic_counts[topic_key] = topic_counts.get(topic_key, 0) + 1
                kept_candidates.append(c)
            state["details"]["topic_capped"] = topic_capped

            items = []
            for c in kept_candidates:
                ent = c["entities"]
                payload = {
                    "title": c["title"],
                    "topic_key": c["topic_key"],
                    "source": c["source_name"],
                    "link": c["link"],
                    "description": c["description"],
                    "author": c["author"],
                    "tags": c["tags"],
                    "published_at": c["published_at"].isoformat() if c["published_at"] else None,
                    "tickers": sorted(ent["tickers"]),
                    "phrases": sorted(ent["phrases"]),
                    "figures": sorted(ent["figures"]),
                    "also_covered_by": c["also_covered_by"],
                    "matched_buckets": c["matched_buckets"],
                }
                external_id = f"{c['source_name']}:{c['external_id']}"
                items.append((external_id, payload))

            source_id = get_or_create_source(conn, CATEGORY, "curated_rss", "rss", {"feeds": list(FEEDS.keys())})
            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items, held=HOLD_ALL_CANDIDATES)
            state["details"]["candidates"] = len(items)
            state["details"]["inserted"] = inserted
            state["details"]["held"] = HOLD_ALL_CANDIDATES

            logger.info(
                "macro_news: fetched=%d too_old_filtered=%d not_relevant_filtered=%d merged_away=%d "
                "topic_capped=%d candidates=%d inserted=%d",
                fetched_total, too_old_filtered, not_relevant_filtered, merged_away,
                topic_capped, len(items), inserted,
            )
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = collect()
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
