"""Polls Telegram getUpdates for operator taps (Section 9, step 3) and drives the
whole approval → write → publish state machine. Runs every 5-15 min via
approval-poll.yml — a fresh, stateless job each time, so all state (which update
we're up to, which approval is mid-edit, which preview is awaiting Publish/Cancel)
lives in Postgres, never in memory between runs.

Callback_data namespaces:
  approve:<raw_item_id>  reject:<raw_item_id>  edit:<raw_item_id>   — from the
      per-candidate digest buttons (bot/notify.py). Keyed by raw_item_id, not an
      approval_id, because notify.py only creates the approvals row AFTER its
      Telegram send succeeds — there's nothing to reference yet when the buttons
      are built. Looked up here as "the pending approval for this raw_item_id".
  mark_sent_a:<preview_id>  mark_sent_b:<preview_id>  discard:<preview_id>   — from
      the post-write preview (this module), mark_sent_b only present during a
      benchmark trial. "Mark as sent" logs to `posts` for history/dedup — as of
      2026-09-10 the bot never posts to a channel itself (see pipeline/publish.py);
      the operator copies/forwards the labeled text themselves.
"""

import html
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

from pipeline.alerts import check_and_alert  # noqa: E402
from pipeline.db import dict_cursor, get_conn  # noqa: E402
from pipeline.publish import get_channel_display_name, mark_as_sent  # noqa: E402
from pipeline.run_log import run_log  # noqa: E402
from pipeline.score import load_category_config  # noqa: E402
from pipeline.telegram_api import answer_callback_query, get_updates, send_message  # noqa: E402
from pipeline.write_post import generate_post, generate_post_variants  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Live-diagnosed 2026-09-10/11: a deliberate test (tap -> poll immediately vs.
# tap -> poll after exactly 10 minutes, nothing else touching Telegram in
# between) showed a tap succeeds 2/2 when polled within seconds and vanishes
# entirely — not just unanswerable, genuinely absent from getUpdates — after
# 10 minutes. This is separate from (and in addition to) the already-known
# "answerCallbackQuery expires" issue _safe_ack handles; a callback_query can
# disappear from the delivery queue itself well under Telegram's documented
# 24h retention for ordinary updates. That makes short-polling (timeout=0,
# "check once, return immediately") fundamentally incompatible with a 5-15
# minute cron — most taps would need to land in the handful of seconds right
# after a scheduled run happens to fire.
#
# Fix, without adding a server (keeping Section 2's zero-infrastructure
# design): long-poll instead. Telegram delivers an update the INSTANT it
# occurs while a getUpdates call with timeout>0 is open — it doesn't wait for
# the timeout to elapse. Holding a long-poll open for most of the gap between
# cron firings, back-to-back, shrinks the blind window from "up to 15
# minutes" to roughly the 10-20s between one run ending and the next
# starting. approval-poll.yml's job timeout and this value need to move
# together — see that file's comment.
LONG_POLL_TIMEOUT_SECONDS = 270  # 4m30s -- leaves headroom in a 6-minute job for setup + any writes


def _allowed_user_ids() -> set:
    raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def _operator_chat_id() -> str:
    chat_id = os.environ.get("TELEGRAM_OPERATOR_CHAT_ID")
    if not chat_id:
        raise RuntimeError("TELEGRAM_OPERATOR_CHAT_ID is not set")
    return chat_id


def _safe_ack(cq_id: str, text: str | None = None) -> None:
    """answer_callback_query, but a failure here (almost always Telegram's
    'query is too old' — live-observed 2026-09-09 to happen even on the FIRST
    answer attempt for some taps, seemingly from delivery-latency variance in
    Telegram's own getUpdates backend, not anything on our end) must never
    propagate and abort the caller. The toast this shows the operator is purely
    cosmetic; the DB state change and any deferred write are what actually
    matter, and a real bug already happened here once: an unguarded
    answer_callback_query() raising mid-_handle_approve silently dropped the
    'return approval' that queues the deferred LLM write, leaving an item
    marked approved with no post ever generated for it (raw_item_id=1902).
    Every acknowledgment in this module goes through this wrapper now."""
    try:
        answer_callback_query(cq_id, text)
    except Exception:
        logger.warning("Failed to ack callback_query_id=%s (likely stale) — continuing anyway", cq_id)


def _get_last_update_id(conn):
    with dict_cursor(conn) as cur:
        cur.execute("SELECT last_update_id FROM telegram_poll_state WHERE id = 1")
        row = cur.fetchone()
    return row["last_update_id"] if row else None


def _set_last_update_id(conn, update_id: int):
    with dict_cursor(conn) as cur:
        cur.execute(
            """INSERT INTO telegram_poll_state (id, last_update_id) VALUES (1, %s)
               ON CONFLICT (id) DO UPDATE SET last_update_id = EXCLUDED.last_update_id""",
            (update_id,),
        )
    conn.commit()


def _fetch_pending_approval(conn, raw_item_id: int):
    """The 'pending' approval for this raw_item_id, if any — there's at most one,
    since a raw_item only ever appears in one notification (Section 7's
    already-notified check) and therefore only ever gets one approvals row."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT a.id AS approval_id, a.decision, a.raw_item_id, a.prompt_message_id,
                      r.category, r.payload, r.collected_at, s.score, s.score_breakdown, n.channel
               FROM approvals a
               JOIN raw_items r ON r.id = a.raw_item_id
               JOIN scores s ON s.raw_item_id = r.id AND s.category = r.category
               JOIN notifications n ON n.id = a.notification_id
               WHERE a.raw_item_id = %s AND a.decision = 'pending'""",
            (raw_item_id,),
        )
        return cur.fetchone()


