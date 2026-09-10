"""Web3 jobs collector — RemoteOK's free, keyless JSON feed (Phase 2,
2026-09-10). Third category, first one shared across two channels (Alpha
Edge Crypto / CoinCraft, alternation via pipeline/channel_router.py).

Verified live before building against it: `?tags=crypto` returns real,
current web3/crypto listings with the expected fields (company, position,
tags, salary_min/max, location, apply_url) — no auth needed.

No dollar-value noise floor here (unlike defi_yields/whale_movements) —
every listing that has the basic required fields gets collected; scoring
(pipeline/score.py) does the real quality filtering.
"""

import html as html_lib
import logging
import re
import sys

from dotenv import load_dotenv

from pipeline.channel_router import assign_channels_to_new_items
from pipeline.db import get_conn
from pipeline.http import get_json
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CATEGORY = "web3_jobs"
SOURCE_NAME = "remoteok_crypto"
API_URL = "https://remoteok.com/api"
# RemoteOK blocks the default requests User-Agent on some setups -- live-
# verified this UA works.
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _fix_mojibake(value):
    """RemoteOK has a server-side encoding bug (live-confirmed 2026-09-10 on
    a real listing's `location` field): non-ASCII text comes back with each
    UTF-8 byte reinterpreted as its own Latin-1 codepoint before JSON-
    escaping — e.g. Arabic 'م' (2 UTF-8 bytes) arrives as two separate
    mojibake characters instead of one real one. The fix (re-encode as
    Latin-1 to recover the original bytes, decode as UTF-8) is applied
    unconditionally to every string field pulled from this API, but is safe
    to run on already-correct text too: pure ASCII round-trips unchanged,
    and genuinely-correct non-ASCII text either round-trips unchanged or
    fails the Latin-1 encode/UTF-8 decode step outright (codepoints beyond
    Latin-1's 0-255 range, or an incomplete multi-byte sequence) — in which
    case this returns the original value rather than risk corrupting it.
    """
    if not isinstance(value, str):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


# Relevance gate (operator direction 2026-09-10, live-confirmed: ~70% of a
# real 56-listing collection were generic corporate roles at non-crypto
# companies — Little Caesars Pizza, The Walt Disney Company, Nestlé Health
# Science, DSW Designer Shoe Warehouse — swept in by RemoteOK's own tag
# system, which some listings had 40-58 tags on, "crypto" just one of them).
# A higher score threshold would filter by SCORE, not relevance — a listing
# can score well on salary/logo/location while still being a pizza-chain
# regional-director role that happens to carry a crypto tag. This is a
# separate, binary gate applied BEFORE scoring, same architectural role as
# defi_yields' MIN_TVL_USD / whale_movements' collect_min_usd.
#
# A pure "how many tags" heuristic isn't reliable either — live-confirmed
# some genuinely relevant companies (Crypto.com, Blockchain.com, Injective)
# also carry high tag counts, so tag bloat alone doesn't separate signal
# from noise. The real signal is COMPANY IDENTITY, with the job TITLE itself
# (not RemoteOK's tags) as the fallback for companies not on this list --
# same "known-entity list + self-maintaining-ish fallback" shape as
# whale_movements' KNOWN_EXCHANGE_ADDRESSES + INSTITUTIONAL_SENT_TX_THRESHOLD,
# though here the fallback is a keyword check, not a numeric heuristic, since
# there's no equivalent on-chain signal for a job listing.
#
# Maintenance: hand-extend this list as clearly-crypto companies show up
# filtered out that shouldn't be -- same spirit as whale_movements' watchlist,
# just lower-stakes (a missed company means one listing's classification is
# imperfect, not a functional/financial error), so no address-style
# verification rigor is needed, just common-knowledge judgment.
KNOWN_WEB3_COMPANIES = {
    "bybit", "crypto.com", "blockchain.com", "binance", "coinbase", "kraken",
    "okx", "kucoin", "bitfinex", "gate.io", "htx", "moonpay", "injective",
    "exodus", "bitmex", "consensys", "chainlink", "uniswap", "opensea",
    "gemini", "rockawayx", "kast", "coinme", "ledger", "metamask", "circle",
    "tether", "solana", "polygon", "avalanche", "aave", "compound",
    "chainalysis", "alchemy", "infura", "the graph", "arbitrum", "optimism",
    "worldcoin", "ripple", "chainlink labs", "paxos", "anchorage", "fireblocks",
}

