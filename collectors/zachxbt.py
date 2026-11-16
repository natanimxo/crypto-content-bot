"""ZachXBT's investigations channel (@investigations) -> hacks_exploits (operator 2026-11-16).

DefiLlama's /hacks (collectors/hacks_exploits.py) is a confirmed-incident TRACKER and lags by hours to days; ZachXBT usually reports an exploit first. His channel posts about
0.2 a day, mostly incidents, and its newest posts are not always one (laundering follow-ups, public-appearance notices, sanction news).

WHAT IS COLLECTED: only a post that says an entity LOST money, with the amount in the same sentence ("X was exploited for $3.8M+", "suspected of losing $106M+", "lost ~$2.84M"),
at or above the same $500k floor as the DefiLlama collector. NOT collected: follow-ups ("Update: ... an additional $27M"), laundering notes that merely mention a dollar figure
("shielding ~2.7K ZEC ($3.8M) from the Bitget exploit"), and posts with no loss amount. Parsing is deterministic regexes, no model.

CERTAINTY DISCIPLINE (same as every hacks item): the payload carries only what the post states. The claim is HIS, not confirmed by DefiLlama or the affected party, and the writer
is told so (post_facts.facts_hacks_exploits). The channel is an individual investigator's account, not an organisation's: tier 3.

DEDUPE with DefiLlama: an incident is one event. If DefiLlama already holds an incident with the same protocol name, this collector skips it; hacks_exploits.collect skips a DefiLlama
incident whose name matches a ZachXBT item already stored (within 14 days).
"""

import logging
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from pipeline import tg_preview
from pipeline.db import dict_cursor, get_conn
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logger = logging.getLogger(__name__)

CATEGORY = "hacks_exploits"
SOURCE_NAME = "zachxbt_telegram"
HANDLE = "investigations"
SOURCE_TIER = 3
MIN_AMOUNT_USD = 500_000
MAX_AGE_HOURS = 336.0          # same freshness window as the category (14 days)
STALE_DAYS = 45                # the channel posts ~0.2/day; a month and a half of silence is dead, not quiet
HOLD_ALL_CANDIDATES = False

_LOSS_RE = re.compile(
    r"(?P<verb>exploited|hacked|drained|stolen|lost|losing|theft|siphoned)\s+(?:for\s+|of\s+)?(?:an estimated\s+|approximately\s+|about\s+)?[~≈]?\s*\$\s?(?P<n>\d[\d,]*(?:\.\d+)?)\s?(?P<u>[KMB])?\b",
    re.IGNORECASE)
_NAME_RE = re.compile(
    r"^(?:community alert|breaking|new|alert)\s*:\s*(?P<name>.+?)\s+(?:was|were|has been|had been|is suspected|are suspected|suffered)\b", re.IGNORECASE)
_UNIT = {"K": 1e3, "M": 1e6, "B": 1e9}


def parse_incident(text: str) -> dict | None:
    """{amount_usd, protocol_name|None, plus (True when the post says 'X+')} for a post that reports a loss, else None."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t or re.match(r"^(?:update|follow[- ]?up)\b", t, re.IGNORECASE):
        return None
    m = _LOSS_RE.search(t)
    if not m:
        return None
    amount = float(m.group("n").replace(",", "")) * _UNIT.get((m.group("u") or "").upper(), 1)
    if amount < MIN_AMOUNT_USD:
        return None
    nm = _NAME_RE.match(t)
    name = nm.group("name").strip(" :-") if nm else None
    if name and (len(name) > 60 or re.match(r"^(?:an?|the)\s+(?:eu|us|institutional|unnamed)", name, re.IGNORECASE)):
        name = None                                        # "An EU entity" / "An institutional entity": a description, not a name
    return {"amount_usd": amount, "protocol_name": name, "plus": bool(re.search(r"\$\s?[\d.,]+\s?[KMB]?\+", t))}


def topic_key(name: str | None, post_id: int) -> str:
    return (name or f"zachxbt-{post_id}").strip().lower()


def to_payload(inc: dict, post: dict) -> dict:
    name = inc["protocol_name"] or "An unnamed entity"
    plus = "+" if inc["plus"] else ""
    return {
        "title": f"{name} reported hit for ${inc['amount_usd']:,.0f}{plus} (per ZachXBT)",
        "topic_key": topic_key(inc["protocol_name"], post["id"]),
        "published_at": post["time"].isoformat(),
        "protocol_name": name, "named": inc["protocol_name"] is not None, "amount_usd": inc["amount_usd"],
        "classification": None, "technique": None, "chain": None, "chains": [], "target_type": None, "bridge_hack": False, "returned_funds": None,
        "defillama_id": None, "protocol_tvl_usd": None, "protocol_category": None,
        "source": "zachxbt", "source_tier": SOURCE_TIER, "source_url": f"https://t.me/{post['post']}", "claim_text": re.sub(r"\s+", " ", post["text"]).strip()[:600],
        "amount_is_lower_bound": inc["plus"],
    }


def collect() -> int:
    load_dotenv()
    conn = get_conn()
    try:
        with run_log(conn, "collect_zachxbt") as state:
            posts = tg_preview.fetch_posts(HANDLE)
            now = datetime.now(timezone.utc)
            newest_age_d = (now - posts[-1]["time"]).days
            state["details"].update({"posts": len(posts), "newest_age_days": newest_age_d})
            if newest_age_d >= STALE_DAYS:
                raise RuntimeError(f"@{HANDLE} is stale: newest post {newest_age_d} days old")
            with dict_cursor(conn) as cur:
                cur.execute("SELECT DISTINCT payload->>'topic_key' k FROM raw_items WHERE category = %s AND payload->>'source' IS DISTINCT FROM 'zachxbt' AND collected_at > now() - interval '14 days'", (CATEGORY,))
                known = {r["k"] for r in cur.fetchall() if r["k"]}
            stats = {"not_incident": 0, "too_old": 0, "duplicate_of_defillama": 0}
            items = []
            for p in posts:
                if (now - p["time"]).total_seconds() > MAX_AGE_HOURS * 3600:
                    stats["too_old"] += 1; continue
                inc = parse_incident(p["text"])
                if not inc:
                    stats["not_incident"] += 1; continue
                if inc["protocol_name"] and inc["protocol_name"].lower() in known:
                    stats["duplicate_of_defillama"] += 1; continue
                items.append((f"zachxbt:{p['id']}", to_payload(inc, p)))
            source_id = get_or_create_source(conn, CATEGORY, SOURCE_NAME, "telegram", {"handle": HANDLE})
            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items, held=HOLD_ALL_CANDIDATES)
            state["details"].update({**stats, "candidates": len(items), "inserted": inserted})
            logger.info("zachxbt: posts=%d %s candidates=%d inserted=%d", len(posts), stats, len(items), inserted)
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collect()
    sys.exit(0)
