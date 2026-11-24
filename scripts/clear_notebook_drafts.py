"""One-off cleanup after Crypto Notebook stopped being draft-first (operator 2026-11-24).

  python scripts/clear_notebook_drafts.py            # dry run: lists what it would do
  python scripts/clear_notebook_drafts.py --apply    # does it

1. Every PENDING Crypto Notebook draft-first approval (approvals.draft_text set) is EXPIRED (decision 'expired', expired_reason 'draft_first_removed'; never 'rejected', so calibration stays clean) -- done first, so a tap on a
   card that is about to disappear finds nothing pending.
2. Every Crypto Notebook draft card the bot sent (the notification's own message, not the forwardable previews) is deleted from the operator's chat where Telegram allows it. A bot may delete its own messages in a
   private chat for 48 hours; older ones are refused and reported, never retried.
We Talk A Lot's draft cards are NOT touched (that channel is still draft-first).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from pipeline.db import dict_cursor, get_conn          # noqa: E402
from pipeline.telegram_api import delete_message       # noqa: E402

CHANNEL = "crypto_notebook"


def main(apply: bool) -> None:
    chat_id = os.environ.get("TELEGRAM_OPERATOR_CHAT_ID")
    if not chat_id:
        raise SystemExit("TELEGRAM_OPERATOR_CHAT_ID is not set")
    conn = get_conn()
    try:
        with dict_cursor(conn) as cur:
            cur.execute("""SELECT a.id AS approval_id, a.decision, n.id AS notification_id, n.telegram_message_id, n.sent_at,
                                  (n.sent_at > now() - interval '48 hours') AS deletable
                           FROM approvals a JOIN notifications n ON n.id = a.notification_id
                           WHERE n.channel = %s AND a.draft_text IS NOT NULL ORDER BY n.id""", (CHANNEL,))
            rows = cur.fetchall()
        pending = [r for r in rows if r["decision"] == "pending"]
        print(f"{len(rows)} Crypto Notebook draft cards ({len(pending)} pending); {sum(1 for r in rows if r['deletable'])} are inside Telegram's 48-hour window")
        if not apply:
            for r in rows:
                print("  would handle", r["approval_id"], r["decision"], "msg", r["telegram_message_id"], str(r["sent_at"])[:16], "deletable" if r["deletable"] else "too old")
            return
        with dict_cursor(conn) as cur:
            cur.execute("""UPDATE approvals a SET decision = 'expired', expired_reason = 'draft_first_removed', decided_at = now()
                           FROM notifications n WHERE n.id = a.notification_id AND n.channel = %s AND a.draft_text IS NOT NULL AND a.decision = 'pending'""", (CHANNEL,))
            print("expired pending approvals:", cur.rowcount)
        conn.commit()
        ok = refused = 0
        for r in rows:
            if not r["telegram_message_id"]:
                continue
            done = delete_message(chat_id, r["telegram_message_id"])
            ok += bool(done)
            refused += not done
            print("  msg", r["telegram_message_id"], "approval", r["approval_id"], str(r["sent_at"])[:16], "deleted" if done else ("REFUSED (too old or already gone)" if not r["deletable"] else "REFUSED (already gone?)"))
        print(f"deleted {ok}, refused {refused}")
    finally:
        conn.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
