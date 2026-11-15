"""The 2-hour earning_opportunities cycle (Phase 5, 2026-11-15): collect -> score -> notify CoinCraft only, then exit.

Operator direction: this category runs every 2 hours, not 4. It is a separate entry point (and, once the Railway service
exists, a separate cron service -- `0 */2 * * *`) because the main cycle's notify visits EVERY channel and its pacing
(select_candidates.CYCLES_PER_DAY) assumes 4 hours; running that at 2 hours would double the other channels' per-cycle share.

Until the service exists, the main 4-hour cycle also runs this category (scripts/collect_cycle.py), so nothing waits on
infrastructure. Both are safe to run together: rows are keyed by (source, external_id), so a repeat inserts nothing.

Same isolation rules as the main cycle: every step is guarded, a failure is logged and recorded, the cycle continues, the
process exits non-zero at the end if anything failed.
"""

import logging
import sys

from dotenv import load_dotenv

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from bot import notify  # noqa: E402
from collectors import earning_opportunities  # noqa: E402
from pipeline import llm_usage  # noqa: E402
from pipeline.alerts import check_and_alert  # noqa: E402
from pipeline.db import get_conn  # noqa: E402
from pipeline.run_log import run_log  # noqa: E402
from pipeline.score import score_new_items  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CATEGORY = "earning_opportunities"
CHANNELS = ["coincraft"]


def main():
    load_dotenv()
    failures: list[str] = []

    def guarded(step: str, fn):
        try:
            fn()
        except Exception:
            logger.exception("collect_earning: step %s failed -- continuing", step)
            failures.append(step)

    def collect():
        try:
            earning_opportunities.collect()
        finally:
            conn = get_conn()
            try:
                check_and_alert(conn, "collect_earning_opportunities")
            finally:
                conn.close()

    def score():
        conn = get_conn()
        try:
            logger.info("Scored %d new %s items", score_new_items(conn, CATEGORY), CATEGORY)
        finally:
            conn.close()

    guarded("collect_earning_opportunities", collect)
    guarded("score_earning_opportunities", score)
    guarded("notify_earning", lambda: notify.run(only_channels=CHANNELS, job_name="notify_earning"))
    conn = get_conn()
    try:
        llm_usage.run_checks(conn)
    finally:
        conn.close()
    conn = get_conn()
    try:
        with run_log(conn, "collect_earning_cycle") as state:
            state["details"]["failed_steps"] = failures
            if failures:
                state["status"] = "partial_failure"
    finally:
        conn.close()
    if failures:
        logger.error("Earning cycle done WITH FAILURES: %s", ", ".join(failures))
        sys.exit(1)
    logger.info("Earning cycle done.")


if __name__ == "__main__":
    main()