def _fetch_preview(conn, preview_id: int):
    with dict_cursor(conn) as cur:
        cur.execute("SELECT * FROM post_previews WHERE id = %s", (preview_id,))
        return cur.fetchone()


def _label_header(conn, category: str, channel: str) -> str:
    """'🌾 DEFI YIELDS → Crypto Notebook' — sits at the very top of what reaches
    the operator, since (2026-09-10) they copy/forward this text themselves and
    need to know at a glance which of the 5 channels it's for."""
    cfg = load_category_config(conn, category)
    label = cfg.get("label") or category
    display_name = get_channel_display_name(conn, channel)
    return f"{label} → {display_name}"


def _send_preview_and_store(conn, approval_id: int, channel: str, category: str, score,
                             variant_a_model: str, variant_a_text: str,
                             variant_b_model: str | None = None, variant_b_text: str | None = None) -> int:
    """Sends TWO (or three, in a benchmark trial) separate Telegram messages
    (2026-09-10, STEP 1 of the delivery/formatting overhaul):
      1. A routing header — operator-only: label, channel, score, and the
         Mark as sent/Discard buttons. Never forwarded.
      2. (+3.) The actual post(s) — already fully-formed HTML from
         pipeline.write_post (real <b>/<blockquote> tags baked in by
         post_format.assemble_post, NOT re-escaped here — escaping already-
         valid HTML again would corrupt it). No buttons, so it forwards
         cleanly; the operator sends this exact message unedited.
    """
    with dict_cursor(conn) as cur:
        cur.execute(
            """INSERT INTO post_previews
                   (approval_id, channel, category, variant_a_model, variant_a_text,
                    variant_b_model, variant_b_text, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending') RETURNING id""",
            (approval_id, channel, category, variant_a_model, variant_a_text, variant_b_model, variant_b_text),
        )
        preview_id = cur.fetchone()["id"]
    conn.commit()

    label_line = html.escape(_label_header(conn, category, channel))
    score_line = f"Score {score}/100" if score is not None else ""

    if variant_b_model:
        header_text = (
            f"<b>{label_line}</b>\n{score_line}\n\n"
            f"Benchmark trial — Version A ({html.escape(variant_a_model)}) is the next message below, "
            f"Version B ({html.escape(variant_b_model)}) the one after. Mark whichever you send."
        )
        keyboard = [
            [{"text": "✅ Mark A as sent", "callback_data": f"mark_sent_a:{preview_id}"},
             {"text": "✅ Mark B as sent", "callback_data": f"mark_sent_b:{preview_id}"}],
            [{"text": "❌ Discard", "callback_data": f"discard:{preview_id}"}],
        ]
    else:
        header_text = f"<b>{label_line}</b>\n{score_line}"
        keyboard = [[
            {"text": "✅ Mark as sent", "callback_data": f"mark_sent_a:{preview_id}"},
            {"text": "❌ Discard", "callback_data": f"discard:{preview_id}"},
        ]]

    header_result = send_message(_operator_chat_id(), header_text, reply_markup={"inline_keyboard": keyboard})
    content_a_result = send_message(_operator_chat_id(), variant_a_text, disable_web_page_preview=True)
    content_b_message_id = None
    if variant_b_model:
        content_b_result = send_message(_operator_chat_id(), variant_b_text, disable_web_page_preview=True)
        content_b_message_id = content_b_result["message_id"]

    with dict_cursor(conn) as cur:
        cur.execute(
            """UPDATE post_previews
               SET telegram_message_id = %s, content_message_id = %s, content_b_message_id = %s
               WHERE id = %s""",
            (header_result["message_id"], content_a_result["message_id"], content_b_message_id, preview_id),
        )
    conn.commit()
    return preview_id


