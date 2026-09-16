"""Builds and sends the per-cycle operator notification (Section 9, steps 1-2).
Runs at the end of every collect.yml cycle: for each channel, gather new
candidates across all of that channel's categories, and — only if there's at
least one — send ONE Telegram message bundling all of them, each with its own
inline Approve/Edit/Reject row.

Deliberately sends nothing when there are no new candidates: "you get pinged
when there's something to post," not on a fixed schedule (Section 9's whole
point, restated in Section 1's philosophy).

CHANNEL-LEVEL CAP wired up 2026-09-16 (real gap, operator direction: "exactly
the kind of silent no-op we've caught three times now") -- channel_config.
soft_daily_cap has been documented since day one (see pipeline/
select_candidates.py's own comment) as "the separate combined cap, applied by
the caller across all of a channel's categories" -- but nothing ever actually
read it here. A channel whose categories each stayed under their own
per-category cap could still have every category fire in the same cycle and
bundle into one oversized notification, with no channel-wide throttle at
all. Fixed below in notify_channel, same rolling-window mechanism and the
same age-bonus tie-break as the per-category cap (pipeline/select_candidates.
_age_bonus) -- consistent pacing logic at both levels, not two different
ideas of "soft cap."
"""

import html
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

from pipeline.alerts import check_and_alert  # noqa: E402
from pipeline.db import dict_cursor, get_conn  # noqa: E402
from pipeline.llm import generate_triage  # noqa: E402
from pipeline.run_log import run_log  # noqa: E402
from pipeline.score import category_config_exists, load_category_config  # noqa: E402
from pipeline.select_candidates import CAP_WINDOW_HOURS, _age_bonus, get_new_candidates  # noqa: E402
from pipeline.telegram_api import send_message  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _get_channels(conn) -> list[dict]:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT channel, categories, soft_daily_cap FROM channel_config")
        return cur.fetchall()


def _channel_notified_recent_count(conn, channel: str, window_hours: float = CAP_WINDOW_HOURS) -> int:
    """How many individual candidates (across every category) this channel
    has been sent in the trailing `window_hours` -- one notification can
    bundle several candidates, so this sums candidate_raw_item_ids' lengths,
    not COUNT(*) of notification rows."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT COALESCE(SUM(array_length(candidate_raw_item_ids, 1)), 0) AS n
               FROM notifications
               WHERE channel = %s AND sent_at > now() - (%s || ' hours')::interval""",
            (channel, window_hours),
        )
        return cur.fetchone()["n"] or 0


def notify_channel(conn, channel: str, categories: list[str], soft_daily_cap: int | None = None) -> int | None:
    all_candidates = []  # list of (category, row)
    for category in categories:
        # channel_config can list categories from a future build phase (Section
        # 16) before their collector/scorer/config exist — skip rather than
        # crash the whole channel's notification for the categories that are live.
        if not category_config_exists(conn, category):
            logger.warning(
                "channel=%s lists category=%s but it has no category_config row yet "
                "(not built/seeded) — skipping.", channel, category,
            )
            continue
        for row in get_new_candidates(conn, category, channel):
            all_candidates.append((category, row))

    if not all_candidates:
        return None

    # Channel-wide cap, on top of each category's own already-applied cap --
    # see module docstring. Same rolling-window + age-bonus tie-break as the
    # per-category cap, applied across the combined, cross-category list so
    # one channel's categories can't collectively overwhelm it even when each
    # stayed under its own individual limit.
    if soft_daily_cap is not None:
        already_recent = _channel_notified_recent_count(conn, channel)
        remaining = max(0, int(soft_daily_cap) - already_recent)
        if len(all_candidates) > remaining:
            # float(...) -- scores.score is NUMERIC/Decimal, _age_bonus returns
            # a plain float; see pipeline/select_candidates.py's same cast for
            # why this isn't cosmetic.
            now = datetime.now(timezone.utc)
            all_candidates.sort(
                key=lambda pair: float(pair[1]["score"]) + _age_bonus(pair[1].get("collected_at"), now),
                reverse=True,
            )
            trimmed = len(all_candidates) - remaining
            all_candidates = all_candidates[:remaining]
            logger.info(
                "notify: channel=%s soft_daily_cap trimmed %d over-cap candidate(s) "
                "(%d already sent in the last %.0fh, cap=%d)",
                channel, trimmed, already_recent, CAP_WINDOW_HOURS, soft_daily_cap,
            )

    if not all_candidates:
        return None

    # Build the whole message and keyboard BEFORE writing anything to the DB.
    # Buttons reference raw_item_id (not an approval_id) precisely so nothing
    # needs to exist in the DB yet to build them — notifications/approvals rows
    # are only ever written after send_message succeeds, further down. If the
    # send fails (rate limit, chat-not-found, transient network), NOTHING is
    # persisted, so these candidates remain "new" and get retried next cycle
    # instead of being silently and permanently marked as already-notified.
    lines = [f"🆕 <b>{len(all_candidates)} new candidate(s)</b> cleared threshold for <b>{channel}</b>\n"]
    keyboard_rows = []

    for category, row in all_candidates:
        cfg = load_category_config(conn, category)
        triage_line = generate_triage(conn, category, row)

        label = cfg.get("label") or category
        lines.append(
            f"<b>{html.escape(label)}</b> — score {row['score']}/100\n{html.escape(triage_line)}\n"
        )
        keyboard_rows.append([
            {"text": "✅ Approve", "callback_data": f"approve:{row['raw_item_id']}"},
            {"text": "✏️ Edit", "callback_data": f"edit:{row['raw_item_id']}"},
            {"text": "❌ Reject", "callback_data": f"reject:{row['raw_item_id']}"},
        ])

    text = "\n".join(lines)
    operator_chat_id = os.environ.get("TELEGRAM_OPERATOR_CHAT_ID")
    if not operator_chat_id:
        raise RuntimeError("TELEGRAM_OPERATOR_CHAT_ID is not set")

    result = send_message(operator_chat_id, text, reply_markup={"inline_keyboard": keyboard_rows})

    # Only now, with a real telegram_message_id in hand, persist the notification
    # and one 'pending' approval per candidate (approval_poller.py looks these up
    # by raw_item_id + decision='pending' when a button is tapped).
    with dict_cursor(conn) as cur:
        cur.execute(
            """INSERT INTO notifications (channel, candidate_raw_item_ids, telegram_message_id)
               VALUES (%s, %s, %s) RETURNING id""",
            (channel, [row["raw_item_id"] for _, row in all_candidates], result["message_id"]),
        )
        notification_id = cur.fetchone()["id"]
        for _, row in all_candidates:
            cur.execute(
                """INSERT INTO approvals (notification_id, raw_item_id, decision)
                   VALUES (%s, %s, 'pending')""",
                (notification_id, row["raw_item_id"]),
            )
    conn.commit()

    logger.info("notify: channel=%s candidates=%d notification_id=%s", channel, len(all_candidates), notification_id)
    return notification_id


def run() -> None:
    load_dotenv()  # no-op in CI (no .env there); picks up local .env when run directly
    conn = get_conn()
    try:
        with run_log(conn, "notify") as state:
            channels = _get_channels(conn)
            sent = 0
            for ch in channels:
                nid = notify_channel(conn, ch["channel"], ch["categories"], ch.get("soft_daily_cap"))
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
