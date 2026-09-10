"""Channel routing for categories shared across multiple channels (spec
Section 3's alternation — Airdrops and Web3 jobs are eligible for both Alpha
Edge Crypto and CoinCraft, and "each individual item is assigned to exactly
one of the two channels ... never both"). First real use, 2026-09-10, for
web3_jobs.

Deterministic round-robin per category, tracked in `channel_alternation`
(existing table from initial scaffolding, unused until now) — assignment
happens ONCE per item, at collection time, and is permanent: it's stored in
the raw_item's own payload (`assigned_channel`), not recomputed later.
select_candidates.get_new_candidates filters on this so a shared-category
item only ever surfaces as a candidate for the one channel it was assigned
to (see that module for the query-side half of this).
"""

from pipeline.db import dict_cursor

# Categories with more than one channel in channel_config.yaml need an entry
# here — the channel LIST order determines alternation order. Single-channel
# categories (the majority) don't need anything here at all; assign_channel
# returns None for them and callers skip assignment entirely.
SHARED_CATEGORY_CHANNELS = {
    "web3_jobs": ["alpha_edge_crypto", "coincraft"],
}


def assign_channel(conn, category: str) -> str | None:
    """Returns the channel a NEW item of this category should be assigned to,
    alternating deterministically each call, or None if `category` isn't
    shared (nothing to assign — the item belongs to whichever single channel
    lists it, same as every other category)."""
    channels = SHARED_CATEGORY_CHANNELS.get(category)
    if not channels:
        return None

    with dict_cursor(conn) as cur:
        cur.execute("SELECT last_channel FROM channel_alternation WHERE category = %s", (category,))
        row = cur.fetchone()

    if not row:
        next_channel = channels[0]
    else:
        try:
            idx = channels.index(row["last_channel"])
            next_channel = channels[(idx + 1) % len(channels)]
        except ValueError:
            # last_channel isn't in the current list (e.g. config changed
            # since) -- restart cleanly from the front rather than error.
            next_channel = channels[0]

    with dict_cursor(conn) as cur:
        cur.execute(
            """INSERT INTO channel_alternation (category, last_channel, updated_at)
               VALUES (%s, %s, now())
               ON CONFLICT (category) DO UPDATE SET last_channel = EXCLUDED.last_channel, updated_at = now()""",
            (category, next_channel),
        )
    conn.commit()
    return next_channel


def assign_channels_to_new_items(conn, category: str) -> int:
    """Post-insert pass: assigns a channel to every raw_item of this category
    that doesn't have one yet. Deliberately separate from insertion itself —
    insert_raw_items_batch's ON CONFLICT DO NOTHING means a re-fetched
    duplicate never touches its (already-assigned) payload again, so this
    only ever advances the round-robin for genuinely NEW items, not every
    item the collector happens to re-fetch this cycle. No-op (0) for
    non-shared categories. Returns the number of items assigned.
    """
    if category not in SHARED_CATEGORY_CHANNELS:
        return 0

    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT id FROM raw_items
               WHERE category = %s AND payload->>'assigned_channel' IS NULL
               ORDER BY id""",
            (category,),
        )
        unassigned_ids = [row["id"] for row in cur.fetchall()]

    for raw_item_id in unassigned_ids:
        channel = assign_channel(conn, category)
        with dict_cursor(conn) as cur:
            cur.execute(
                """UPDATE raw_items SET payload = payload || jsonb_build_object('assigned_channel', %s::text)
                   WHERE id = %s""",
                (channel, raw_item_id),
            )
        conn.commit()

    return len(unassigned_ids)