def _generate_and_preview(conn, approval: dict):
    category = approval["category"]
    channel = approval["channel"]
    cfg = load_category_config(conn, category)
    raw_item = {
        "raw_item_id": approval["raw_item_id"],
        "payload": approval["payload"],
        "collected_at": approval["collected_at"],
        "score": approval["score"],
        "score_breakdown": approval["score_breakdown"],
    }

    if cfg["write_benchmark_status"] == "trial":
        variants = generate_post_variants(conn, category, channel, raw_item)
        _send_preview_and_store(
            conn, approval["approval_id"], channel, category, approval["score"],
            "deepseek-v4-flash", variants["deepseek-v4-flash"],
            "claude-sonnet-5", variants["claude-sonnet-5"],
        )
    else:
        text = generate_post(conn, category, channel, raw_item)
        _send_preview_and_store(conn, approval["approval_id"], channel, category, approval["score"],
                                 cfg["write_model"], text)


def _handle_approve(conn, raw_item_id: int, cq_id: str) -> dict | None:
    """Fast phase only: mark approved and ack the tap. Returns the approval row
    if the caller (run()) should now do the slow LLM write for it, else None.

    Deliberately does NOT call _generate_and_preview() here — see the "why
    two-phase" note on run(). A live bug (2026-09-09): when this used to
    generate-and-send inline, a 3-tap batch (approve+reject+edit landing in the
    same poll) failed with 'query is too old and response timeout expired' on
    the 2nd and 3rd taps, because the 1st tap's benchmark-trial dual LLM
    generate (DeepSeek + Sonnet, both real API round trips) ran before the loop
    ever reached the other two callback_query_ids — Telegram's callback
    validity window doesn't wait for us. Every tap in a batch is now acked
    before any tap's slow work begins.
    """
    approval = _fetch_pending_approval(conn, raw_item_id)
    if not approval:
        _safe_ack(cq_id, "Already handled.")
        return None
    with dict_cursor(conn) as cur:
        cur.execute("UPDATE approvals SET decision = 'approved', decided_at = now() WHERE id = %s",
                     (approval["approval_id"],))
    conn.commit()
    _safe_ack(cq_id, "Approved — writing post...")
    return approval


def _handle_reject(conn, raw_item_id: int, cq_id: str):
    approval = _fetch_pending_approval(conn, raw_item_id)
    if not approval:
        _safe_ack(cq_id, "Already handled.")
        return
    with dict_cursor(conn) as cur:
        cur.execute("UPDATE approvals SET decision = 'rejected', decided_at = now() WHERE id = %s",
                     (approval["approval_id"],))
    conn.commit()
    _safe_ack(cq_id, "Rejected.")


