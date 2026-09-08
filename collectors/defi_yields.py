"""DeFi yields collector — DefiLlama's yields API (free, no auth, 200+ protocols;
Section 5 calls this "best free resource of any category").

Runs standalone (`python -m collectors.defi_yields`) or is called by the collect
workflow. Every raw item it stores carries the exact fields score.py needs — this
collector does no scoring itself, just fetch → filter obvious noise → store.
"""

import logging
import sys

from pipeline.db import get_conn
from pipeline.http import get_json
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CATEGORY = "defi_yields"
SOURCE_NAME = "defillama_yields"
API_URL = "https://yields.llama.fi/pools"

# Noise floor, not a quality judgment — score.py's credibility weight does the real
# work. This just keeps obviously-untracked micro-pools out of raw_items entirely.
MIN_TVL_USD = 100_000


def _topic_key(pool: dict) -> str:
    return f"{pool.get('project', '')}|{pool.get('chain', '')}|{pool.get('symbol', '')}".lower()


def _title(pool: dict) -> str:
    apy = pool.get("apy") or 0.0
    tvl = pool.get("tvlUsd") or 0.0
    return (
        f"{pool.get('project', 'unknown')} {pool.get('symbol', '')} on {pool.get('chain', '')}: "
        f"{apy:.2f}% APY (TVL ${tvl:,.0f})"
    )


def fetch_pools() -> list[dict]:
    body = get_json(API_URL)
    return body.get("data", [])


def collect() -> int:
    conn = get_conn()
    inserted = 0
    try:
        with run_log(conn, "collect_defi_yields") as state:
            source_id = get_or_create_source(
                conn, CATEGORY, SOURCE_NAME, "api", {"url": API_URL}
            )
            pools = fetch_pools()
            state["details"]["fetched"] = len(pools)

            # Build the whole batch in Python first, then one round trip to
            # store it — thousands of individual INSERT+commit calls over the
            # network is what actually risks blowing collect.yml's job timeout,
            # not the fetch/filter work itself (Section 12).
            items = []
            for pool in pools:
                tvl = pool.get("tvlUsd") or 0
                apy = pool.get("apy")
                pool_id = pool.get("pool")
                if not pool_id or apy is None or tvl < MIN_TVL_USD:
                    continue
                if pool.get("outlier"):  # DefiLlama's own anomalous-data flag
                    continue

                payload = {
                    "title": _title(pool),
                    "topic_key": _topic_key(pool),
                    "pool_id": pool_id,
                    "project": pool.get("project"),
                    "chain": pool.get("chain"),
                    "symbol": pool.get("symbol"),
                    "apy": pool.get("apy"),
                    "apy_base": pool.get("apyBase"),
                    "apy_reward": pool.get("apyReward"),
                    "apy_pct_1d": pool.get("apyPct1D"),
                    "apy_pct_7d": pool.get("apyPct7D"),
                    "tvl_usd": tvl,
                    "il_risk": pool.get("ilRisk"),
                    "exposure": pool.get("exposure"),
                    "stablecoin": pool.get("stablecoin"),
                    "prediction": (pool.get("predictions") or {}).get("predictedClass"),
                }
                items.append((pool_id, payload))

            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items)
            state["details"]["inserted"] = inserted
            logger.info("defi_yields: fetched=%d inserted=%d", len(pools), inserted)
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = collect()
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
