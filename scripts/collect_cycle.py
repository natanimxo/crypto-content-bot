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
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

from bot import notify  # noqa: E402
from collectors import defi_yields, web3_jobs, whale_movements  # noqa: E402
from pipeline.db import get_conn  # noqa: E402
from pipeline.score import score_new_items  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

COLLECTORS = {
    "defi_yields": defi_yields.collect,
    "whale_movements": whale_movements.collect,
    "web3_jobs": web3_jobs.collect,
}


def main():
    load_dotenv()

    for category, collect_fn in COLLECTORS.items():
        logger.info("Collecting %s...", category)
        inserted = collect_fn()
        logger.info("Collected %d new %s raw items", inserted, category)

        conn = get_conn()
        try:
            scored = score_new_items(conn, category)
            logger.info("Scored %d new %s items", scored, category)
        finally:
            conn.close()

    logger.info("Running notify...")
    notify.run()
    logger.info("Collect cycle done.")


if __name__ == "__main__":
    main()
