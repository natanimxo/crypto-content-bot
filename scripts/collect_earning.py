"""GROUP B cycle (operator 2026-11-21, pipeline/cycle_groups.py): Hustle to Million, Earn Stack (coincraft) and We Talk A Lot, every 4 hours at 02 06 10 14 18 22 UTC (cron `0 2-23/4 * * *`,
Railway service `collect-earning`). It collects only group B's categories (tool launches, startup jobs, macro news, startup news, earning opportunities, and the humor channel's three news / satire / jokes
collectors and six libraries), scores them and notifies ONLY group B's channels, then runs the extras that used to ride the 2-hour Earn Stack cycle: the daily below-the-line digest (at most once a day, not
before 06:00 UTC, see bot/below_line.py) and the Railway-side source probes. The file keeps its old name so the Railway service and its start command (`python scripts/collect_earning.py`) do not change.

Earn Stack moved from every 2 hours to every 4: its per-cycle share of the daily cap is now 1/6, so ceil(8 / 6) = 2 cards a cycle and the daily total stays 8 (select_candidates.CYCLES_PER_DAY_BY_CATEGORY is empty now).
Group A (Crypto Wall Street, Alpha Edge Crypto, Crypto Notebook) is scripts/collect_cycle.py; both share `collect_cycle.run_cycle`: every step guarded, a failure logged and recorded, the cycle continues, non-zero exit at the end.
"""

import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import collect_cycle  # noqa: E402  (scripts/collect_cycle.py: run_cycle and the one COLLECTORS list)
from pipeline.db import get_conn  # noqa: E402
from pipeline.run_log import run_log  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def probe():
    """Once a day, probe the candidate income sources FROM HERE (Railway) and record what they returned: a source is not verified
    until it has run on Railway -- OKX served a different feed from there and Bybit refused it. Read-only; see pipeline/source_probe.py."""
    from pipeline import source_probe
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT details FROM run_logs WHERE job_name = 'source_probe' AND started_at > now() - interval '20 hours' ORDER BY started_at DESC LIMIT 1")
            row = cur.fetchone()
            results_prev = ((row[0] or {}).get("results") or []) if row else []
            done_ids = {r.get("id") for r in results_prev} if row else None
            article_row = next((r for r in results_prev if r.get("id") == "article_probe"), None)
            # probe_all() also appends the dynamic "article_probe" (not in PROBES): it is part of the expected set, and a stored one without its per-outlet list is the old format, probed again once
            if done_ids is not None and done_ids == {p["id"] for p in source_probe.PROBES} | {"article_probe"} and article_row and "fetched" in article_row:
                return                  # probed within 20h AND with the same probe set; a new probe is verified at the next cycle
        results = source_probe.probe_all()
        keep = ("id", "dir", "status", "kind", "items", "newest", "bytes", "secs", "auth_or_blocked", "login_wall", "error", "sample", "html_title", "snippet", "articles", "newest_post", "content_range", "fetched", "opened")
        with run_log(conn, "source_probe") as state:
            state["details"]["results"] = [{k: r[k] for k in keep if k in r} for r in results]
    finally:
        conn.close()


def tg_probe():
    """Telegram channels the operator asked for, probed FROM RAILWAY (see source_probe.TELEGRAM_CANDIDATES); re-runs when the candidate set changes."""
    from pipeline import source_probe
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT details FROM run_logs WHERE job_name = 'telegram_probe' AND started_at > now() - interval '20 hours' ORDER BY started_at DESC LIMIT 1")
            row = cur.fetchone()
            if row and {r.get("id") for r in (row[0] or {}).get("results", [])} == {"tg:" + h for h in source_probe.TELEGRAM_CANDIDATES}:
                return
        results = source_probe.probe_telegram_all()
        with run_log(conn, "telegram_probe") as state:
            state["details"]["results"] = results
    finally:
        conn.close()


def extras(guarded):
    """Steps after the group's notify: a slow probe must never delay a card, which is why they come last."""
    guarded("below_line_digest", lambda: __import__("bot.below_line", fromlist=["run_daily"]).run_daily())   # at most once a day; see bot/below_line.py
    guarded("source_probe", probe)
    guarded("telegram_probe", tg_probe)


def main():
    load_dotenv()
    collect_cycle.run_cycle("B", after_notify=extras)


if __name__ == "__main__":
    main()
