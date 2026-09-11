"""News collector (Phase 3, 2026-09-11/12) -- Crypto Wall Street, alongside
whale_movements. Deliberate spec deviation, operator-approved: skips
CryptoPanic (Section 5's original source) entirely in favor of a curated
list of original-outlet RSS feeds. No API key, no rate limits, and source
credibility becomes a solved problem by construction -- we chose outlets we
already trust, rather than inheriting whatever an aggregator happens to
surface. Every feed below was fetched and parsed live before being added,
same discipline as RemoteOK/Etherscan/GoPlus/DefiLlama.

FEEDS: CoinDesk, The Block, Decrypt, Blockworks, The Defiant, Protos.
Cointelegraph deliberately left out (operator direction 2026-09-11) -- six
clean sources is the better starting default for a market-summary channel;
revisit once the opinion/sponsored gate below has a track record.
CryptoSlate dropped outright -- returns HTTP 403 regardless of User-Agent,
real bot-blocking, not a fixable UA issue.

Blockworks' feed is ATOM, not RSS (confirmed live -- a raw XML `.//item`
query silently returned zero results before this was caught). feedparser
handles both formats uniformly, which is why it's the parsing library here
rather than hand-rolled XML.

CREDIBILITY (operator direction): checked real category/author data across
all seven candidate feeds before proposing anything. The real, checkable
signal found -- explicit "Opinion"/newsletter-recap categories -- is binary
(is this fresh reporting or not), not a continuous scale; once past that
gate every remaining item is from an already-curated, reputable outlet, so
there's no further real signal to rank "more credible" vs. "less credible"
within that set. So: NOT a scored component. `_is_original_reporting()`
below is a hard pre-score gate (same shape as web3_jobs' relevance gate),
and category_config.yaml's news.score_weights only has three keys
(impact/novelty/actionability) -- credibility's weight is redistributed,
not left dead.

HOLD: every candidate this collector inserts is held=True unconditionally
(HOLD_ALL_CANDIDATES below) -- operator direction 2026-09-11/12: the
Hetzner approval-poller host is still pending verification, defi_yields and
gems_security already have candidates waiting, and a fourth category adding
to an already-unactionable queue isn't wanted. Flip HOLD_ALL_CANDIDATES to
False (or release individually via scripts/hold_candidates.py) once that's
resolved.
"""

import logging
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

CATEGORY = "news"

FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "theblock": "https://www.theblock.co/rss.xml",
    "decrypt": "https://decrypt.co/feed",
    "blockworks": "https://blockworks.co/feed",
    "thedefiant": "https://thedefiant.io/feed",
    "protos": "https://protos.com/feed/",
}

MAX_AGE_HOURS = 48  # no point surfacing news that's already two days stale

# See module docstring. Live-checked category values across all candidate
# feeds 2026-09-11: CoinDesk tags "Opinion" explicitly; Blockworks mixes
# newsletter-recap content ("0xResearch Newsletter", "The Breakdown", etc.)
# into the same feed as fresh reporting. Matched case-insensitively against
# each entry's tags. Maintained like every other known-entity list in this
# codebase -- hand-extend as a real cycle surfaces a category value that
# clearly isn't original reporting but isn't caught here yet.
NOT_ORIGINAL_REPORTING_CATEGORIES = {
    "opinion", "sponsored", "press release", "partner content", "advertorial",
    "0xresearch newsletter", "the breakdown", "forward guidance newsletter",
    "empire newsletter", "lightspeed newsletter",
}

# Same-cycle cross-outlet dedup: two candidates from DIFFERENT sources,
# published within this window, whose fingerprint_overlap (pipeline/entities.py)
# clears this threshold are treated as the same underlying story. First-pass
# calibration, not tuned against real data yet -- deliberately conservative
# (0.5 requires more than ticker-overlap alone; see fingerprint_overlap's
# scoring comment) since wrongly merging two distinct stories is a worse
# failure than posting one avoidable near-duplicate. Revisit once real
# cycles show how often this over- or under-merges.
DEDUP_WINDOW_HOURS = 12
DEDUP_MERGE_THRESHOLD = 0.5

# Operator direction 2026-09-11/12 -- see module docstring's HOLD section.
HOLD_ALL_CANDIDATES = True


def _is_original_reporting(tags: list[str]) -> bool:
    lowered = {t.lower() for t in tags if t}
    return not (lowered & NOT_ORIGINAL_REPORTING_CATEGORIES)


