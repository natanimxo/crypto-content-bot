"""The daily "below the line" digest for Earn Stack (operator 2026-11-16).

The operator does not trust the ratings yet and does not want to miss anything -- referral offers above all. Once a day this sends ONE batch of
messages listing every earning_opportunities item that failed only a SOFT floor, with its scores and the reason, each with an Approve button that
goes through the normal write flow (the approval row is created exactly as notify.py creates it, against the same channel).

NOTHING DISAPPEARS SILENTLY (operator 2026-11-17). Two kinds of item land here, each with its reason:
  * a SOFT-floor failure: trust < 40, value < 40, a RAFFLE's expected payout < $10 (never a guaranteed payout: a $2 deposit bonus must pass), a competition's largest prize under the
    size floor, or a token-denominated reward (value capped at 30);
  * a HARD-GATED item (seed-phrase wording, connect-wallet-to-claim, upfront-fee wording, a GoPlus-flagged token contract): carried with a RED label and the gate's reason. The
    operator handles security himself; the gate keeps such an item out of the normal cards, the digest keeps it in sight.
STILL EXCLUDED (the operator's own rules): not income (discounts, free games, freebies, credits), roundup posts (their individual offers are extracted and processed instead), spam.
There is NO recruitment filter: multi-level wording is a warning label on the line, nothing more.

Why it does not touch the cap of 8: these notifications are stored with notifications.kind = 'below_line', and every pacing / cap query counts only
kind = 'candidate'. They DO count as "already notified" (never re-sent) and their topic enters cooldown, like any card.

    python -m bot.below_line --dry-run     # print what would be sent, send and store nothing
    python -m bot.below_line               # send now (the 2-hour earning cycle calls run_daily(), which sends at most once a day)
"""

import html
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import earning  # noqa: E402
from pipeline.db import dict_cursor, get_conn  # noqa: E402
from pipeline.publish import get_channel_display_name  # noqa: E402
from pipeline.run_log import run_log  # noqa: E402
from pipeline.select_candidates import _already_notified_ids, _earning_still_valid  # noqa: E402
from pipeline.telegram_api import send_message  # noqa: E402

logger = logging.getLogger(__name__)

CATEGORY = "earning_opportunities"
CHANNEL = "coincraft"
JOB = "below_line_digest"
MIN_HOURS_BETWEEN = 20            # the daily guard
DEFAULT_LOOKBACK_HOURS = 26       # first run, or after a missed day: items collected since the last digest, at most this far back unless the last digest is older
MAX_LOOKBACK_HOURS = 72
MAX_MESSAGE_CHARS = 3600          # Telegram's limit is 4096; leave room for the header
MAX_ITEMS_PER_MESSAGE = 20


def reasons_for(breakdown: dict, payload: dict) -> list[str]:
    """Every soft floor this item failed, as short plain phrases. [] means it clears all of them (so it is not a below-the-line item)."""
    out = []
    trust, value = float(breakdown.get("trust", 0)), float(breakdown.get("value", 0))
    if trust < earning.TRUST_FLOOR:
        out.append(f"trust {trust:.0f} < {earning.TRUST_FLOOR:.0f}")
    capped = breakdown.get("value_capped_by")
    if capped:
        out.append(f"reward paid in {capped.get('token') or 'a token'} (not a stablecoin): value capped at {capped.get('cap'):.0f}")
    elif value < earning.VALUE_FLOOR:
        out.append(f"value {value:.0f} < {earning.VALUE_FLOOR:.0f}")
    if breakdown.get("award_kind") == "competition":
        top = breakdown.get("top_prize_usd")
        if top is not None and float(top) < earning.prizes.COMPETITION_MIN_TOP_PRIZE_USD:
            out.append("competition: largest prize is under the 100 floor (dollar equivalent)")
    ep = breakdown.get("expected_payout_usd")
    if breakdown.get("award_kind") == "raffle" and ep is not None and float(ep) < earning.EXPECTED_PAYOUT_FLOOR:
        out.append(f"raffle: expected payout ${float(ep):,.2f} < ${earning.EXPECTED_PAYOUT_FLOOR:.0f}")
    return out


