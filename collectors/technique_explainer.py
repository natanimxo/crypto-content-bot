"""Technique of the week -> Crypto Notebook (operator 2026-11-20).

One explainer a week about an ATTACK TYPE, built from the incidents this pipeline has already stored from DefiLlama's /hacks tracker (collectors/hacks_exploits.py). No new source and no
model call here: this collector only picks the type and gathers the real incidents the post may cite. The post itself is written later, on Approve, in Notebook's explainer voice
(pipeline/post_facts.py FactSpec "technique_explainer"; the guard in pipeline/write_post.py refuses any figure, year or count that is not in the facts).

WHAT AN "ATTACK TYPE" IS: DefiLlama's own `classification` -- 13 values (Key Compromise, Access Control, Oracle Manipulation, Token & Share Accounting, Frontend & Infrastructure, Protocol Logic,
Social Engineering, Bridge & Cross-Chain, Reentrancy, Input Validation, Rugpull, Governance, Market Manipulation). Every one has 10+ stored incidents. We never invent our own taxonomy.

WHICH TYPE THIS WEEK (deterministic, so a re-run the same week picks the same one):
  1. a type that has never been covered, else the one covered longest ago;
  2. ties go to the type with the most recent incident (a reader's "this still happens" is the point);
  3. only a type with at least MIN_INCIDENTS incidents, at least MIN_EXAMPLES NAMED incidents to cite, and an incident in the last RECENT_WINDOW_DAYS is eligible.

EXAMPLES: three real incidents, each stated only as the tracker has it -- the largest of the type, the most recent, and the largest one with a different technique label (so the post
shows the type is not one trick). Names, dates, amounts, chains and technique labels come straight from the stored DefiLlama payloads; "Unknown" targets are never used.

ONE PER ISO WEEK: the item's external_id is the ISO week, so the unique (source, external_id) key makes a second run in the same week a no-op. The collector therefore runs every cycle
and costs one cheap query once the week's item exists.

CERTAINTY DISCIPLINE (same as every hacks item): counts and totals describe the TRACKER ("of $500,000 or more, as recorded by DefiLlama"), not the world; nothing about who attacked or why.
"""

import logging
import sys
from collections import Counter
from datetime import datetime, timezone

from dotenv import load_dotenv

from pipeline.db import dict_cursor, get_conn
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logger = logging.getLogger(__name__)

CATEGORY = "technique_explainer"
SOURCE_NAME = "defillama_tracker_aggregate"
MIN_INCIDENTS = 10
MIN_EXAMPLES = 3
RECENT_WINDOW_DAYS = 730
HOLD_ALL_CANDIDATES = False


def iso_week(now: datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def load_incidents(conn) -> list[dict]:
    """Every stored DefiLlama incident (the tracker's own rows; ZachXBT and any later per-source reports are not tracker data)."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT id, payload FROM raw_items
               WHERE category = 'hacks_exploits' AND COALESCE(payload->>'source', '') = ''
                 AND payload->>'classification' IS NOT NULL""")
        rows = cur.fetchall()
    out = []
    for r in rows:
        p = r["payload"] or {}
        try:
            when = datetime.fromisoformat(p["published_at"])
        except (KeyError, ValueError, TypeError):
            continue
        out.append({
            "raw_item_id": r["id"], "name": (p.get("protocol_name") or "").strip(), "when": when, "amount_usd": float(p.get("amount_usd") or 0),
            "classification": p.get("classification"), "technique": (p.get("technique") or "").strip() or None, "chain": p.get("chain"),
            "returned_usd": p.get("returned_funds") or None,
        })
    return out


def _named(i: dict) -> bool:
    return bool(i["name"]) and i["name"].lower() not in {"unknown", "unknown protocol", "n/a"}


