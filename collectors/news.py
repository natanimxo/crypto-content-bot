"""News collector (Phase 3, 2026-09-11/12) -- Crypto Wall Street, alongside
whale_movements. Deliberate spec deviation, operator-approved: skips
CryptoPanic (Section 5's original source) entirely in favor of a curated
list of original-outlet RSS feeds. No API key, no rate limits, and source
credibility becomes a solved problem by construction -- we chose outlets we
already trust, rather than inheriting whatever an aggregator happens to
surface. Every feed below was fetched and parsed live before being added,
same discipline as RemoteOK/Etherscan/GoPlus/DefiLlama.

FEEDS: CoinDesk, The Block, Decrypt, Unchained, The Defiant, Protos.
(Blockworks was the sixth until 2026-11-14: its feed still answers 200 and parses
cleanly, but its newest item is 311 days old -- frozen, not moved: blockworks.co now
redirects to blockworks.com/feed, same content. Nothing in the collector flagged it,
because an empty-after-the-age-filter feed is indistinguishable from a quiet one.
Replacement chosen from live checks 2026-11-14, see BACKLOG.md: Unchained.)
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

HOLD: HOLD_ALL_CANDIDATES flipped to False 2026-09-15 (operator direction,
applied identically across every category) -- the always-on Railway poller
is now verified healthy, which was the entire reason every collector held
unconditionally since 2026-09-11/12. New candidates flow to notify.py
normally from here on; the existing held backlog is untouched and worked
through manually via scripts/hold_candidates.py.
"""

import html
import logging
import re
import sys
from datetime import datetime, timezone
from time import mktime

import feedparser
from dotenv import load_dotenv

from pipeline import entities as entity_lib
from pipeline import llm_usage, tg_preview
from pipeline.channel_router import assign_channels_to_new_items
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
    # Replaces "blockworks" (frozen since ~Jan 2026, 311 days). Unchained: original reporting,
    # 9 of its 10 items in the last 48h were stories none of the other five feeds had, every
    # item passes the opinion gate, robots.txt allows the feed. NOT Cointelegraph: it is the
    # larger and fresher feed (18 of 21 items new) but was deliberately left out by operator
    # direction 2026-09-11 ("revisit once the gate has a track record") -- that call is the
    # operator's to reverse, see BACKLOG.md.
    "unchained": "https://unchainedcrypto.com/feed/",
    "thedefiant": "https://thedefiant.io/feed",
    "protos": "https://protos.com/feed/",
}

# Telegram channels used as news sources (operator 2026-11-16), through the SAME pipeline as the RSS feeds: the cross-outlet dedup below, the
# opinion / sponsored gate (text-based here: a Telegram post has no category tags), and the copyright guard at write time.
# tier: Cointelegraph's channel is the OFFICIAL account of the outlet (2); Wu Blockchain is an individual's news brand (3).
# COINTELEGRAPH REVERSES THE SEPTEMBER EXCLUSION -- a deliberate operator change (2026-09-11 "left out ... revisit once the gate has a track record" ->
# 2026-11-16 "reverses my September exclusion"), recorded in BACKLOG.md. It enters through the Telegram channel, not its RSS feed.
TELEGRAM_NEWS = {
    "wublockchain": {"handle": "wublockchainenglish", "tier": 3},
    "cointelegraph": {"handle": "cointelegraph", "tier": 2},
}

MAX_AGE_HOURS = 48  # no point surfacing news that's already two days stale
STALE_FEED_DAYS = 7  # a feed whose newest item is older than this is dead, not quiet

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

# Flipped to False 2026-09-15 -- see collectors/defi_yields.py's comment
# (same operator direction, applied identically across every category):
# the always-on Railway poller is now verified healthy, which was the
# entire reason every collector held unconditionally. Existing held rows
# are untouched by this; only new candidates from here on are affected.
HOLD_ALL_CANDIDATES = False


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# WordPress feeds (Unchained, Protos) append a "The post X appeared first on Y." line to the excerpt.
_APPEARED_FIRST_RE = re.compile(r"\s*The post .{0,300}? appeared first on .{0,120}?\.?\s*$", re.IGNORECASE | re.DOTALL)


