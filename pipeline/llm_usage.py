"""LLM spend tracking + cost guard (2026-10-06).

Why this exists, real incident: a misconfigured category (hacks_exploits in
a dual-model trial mode, since removed, with a missing API key) made the approval poller's
recovery sweep re-run a paid DeepSeek write ~19,000 times over six days. Every
call succeeded on the DeepSeek half and then raised, so nothing was stored, the
operator saw nothing, and the DeepSeek balance went from $1.97 to $0.07.
Nothing recorded token usage -- the response's `usage` block was thrown away --
so the only way to reconstruct it was a manual estimate. (That estimate, from
measured per-call tokens, came to $1.91 against the observed $1.90.)

TWO KINDS OF GUARD, both alerting the operator on Telegram.

NEAR-REAL-TIME ANOMALY GUARDS (added 2026-10-07 -- the daily guards below
would have spoken a full day into the incident and then gone quiet while it
kept running). Evaluated on every LLM call, and they RE-ALERT every
LLM_REALERT_MINUTES for as long as the condition holds, so a runaway cannot
go quiet after one message:
  rate     more than LLM_RATE_ALERT_CALLS calls in the last
           LLM_RATE_WINDOW_MINUTES (failed calls count)
  repeat   the SAME prompt sent LLM_REPEAT_ALERT_COUNT+ times within
           LLM_REPEAT_WINDOW_MINUTES. This is the retry loop's signature,
           independent of volume: every one of its ~19,000 calls was an
           identical prompt, and every one SUCCEEDED at the LLM -- the
           failure happened afterwards, in code this module never sees --
           so 'failed calls' can't be what detects it, but a repeated
           identical prompt can, whichever caller is looping.

DAILY BACKSTOP GUARDS, at most once per UTC day per kind (llm_spend_alerts
claims the slot BEFORE sending and releases it if the send fails, so an
alert is neither spammed nor silently lost):
  spend    today's estimated spend crossed LLM_DAILY_SPEND_ALERT_USD
  calls    today's call count crossed LLM_DAILY_CALLS_ALERT -- counts FAILED
           calls too, and doesn't depend on a price table being right, so it
           still catches a runaway loop if prices change or a call never
           returns usage
  balance  the DeepSeek balance fell below LLM_BALANCE_ALERT_USD (its own
           /user/balance endpoint) -- the failure the operator actually hit

Thresholds are env vars on the Railway service (defaults below are ~100x a
normal day, which is a handful of cheap writes, and below what the incident
burned: ~$0.32/day, ~3,200 calls/day).

record() runs on every LLM call; run_checks() adds the balance check and runs
once per collect cycle (scripts/collect_cycle.py).

COST ESTIMATE is an estimate, not a bill: DeepSeek Flash rates from
api-docs.deepseek.com/quick_start/pricing as of 2026-10-06, with peak
(01-04 and 06-10 UTC, Mon-Fri) at 2x. The page also says Chinese public
holidays are off-peak; that isn't modelled, so on those days this slightly
OVER-estimates, which is the safe direction for a guard. A provider added
later needs its own rate in estimate_cost_usd (unknown providers cost 0 here,
so the call-count guard is the one that still protects it).
"""

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

from pipeline.db import dict_cursor
from pipeline.telegram_api import send_message

logger = logging.getLogger(__name__)

DEFAULT_DAILY_SPEND_ALERT_USD = 0.10
DEFAULT_DAILY_CALLS_ALERT = 150
DEFAULT_BALANCE_ALERT_USD = 0.50

# USD per 1M tokens
_DEEPSEEK = {"off": {"hit": 0.003, "miss": 0.15, "out": 0.6}, "peak": {"hit": 0.006, "miss": 0.30, "out": 1.2}}


DEFAULT_RATE_WINDOW_MINUTES = 15
DEFAULT_RATE_ALERT_CALLS = 20
DEFAULT_REPEAT_WINDOW_MINUTES = 30
DEFAULT_REPEAT_ALERT_COUNT = 5
DEFAULT_REALERT_MINUTES = 60


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _is_deepseek_peak(ts: datetime) -> bool:
    return ts.weekday() < 5 and ts.hour in (1, 2, 3, 6, 7, 8, 9)


def estimate_cost_usd(provider: str, usage: dict | None, now: datetime | None = None) -> float | None:
    if not usage:
        return None
    now = now or datetime.now(timezone.utc)
    if provider == "deepseek":
        r = _DEEPSEEK["peak" if _is_deepseek_peak(now) else "off"]
        hit = usage.get("cache_hit_tokens") or 0
        miss = (usage.get("prompt_tokens") or 0) - hit
        return (hit * r["hit"] + miss * r["miss"] + (usage.get("completion_tokens") or 0) * r["out"]) / 1e6
    return 0.0  # gemini free tier / any provider without a rate yet


def prompt_fingerprint(prompt: str) -> str:
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]


