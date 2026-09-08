"""Builds and sends the per-cycle operator notification (Section 9, steps 1-2).
Runs at the end of every collect.yml cycle: for each channel, gather new
candidates across all of that channel's categories, and — only if there's at
least one — send ONE Telegram message bundling all of them, each with its own
inline Approve/Edit/Reject row.

Deliberately sends nothing when there are no new candidates: "you get pinged
when there's something to post," not on a fixed schedule (Section 9's whole
point, restated in Section 1's philosophy).
"""

import html
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.alerts import check_and_alert  # noqa: E402
from pipeline.db import dict_cursor, get_conn  # noqa: E402
from pipeline.llm import generate_triage  # noqa: E402
from pipeline.run_log import run_log  # noqa: E402
from pipeline.score import load_category_config  # noqa: E402
from pipeline.select_candidates import get_new_candidates  # noqa: E402
from pipeline.telegram_api import send_message  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _get_channels(conn) -> list[dict]:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT channel, categories FROM channel_config")
        return cur.fetchall()


def notify_channel(conn, channel: str, categories: list[str]) -> int | None:
    all_candidates = []  # list of (category, row)
    for category in categories:
        for row in get_new_candidates(conn, category, channel):
            all_candidates.append((category, row))

    if not all_candidates:
        return None

    with dict_cursor(conn) as cur:
        cur.execute(
            "INSERT INTO notifications (channel, candidate_raw_item_ids) VALUES (%s, %s) RETURNING id",
            (channel, [row["raw_item_id"] for _, row in all_candidates]),
        )
        notification_id = cur.fetchone()["id"]
    conn.commit()

    lines = [f"🆕 <b>{len(all_candidates)} new candidate(s)</b> cleared threshold for <b>{channel}</b>\n"]
    keyboard_rows = []

    for category, row in all_candidates:
        cfg = load_category_config(conn, category)
        triage_line = generate_triage(conn, category, row)

        with dict_cursor(conn) as cur:
            cur.execute(
                """INSERT INTO approvals (notification_id, raw_item_id, decision)
                   VALUES (%s, %s, 'pending') RETURNING id""",
                (notification_id, row["raw_item_id"]),
            )
            approval_id = cur.fetchone()["id"]
        conn.commit()

        label = cfg.get("label") or category
        lines.append(
            f"<b>{html.escape(label)}</b> — score {row['score']}/100\n{html.escape(triage_line)}\n"
        )
        keyboard_rows.append([
            {"text": "✅ Approve", "callback_data": f"approve:{approval_id}"},
            {"text": "✏️ Edit", "callback_data": f"edit:{approval_id}"},
            {"text": "❌ Reject", "callback_data": f"reject:{approval_id}"},
        ])

    text = "\n".join(lines)
    operator_chat_id = os.environ.get("TELEGRAM_OPERATOR_CHAT_ID")
    if not operator_chat_id:
        raise RuntimeError("TELEGRAM_OPERATOR_CHAT_ID is not set")

    result = send_message(operator_chat_id, text, reply_markup={"inline_keyboard": keyboard_rows})

    with dict_cursor(conn) as cur:
        cur.execute(
            "UPDATE notifications SET telegram_message_id = %s WHERE id = %s",
            (result["message_id"], notification_id),
        )
    conn.commit()

    logger.info("notify: channel=%s candidates=%d notification_id=%s", channel, len(all_candidates), notification_id)
    return notification_id


def run() -> None:
    conn = get_conn()
    try:
        with run_log(conn, "notify") as state:
            channels = _get_channels(conn)
            sent = 0
            for ch in channels:
                nid = notify_channel(conn, ch["channel"], ch["categories"])
                if nid:
                    sent += 1
            state["details"]["notifications_sent"] = sent
        check_and_alert(conn, "notify")
    except Exception:
        check_and_alert(conn, "notify")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run()
