"""Publishing (Section 11): sendMessage to the channel's chat id, record the
resulting telegram_message_id in `posts`. Only ever called from the Publish
button handler in bot/approval_poller.py, after an operator has seen the final
text (Section 9 step 4).
"""

from datetime import datetime, timezone

from pipeline.db import dict_cursor
from pipeline.telegram_api import send_message


def get_channel_chat_id(conn, channel: str) -> str:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT telegram_chat_id FROM channel_config WHERE channel = %s", (channel,))
        row = cur.fetchone()
    if not row or not row["telegram_chat_id"]:
        raise RuntimeError(
            f"channel_config.telegram_chat_id is not set for '{channel}' — check the "
            f"TELEGRAM_CHAT_ID_* env var referenced in config/channel_config.yaml."
        )
    return row["telegram_chat_id"]


def publish_post(conn, approval_id: int, channel: str, category: str, final_text: str,
                  label: str | None = None) -> int:
    """Sends final_text to the channel (with its notification label as an eyebrow
    line, Section 10) and records the post. Returns the new posts.id."""
    chat_id = get_channel_chat_id(conn, channel)

    body = f"<b>{label}</b>\n\n{final_text}" if label else final_text
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