def _handle_edit_prompt(conn, raw_item_id: int, cq_id: str):
    approval = _fetch_pending_approval(conn, raw_item_id)
    if not approval:
        _safe_ack(cq_id, "Already handled.")
        return
    result = send_message(
        _operator_chat_id(),
        f"Reply to THIS message with the final post text for approval #{approval['approval_id']}.",
    )
    with dict_cursor(conn) as cur:
        cur.execute("UPDATE approvals SET prompt_message_id = %s WHERE id = %s",
                     (result["message_id"], approval["approval_id"]))
    conn.commit()
    _safe_ack(cq_id, "Send your edit as a reply to my message.")


def _handle_mark_sent(conn, preview_id: int, cq_id: str, variant: str):
    """No Telegram send here — the operator has already copied/forwarded the
    text themselves. This just logs it to `posts` for history/dedup."""
    preview = _fetch_preview(conn, preview_id)
    if not preview or preview["status"] != "pending":
        _safe_ack(cq_id, "Already handled.")
        return

    text = preview["variant_a_text"] if variant == "a" else preview["variant_b_text"]
    model_used = preview["variant_a_model"] if variant == "a" else preview["variant_b_model"]

    mark_as_sent(conn, preview["approval_id"], preview["channel"], preview["category"], text)

    with dict_cursor(conn) as cur:
        cur.execute(
            "UPDATE post_previews SET status = 'published', chosen_variant = %s WHERE id = %s",
            (variant, preview_id),
        )
    conn.commit()

    if preview["variant_b_model"]:  # this was a benchmark trial comparison
        with dict_cursor(conn) as cur:
            cur.execute(
                """INSERT INTO write_benchmark (approval_id, category, deepseek_text, sonnet_text, operator_chose)
                   VALUES (%s, %s, %s, %s, %s)""",
                (
                    preview["approval_id"], preview["category"],
                    preview["variant_a_text"], preview["variant_b_text"],
                    "deepseek" if variant == "a" else "sonnet",
                ),
            )
        conn.commit()

    _safe_ack(cq_id, f"Marked as sent ({model_used}).")


def _handle_discard(conn, preview_id: int, cq_id: str):
    with dict_cursor(conn) as cur:
        cur.execute(
            "UPDATE post_previews SET status = 'cancelled' WHERE id = %s AND status = 'pending'",
            (preview_id,),
        )
        updated = cur.rowcount
    conn.commit()
    _safe_ack(cq_id, "Discarded." if updated else "Already handled.")


def handle_callback_query(conn, cq: dict) -> dict | None:
    """Fast phase: ack the tap and do any cheap (non-LLM) state update. Returns
    an approval row if slow LLM write work is still owed for it (only the
    'approve' action ever returns non-None) — run() does that in a second pass,
    after every callback_query in the batch has already been acked."""
    user_id = cq.get("from", {}).get("id")
    if user_id not in _allowed_user_ids():
        _safe_ack(cq["id"], "Not authorized.")
        return None

    data = cq.get("data", "")
    if ":" not in data:
        _safe_ack(cq["id"], "Bad request.")
        return None
    action, id_str = data.split(":", 1)
    try:
        target_id = int(id_str)
    except ValueError:
        _safe_ack(cq["id"], "Bad request.")
        return None

    if action == "approve":
        return _handle_approve(conn, target_id, cq["id"])
    elif action == "reject":
        _handle_reject(conn, target_id, cq["id"])
    elif action == "edit":
        _handle_edit_prompt(conn, target_id, cq["id"])
    elif action == "mark_sent_a":
        _handle_mark_sent(conn, target_id, cq["id"], "a")
    elif action == "mark_sent_b":
        _handle_mark_sent(conn, target_id, cq["id"], "b")
    elif action == "discard":
        _handle_discard(conn, target_id, cq["id"])
    else:
        _safe_ack(cq["id"], "Unknown action.")
    return None