def record(conn, *, provider: str, model: str, category: str | None, usage: dict | None, ok: bool,
           prompt_hash: str | None = None) -> None:
    """Log one LLM call (successful or not) and run the spend/call-count
    guards. Must never raise into the caller: a metering failure must not
    take a real write down with it -- but it logs loudly, never silently."""
    try:
        cost = estimate_cost_usd(provider, usage)
        u = usage or {}
        with dict_cursor(conn) as cur:
            cur.execute(
                """INSERT INTO llm_usage (provider, model, category, prompt_tokens, cache_hit_tokens,
                                          completion_tokens, est_cost_usd, ok, prompt_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (provider, model, category, u.get("prompt_tokens"), u.get("cache_hit_tokens"),
                 u.get("completion_tokens"), cost, ok, prompt_hash),
            )
        conn.commit()
        _check_anomaly(conn)
        _check_daily(conn)
    except Exception:
        logger.exception("llm_usage: could not record/check usage (call itself is unaffected)")
        try:
            conn.rollback()
        except Exception:
            pass


def today_totals(conn) -> dict:
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT COUNT(*) AS calls, COALESCE(SUM(est_cost_usd), 0)::float AS spend,
                      COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                      COALESCE(SUM(completion_tokens), 0) AS completion_tokens
               FROM llm_usage WHERE (ts AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date"""
        )
        return dict(cur.fetchone())


def _alert_once_per_day(conn, kind: str, text: str) -> bool:
    """Claim today's slot for this kind, send, and release the claim if the
    send fails so the next call retries instead of the alert being lost."""
    with dict_cursor(conn) as cur:
        cur.execute(
            """INSERT INTO llm_spend_alerts (day, kind) VALUES ((now() AT TIME ZONE 'UTC')::date, %s)
               ON CONFLICT DO NOTHING RETURNING kind""",
            (kind,),
        )
        claimed = cur.fetchone() is not None
    conn.commit()
    if not claimed:
        return False
    chat_id = os.environ.get("TELEGRAM_OPERATOR_CHAT_ID")
    try:
        if not chat_id:
            raise RuntimeError("TELEGRAM_OPERATOR_CHAT_ID is not set")
        send_message(chat_id, text)
        logger.error("llm_usage: ALERT sent (%s): %s", kind, text.replace("\n", " | "))
        return True
    except Exception:
        logger.exception("llm_usage: could not send %s alert -- releasing the slot so it retries", kind)
        with dict_cursor(conn) as cur:
            cur.execute("DELETE FROM llm_spend_alerts WHERE day = (now() AT TIME ZONE 'UTC')::date AND kind = %s", (kind,))
        conn.commit()
        return False


