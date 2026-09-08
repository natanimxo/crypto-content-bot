"""Publishing (Section 11): sendMessage to the channel's chat id, record the
resulting telegram_message_id in `posts`. Only ever called from the Publish
button handler in bot/approval_poller.py, after an operator has seen the final
text (Section 9 step 4).
"""

import html
from datetime import datetime, timezone

from pipeline.db import dict_cursor
from pipeline.telegram_api import send_message


def get_channel_chat_id(conn, channel: str) -> str:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT chat_id FROM channel_config WHERE channel = %s", (channel,))
        row = cur.fetchone()
    if not row or not row["chat_id"]:
        raise RuntimeError(
            f"channel_config.chat_id is not set for '{channel}' — check config/channel_config.yaml "
            f"and re-run scripts/seed_config.py."
        )
    return row["chat_id"]


def publish_post(conn, approval_id: int, channel: str, category: str, final_text: str,
                  label: str | None = None) -> int:
    """Sends final_text to the channel (with its notification label as an eyebrow
    line, Section 10) and records the post. Returns the new posts.id."""
    chat_id = get_channel_chat_id(conn, channel)

    # final_text is LLM- or operator-authored free text sent with parse_mode=HTML
    # (Section 10's label styling) — escape it so a stray '<', '>', or '&' doesn't
    # make Telegram reject the whole publish call. posts.final_text below stores
    # the original, unescaped text; only the wire body is escaped.
    escaped_text = html.escape(final_text)
    body = f"<b>{html.escape(label)}</b>\n\n{escaped_text}" if label else escaped_text
    result = send_message(chat_id, body)

    with dict_cursor(conn) as cur:
        cur.execute(
            """INSERT INTO posts (approval_id, channel, category, final_text, published_at, telegram_message_id)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (approval_id, channel, category, final_text, datetime.now(timezone.utc), result["message_id"]),
        )
        post_id = cur.fetchone()["id"]
    conn.commit()
    return post_id