def _clean_description(raw: str) -> str:
    """Plain text from a feed summary. Found 2026-11-14 while adding Unchained: its summaries (and
    Protos', already stored that way) are HTML -- an <img> tag with alt text and inline styles --
    which then flows into the write prompt, the reproduction guard's source text and entity
    extraction. CoinDesk/The Block/Decrypt/The Defiant summaries have no markup, so this is a
    no-op for them."""
    text = html.unescape(_TAG_RE.sub(" ", raw or ""))
    text = _WS_RE.sub(" ", text).strip()
    return _APPEARED_FIRST_RE.sub("", text).strip()


def _is_original_reporting(tags: list[str]) -> bool:
    lowered = {t.lower() for t in tags if t}
    return not (lowered & NOT_ORIGINAL_REPORTING_CATEGORIES)


# A Telegram post has no category tags, so "is this original reporting" is decided on the TEXT: advertising, promotion, video clips and recurring
# round-ups are not news. Deliberately conservative -- a wrongly dropped post is one story; a wrongly kept advert is on the channel.
_TG_NOT_NEWS_RE = re.compile(
    r"^\W*(?:highlight clip|sponsored|advertis|promoted|partner(?:ed)? content|#ad\b)|\bjoin (?:@|our|the) |\buse (?:my |our )?code\b|sign up (?:now|today)|click here|"
    r"presented by|\bpromo code\b|\bgiveaway\b|bitcoin_priceupdatebot|\bsubscribe to\b|\bweekly top ?10\b", re.IGNORECASE)
_TG_BANNER_RE = re.compile(r"^(?:[^\w$#]*)(?:(?:JUST IN|BREAKING|NEW|BIG|UPDATE|TODAY|REKT|ALERT|NEWS|DEVELOPING)\s*:?\s*)+", re.IGNORECASE)
_TG_NAV_RE = re.compile(r"\s*(?:News\s*\|\s*Markets\s*\|\s*YouTube|Read more|Read Analysis)\s*$", re.IGNORECASE)


def is_telegram_news(text: str) -> bool:
    return bool(text and len(text) >= 40 and not _TG_NOT_NEWS_RE.search(text))


