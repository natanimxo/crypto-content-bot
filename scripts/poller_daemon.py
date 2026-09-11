"""Runs bot.approval_poller.run() forever, for the always-on host (2026-09-11
-- see BACKLOG.md's "Telegram / bot reliability" section for why the
Actions-cron version of this couldn't hold: GitHub's scheduler only actually
fired the poll roughly every 3.6-4.9 hours regardless of the requested
interval, and Telegram drops an un-fetched callback_query well under 10
minutes, so most taps were lost in the gap between runs no matter how the
cron was tuned).

run() itself is unchanged and still correct: one long-poll-and-process cycle,
opening its own DB connection, blocking for up to LONG_POLL_TIMEOUT_SECONDS
waiting on Telegram. This script's only job is to call that in a loop
forever, so the "gap between runs" this whole fix exists for shrinks to
essentially zero (the moment one long-poll returns, the next one opens) --
not to reimplement anything approval_poller.py already does correctly.

A single uncaught exception here must never take the whole process down --
a transient DB hiccup or network blip should log and retry, not require
systemd to notice the process died and restart it (Restart=always in the
unit file is a second line of defense, not the primary one; relying on it
alone means a burst of errors could trip systemd's restart-rate-limit and
leave the service down until someone notices).
"""

import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.approval_poller import run  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Backoff after a failed cycle -- deliberately short. A real long-poll cycle
# already blocks for minutes when Telegram/the DB are healthy, so this only
# ever matters when something's actually broken; keeping it short means a
# transient blip (one dropped connection) costs seconds, not minutes, of
# additional blind window -- the whole point of moving off the 4-hour cron.
ERROR_BACKOFF_SECONDS = 15


def main():
    logger.info("poller_daemon: starting -- looping bot.approval_poller.run() forever")
    while True:
        try:
            run()
        except KeyboardInterrupt:
            logger.info("poller_daemon: received interrupt, stopping")
            raise
        except Exception:
            logger.exception("poller_daemon: run() raised -- backing off %ds and retrying", ERROR_BACKOFF_SECONDS)
            time.sleep(ERROR_BACKOFF_SECONDS)


if __name__ == "__main__":
    main()