def _entry_datetime(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)


def fetch_entries(source_name: str, url: str) -> list[dict]:
    parsed = feedparser.parse(url)
    if parsed.bozo and not parsed.entries:
        # A real parse failure (not just a lenient warning feedparser
        # tolerated anyway -- see Blockworks' harmless encoding-declaration
        # mismatch, live-verified 2026-09-11) -- skip this source, never let
        # one dead feed block the rest of the cycle (Section 12).
        logger.warning("news: %s failed to parse: %s", source_name, parsed.get("bozo_exception"))
        return []
    return parsed.entries


def collect() -> int:
    load_dotenv()
    conn = get_conn()
    inserted = 0
    try:
        with run_log(conn, "collect_news") as state:
            now = datetime.now(timezone.utc)
            candidates = []  # list of dicts: source_name, entry-derived fields, entities
            fetched_total = 0
            not_original_filtered = 0
            too_old_filtered = 0

            for source_name, url in FEEDS.items():
                try:
                    entries = fetch_entries(source_name, url)
                except Exception:
                    logger.exception("news: failed to fetch %s", source_name)
                    continue
                fetched_total += len(entries)

                for entry in entries:
                    published_at = _entry_datetime(entry)
                    if published_at and (now - published_at).total_seconds() > MAX_AGE_HOURS * 3600:
                        too_old_filtered += 1
                        continue

                    tags = [t.term for t in entry.get("tags", []) if getattr(t, "term", None)]
                    if not _is_original_reporting(tags):
                        not_original_filtered += 1
                        continue

                    title = (entry.get("title") or "").strip()
                    description = (entry.get("summary") or "").strip()
                    link = entry.get("link") or ""
                    external_id = entry.get("id") or link
                    if not title or not link or not external_id:
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
                    })

            state["details"]["fetched"] = fetched_total
            state["details"]["not_original_filtered"] = not_original_filtered
            state["details"]["too_old_filtered"] = too_old_filtered

            # Same-cycle cross-outlet dedup -- greedy clustering: walk
            # candidates in order, absorb any later, still-unassigned
            # candidate from a DIFFERENT source whose fingerprint overlaps
            # enough within the time window. The first (earliest-seen)
            # candidate in a cluster is kept as the representative; the
            # others are dropped from candidacy but their source names are
            # recorded, so a story confirmed across multiple outlets can
            # eventually say so (pipeline/write_post.py).
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

            # Live bug, first real run 2026-09-12: cooldown_hours only
            # suppresses RE-notifying a topic that was ALREADY notified
            # before -- it does nothing for many new candidates sharing a
            # topic_key that all show up TOGETHER in one cycle's first
            # pass, and the cross-source merge above doesn't catch this
            # either (it only compares DIFFERENT sources, by design, to
            # catch multi-outlet coverage -- not one outlet republishing its
            # own live-updating article). Confirmed live: CoinDesk alone
            # produced 19 separate "Bitcoin near $77k" items sharing
            # topic_key=BTC in a single cycle (a live-updating price
            # article getting re-emitted on every price tick). Capped here,
            # per topic_key, per cycle -- keeps the freshest few rather than
            # collapsing to one, since topic_key is deliberately coarse
            # (pipeline/entities.py's primary_entity docstring: "same
            # primary subject", not "same specific event") and two
            # genuinely different stories can legitimately share one.
            NEWS_PER_TOPIC_CYCLE_CAP = 2
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
                if topic_key in topic_counts and topic_counts[topic_key] >= NEWS_PER_TOPIC_CYCLE_CAP:
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
                }
                external_id = f"{c['source_name']}:{c['external_id']}"
                items.append((external_id, payload))

            source_id = get_or_create_source(conn, CATEGORY, "curated_rss", "rss", {"feeds": list(FEEDS.keys())})
            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items, held=HOLD_ALL_CANDIDATES)
            state["details"]["candidates"] = len(items)
            state["details"]["inserted"] = inserted
            state["details"]["held"] = HOLD_ALL_CANDIDATES

            logger.info(
                "news: fetched=%d not_original_filtered=%d too_old_filtered=%d merged_away=%d "
                "candidates=%d inserted=%d",
                fetched_total, not_original_filtered, too_old_filtered, merged_away,
                len(items), inserted,
            )
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = collect()
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