WEB3_TITLE_KEYWORDS = {
    "crypto", "web3", "web 3", "blockchain", "defi", "bitcoin", "ethereum",
    "solidity", "smart contract", "nft", "dao", "token", "on-chain", "onchain",
    "digital asset", "cryptocurrency",
}

# Genuine location-restriction signal for scoring's actionability component
# (pipeline/score.py::score_web3_jobs) -- operator direction 2026-09-11,
# fixing a logic error: the scorer used to treat ANY populated `location`
# field as "region-restricted" (actionability 60 instead of 90), on a board
# whose own premise is remote-by-definition. Live-checked 2026-09-11 against
# the full crypto-tagged feed: 50/55 listings had a specific location
# string, but only 3/50 (6%) actually contained real residency-restriction
# language in the description -- the other 94% just stated a company
# HQ/timezone with no actual restriction (confirmed by reading all 3 hits:
# "Remote, must reside in the East Region", "Candidate must live in within
# their county area", "Candidates must reside in MA, IL, NY/NJ, NC, or FL"
# -- all real field/territory-sales roles, not the crypto-relevant listings
# this category actually surfaces). `location` alone was penalizing the
# 94% for a signal that doesn't mean what it looked like. Real restriction
# is instead detected from explicit residency/eligibility language in the
# free-text description; a bare location mention no longer suppresses
# actionability on its own.
LOCATION_RESTRICTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"must (?:be )?(?:based|located|residing)",
        r"must reside",
        r"must live in",
        r"only (?:accepting|considering|hiring)[^.]{0,30}(?:candidates|applicants)",
        r"no visa sponsorship",
        r"visa sponsorship (?:is )?not (?:available|provided|offered)",
        r"eligib(?:le|ility) to work in",
        r"authoriz(?:ed|ation) to work in",
        r"\bus\s*citizens?\s*only\b",
        r"\beu\s*citizens?\s*only\b",
        r"overlap[^.]{0,20}timezone",
        r"time ?zone[^.]{0,20}(?:required|overlap|must)",
        r"restricted to",
        r"candidates? (?:must|should) be (?:located|based)",
    ]
]

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Live-observed 2026-09-11: RemoteOK's `location` field frequently repeats a
# word/segment back-to-back at the source -- confirmed by inspecting the raw
# API response directly, before any of our own processing touches it: 'New
# York, New York, New York, United States', 'Miami, Miami, Florida, United
# States', and the same pattern in the Arabic listings once mojibake-corrected
# ('دبي, دبي دبي الإمارات العربية المتحدة' -- literally 'Dubai, Dubai Dubai
# United Arab Emirates'). This isn't something our mojibake fix introduces or
# RemoteOK's tag system -- it's RemoteOK's own location string, duplicated
# before it ever reaches us. `location` feeds both the digest triage line
# (llm_providers/template.py) and the write-time prompt context
# (write_post.py), so a dirty value here would show up in both the operator's
# digest AND -- if the writer LLM echoes its prompt context verbatim, which it
# sometimes does for factual fields -- the actual subscriber-facing post.
# Fixed at the source (here, alongside the mojibake fix) so every downstream
# consumer gets clean text automatically.
#
# Deliberately conservative: only collapses a word that repeats IMMEDIATELY
# (comma or whitespace between, case-insensitive) -- never a "drop any word
# seen anywhere in the string" global dedup, so a legitimate compound name
# that happens to share a word with an unrelated, non-adjacent segment is
# never touched. Verified against every duplication pattern actually observed
# in the live feed; a global dedup would have been more aggressive than the
# data actually requires and risked mangling a genuinely distinct segment.
def _dedupe_location(value: str) -> str:
    if not value:
        return value
    out_segments = []
    prev_words: list[str] | None = None  # word list of the last segment actually KEPT
    for segment in value.split(","):
        # 1. Collapse a word immediately repeating itself WITHIN this segment
        # (the 'دبي دبي' -> 'دبي' half of the Dubai case).
        words = []
        for word in segment.split():
            if words and word.lower() == words[-1].lower():
                continue
            words.append(word)

        # 2. Strip a leading run that exactly repeats the previous KEPT
        # segment's words (case-insensitive) -- covers both a whole segment
        # repeating verbatim ('New York, New York' -> segment 2 fully
        # stripped, dropped) and a repeat that lands inside the next segment
        # instead of getting its own comma ('دبي, دبي الإمارات...' -> the
        # leading 'دبي' half of segment 2 stripped, leaving just the country).
        # A dropped/empty segment intentionally leaves prev_words unchanged,
        # so a chain of 3+ consecutive repeats all collapse against the same
        # original segment.
        if prev_words:
            n = len(prev_words)
            if len(words) >= n and [w.lower() for w in words[:n]] == [w.lower() for w in prev_words]:
                words = words[n:]

        if words:
            out_segments.append(" ".join(words))
            prev_words = words
    return ", ".join(out_segments)


