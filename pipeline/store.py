"""Shared insert/dedup logic. Collectors call `insert_raw_item`; nothing else in the
codebase should INSERT into `sources` or `raw_items` directly, so idempotency and
dedup rules live in exactly one place (Section 7 / Section 12)."""

import difflib
import json

from psycopg2.extras import execute_values

from pipeline.db import dict_cursor


def get_or_create_source(conn, category: str, name: str, kind: str, config: dict) -> int:
    """Return the source id for (category, name), creating the row on first use.

    Sources are cheap and rarely change, so this is a plain get-or-create rather
    than something collectors configure once and cache — a fresh GitHub Actions
    runner has no in-memory cache to hold anyway.
    """
    with dict_cursor(conn) as cur:
        cur.execute(
            "SELECT id FROM sources WHERE category = %s AND name = %s",
            (category, name),
        )
        row = cur.fetchone()
        if row:
            return row["id"]
        cur.execute(
            """INSERT INTO sources (category, name, kind, config, enabled)
               VALUES (%s, %s, %s, %s, TRUE) RETURNING id""",
            (category, name, kind, json.dumps(config)),
        )
        source_id = cur.fetchone()["id"]
        conn.commit()
        return source_id


def insert_raw_item(conn, source_id: int, category: str, external_id: str, payload: dict) -> int | None:
    """Insert one raw item. Returns its id, or None if it was already collected
    (the UNIQUE(source_id, external_id) constraint makes this safe to call
    repeatedly — reruns after a partial failure never double-insert, Section 12).
    """
    with dict_cursor(conn) as cur:
        cur.execute(
            """INSERT INTO raw_items (source_id, category, external_id, payload)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (source_id, external_id) DO NOTHING
               RETURNING id""",
            (source_id, category, external_id, json.dumps(payload)),
        )
        row = cur.fetchone()
        conn.commit()
        return row["id"] if row else None


def insert_raw_items_batch(conn, source_id: int, category: str, items: list[tuple[str, dict]]) -> int:
    """Bulk version of insert_raw_item — one round trip for the whole batch instead
    of one per row. A category like defi_yields can easily produce several
    thousand candidate rows per cycle; inserting those one at a time (each with
    its own network round trip + commit to Supabase) is what actually risks
    blowing collect.yml's job timeout, not the collector's own fetch/filter work
    (Section 12). `items` is a list of (external_id, payload) pairs. Returns the
    number of rows actually inserted (existing ones are silently skipped via the
    same UNIQUE(source_id, external_id) ON CONFLICT DO NOTHING as insert_raw_item).
    """
    if not items:
        return 0
    values = [(source_id, category, external_id, json.dumps(payload)) for external_id, payload in items]
    with conn.cursor() as cur:
        inserted_rows = execute_values(
            cur,
            """INSERT INTO raw_items (source_id, category, external_id, payload)
               VALUES %s
               ON CONFLICT (source_id, external_id) DO NOTHING
               RETURNING id""",
            values,
            template="(%s, %s, %s, %s)",
            page_size=500,
            fetch=True,
        )
    conn.commit()
    return len(inserted_rows)


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


def is_likely_duplicate(conn, category: str, title: str, lookback_hours: int = 48, threshold: float = 0.85) -> bool:
    """Cross-source dedup hook (Section 7: 'checked at the raw_items level via
    external_id and text similarity, never at the scoring level').

    Exact-id dedup is handled for free by the UNIQUE constraint in insert_raw_item;
    this catches the case where two different sources describe the same real-world
    event with slightly different text. Not wired into the MVP's single-source
    defi_yields collector yet — becomes relevant once Phase 2 adds multiple
    sources per category. Kept here now so callers have one place to reach for it.
    """
    norm = _normalize_title(title)
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT payload FROM raw_items
               WHERE category = %s AND collected_at > now() - (%s || ' hours')::interval""",
            (category, lookback_hours),
        )
        for row in cur.fetchall():
            other_title = row["payload"].get("title") or row["payload"].get("pool") or ""
            if not other_title:
                continue
            ratio = difflib.SequenceMatcher(None, norm, _normalize_title(other_title)).ratio()
            if ratio >= threshold:
                return True
    return False
