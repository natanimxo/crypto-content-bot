"""Post finalization. Originally (Section 11) this sendMessage'd straight to the
target channel's chat_id. Changed 2026-09-10 per explicit operator direction:
the bot never posts to channels directly. The operator copies/forwards the
final text themselves; this module's job is just to record that decision in
`posts` for history/dedup (the "memory" differentiator, Section 1 #1, still
needs a real record of what was actually sent, regardless of how it got
there). No Telegram send happens here — channel_config.chat_id stays in the DB
for potential future reactivation but nothing in this module reads it anymore.
"""

from datetime import datetime, timezone

from pipeline.db import dict_cursor


def get_channel_display_name(conn, channel: str) -> str:
    """Human-readable name for the label header (e.g. "Crypto Notebook") —
    falls back to the raw channel slug if display_name was never seeded."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT display_name FROM channel_config WHERE channel = %s", (channel,))
        row = cur.fetchone()
    return (row["display_name"] if row and row["display_name"] else channel)


def mark_as_sent(conn, approval_id: int, channel: str, category: str, final_text: str) -> int:
    """Records that the operator has taken this text and sent it themselves.
    Returns the new posts.id. No network call, no telegram_message_id."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """INSERT INTO posts (approval_id, channel, category, final_text, published_at, telegram_message_id)
               VALUES (%s, %s, %s, %s, %s, NULL) RETURNING id""",
            (approval_id, channel, category, final_text, datetime.now(timezone.utc)),
        )
        post_id = cur.fetchone()["id"]
    conn.commit()
    return post_id
