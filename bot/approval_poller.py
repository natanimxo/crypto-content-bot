"""Polls Telegram getUpdates for operator taps (Section 9, step 3) and drives the
whole approval → write → publish state machine. Runs every 5-15 min via
approval-poll.yml — a fresh, stateless job each time, so all state (which update
we're up to, which approval is mid-edit, which preview is awaiting Publish/Cancel)
lives in Postgres, never in memory between runs.

Callback_data namespaces:
  approve:<approval_id>  reject:<approval_id>  edit:<approval_id>   — from the
      per-candidate digest buttons (bot/notify.py)
  pub_a:<preview_id>  pub_b:<preview_id>  pub_cancel:<preview_id>   — from the
      post-write preview (this module), pub_b only present during a benchmark trial
"""

import html
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.alerts import check_and_alert  # noqa: E402
from pipeline.db import dict_cursor, get_conn  # noqa: E402
from pipeline.publish import publish_post  # noqa: E402
from pipeline.run_log import run_log  # noqa: E402
from pipeline.score import load_category_config  # noqa: E402
from pipeline.telegram_api import answer_callback_query, get_updates, send_message  # noqa: E402
from pipeline.write_post import generate_post, generate_post_variants  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Section 9 step 4: "Pick one; it's a one-line config flag." True = always show a
# final Publish/Cancel confirm after writing (safer default). Flip to False to
# publish immediately on Approve/Edit and skip the confirm step.
REQUIRE_PUBLISH_CONFIRM = True


def _allowed_user_ids() -> set:
    raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def _operator_chat_id() -> str:
    chat_id = os.environ.get("TELEGRAM_OPERATOR_CHAT_ID")
    if not chat_id:
        raise RuntimeError("TELEGRAM_OPERATOR_CHAT_ID is not set")
    return chat_id


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