def handle_message(conn, msg: dict):
    user_id = msg.get("from", {}).get("id")
    if user_id not in _allowed_user_ids():
        return
    reply_to = msg.get("reply_to_message")
    text = msg.get("text")
    if not reply_to or not text:
        return

    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT a.id AS approval_id, r.category, n.channel, s.score
               FROM approvals a
               JOIN raw_items r ON r.id = a.raw_item_id
               JOIN scores s ON s.raw_item_id = r.id AND s.category = r.category
               JOIN notifications n ON n.id = a.notification_id
               WHERE a.prompt_message_id = %s AND a.decision = 'pending'""",
            (reply_to["message_id"],),
        )
        row = cur.fetchone()
    if not row:
        return

    with dict_cursor(conn) as cur:
        cur.execute(
            "UPDATE approvals SET decision = 'edited', edited_text = %s, decided_at = now() WHERE id = %s",
            (text, row["approval_id"]),
        )
    conn.commit()

    # Edited text IS the final post (Section 9 step 5: "the next poll picks it up
    # as the final version") — no LLM call, straight to the same preview/confirm
    # step as an approved+written post. Unlike LLM-generated posts (already
    # valid HTML from post_format.assemble_post), this is the operator's raw
    # typed text — it must be escaped here, once, so _send_preview_and_store's
    # invariant ("variant text is always ready-to-send HTML") holds for every
    # caller. approvals.edited_text above keeps the raw, unescaped original.
    content_html = html.escape(text, quote=False)
    _send_preview_and_store(conn, row["approval_id"], row["channel"], row["category"], row["score"],
                             "operator_edited", content_html)


def process_update(conn, update: dict) -> dict | None:
    if "callback_query" in update:
        return handle_callback_query(conn, update["callback_query"])
    elif "message" in update:
        handle_message(conn, update["message"])
    return None


def run() -> None:
    """Two-phase per poll, not one pass per update — see _handle_approve's
    docstring for the live bug this fixes. Phase 1 acks every update in the
    batch (fast: DB state checks + answerCallbackQuery, no LLM calls), so a
    slow write for one candidate can never cause another candidate's tap to go
    stale waiting behind it. Phase 2 then does the actual (possibly slow) LLM
    writes, one approval at a time, now that nothing is waiting on them."""
    load_dotenv()  # no-op in CI (no .env there); picks up local .env when run directly
    conn = get_conn()
    try:
        with run_log(conn, "approval_poll") as state:
            offset = _get_last_update_id(conn)
            requested_offset = (offset + 1) if offset else None
            updates = get_updates(offset=requested_offset, timeout=LONG_POLL_TIMEOUT_SECONDS)

            # Log exactly what this run saw, before doing anything with it — a
            # 2026-09-09 live session spent a long manual DB/API investigation
            # reconstructing which update_ids a run actually received after the
            # fact, because success was silent and only failures were logged.
            # This makes that reconstructible directly from run_logs next time.
            update_summary = [
                {
                    "update_id": u["update_id"],
                    "kind": "callback_query" if "callback_query" in u else ("message" if "message" in u else "other"),
                    "data": u.get("callback_query", {}).get("data"),
                }
                for u in updates
            ]
            logger.info("poll: requested_offset=%s fetched=%d %s", requested_offset, len(updates), update_summary)

            deferred_approvals = []
            processed = 0
            for update in updates:
                try:
                    result = process_update(conn, update)
                    if result:
                        deferred_approvals.append(result)
                    logger.info("poll: processed update_id=%s ok", update["update_id"])
                except Exception:
                    logger.exception("Failed processing update %s", update.get("update_id"))
                finally:
                    _set_last_update_id(conn, update["update_id"])
                    processed += 1

            for approval in deferred_approvals:
                try:
                    _generate_and_preview(conn, approval)
                    logger.info("poll: generated write for approval_id=%s", approval["approval_id"])
                except Exception:
                    logger.exception("Failed generating post for approval_id=%s", approval["approval_id"])

            state["details"]["requested_offset"] = requested_offset
            state["details"]["fetched_update_ids"] = [u["update_id"] for u in updates]
            state["details"]["updates_processed"] = processed
            state["details"]["writes_generated"] = len(deferred_approvals)
        check_and_alert(conn, "approval_poll")
    except Exception:
        check_and_alert(conn, "approval_poll")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run()
