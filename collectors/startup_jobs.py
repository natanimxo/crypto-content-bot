"""Startup/tech jobs collector (Phase 3, 2026-09-12) -- Hustle to Million.
Reuses RemoteOK exactly as collectors/web3_jobs.py does (same API, same
real data-quality bugs already fixed there: server-side mojibake, duplicated
location strings, RemoteOK's own tag system being too noisy to trust alone)
-- but the relevance gate is the INVERSE (operator direction): web3_jobs
denies-by-default and allows in via a known-crypto-company list or a crypto
keyword fallback; this category allows-by-default and denies via a known
large-non-startup-company list or a generic-non-tech-role-title fallback.

Live-verified before building: RemoteOK's UNFILTERED feed (no `tags=`
param) is a real, wide mix -- some genuinely startup/tech-shaped roles
(TestGorilla, Benzinga "AI Engineer", HighLevel "Lead Product Designer"),
but also the exact kind of noise web3_jobs hit (Johnson Controls, Liberty
Mutual, WestJet, American Bureau of Shipping -- large, non-startup
corporations) PLUS a second noise category web3_jobs didn't have to deal
with: generic labor/admin/BPO-agency roles regardless of company size
("Machine Operator & Labourers", "Gardener Handyman Driver", "Healthcare
Virtual Assistant", "Legal Receptionist") -- these aren't "wrong audience"
the way a pizza-chain listing was for crypto, they're just not
startup/tech-shaped work at all. Both exclusion lists below are grounded in
that real feed, not guessed; tested against the live 99-listing feed before
committing to them (83/99 correctly kept, all 16 drops were genuine noise).

NOT a full architectural extraction into a shared pipeline/remoteok.py
module (a real candidate for one, given how much overlaps with
web3_jobs.py) -- deliberately duplicated instead, to avoid touching a
working, already-relied-upon category mid-session for a DRY concern. Noted
in BACKLOG.md as a real, low-priority follow-up.

HOLD: every row this collector inserts is held=True unconditionally, same
as every Phase 3 category -- nothing notifies until the Hetzner poller is
verified.
"""

import logging
import re
import sys

from dotenv import load_dotenv

from pipeline.db import get_conn
from pipeline.http import get_json
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CATEGORY = "startup_jobs"
SOURCE_NAME = "remoteok_general"
API_URL = "https://remoteok.com/api"
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}


# --- Duplicated from collectors/web3_jobs.py verbatim (same source, same
# real data-quality bugs) -- see that module's own comments for the full
# live-verification writeup behind each of these. Not re-derived here. ---

def _fix_mojibake(value):
    if not isinstance(value, str):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _dedupe_location(value: str) -> str:
    if not value:
        return value
    out_segments = []
    prev_words: list[str] | None = None
    for segment in value.split(","):
        words = []
        for word in segment.split():
            if words and word.lower() == words[-1].lower():
                continue
            words.append(word)
        if prev_words:
            n = len(prev_words)
            if len(words) >= n and [w.lower() for w in words[:n]] == [w.lower() for w in prev_words]:
                words = words[n:]
        if words:
            out_segments.append(" ".join(words))
            prev_words = words
    return ", ".join(out_segments)


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


def _is_location_restricted(listing: dict) -> bool:
    description = _fix_mojibake(_HTML_TAG_RE.sub(" ", listing.get("description") or ""))
    return any(pattern.search(description) for pattern in LOCATION_RESTRICTION_PATTERNS)


def _clean_field(key: str, value):
    if key == "tags":
        return [_fix_mojibake(t) for t in value]
    value = _fix_mojibake(value)
    if key == "location":
        value = _dedupe_location(value)
    return value


# --- New for this category: the inverse relevance gate. ---

# Live-checked 2026-09-12 against the real unfiltered RemoteOK feed. Same
# "hand-maintained list, low-stakes, common-knowledge judgment, no
# address-style rigor needed" maintenance shape as every other such list in
# this codebase -- a missed exclusion means one non-startup listing gets
# through, not a functional error. Reuses the exact company names already
# identified as crypto-feed noise (web3_jobs.py's KNOWN_WEB3_COMPANIES
# investigation) plus new ones observed in this feed's general mix.
KNOWN_LARGE_CORPORATIONS = {
    "little caesars", "walt disney", "nestl", "dsw designer shoe warehouse",
    "johnson controls", "liberty mutual", "westjet",
    "american bureau of shipping", "slb", "harvey nash",
}

# Not "wrong industry" the way KNOWN_LARGE_CORPORATIONS is -- these are
# generic labor/admin/BPO-agency role TITLES that show up regardless of
# company size and aren't startup/tech-shaped work at all.
GENERIC_NON_TECH_TITLE_KEYWORDS = {
    "handyman", "gardener", "driver", "labourer", "laborer", "machine operator",
    "receptionist", "voice over artist", "flight operations", "surveyor",
    "service technician", "bidder", "paralegal", "dispense technician",
    "healthcare virtual assistant",
}


def _is_startup_relevant(payload: dict) -> bool:
    company = (payload.get("company") or "").lower()
    position = (payload.get("position") or "").lower()
    if any(k in company for k in KNOWN_LARGE_CORPORATIONS):
        return False
    if any(k in position for k in GENERIC_NON_TECH_TITLE_KEYWORDS):
        return False
    return True


def fetch_listings() -> list[dict]:
    body = get_json(API_URL, headers=REQUEST_HEADERS)  # no tags= param -- the general feed, not crypto-filtered
    listings = [item for item in body if item.get("id") and item.get("position")]
    return [
        {k: _clean_field(k, v) for k, v in listing.items()}
        for listing in listings
    ]


def _title(listing: dict) -> str:
    return f"{listing.get('position')} at {listing.get('company')}"


def collect() -> int:
    load_dotenv()
    conn = get_conn()
    try:
        with run_log(conn, "collect_startup_jobs") as state:
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
                    continue
                if not _is_startup_relevant(listing):
                    not_relevant_count += 1
                    continue

                payload = {
                    "title": _title(listing),
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

            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items, held=True)
            state["details"]["inserted"] = inserted

            logger.info(
                "startup_jobs: fetched=%d not_relevant_filtered=%d inserted=%d",
                len(listings), not_relevant_count, inserted,
            )
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = collect()
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