def _fetch_approval(conn, approval_id: int):
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT a.id AS approval_id, a.decision, a.raw_item_id, a.prompt_message_id,
                      r.category, r.payload, s.score, s.score_breakdown, n.channel
               FROM approvals a
               JOIN raw_items r ON r.id = a.raw_item_id
               JOIN scores s ON s.raw_item_id = r.id AND s.category = r.category
               JOIN notifications n ON n.id = a.notification_id
               WHERE a.id = %s""",
            (approval_id,),
        )
        return cur.fetchone()


def _fetch_preview(conn, preview_id: int):
    with dict_cursor(conn) as cur:
        cur.execute("SELECT * FROM post_previews WHERE id = %s", (preview_id,))
        return cur.fetchone()


def _send_preview_and_store(conn, approval_id: int, channel: str, category: str,
                             variant_a_model: str, variant_a_text: str,
                             variant_b_model: str | None = None, variant_b_text: str | None = None) -> int:
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

    if variant_b_model:
        text = (
            f"<b>Version A</b> ({html.escape(variant_a_model)}):\n{html.escape(variant_a_text)}\n\n"
            f"<b>Version B</b> ({html.escape(variant_b_model)}):\n{html.escape(variant_b_text)}\n\n"
            f"Publish which one?"
        )
        keyboard = [
            [{"text": "📤 Publish A", "callback_data": f"pub_a:{preview_id}"},
             {"text": "📤 Publish B", "callback_data": f"pub_b:{preview_id}"}],
            [{"text": "❌ Cancel", "callback_data": f"pub_cancel:{preview_id}"}],
        ]
    else:
        text = f"{html.escape(variant_a_text)}\n\nPublish this?"
        keyboard = [[
            {"text": "📤 Publish", "callback_data": f"pub_a:{preview_id}"},
            {"text": "❌ Cancel", "callback_data": f"pub_cancel:{preview_id}"},
        ]]

    result = send_message(_operator_chat_id(), text, reply_markup={"inline_keyboard": keyboard})
    with dict_cursor(conn) as cur:
        cur.execute("UPDATE post_previews SET telegram_message_id = %s WHERE id = %s",
                    (result["message_id"], preview_id))
    conn.commit()
    return preview_id


def _generate_and_preview(conn, approval: dict):
    category = approval["category"]
    channel = approval["channel"]
    cfg = load_category_config(conn, category)
    raw_item = {
        "raw_item_id": approval["raw_item_id"],
        "payload": approval["payload"],
        "score": approval["score"],
        "score_breakdown": approval["score_breakdown"],
    }

    if cfg["write_benchmark_status"] == "trial":
        variants = generate_post_variants(conn, category, raw_item)
        _send_preview_and_store(
            conn, approval["approval_id"], channel, category,
            "deepseek-v4-flash", variants["deepseek-v4-flash"],
            "claude-sonnet-5", variants["claude-sonnet-5"],
        )
    else:
        text = generate_post(conn, category, raw_item)
        _send_preview_and_store(conn, approval["approval_id"], channel, category, cfg["write_model"], text)


def _handle_approve(conn, approval_id: int, cq_id: str):
    approval = _fetch_approval(conn, approval_id)
    if not approval or approval["decision"] != "pending":
        answer_callback_query(cq_id, "Already handled.")
        return
    with dict_cursor(conn) as cur:
        cur.execute("UPDATE approvals SET decision = 'approved', decided_at = now() WHERE id = %s", (approval_id,))
    conn.commit()
    answer_callback_query(cq_id, "Approved — writing post...")
    _generate_and_preview(conn, approval)


def _handle_reject(conn, approval_id: int, cq_id: str):
    with dict_cursor(conn) as cur:
        cur.execute(
            "UPDATE approvals SET decision = 'rejected', decided_at = now() WHERE id = %s AND decision = 'pending'",
            (approval_id,),
        )
        updated = cur.rowcount
    conn.commit()
    answer_callback_query(cq_id, "Rejected." if updated else "Already handled.")


def _handle_edit_prompt(conn, approval_id: int, cq_id: str):
    approval = _fetch_approval(conn, approval_id)
    if not approval or approval["decision"] != "pending":
        answer_callback_query(cq_id, "Already handled.")
        return
    result = send_message(
        _operator_chat_id(),
        f"Reply to THIS message with the final post text for approval #{approval_id}.",
    )
    with dict_cursor(conn) as cur:
        cur.execute("UPDATE approvals SET prompt_message_id = %s WHERE id = %s", (result["message_id"], approval_id))
    conn.commit()
    answer_callback_query(cq_id, "Send your edit as a reply to my message.")


def _handle_publish(conn, preview_id: int, cq_id: str, variant: str):
    preview = _fetch_preview(conn, preview_id)
    if not preview or preview["status"] != "pending":
        answer_callback_query(cq_id, "Already handled.")
        return

    text = preview["variant_a_text"] if variant == "a" else preview["variant_b_text"]
    model_used = preview["variant_a_model"] if variant == "a" else preview["variant_b_model"]
    cfg = load_category_config(conn, preview["category"])

    publish_post(conn, preview["approval_id"], preview["channel"], preview["category"], text, label=cfg.get("label"))

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

    answer_callback_query(cq_id, f"Published ({model_used}).")


def _handle_cancel(conn, preview_id: int, cq_id: str):
    with dict_cursor(conn) as cur:
        cur.execute(
            "UPDATE post_previews SET status = 'cancelled' WHERE id = %s AND status = 'pending'",
            (preview_id,),
        )
        updated = cur.rowcount
    conn.commit()
    answer_callback_query(cq_id, "Cancelled — not published." if updated else "Already handled.")


def handle_callback_query(conn, cq: dict):
    user_id = cq.get("from", {}).get("id")
    if user_id not in _allowed_user_ids():
        answer_callback_query(cq["id"], "Not authorized.")
        return

    data = cq.get("data", "")
    if ":" not in data:
        answer_callback_query(cq["id"], "Bad request.")
        return
    action, id_str = data.split(":", 1)
    try:
        target_id = int(id_str)
    except ValueError:
        answer_callback_query(cq["id"], "Bad request.")
        return

    handlers = {
        "approve": lambda: _handle_approve(conn, target_id, cq["id"]),
        "reject": lambda: _handle_reject(conn, target_id, cq["id"]),
        "edit": lambda: _handle_edit_prompt(conn, target_id, cq["id"]),
        "pub_a": lambda: _handle_publish(conn, target_id, cq["id"], "a"),
        "pub_b": lambda: _handle_publish(conn, target_id, cq["id"], "b"),
        "pub_cancel": lambda: _handle_cancel(conn, target_id, cq["id"]),
    }
    handler = handlers.get(action)
    if not handler:
        answer_callback_query(cq["id"], "Unknown action.")
        return
    handler()


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
            """SELECT a.id AS approval_id, r.category, n.channel
               FROM approvals a
               JOIN raw_items r ON r.id = a.raw_item_id
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
    # step as an approved+written post.
    _send_preview_and_store(conn, row["approval_id"], row["channel"], row["category"], "operator_edited", text)


def process_update(conn, update: dict):
    if "callback_query" in update:
        handle_callback_query(conn, update["callback_query"])
    elif "message" in update:
        handle_message(conn, update["message"])


def run() -> None:
    conn = get_conn()
    try:
        with run_log(conn, "approval_poll") as state:
            offset = _get_last_update_id(conn)
            updates = get_updates(offset=(offset + 1) if offset else None)
            processed = 0
            for update in updates:
                try:
                    process_update(conn, update)
                except Exception:
                    logger.exception("Failed processing update %s", update.get("update_id"))
                finally:
                    _set_last_update_id(conn, update["update_id"])
                    processed += 1
            state["details"]["updates_processed"] = processed
        check_and_alert(conn, "approval_poll")
    except Exception:
        check_and_alert(conn, "approval_poll")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run()