def telegram_title_and_description(text: str) -> tuple[str, str]:
    """(headline, description) from a post: a multi-line post's first line is its headline (Wu Blockchain); a one-paragraph post (Cointelegraph) uses its first
    sentence with the 'JUST IN:' style banner removed. The description is the whole text without the channel's navigation links."""
    clean = _TG_NAV_RE.sub("", (text or "").strip())
    lines = [l.strip() for l in clean.split("\n") if l.strip()]
    first = lines[0] if lines else ""
    first = _TG_BANNER_RE.sub("", first).strip()
    if len(lines) == 1 or len(first) > 160:
        m = re.match(r"(.{20,160}?[.!?])(?:\s|$)", first)
        title = (m.group(1) if m else first[:140]).strip()
    else:
        title = first
    return title, _clean_description(re.sub(r"\s+", " ", clean))


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
            stale_feeds: dict[str, int] = {}
            not_original_filtered = 0
            too_old_filtered = 0

            for source_name, url in FEEDS.items():
                try:
                    entries = fetch_entries(source_name, url)
                except Exception:
                    logger.exception("news: failed to fetch %s", source_name)
                    continue
                fetched_total += len(entries)

                # DEAD-FEED DETECTION (2026-11-14): Blockworks answered 200, parsed cleanly and
                # returned 50 entries for 311 days while every one of them aged out at the 48h
                # filter below -- indistinguishable, in this collector's own numbers, from a quiet
                # feed. A feed whose NEWEST entry is older than STALE_FEED_DAYS is dead: recorded in
                # run_logs and the operator is told (once a week per feed).
                newest = max((d for d in (_entry_datetime(e) for e in entries) if d), default=None)
                if entries and newest and (now - newest).days >= STALE_FEED_DAYS:
                    stale_feeds[source_name] = (now - newest).days
                    logger.error("news: feed %s is STALE -- newest item is %d days old", source_name,
                                 (now - newest).days)
                    llm_usage._send_anomaly_alert(
                        conn, "feed_stale", source_name,
                        f"📰 <b>News feed gone quiet: {source_name}</b>\nIts newest item is {(now - newest).days} "
                        f"days old (alert at {STALE_FEED_DAYS}). It still responds, so nothing else would flag it "
                        f"-- replace or remove it in collectors/news.py FEEDS.",
                        now, 7 * 24 * 60)

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
                    description = _clean_description(entry.get("summary") or "")
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

            # ---- Telegram channels (same candidate shape as an RSS entry)
            tg_stats = {}
            for source_name, cfg in TELEGRAM_NEWS.items():
                stats = tg_stats[source_name] = {"posts": 0, "not_news": 0, "too_old": 0, "kept": 0}
                try:
                    posts = tg_preview.fetch_posts(cfg["handle"])
                except Exception as exc:
                    logger.warning("news: telegram %s failed: %s", source_name, exc)
                    stats["error"] = str(exc)[:100]
                    continue
                stats["posts"] = len(posts)
                fetched_total += len(posts)
                newest = posts[-1]["time"]
                if (now - newest).days >= STALE_FEED_DAYS:
                    stale_feeds[source_name] = (now - newest).days
                    llm_usage._send_anomaly_alert(
                        conn, "feed_stale", source_name,
                        f"📰 <b>News channel gone quiet: {source_name}</b>\nIts newest post is {(now - newest).days} days old (alert at {STALE_FEED_DAYS}) -- "
                        f"replace or remove it in collectors/news.py TELEGRAM_NEWS.", now, 7 * 24 * 60)
                for post in posts:
                    if (now - post["time"]).total_seconds() > MAX_AGE_HOURS * 3600:
                        stats["too_old"] += 1
                        continue
                    if not is_telegram_news(post["text"]):
                        stats["not_news"] += 1
                        continue
                    title, description = telegram_title_and_description(post["text"])
                    if not title:
                        continue
                    stats["kept"] += 1
                    candidates.append({
                        "source_name": source_name, "external_id": str(post["id"]), "title": title, "description": description,
                        "link": f"https://t.me/{post['post']}", "author": "", "tags": [], "published_at": post["time"],
                        "entities": entity_lib.extract_entities(title, description), "tier": cfg["tier"],
                    })
            state["details"]["telegram"] = tg_stats

            state["details"]["fetched"] = fetched_total
            state["details"]["stale_feeds"] = stale_feeds
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
                    # is_duplicate_story, not fingerprint_overlap directly (2026-09-16 fix):
                    # an exact title match OR fingerprint overlap, either sufficient --
                    # see pipeline/entities.py's docstring for the live bug this closes
                    # (a byte-identical headline from two outlets scored fingerprint_
                    # overlap=0.0 because neither side had a ticker/figure to trip the
                    # gate on, which phrase-based matching alone would never catch since
                    # titles are deliberately excluded from phrase extraction).
                    if entity_lib.is_duplicate_story(c["entities"], c["title"], other["entities"], other["title"],
                                                       DEDUP_MERGE_THRESHOLD):
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
                if c.get("tier"):
                    payload["source_tier"] = c["tier"]         # a Telegram channel: 2 = the outlet's own official account, 3 = anyone else (labelled, not scored)
                external_id = f"{c['source_name']}:{c['external_id']}"
                items.append((external_id, payload))

            source_id = get_or_create_source(conn, CATEGORY, "curated_rss", "rss", {"feeds": list(FEEDS.keys())})
            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items, held=HOLD_ALL_CANDIDATES)
            state["details"]["candidates"] = len(items)
            state["details"]["inserted"] = inserted
            state["details"]["held"] = HOLD_ALL_CANDIDATES

            # Shared across crypto_wall_street and alpha_edge_crypto since
            # 2026-09-30 (operator direction) -- news clears 35+/day against a
            # single channel's 10/day cap, real volume going unused while
            # Alpha Edge had no news of its own. Same round-robin mechanism as
            # web3_jobs (pipeline/channel_router.py); no-op before that date's
            # config change (SHARED_CATEGORY_CHANNELS lookup returns None for
            # a category that isn't listed there).
            assigned = assign_channels_to_new_items(conn, CATEGORY)
            state["details"]["channel_assigned"] = assigned

            logger.info(
                "news: fetched=%d not_original_filtered=%d too_old_filtered=%d merged_away=%d "
                "candidates=%d inserted=%d channel_assigned=%d",
                fetched_total, not_original_filtered, too_old_filtered, merged_away,
                len(items), inserted, assigned,
            )
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = collect()
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
