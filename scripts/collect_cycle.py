"""Orchestrates one full collect cycle: collect -> score -> notify (Section 2's
top-level flow, minus triage/write which only run on demand). This is what
.github/workflows/collect.yml actually invokes every 4-6h.

COLLECTORS is the single place that lists which categories are "live" for this
build phase (Section 16). Add a category here only once its collector, scorer
(pipeline/score.py), and prompt builder (pipeline/write_post.py) all exist —
score_new_items / write_post will raise a clear error otherwise rather than
silently skip.
"""

import logging
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

from bot import notify  # noqa: E402
from collectors import (  # noqa: E402
    defi_yields, exchange_listings, gems_security, hacks_exploits, macro_news, news, startup_jobs, tool_launches,
    whale_movements,
)
from pipeline import llm_usage  # noqa: E402
from pipeline.alerts import check_and_alert  # noqa: E402
from pipeline.db import get_conn  # noqa: E402
from pipeline.run_log import run_log  # noqa: E402
from pipeline.score import score_new_items  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

COLLECTORS = {
    "defi_yields": defi_yields.collect,
    "whale_movements": whale_movements.collect,
    # web3_jobs RETIRED 2026-11-14 (operator direction, Phase 2): its RemoteOK feed had been dry for two
    # months (3 notified cards in 60 days). The collector, scorer, prompt builder and category_config row
    # are left in place for history; to revive it, re-add it here and to channel_config.yaml.
    # gems_security runs LAST among the GoPlus-dependent categories,
    # deliberately -- its own GoPlus sweep shares pipeline.goplus's
    # module-level rate-limit pacing with defi_yields' scoring-time retrofit
    # (score_new_items below calls it too), so letting defi_yields' small,
    # always-small (~25/cycle) retrofit volume go first means gems_security's
    # larger budget sweep never gets starved by it.
    "gems_security": gems_security.collect,
    # news / tool_launches last -- no GoPlus/rate-limit interaction with
    # anything above, just simplest to append.
    "news": news.collect,
    "tool_launches": tool_launches.collect,
    "startup_jobs": startup_jobs.collect,
    "macro_news": macro_news.collect,
    # hacks_exploits, added 2026-09-30 -- DefiLlama /hacks + /protocols, no
    # GoPlus/rate-limit interaction with anything above, appended last.
    "hacks_exploits": hacks_exploits.collect,
    # exchange_listings, added 2026-11-15 (Phase 4) -- four keyless exchange announcement APIs, no GoPlus/rate-limit
    # interaction with anything above; per-exchange isolation lives inside the collector.
    "exchange_listings": exchange_listings.collect,
}


# Wall-clock ceiling per step (Linux/Railway only -- SIGALRM does not exist on Windows). A collector
# that HANGS (rather than raises) would otherwise hold the whole cycle forever; the slowest healthy
# step is gems_security at ~6 minutes. On expiry the step raises TimeoutError and is isolated like any
# other failure.
STEP_TIMEOUT_SECONDS = 25 * 60


def main():
    load_dotenv()

    # ISOLATION (2026-11-14, Phase 2): every step below is wrapped so one failing collector can
    # never take the rest of the cycle with it. Real incident, 2026-11-03: a single Etherscan timeout
    # in whale_movements propagated out of this loop and killed the seven collectors after it AND the
    # notify step -- a whole cycle lost to one slow API. Now a failing collector (or scorer) is logged
    # with its traceback, recorded, and the cycle moves on; notify still runs on whatever was
    # collected. The failure is not hidden: collect_X's own run_logs row already says 'failure', a
    # `collect_cycle` summary row lists what failed, the 3-consecutive-failures operator alert is
    # now actually wired for collectors (it only covered notify and the poller before), and the
    # process exits non-zero at the very end so the Railway deploy shows as failed.
    failures: list[str] = []

    def _on_timeout(signum, frame):
        raise TimeoutError(f"step exceeded {STEP_TIMEOUT_SECONDS}s")

    def guarded(step: str, fn):
        has_alarm = hasattr(signal, "SIGALRM")
        try:
            if has_alarm:
                signal.signal(signal.SIGALRM, _on_timeout)
                signal.alarm(STEP_TIMEOUT_SECONDS)
            return fn()
        except Exception:
            logger.exception("collect cycle: %s FAILED -- continuing with the rest of the cycle", step)
            failures.append(step)
            return None
        finally:
            if has_alarm:
                signal.alarm(0)

    def alert_if_repeating(job: str):
        conn = get_conn()
        try:
            check_and_alert(conn, job)
        except Exception:
            logger.exception("collect cycle: could not evaluate/send the repeated-failure alert for %s", job)
        finally:
            conn.close()

    for category, collect_fn in COLLECTORS.items():
        logger.info("Collecting %s...", category)
        inserted = guarded(f"collect_{category}", collect_fn)
        if inserted is None:
            alert_if_repeating(f"collect_{category}")  # collect_X's own run_logs row is the failure record
        else:
            logger.info("Collected %d new %s raw items", inserted, category)

        # Score even after a failed collect: items from earlier cycles may still be unscored, and
        # scoring a category with nothing new is a no-op.
        def score():
            conn = get_conn()
            try:
                scored = score_new_items(conn, category)
                logger.info("Scored %d new %s items", scored, category)
            finally:
                conn.close()
        guarded(f"score_{category}", score)

    logger.info("Running notify...")
    guarded("notify", notify.run)  # notify.run() records its own failure and alerts (bot/notify.py)

    # LLM cost guard (pipeline/llm_usage.py): balance + daily spend/call-count
    # alerts. Never raises.
    conn = get_conn()
    try:
        llm_usage.run_checks(conn)
    finally:
        conn.close()

    # One summary row per cycle: what failed, so a degraded cycle is a query, not a log trawl.
    conn = get_conn()
    try:
        with run_log(conn, "collect_cycle") as state:
            state["details"]["failed_steps"] = failures
            state["details"]["collectors"] = len(COLLECTORS)
            if failures:
                state["status"] = "partial_failure"
    finally:
        conn.close()

    if failures:
        logger.error("Collect cycle done WITH FAILURES: %s", ", ".join(failures))
        sys.exit(1)
    logger.info("Collect cycle done.")


if __name__ == "__main__":
    main()