def _realert_claim(conn, kind: str, key: str, now: datetime, realert_minutes: float):
    """Atomically claim the right to alert for (kind, key): True if never
    alerted, or last alerted at least `realert_minutes` ago. Returns
    (claimed, previous_alerted_at) so a failed send can put things back."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT last_alerted_at FROM llm_anomaly_alerts WHERE kind = %s AND key = %s", (kind, key))
        row = cur.fetchone()
        prev = row["last_alerted_at"] if row else None
        cur.execute(
            """INSERT INTO llm_anomaly_alerts (kind, key, last_alerted_at) VALUES (%s, %s, %s)
               ON CONFLICT (kind, key) DO UPDATE SET last_alerted_at = EXCLUDED.last_alerted_at
                 WHERE llm_anomaly_alerts.last_alerted_at <= %s - make_interval(secs => %s)
               RETURNING key""",
            (kind, key, now, now, realert_minutes * 60.0),
        )
        claimed = cur.fetchone() is not None
    conn.commit()
    return claimed, prev


def _send_anomaly_alert(conn, kind: str, key: str, text: str, now: datetime, realert_minutes: float) -> bool:
    claimed, prev = _realert_claim(conn, kind, key, now, realert_minutes)
    if not claimed:
        return False
    chat_id = os.environ.get("TELEGRAM_OPERATOR_CHAT_ID")
    try:
        if not chat_id:
            raise RuntimeError("TELEGRAM_OPERATOR_CHAT_ID is not set")
        send_message(chat_id, text)
        logger.error("llm_usage: ANOMALY ALERT sent (%s/%s): %s", kind, key, text.replace("\n", " | "))
        return True
    except Exception:
        logger.exception("llm_usage: could not send %s anomaly alert -- restoring state so it retries", kind)
        with dict_cursor(conn) as cur:
            if prev is None:
                cur.execute("DELETE FROM llm_anomaly_alerts WHERE kind = %s AND key = %s", (kind, key))
            else:
                cur.execute("UPDATE llm_anomaly_alerts SET last_alerted_at = %s WHERE kind = %s AND key = %s",
                            (prev, kind, key))
        conn.commit()
        return False


def _check_anomaly(conn, now: datetime | None = None) -> None:
    """Near-real-time rate + repeated-prompt guards. `now` is injectable so the
    re-alert cadence can be tested by simulation instead of waiting hours."""
    now = now or datetime.now(timezone.utc)
    rate_win = _env_float("LLM_RATE_WINDOW_MINUTES", DEFAULT_RATE_WINDOW_MINUTES)
    rate_cap = int(_env_float("LLM_RATE_ALERT_CALLS", DEFAULT_RATE_ALERT_CALLS))
    rep_win = _env_float("LLM_REPEAT_WINDOW_MINUTES", DEFAULT_REPEAT_WINDOW_MINUTES)
    rep_cap = int(_env_float("LLM_REPEAT_ALERT_COUNT", DEFAULT_REPEAT_ALERT_COUNT))
    realert = _env_float("LLM_REALERT_MINUTES", DEFAULT_REALERT_MINUTES)

    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT COUNT(*) AS calls, COALESCE(SUM(est_cost_usd), 0)::float AS spend
               FROM llm_usage WHERE ts > %s AND ts <= %s""",
            (now - timedelta(minutes=rate_win), now),
        )
        w = dict(cur.fetchone())
        cur.execute(
            """SELECT prompt_hash, COUNT(*) AS n, MAX(category) AS category,
                      COALESCE(SUM(est_cost_usd), 0)::float AS spend, MIN(ts) AS first_ts
               FROM llm_usage WHERE ts > %s AND ts <= %s AND prompt_hash IS NOT NULL
               GROUP BY prompt_hash HAVING COUNT(*) >= %s ORDER BY n DESC""",
            (now - timedelta(minutes=rep_win), now, rep_cap),
        )
        repeats = cur.fetchall()

    if w["calls"] >= rate_cap:
        _send_anomaly_alert(
            conn, "rate", "all",
            f"🚨 <b>LLM call-rate anomaly</b>\n{w['calls']} LLM calls in the last {rate_win:g} minutes "
            f"(alert at {rate_cap}; normal use is a few calls an hour). ~${w['spend']:.3f} in that window.\n"
            f"Something may be looping -- check the Railway poller logs now. "
            f"I'll repeat this every {realert:g} min while it continues.",
            now, realert,
        )
    for r in repeats:
        _send_anomaly_alert(
            conn, "repeat", r["prompt_hash"],
            f"🔁 <b>Same LLM prompt sent {r['n']}x in {rep_win:g} minutes</b>\n"
            f"Category: {r['category'] or '?'} · prompt {r['prompt_hash']} · ~${r['spend']:.4f} so far. "
            f"A normal write sends a given prompt once (a capped retry sends it up to 3x).\n"
            f"This is the shape of a retry loop -- check the Railway poller logs now. "
            f"I'll repeat this every {realert:g} min while it continues.",
            now, realert,
        )


def _check_daily(conn) -> None:
    spend_cap = _env_float("LLM_DAILY_SPEND_ALERT_USD", DEFAULT_DAILY_SPEND_ALERT_USD)
    calls_cap = int(_env_float("LLM_DAILY_CALLS_ALERT", DEFAULT_DAILY_CALLS_ALERT))
    t = today_totals(conn)
    if t["spend"] >= spend_cap:
        _alert_once_per_day(
            conn, "spend",
            f"💸 <b>LLM spend alert</b>\nEstimated spend today (UTC): ${t['spend']:.3f} across {t['calls']} calls "
            f"(threshold ${spend_cap:g}). A normal day is well under a cent.\n"
            f"Something may be looping -- check the Railway poller logs for repeated write failures.",
        )
    if t["calls"] >= calls_cap:
        _alert_once_per_day(
            conn, "calls",
            f"🔁 <b>LLM call-count alert</b>\n{t['calls']} LLM calls so far today (UTC), threshold {calls_cap}. "
            f"A normal day is single digits to low tens. Counts failed calls too.\n"
            f"Something may be looping -- check the Railway poller logs.",
        )


def fetch_deepseek_balance_usd() -> float | None:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    resp = requests.get("https://api.deepseek.com/user/balance",
                        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}, timeout=20)
    resp.raise_for_status()
    for info in resp.json().get("balance_infos", []):
        if info.get("currency") == "USD":
            return float(info["total_balance"])
    return None


def run_checks(conn) -> None:
    """Once per collect cycle: re-run the daily guards (covers a cycle with no
    LLM calls) and check the DeepSeek balance. Never raises."""
    try:
        _check_daily(conn)
        floor = _env_float("LLM_BALANCE_ALERT_USD", DEFAULT_BALANCE_ALERT_USD)
        balance = fetch_deepseek_balance_usd()
        if balance is not None and balance < floor:
            _alert_once_per_day(
                conn, "balance",
                f"🪫 <b>DeepSeek balance low</b>\n${balance:.2f} left (alert threshold ${floor:g}). "
                f"Writes will start failing when it hits zero -- top up at platform.deepseek.com.",
            )
    except Exception:
        logger.exception("llm_usage: run_checks failed")
