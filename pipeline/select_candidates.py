"""Turns scored raw_items into the list that actually gets notified to the
operator. This is where Section 7's cooldown suppression and Section 9's
soft-daily-cap holding happen — both deterministic, both before anything reaches
an LLM.

An item can clear review_threshold and still not be notified this cycle, either
because its topic was already surfaced within cooldown_hours, or because the
channel already hit its soft_daily_cap for today — in both cases it's simply
skipped, not marked as anything, so a later cycle (once the cooldown lapses, or
tomorrow resets the cap) can pick it back up.
"""

from datetime import datetime, timezone

from pipeline.db import dict_cursor
from pipeline.score import load_category_config


def _already_notified_ids(conn, category: str) -> set[int]:
    """raw_item ids that have appeared in any notification before (so we never
    re-notify the same item, independent of cooldown/topic logic)."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT DISTINCT r.id AS raw_item_id
               FROM notifications n
               JOIN raw_items r ON r.id = ANY(n.candidate_raw_item_ids)
               WHERE r.category = %s""",
            (category,),
        )
        return {row["raw_item_id"] for row in cur.fetchall()}


def _topics_in_cooldown(conn, category: str, cooldown_hours: float) -> set[str]:
    """topic_keys that were notified within the cooldown window — suppresses
    same-topic repeats (Section 7) regardless of score."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT DISTINCT r.payload->>'topic_key' AS topic_key
               FROM notifications n
               JOIN raw_items r ON r.id = ANY(n.candidate_raw_item_ids)
               WHERE r.category = %s
                 AND n.sent_at > now() - (%s || ' hours')::interval""",
            (category, cooldown_hours),
        )
        return {row["topic_key"] for row in cur.fetchall() if row["topic_key"]}


def _category_notified_today_count(conn, channel: str, category: str) -> int:
    """How many items of this specific category were already notified to this
    channel today — category_config.soft_daily_cap is a per-category pace, not a
    channel-wide one (channel_config.soft_daily_cap is the separate combined cap,
    applied by the caller across all of a channel's categories)."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT COUNT(*) AS n
               FROM notifications nf
               JOIN raw_items r ON r.id = ANY(nf.candidate_raw_item_ids)
               WHERE nf.channel = %s AND r.category = %s
                 AND nf.sent_at::date = (now() AT TIME ZONE 'utc')::date""",
            (channel, category),
        )
        return cur.fetchone()["n"] or 0


def get_new_candidates(conn, category: str, channel: str) -> list[dict]:
    """Return raw_items (with score + breakdown) that are new, clear threshold,
    aren't in cooldown, and still fit under today's soft_daily_cap for this
    channel — ordered highest score first."""
    cfg = load_category_config(conn, category)
    threshold = float(cfg["review_threshold"])
    cooldown_hours = float(cfg["cooldown_hours"])

    already_notified = _already_notified_ids(conn, category)
    cooling_down_topics = _topics_in_cooldown(conn, category, cooldown_hours)

    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT r.id AS raw_item_id, r.payload, s.score, s.score_breakdown
               FROM raw_items r
               JOIN scores s ON s.raw_item_id = r.id
               WHERE r.category = %s AND s.score >= %s
               ORDER BY s.score DESC""",
            (category, threshold),
        )
        rows = cur.fetchall()

    candidates = []
    for row in rows:
        if row["raw_item_id"] in already_notified:
            continue
        topic_key = (row["payload"] or {}).get("topic_key")
        if topic_key and topic_key in cooling_down_topics:
            continue
        candidates.append(row)

    soft_cap = cfg.get("soft_daily_cap")
    if soft_cap is not None:
        already_today = _category_notified_today_count(conn, channel, category)
        remaining = max(0, int(soft_cap) - already_today)
        candidates = candidates[:remaining]

    return candidates
