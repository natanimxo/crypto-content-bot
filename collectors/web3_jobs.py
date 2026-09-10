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

import logging
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


def fetch_listings() -> list[dict]:
    body = get_json(API_URL, params={"tags": "crypto"}, headers=REQUEST_HEADERS)
    # First element is RemoteOK's own legal/attribution notice, not a job —
    # verified live (has 'legal' key, no 'id'/'position').
    listings = [item for item in body if item.get("id") and item.get("position")]
    return [
        {k: (_fix_mojibake(v) if k != "tags" else [_fix_mojibake(t) for t in v]) for k, v in listing.items()}
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
            for listing in listings:
                external_id = str(listing["id"])
                company = listing.get("company") or ""
                position = listing.get("position") or ""
                apply_url = listing.get("apply_url") or listing.get("url")
                if not company or not position or not apply_url:
                    continue  # missing the basics -- not enough to write a real post about

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
                    "company_logo": listing.get("company_logo") or listing.get("logo") or "",
                    "description_length": len(listing.get("description") or ""),
                    "apply_url": apply_url,
                    "epoch": listing.get("epoch"),
                    "date": listing.get("date"),
                }
                items.append((external_id, payload))

            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items)
            state["details"]["inserted"] = inserted

            assigned = assign_channels_to_new_items(conn, CATEGORY)
            state["details"]["channel_assigned"] = assigned

            logger.info("web3_jobs: fetched=%d inserted=%d channel_assigned=%d",
                        len(listings), inserted, assigned)
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = collect()
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