def covered_before(conn) -> dict[str, datetime]:
    """{type: when it was last turned into a weekly item}."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT payload->>'technique_type' AS t, MAX(collected_at) AS last FROM raw_items WHERE category = %s GROUP BY 1", (CATEGORY,))
        return {r["t"]: r["last"] for r in cur.fetchall() if r["t"]}


def pick_type(incidents: list[dict], covered: dict[str, datetime], now: datetime) -> str | None:
    by_type: dict[str, list[dict]] = {}
    for i in incidents:
        by_type.setdefault(i["classification"], []).append(i)
    eligible = []
    for t, items in by_type.items():
        named = [i for i in items if _named(i)]
        newest = max(i["when"] for i in items)
        if len(items) >= MIN_INCIDENTS and len(named) >= MIN_EXAMPLES and (now - newest).days <= RECENT_WINDOW_DAYS:
            eligible.append((t, newest))
    if not eligible:
        return None
    never = datetime.min.replace(tzinfo=timezone.utc)
    # least recently covered first (never = oldest), then the type with the newest incident
    eligible.sort(key=lambda te: (covered.get(te[0], never), -te[1].timestamp()))
    return eligible[0][0]


def pick_examples(items: list[dict]) -> list[dict]:
    named = sorted((i for i in items if _named(i) and i["amount_usd"] > 0), key=lambda i: -i["amount_usd"])
    if not named:
        return []
    chosen = [named[0]]
    recent = max((i for i in named if i not in chosen), key=lambda i: i["when"], default=None)
    if recent:
        chosen.append(recent)
    used_tech = {i["technique"] for i in chosen}
    other = next((i for i in named if i not in chosen and i["technique"] not in used_tech), None) or next((i for i in named if i not in chosen), None)
    if other:
        chosen.append(other)
    return sorted(chosen, key=lambda i: i["when"])


def build_payload(technique_type: str, items: list[dict], now: datetime, week: str) -> dict:
    total = sum(i["amount_usd"] for i in items)
    last12 = [i for i in items if (now - i["when"]).days <= 365]
    techniques = Counter(i["technique"] for i in items if i["technique"] and i["technique"].lower() != "unknown")
    return {
        "title": f"Technique of the week: {technique_type}",
        "topic_key": f"technique:{technique_type.lower()}",
        "published_at": now.isoformat(),
        "week": week,
        "technique_type": technique_type,
        "incident_count": len(items),
        "total_lost_usd": total,
        "first_year": min(i["when"] for i in items).year,
        "last_incident": max(i["when"] for i in items).date().isoformat(),
        "last12_count": len(last12),
        "last12_lost_usd": sum(i["amount_usd"] for i in last12),
        "top_techniques": [{"technique": t, "count": n} for t, n in techniques.most_common(3)],
        "examples": [{
            "raw_item_id": e["raw_item_id"], "name": e["name"], "date": e["when"].date().isoformat(), "amount_usd": e["amount_usd"],
            "chain": e["chain"], "technique": e["technique"], "returned_usd": e["returned_usd"],
        } for e in pick_examples(items)],
        "source": "defillama_tracker_aggregate",
        "source_name": "DefiLlama",
    }


def collect(now: datetime | None = None) -> int:
    load_dotenv()
    conn = get_conn()
    try:
        with run_log(conn, "collect_technique_explainer") as state:
            now = now or datetime.now(timezone.utc)
            week = iso_week(now)
            with dict_cursor(conn) as cur:
                cur.execute("SELECT 1 FROM raw_items WHERE category = %s AND payload->>'week' = %s", (CATEGORY, week))
                if cur.fetchone():
                    state["details"].update({"week": week, "already_have_this_week": True, "inserted": 0})
                    return 0
            incidents = load_incidents(conn)
            technique_type = pick_type(incidents, covered_before(conn), now)
            if not technique_type:
                state["details"].update({"week": week, "inserted": 0, "no_eligible_type": True, "stored_incidents": len(incidents)})
                return 0
            payload = build_payload(technique_type, [i for i in incidents if i["classification"] == technique_type], now, week)
            source_id = get_or_create_source(conn, CATEGORY, SOURCE_NAME, "aggregate", {"from": "hacks_exploits (DefiLlama)"})
            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, [(f"technique:{week}", payload)], held=HOLD_ALL_CANDIDATES)
            state["details"].update({"week": week, "type": technique_type, "incidents": payload["incident_count"], "examples": [e["name"] for e in payload["examples"]], "inserted": inserted})
            logger.info("technique_explainer: %s -> %s (%d incidents, examples %s)", week, technique_type, payload["incident_count"], [e["name"] for e in payload["examples"]])
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collect()
    sys.exit(0)