def referral_text(breakdown: dict) -> str:
    ref = breakdown.get("referral") or {}
    return "referral yes" if ref.get("available") else "referral unconfirmed" if ref.get("link_code") else "referral no"


# gate reasons the digest SHOWS (a security-flavoured exclusion the operator reviews himself); every other gate reason (roundup, not income) stays out
_VISIBLE_GATE_MARKERS = ("seed phrase", "connect-wallet-to-claim", "upfront payment", "GoPlus")


def visible_gate_reasons(payload: dict) -> list[str]:
    reasons = ((payload or {}).get("gate") or {}).get("reasons") or []
    if reasons and all(any(m in r for m in _VISIBLE_GATE_MARKERS) for r in reasons):
        return list(reasons)
    return []


def select_items(conn, now: datetime, since: datetime) -> list[dict]:
    """Live, scored, still valid, never notified, collected since `since`: every item that failed a SOFT floor, plus every HARD-GATED item whose reasons are all security-flavoured
    (marked `gated`). Ordered best value first."""
    notified = _already_notified_ids(conn, CATEGORY)
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT r.id AS raw_item_id, r.payload, r.collected_at, r.held, s.score, s.score_breakdown
               FROM raw_items r JOIN scores s ON s.raw_item_id = r.id
               WHERE r.category = %s AND r.expired_at IS NULL AND r.collected_at > %s""",
            (CATEGORY, since))
        rows = cur.fetchall()
    items = []
    for r in rows:
        if r["raw_item_id"] in notified or not _earning_still_valid(r["payload"], now):
            continue
        if r["held"]:
            gate = visible_gate_reasons(r["payload"] or {})
            if gate:
                items.append({**r, "reasons": gate, "gated": True})
            continue
        why = reasons_for(r["score_breakdown"] or {}, r["payload"] or {})
        if why:
            items.append({**r, "reasons": why, "gated": False})
    items.sort(key=lambda r: -float(r["score_breakdown"].get("value", 0)))
    return items


def format_item(n: int, it: dict) -> str:
    b, p = it["score_breakdown"], it["payload"]
    ep = b.get("expected_payout_usd")
    kyc = b.get("kyc")
    # a COMPETITION shows its prize structure and odds, never an expected amount; amounts are in their ORIGINAL currency
    money = b["prize_line"] if b.get("award_kind") == "competition" and b.get("prize_line") else f"expected {'$' + format(float(ep), ',.0f') if ep is not None else 'unknown'}"
    facts = [f"value {float(b['value']):.0f}", f"trust {float(b['trust']):.0f}", money, referral_text(b)]
    if b.get("skill"):
        facts.append("skill: " + ", ".join(b["skill"]))
    if kyc is not None:
        facts.append("KYC required" if kyc else "no KYC")
    if (b.get("referral") or {}).get("multi_level"):
        facts.append("⚠ multi-level wording")
    if b.get("monetizable"):
        facts.append("💸 monetizable")
    title = html.escape((p.get("title") or "")[:90])
    red = "🔴 <b>GATED</b> " if it.get("gated") else ""
    return (f"<b>{n}.</b> {red}{title}\n"
            f"   {html.escape(str(p.get('source_name') or p.get('source')))} · {html.escape(str(p.get('type') or ''))} · " + " · ".join(facts) + "\n"
            f"   <i>{'gate' if it.get('gated') else 'why'}: {html.escape('; '.join(it['reasons']))}</i>")


def build_messages(items: list[dict], display_name: str) -> list[tuple[str, list[dict]]]:
    """[(text, items_in_this_message)] -- as few messages as fit (Telegram caps a message at 4096 characters and a keyboard at 100 buttons)."""
    chunks, cur, size = [], [], 0
    for it in items:
        line = format_item(len(cur) + 1, it)
        if cur and (size + len(line) > MAX_MESSAGE_CHARS or len(cur) >= MAX_ITEMS_PER_MESSAGE):
            chunks.append(cur); cur, size = [], 0
        cur.append(it); size += len(line) + 1
    if cur:
        chunks.append(cur)
    out = []
    for i, chunk in enumerate(chunks, 1):
        head = (f"📉 <b>Below the line — {html.escape(display_name)}</b> ({i}/{len(chunks)})\n"
                f"{len(items)} item(s) failed a soft floor or were hard-gated (🔴); each has an Approve button that goes through the normal write flow.\n")
        out.append((head + "\n" + "\n".join(format_item(n, it) for n, it in enumerate(chunk, 1)), chunk))
    return out


def send_digest(conn, *, dry_run: bool = False, now: datetime | None = None, since_hours: float | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    with dict_cursor(conn) as cur:
        cur.execute("SELECT max(started_at) AS t FROM run_logs WHERE job_name = %s AND status = 'success'", (JOB,))
        last = cur.fetchone()["t"]
    since = max(last or (now - timedelta(hours=DEFAULT_LOOKBACK_HOURS)), now - timedelta(hours=MAX_LOOKBACK_HOURS))
    if since_hours is not None:            # manual preview: look back this far regardless of the last digest
        since = now - timedelta(hours=since_hours)
    items = select_items(conn, now, since)
    display = get_channel_display_name(conn, CHANNEL)
    messages = build_messages(items, display) if items else []
    if dry_run:
        for text, chunk in messages:
            print(text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")); print(f"   [{len(chunk)} Approve buttons]\n")
        return {"items": len(items), "messages": len(messages), "sent": 0}
    operator_chat_id = os.environ.get("TELEGRAM_OPERATOR_CHAT_ID")
    if not operator_chat_id:
        raise RuntimeError("TELEGRAM_OPERATOR_CHAT_ID is not set")
    sent = 0
    for text, chunk in messages:
        keyboard = [[{"text": f"✅ Approve {n}", "callback_data": f"approve:{it['raw_item_id']}"}] for n, it in enumerate(chunk, 1)]
        result = send_message(operator_chat_id, text, reply_markup={"inline_keyboard": keyboard})
        with dict_cursor(conn) as cur:                   # same rows notify.py writes, so the normal approval flow finds them; kind keeps them out of the cap
            cur.execute("INSERT INTO notifications (channel, candidate_raw_item_ids, telegram_message_id, kind) VALUES (%s, %s, %s, 'below_line') RETURNING id",
                        (CHANNEL, [it["raw_item_id"] for it in chunk], result["message_id"]))
            nid = cur.fetchone()["id"]
            for it in chunk:
                cur.execute("INSERT INTO approvals (notification_id, raw_item_id, decision) VALUES (%s, %s, 'pending')", (nid, it["raw_item_id"]))
        conn.commit()
        sent += 1
    return {"items": len(items), "messages": len(messages), "sent": sent}


def run_daily(conn=None) -> dict | None:
    """Called from every group-B cycle (every 4 hours, 02 06 10 14 18 22 UTC): sends at most once per MIN_HOURS_BETWEEN, and not before 06:00 UTC (09:00 for the operator)."""
    own = conn is None
    conn = conn or get_conn()
    try:
        now = datetime.now(timezone.utc)
        if now.hour < 6:
            return None
        with dict_cursor(conn) as cur:
            cur.execute("SELECT 1 FROM run_logs WHERE job_name = %s AND status = 'success' AND started_at > now() - (%s || ' hours')::interval LIMIT 1", (JOB, MIN_HOURS_BETWEEN))
            if cur.fetchone():
                return None
        with run_log(conn, JOB) as state:
            result = send_digest(conn, now=now)
            state["details"].update(result)
        return result
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    c = get_conn()
    try:
        hours = float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else None
        print(send_digest(c, dry_run="--dry-run" in sys.argv, since_hours=hours))
    finally:
        c.close()
