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

from pipeline import verbatim, written  # noqa: E402
from pipeline.alerts import check_and_alert  # noqa: E402
from pipeline.channel_router import SHARED_CATEGORY_CHANNELS  # noqa: E402
from pipeline.db import dict_cursor, get_conn  # noqa: E402
from pipeline.llm import generate_triage  # noqa: E402
from pipeline.publish import get_channel_display_name  # noqa: E402
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
               WHERE channel = %s AND kind = 'candidate' AND sent_at > now() - (%s || ' hours')::interval""",
            (channel, window_hours),
        )
        return cur.fetchone()["n"] or 0


def _order_channels_for_shared_caps(conn, channels: list[dict]) -> list[dict]:
    """Visit the channel that has had the LEAST of the shared categories' output
    in the trailing 24h first. A shared category (news) has one category-wide
    per-cycle slot (pipeline/select_candidates.py), and whoever is visited first
    takes it -- with a fixed order the first channel would take it every cycle
    and the other would starve. Least-served-first makes the slot alternate
    (3/3 over a day at a 6/day cap); ties go to the channel that was sent a
    shared-category item longest ago, then to name order. A channel with no
    eligible candidate this cycle sends nothing and the slot falls through to
    the next, so it is never wasted."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT nf.channel, COUNT(*) AS n, MAX(nf.sent_at) AS last_sent
               FROM notifications nf
               JOIN raw_items r ON r.id = ANY(nf.candidate_raw_item_ids)
               WHERE r.category = ANY(%s) AND nf.sent_at > now() - (%s || ' hours')::interval
               GROUP BY nf.channel""",
            (list(SHARED_CATEGORY_CHANNELS), CAP_WINDOW_HOURS),
        )
        stats = {row["channel"]: row for row in cur.fetchall()}
    epoch = datetime.min.replace(tzinfo=timezone.utc)

    def key(ch: dict):
        st = stats.get(ch["channel"])
        return (st["n"] if st else 0, st["last_sent"] if st else epoch, ch["channel"])

    return sorted(channels, key=key)


# Categories that carry TWO scores, shown side by side and never combined (operator direction, Phase 5): the card says
# "value 72 · trust 57", not a single number. For these, scores.score is the value (see pipeline/score.py).
DUAL_SCORE_CATEGORIES = {"earning_opportunities"}


def _score_text(category: str, row: dict) -> str:
    if category in DUAL_SCORE_CATEGORIES:
        b = row.get("score_breakdown") or {}
        if "value" in b and "trust" in b:
            text = f"value {float(b['value']):.0f} · trust {float(b['trust']):.0f}"
            ref = b.get("referral")
            if ref is not None:        # a fact beside the two scores, not part of either: can the reader invite others and earn
                text += " · referral " + ("yes" if ref.get("available") else "unconfirmed (source link carried a code)" if ref.get("link_code") else "no")
                if ref.get("multi_level"):                  # a WARNING LABEL only (operator 2026-11-17): multi-level / downline wording, no score effect
                    text += " · ⚠ multi-level wording"
            if b.get("kyc") is not None:                # a fact beside the scores (113 of 218 Immunefi programs require it): shown, never inferred
                text += " · KYC required" if b["kyc"] else " · no KYC"
            if b.get("monetizable"):                    # a deposit is asked / an exchange or bank sign-up / a referral program: the channel owner can earn from it
                text += " · 💸 monetizable"
            if b.get("skill"):                          # professional expertise needed (ML / engineering / security): a fact, shown beside the scores
                text += " · skill: " + ", ".join(b["skill"])
            return text
    return f"score {row['score']}/100"


def render_candidates(conn, channel: str, all_candidates: list) -> tuple[str, list]:
    """The candidate message and its keyboard for [(category, row)]. Also used to re-render an already-sent card in place (editMessageText)."""
    lines = [f"🆕 <b>{len(all_candidates)} new candidate(s)</b> cleared threshold for <b>{html.escape(get_channel_display_name(conn, channel))}</b>\n"]
    keyboard_rows = []
    for category, row in all_candidates:
        cfg = load_category_config(conn, category)
        triage_line = generate_triage(conn, category, row)
        label = cfg.get("label") or category
        lines.append(
            f"<b>{html.escape(label)}</b> — {_score_text(category, row)}\n{html.escape(triage_line)}\n"
        )
        keyboard_rows.append([
            {"text": "✅ Approve", "callback_data": f"approve:{row['raw_item_id']}"},
            {"text": "✏️ Edit", "callback_data": f"edit:{row['raw_item_id']}"},
            {"text": "❌ Reject", "callback_data": f"reject:{row['raw_item_id']}"},
        ])
    return "\n".join(lines), keyboard_rows


# Channels whose candidates arrive as FINISHED DRAFTS (operator 2026-11-20, Crypto Notebook): the post is written BEFORE the card is sent, the card IS the draft, and the operator only
# approves, edits or skips -- no second wait for a write after the tap. Each draft is its own message (a draft is ~900 characters; a bundle would hit Telegram's 4096 limit).
# The text is stored on the approval (approvals.draft_text) and Approve sends exactly that text as the forwardable preview. A candidate whose draft cannot be written (a guard kept
# failing, a model outage) is NOT lost: it goes out as the ordinary triage card, and Approve writes it then, as before.
DRAFT_FIRST_CHANNELS = {"crypto_notebook", "we_talk_a_lot"}
CHANNEL_PER_CYCLE_LIMIT = {"we_talk_a_lot": 2}   # cards per notify run for the whole channel, on top of each category's own per-cycle slot
DRAFT_CARD_MAX_CHARS = 3900


def render_draft_card(display_name: str, label: str, score_text: str, draft_html: str, raw_item_id: int, *, original_html: str | None = None, note: str | None = None) -> tuple[str, list]:
    """(message text, one keyboard row) for a finished draft. `draft_html` is already valid Telegram HTML (post_format.assemble_post), so it is NOT escaped again.
    `original_html` (the humor channel) is shown BELOW the draft so the operator can compare and edit from either; `note` is a warning line above the draft (e.g. the exact-words check failed)."""
    text = (f"✍️ <b>Finished draft for {html.escape(display_name)}</b>\n<b>{html.escape(label)}</b> — {score_text}\n"
            f"Approve to get it ready to forward, Edit to change it, Skip to drop it.\n" + (f"{note}\n" if note else "") + f"\n{draft_html}")
    if original_html:
        text += f"\n\n— — —\n{original_html}"
    keyboard = [
        {"text": "✅ Approve", "callback_data": f"approve:{raw_item_id}"},
        {"text": "✏️ Edit", "callback_data": f"edit:{raw_item_id}"},
        {"text": "❌ Skip", "callback_data": f"reject:{raw_item_id}"},
    ]
    return text, keyboard


def _try_draft(conn, category: str, channel: str, row: dict) -> str | None:
    """The finished post for this candidate, or None when it cannot be written (the caller then sends the ordinary card)."""
    from pipeline.write_post import generate_post          # imported here: the write path pulls the model clients, which a notify run without drafts never needs
    raw_item = {"raw_item_id": row["raw_item_id"], "payload": row["payload"], "collected_at": row.get("collected_at"), "score": row["score"], "score_breakdown": row.get("score_breakdown")}
    try:
        return generate_post(conn, category, channel, raw_item)
    except Exception:
        logger.exception("notify: no draft for raw_item_id=%s (%s) -- sending the ordinary card instead", row["raw_item_id"], category)
        return None


def _send_draft_cards(conn, channel: str, candidates: list, prebuilt: dict | None = None) -> tuple[list, int | None]:
    """Write and send a finished-draft card per candidate. Returns (the candidates that still need an ordinary card, the id of the last draft notification or None)."""
    operator_chat_id = os.environ.get("TELEGRAM_OPERATOR_CHAT_ID")
    if not operator_chat_id:
        raise RuntimeError("TELEGRAM_OPERATOR_CHAT_ID is not set")
    display_name = get_channel_display_name(conn, channel)
    rest, last_id = [], None
    for category, row in candidates:
        extra = {}
        if prebuilt and row["raw_item_id"] in prebuilt:
            # WRITTEN mode (pipeline/written.py): the model wrote this post before the card was assembled; the original source text and link go below the draft
            w = prebuilt[row["raw_item_id"]]
            draft = w["text"]
            extra = {"original_html": w["original_html"], "note": None}
        elif channel == verbatim.CHANNEL:
            # the source's exact words, formatted by code (pipeline/verbatim.py): never "no draft" -- a failed exact-words check returns the original unformatted, flagged on the card
            built = verbatim.build(conn, category, {"raw_item_id": row["raw_item_id"], "payload": row["payload"]})
            draft = built["text"]
            extra = {"original_html": built["original_html"], "note": None if built["ok"] else "⚠️ <b>Formatting check failed</b> -- this is the ORIGINAL text, unformatted."}
        else:
            draft = _try_draft(conn, category, channel, row)
        if not draft:
            rest.append((category, row))
            continue
        cfg = load_category_config(conn, category)
        text, keyboard = render_draft_card(display_name, cfg.get("label") or category, _score_text(category, row), draft, row["raw_item_id"], **extra)
        if len(text) > DRAFT_CARD_MAX_CHARS:
            logger.warning("notify: draft for raw_item_id=%s is %d characters -- too long for one card, sending the ordinary card", row["raw_item_id"], len(text))
            rest.append((category, row))
            continue
        result = send_message(operator_chat_id, text, reply_markup={"inline_keyboard": [keyboard]}, disable_web_page_preview=True)
        # persisted only after a successful send, as for every card: a failed send leaves the candidate "new" and it is retried next cycle
        with dict_cursor(conn) as cur:
            cur.execute("INSERT INTO notifications (channel, candidate_raw_item_ids, telegram_message_id) VALUES (%s, %s, %s) RETURNING id",
                        (channel, [row["raw_item_id"]], result["message_id"]))
            last_id = cur.fetchone()["id"]
            cur.execute("INSERT INTO approvals (notification_id, raw_item_id, decision, draft_text) VALUES (%s, %s, 'pending', %s)", (last_id, row["raw_item_id"], draft))
        conn.commit()
        logger.info("notify: channel=%s sent a finished draft for raw_item_id=%s notification_id=%s", channel, row["raw_item_id"], last_id)
    return rest, last_id


def notify_channel(conn, channel: str, categories: list[str], soft_daily_cap: int | None = None) -> int | None:
    all_candidates = []  # list of (category, row)
    prebuilt: dict = {}  # raw_item_id -> written post (written categories only)
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
        if channel == verbatim.CHANNEL and written.live_mode(category) == "written":
            # WRITTEN category (operator 2026-11-21): the model writes the post and may SKIP a boring item; a SKIP drops it (held) and the next candidate is tried, at most
            # written.MAX_SKIPS_PER_CATEGORY_PER_CYCLE drops per category per cycle. Only what the caps allow is kept; the rest are spares for replacements.
            cap_out: list = []
            pool = get_new_candidates(conn, category, channel, spare=written.MAX_SKIPS_PER_CATEGORY_PER_CYCLE, capacity_out=cap_out)
            capacity = cap_out[0] if cap_out else len(pool)
            if capacity <= 0:
                continue
            accepted, wstats = written.draw(conn, category, pool, capacity)
            logger.info("notify: written category=%s %s", category, wstats)
            for row, res in accepted:
                prebuilt[row["raw_item_id"]] = res
                all_candidates.append((category, row))
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

    # Per-cycle limit for a channel as a whole (operator 2026-11-20: We Talk A Lot, at most 2 cards per 4-hour cycle). The categories' own per-cycle slots add up to more
    # (weird_news 1 + satire 1 + jokes 1, and more as phases 2-3 add categories), so this trims the combined list, highest score first. The rest stay "new" and are offered next cycle.
    cycle_limit = CHANNEL_PER_CYCLE_LIMIT.get(channel)
    if cycle_limit is not None and len(all_candidates) > cycle_limit:
        all_candidates.sort(key=lambda pair: float(pair[1]["score"]), reverse=True)
        logger.info("notify: channel=%s per-cycle limit %d trimmed %d candidate(s)", channel, cycle_limit, len(all_candidates) - cycle_limit)
        all_candidates = all_candidates[:cycle_limit]

    if not all_candidates:
        return None

    draft_notification_id = None
    if channel in DRAFT_FIRST_CHANNELS:
        all_candidates, draft_notification_id = _send_draft_cards(conn, channel, all_candidates, prebuilt)
        if not all_candidates:
            return draft_notification_id

    # Build the whole message and keyboard BEFORE writing anything to the DB.
    # Buttons reference raw_item_id (not an approval_id) precisely so nothing
    # needs to exist in the DB yet to build them — notifications/approvals rows
    # are only ever written after send_message succeeds, further down. If the
    # send fails (rate limit, chat-not-found, transient network), NOTHING is
    # persisted, so these candidates remain "new" and get retried next cycle
    # instead of being silently and permanently marked as already-notified.
    text, keyboard_rows = render_candidates(conn, channel, all_candidates)
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


def run(only_channels: list[str] | None = None, job_name: str = "notify") -> None:
    """`only_channels` scopes a run to some channels (the 2-hour earning_opportunities service notifies Earn Stack only --
    running the full notify at 2 hours would double every other channel's per-cycle pacing); `job_name` keeps that run's
    run_logs row and its 3-consecutive-failures alert separate from the main cycle's."""
    load_dotenv()  # no-op in CI (no .env there); picks up local .env when run directly
    conn = get_conn()
    try:
        with run_log(conn, job_name) as state:
            channels = _order_channels_for_shared_caps(conn, _get_channels(conn))
            if only_channels is not None:
                channels = [c for c in channels if c["channel"] in only_channels]
            sent = 0
            for ch in channels:
                nid = notify_channel(conn, ch["channel"], ch["categories"], ch.get("soft_daily_cap"))
                if nid:
                    sent += 1
            state["details"]["notifications_sent"] = sent
        check_and_alert(conn, job_name)
    except Exception:
        check_and_alert(conn, job_name)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run()