def _is_location_restricted(listing: dict) -> bool:
    """True only if the listing's free-text description contains real
    residency/eligibility-restriction language -- NOT just because
    `location` is populated (see the block comment above). Checked against
    description, not the location field itself, since location is
    frequently just HQ/timezone context with no actual restriction attached."""
    description = html_lib.unescape(_HTML_TAG_RE.sub(" ", listing.get("description") or ""))
    return any(pattern.search(description) for pattern in LOCATION_RESTRICTION_PATTERNS)


def _is_web3_relevant(payload: dict) -> bool:
    """True if this listing has a real signal of being about web3/crypto
    work, beyond just carrying RemoteOK's own (unreliable) 'crypto' tag."""
    company_lower = (payload.get("company") or "").lower()
    if any(known in company_lower for known in KNOWN_WEB3_COMPANIES):
        return True
    position_lower = (payload.get("position") or "").lower()
    return any(kw in position_lower for kw in WEB3_TITLE_KEYWORDS)


def _clean_field(key: str, value):
    if key == "tags":
        return [_fix_mojibake(t) for t in value]
    value = _fix_mojibake(value)
    if key == "location":
        # Mojibake-fix first, then dedupe -- the Arabic duplication pattern
        # only resolves into readable repeated words after decoding is fixed.
        value = _dedupe_location(value)
    return value


def fetch_listings() -> list[dict]:
    body = get_json(API_URL, params={"tags": "crypto"}, headers=REQUEST_HEADERS)
    # First element is RemoteOK's own legal/attribution notice, not a job —
    # verified live (has 'legal' key, no 'id'/'position').
    listings = [item for item in body if item.get("id") and item.get("position")]
    return [
        {k: _clean_field(k, v) for k, v in listing.items()}
        for listing in listings
    ]


def _title(listing: dict) -> str:
    return f"{listing.get('position')} at {listing.get('company')}"


def collect() -> int:
    load_dotenv()  # no-op in CI (no .env there); picks up local .env when run directly
    conn = get_conn()
    try:
        with run_log(conn, "collect_web3_jobs") as state:
            source_id = get_or_create_source(conn, CATEGORY, SOURCE_NAME, "api", {"url": API_URL})

            listings = fetch_listings()
            state["details"]["fetched"] = len(listings)

            items = []
            not_relevant_count = 0
            for listing in listings:
                external_id = str(listing["id"])
                company = listing.get("company") or ""
                position = listing.get("position") or ""
                apply_url = listing.get("apply_url") or listing.get("url")
                if not company or not position or not apply_url:
                    continue  # missing the basics -- not enough to write a real post about
                if not _is_web3_relevant(listing):
                    not_relevant_count += 1
                    continue

                payload = {
                    "title": _title(listing),
                    # Not used for history (this category deliberately has
                    # none — see pipeline/write_post.py's web3_jobs section)
                    # but kept for structural consistency with every other
                    # category, and it's a real, stable per-listing key.
                    "topic_key": external_id,
                    "company": company,
                    "position": position,
                    "tags": listing.get("tags") or [],
                    "salary_min": listing.get("salary_min") or 0,
                    "salary_max": listing.get("salary_max") or 0,
                    "location": listing.get("location") or "",
                    "location_restricted": _is_location_restricted(listing),
                    "company_logo": listing.get("company_logo") or listing.get("logo") or "",
                    "description_length": len(listing.get("description") or ""),
                    "apply_url": apply_url,
                    "epoch": listing.get("epoch"),
                    "date": listing.get("date"),
                }
                items.append((external_id, payload))

            state["details"]["not_relevant_filtered"] = not_relevant_count

            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items)
            state["details"]["inserted"] = inserted

            assigned = assign_channels_to_new_items(conn, CATEGORY)
            state["details"]["channel_assigned"] = assigned

            logger.info("web3_jobs: fetched=%d not_relevant_filtered=%d inserted=%d channel_assigned=%d",
                        len(listings), not_relevant_count, inserted, assigned)
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = collect()
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
