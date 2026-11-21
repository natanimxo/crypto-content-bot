"""Orchestrates one collect cycle: collect -> score -> notify (Section 2's
top-level flow, minus triage/write which only run on demand).

TWO STAGGERED GROUPS (operator 2026-11-21, pipeline/cycle_groups.py): this script is GROUP A -- Crypto Wall Street, Alpha Edge Crypto, Crypto Notebook, cron `0 */4 * * *`
(00 04 08 12 16 20 UTC), Railway service `collect-cycle`: it collects only group A's categories and notifies only group A's channels. Group B (Hustle to Million, Earn Stack, We Talk A Lot, cron
`0 2-23/4 * * *`) is scripts/collect_earning.py, which reuses `run_cycle` below. COLLECTORS stays the one list of every live category; the group decides which of them a run executes.

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
    defi_yields, earning_opportunities, exchange_listings, gems_security, hacks_exploits, macro_news, news, startup_jobs, startup_news,
    rekt, technique_explainer, tool_launches, whale_alert, whale_movements, wtal_jokes, wtal_library, wtal_news, zachxbt,
)
from pipeline import cycle_groups, llm_usage  # noqa: E402
from pipeline.alerts import check_and_alert  # noqa: E402
from pipeline.db import get_conn  # noqa: E402
from pipeline.run_log import run_log  # noqa: E402
from pipeline.score import score_new_items  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# A collector that feeds ANOTHER category's items (a second source for the same category) is scored as that category.
SCORED_AS = {"whale_alert": "whale_movements", "zachxbt": "hacks_exploits", "rekt": "hacks_exploits"}

COLLECTORS = {
    "defi_yields": defi_yields.collect,
    "whale_movements": whale_movements.collect,
    # Whale Alert's official Telegram channel -> whale_movements (2026-11-16): BTC and other chains, transfers only, deduped by tx hash against the
    # Etherscan collector above (each skips the other's hashes). After whale_movements so the Etherscan side has stored first.
    "whale_alert": whale_alert.collect,
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
    # ZachXBT's investigations channel -> hacks_exploits (2026-11-16): reports an incident hours-to-days before DefiLlama; deduped by protocol name.
    "zachxbt": zachxbt.collect,
    # Rekt.news post-mortems -> hacks_exploits (2026-11-20): the homepage lists them (the RSS feed is essays only); matched to the incident by name, skipped when the operator already approved that story.
    "rekt": rekt.collect,
    # technique of the week (2026-11-20) -> Crypto Notebook: one explainer per ISO week on an attack type, from the incidents stored above (so after hacks_exploits and zachxbt).
    "technique_explainer": technique_explainer.collect,
    # We Talk A Lot (humor channel, 2026-11-20), phase 1: two polled categories and the jokes library (bulk once, monthly refresh; a no-op on every other cycle). Items are the sources' exact words (pipeline/verbatim.py).
    "weird_news": wtal_news.collect_weird_news,
    "satire": wtal_news.collect_satire,
    "jokes": wtal_jokes.collect,
    # We Talk A Lot phase 2 (2026-11-21): six libraries from Wikipedia lists / On This Day, Lemmy c/showerthoughts and RecordSetter (collectors/wtal_library.py); each is a no-op until due (30 days; dark_history reads 40 calendar days a cycle until all 366 are done).
    "dark_history": wtal_library.collect_dark_history,
    "unusual_deaths": wtal_library.collect_unusual_deaths,
    "world_records": wtal_library.collect_world_records,
    "conspiracies": wtal_library.collect_conspiracies,
    "weird_facts": wtal_library.collect_weird_facts,
    "shower_thoughts": wtal_library.collect_shower_thoughts,
    # startup_news (Phase 6, 2026-11-16) -> Hustle to Million: Tech.eu / Sifted / TechCrunch Venture RSS, funding with a stated amount and acquisitions.
    "startup_news": startup_news.collect,
    # exchange_listings, added 2026-11-15 (Phase 4) -- four keyless exchange announcement APIs, no GoPlus/rate-limit
    # interaction with anything above; per-exchange isolation lives inside the collector.
    "exchange_listings": exchange_listings.collect,
    # earning_opportunities, added 2026-11-15 (Phase 5): Telegram t.me/s/ + RSS/API. Its own 2-hour entry point is
    # scripts/collect_earning.py (a separate Railway cron service, once created); until then it also rides this 4-hour
    # cycle. Running both is harmless -- rows are keyed by (source, external_id).
    "earning_opportunities": earning_opportunities.collect,
}


# Wall-clock ceiling per step (Linux/Railway only -- SIGALRM does not exist on Windows). A collector
# that HANGS (rather than raises) would otherwise hold the whole cycle forever; the slowest healthy
# step is gems_security at ~6 minutes. On expiry the step raises TimeoutError and is isolated like any
# other failure.
STEP_TIMEOUT_SECONDS = 25 * 60


def run_cycle(group_key: str, after_notify=None):
    """One cycle for a group ("A" or "B", pipeline/cycle_groups.py): its collectors and scorers, then notify for its channels only. `after_notify(guarded)` runs extra guarded steps after the notify
    (group B: the daily below-the-line digest and the Railway source probes). Exits non-zero at the end when any step failed."""
    load_dotenv()
    group = cycle_groups.GROUPS[group_key]

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

    for collector_key, collect_fn in [(k, f) for k, f in COLLECTORS.items() if k in group["collectors"]]:
        category = SCORED_AS.get(collector_key, collector_key)
        logger.info("Collecting %s...", collector_key)
        inserted = guarded(f"collect_{collector_key}", collect_fn)
        if inserted is None:
            alert_if_repeating(f"collect_{collector_key}")  # collect_X's own run_logs row is the failure record
        else:
            logger.info("Collected %d new %s raw items", inserted, collector_key)

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

    logger.info("Running notify for %s: %s", group["label"], ", ".join(group["channels"]))
    guarded(group["notify_job"], lambda: notify.run(only_channels=group["channels"], job_name=group["notify_job"]))  # notify.run() records its own failure and alerts (bot/notify.py)
    if after_notify:
        after_notify(guarded)

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
        with run_log(conn, group["summary_job"]) as state:
            state["details"]["group"] = group_key
            state["details"]["channels"] = group["channels"]
            state["details"]["failed_steps"] = failures
            state["details"]["collectors"] = len(group["collectors"])
            if failures:
                state["status"] = "partial_failure"
    finally:
        conn.close()

    if failures:
        logger.error("%s cycle done WITH FAILURES: %s", group["label"], ", ".join(failures))
        sys.exit(1)
    logger.info("%s cycle done.", group["label"])


def main():
    run_cycle("A")


if __name__ == "__main__":
    main()
