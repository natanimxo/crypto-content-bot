"""gems/security screening collector (Phase 2/3, 2026-09-11) -- Crypto Notebook.

Discovery source (operator-approved plan): reuses the SAME DefiLlama /pools
feed defi_yields already collects, rather than a separate "new token" feed --
there isn't a free, keyless one that catches tokens earlier than "someone
built real DEX liquidity for it" (a known, accepted gap -- see BACKLOG.md).
`underlyingTokens` gives real contract addresses on 17,146/17,148 live pools;
defi_yields' own $100k TVL floor doubles as a real-activity filter here too.

Editorial premise (operator-approved, deliberately narrow): post ONLY when a
pool with real TVL has a token carrying a genuine, specific GoPlus red flag.
No "notable launch, clean check" case -- without a concrete concern to name,
that post is just "here's a token," promotion regardless of hedging. See
pipeline/goplus.py and pipeline/write_post.py for the full "never overstate
what a check proves" chain (missing fields coded as unknown, never clear;
a banned-overclaim-phrase guard on the LLM's actual output).

Rate-limit-aware by design (pipeline/goplus.py has the live measurement:
~10 keyless requests per ~30-45s). Screening the full qualifying universe in
one run isn't possible -- MAX_GOPLUS_CALLS_PER_RUN caps real API calls per
cycle. Cache lookups are batched per-chain (~10 bulk queries, not one per
token per pool) so the qualifying set can be swept cheaply; only genuine
cache MISSES consume the real per-run budget. Pools walk TVL-descending
WITHIN a TVL band (MIN_TVL_USD..MAX_TVL_USD_FOR_SCREENING) rather than
across the whole universe -- see that constant's comment for why: unbounded
TVL-descending order structurally guarantees the budget gets spent on the
LEAST "gem"-like assets in DeFi first, every cycle.

Also filters out pools where the same underlying token dominates the
cycle's candidates (PROTOCOL_DOMINANCE_THRESHOLD) -- a token GoPlus flags
across many independent pool deployments in one cycle is exhibiting a
property of its own design, not something wrong with any one pool.
"""

import logging
import re
import sys
from collections import Counter, defaultdict

from dotenv import load_dotenv

from pipeline import goplus
from pipeline.db import get_conn
from pipeline.http import get_json
from pipeline.run_log import run_log
from pipeline.store import get_or_create_source, insert_raw_items_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CATEGORY = "gems_security"
SOURCE_NAME = "goplus_defillama_pools"
POOLS_API_URL = "https://yields.llama.fi/pools"

MIN_TVL_USD = 100_000  # matches defi_yields' own noise floor -- collectors/defi_yields.py

# TVL screening ceiling, added 2026-09-11 (operator direction, after live
# verification of the corrected run): TVL-descending prioritization
# structurally guarantees the per-run budget gets spent on the LEAST
# "gem"-like assets in DeFi first, every single cycle -- the entire
# qualifying universe's top ~2-3% by TVL is wrapped-BTC variants, major
# stablecoins, and liquid-staking tokens, exactly the population GoPlus's
# heuristics are worst-suited to (see KNOWN_MAJOR_TOKENS and
# pipeline.goplus.NON_GATING_FIELDS above/below). Live-measured: a $100M
# ceiling excludes only the top 2.6% of the qualifying universe by count
# (176/6,799 pools) -- keeping 97.4% of it in play -- while cleanly excluding
# every remaining false-positive-prone institutional/LST token from the
# corrected run (osETH $312M, kBTC $251M/$186M, tBTC $132M, rETH $103M, a
# Securitize tokenized fund $103M) and still including both of that run's
# genuinely useful findings (BMD-USDC $97M, WETH-GFC $67.5M) -- proof the
# ceiling is set close to where real signal actually lives, not arbitrarily.
# Sort order below stays TVL-descending WITHIN this band deliberately: both
# genuine finds sat near the top of it, not the bottom, so there's no
# evidence yet that flipping to ascending-within-the-band would help: as
# cache coverage fills in over successive cycles, the effective screening
# frontier will naturally work its way down through the band on its own.
MAX_TVL_USD_FOR_SCREENING = 100_000_000

# Live bug, first real run 2026-09-11: sorting qualifying pools TVL-descending
# (below) means the highest-TVL pools -- overwhelmingly wrapped-BTC variants,
# major stablecoins, and liquid-staking tokens -- consume the ENTIRE per-run
# screening budget every cycle, and GoPlus's heuristics routinely flag
# well-known, audited, intentional properties of these specific contracts as
# if they were red flags: WBTC's custodian genuinely can mint/burn as part of
# the wrap/unwrap mechanism (that's the design, not a rug risk); USDT's
# contract genuinely has pause/blacklist functions by design; BUIDL --
# BlackRock's $649M tokenized fund, 5 holders, 80% in one institutional
# wallet -- got flagged "creator has deployed a honeypot before", a false
# positive from GoPlus's same-creator heuristic conflating shared
# institutional tokenization infrastructure (verified live: 0/18 first-run
# candidates were anything but a top-20 blue-chip asset). Posting "security
# finding" about USDT or a BlackRock fund isn't "flag risk, don't endorse
# safety" -- it's a false alarm that actively misleads readers about the most
# established, audited assets in DeFi, undermining exactly the trust this
# category exists to protect. Excluded here, before screening (so budget
# isn't wasted on them either) -- same hand-maintained "known-entity list"
# pattern as whale_movements' KNOWN_EXCHANGE_ADDRESSES and web3_jobs'
# KNOWN_WEB3_COMPANIES. Maintenance: extend as other clearly-blue-chip,
# widely-audited majors show up flagged -- this is a low-stakes list (a
# missed exclusion means one over-cautious post, not a functional error),
# so no address-style verification rigor is needed, just common-knowledge
# judgment about what's genuinely a top-tier, widely-recognized asset.
KNOWN_MAJOR_TOKENS = {
    ("ethereum", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"),  # WBTC
    ("ethereum", "0xdac17f958d2ee523a2206206994597c13d831ec7"),  # USDT
    ("ethereum", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),  # USDC
    ("ethereum", "0x6b175474e89094c44da98b954eedeac495271d0f"),  # DAI
    ("ethereum", "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0"),  # wstETH
    ("ethereum", "0xae7ab96520de3a18e5e111b5eaab095312d7fe84"),  # stETH
    ("ethereum", "0x9d39a5de30e57443bff2a8307a4256c8797a3497"),  # sUSDe (Ethena)
    ("ethereum", "0x4c9edd5852cd905f086c759e8383e09bff1e68b3"),  # USDe (Ethena)
    ("ethereum", "0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041"),  # BUIDL (BlackRock)
    ("ethereum", "0x56072c95faa701256059aa122697b133aded9279"),  # SKY (MakerDAO)
    ("ethereum", "0x8236a87084f8b84306f72007f36f2618a5634494"),  # LBTC (Lombard)
    ("bsc", "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c"),        # BTCB
}


def _is_known_major(chain: str, address: str) -> bool:
    return ((chain or "").lower(), (address or "").lower()) in KNOWN_MAJOR_TOKENS

# Same-protocol/product dominance rule, added 2026-09-11, extended same day
# (operator direction): "if one protocol/product dominates a cycle's
# candidates, that's a signal the flag means something structural rather
# than something wrong." Started address-only (9/21 candidates in one run
# were different pools built around Maple Finance's Syrup product, one
# shared token contract) but that missed a second real case the very next
# cycle: 12/22 candidates were Pendle Principal/Yield/Standardized-Yield
# tokens spanning 10 DIFFERENT contract addresses (Pendle mints a genuinely
# distinct token per maturity date), so no single address ever repeated
# enough to trip an address-only check, even though the underlying cause --
# one protocol's factory template -- was identical every time.
#
# Grouping now checks THREE keys per candidate, any one of which hitting the
# threshold excludes it: the flagged token's own address (catches the same
# contract redeployed across many pools -- Syrup), the pool's host `project`
# (catches many pools built directly on one protocol -- e.g. many pendle-v2
# pools), and a recognized tokenization-template naming prefix (catches a
# protocol's standardized product minted as genuinely distinct contracts
# across OTHER hosts entirely -- Pendle's PT-/YT-/SY- convention specifically,
# since those tokens get deposited as collateral into aave/morpho/etc., not
# just held within Pendle's own pools). The naming-prefix key is deliberately
# narrow (Pendle's own documented, stable naming convention, not a fuzzy
# guess) so it generalizes to every future Pendle market without maintenance,
# without over-matching unrelated tokens that happen to start with similar
# letters.
PROTOCOL_DOMINANCE_THRESHOLD = 3

_TOKENIZATION_TEMPLATE_PREFIXES = re.compile(r"^(PT|YT|SY)-", re.IGNORECASE)


def _dominance_keys(payload: dict) -> set[str]:
    keys = {f"addr:{a}" for a in payload.get("flagged_token_addresses") or []}
    if payload.get("project"):
        keys.add(f"project:{payload['project']}")
    m = _TOKENIZATION_TEMPLATE_PREFIXES.match(payload.get("symbol") or "")
    if m:
        keys.add(f"template:{m.group(1).upper()}")
    return keys

# 60 real API calls * ~6s safe pace (pipeline/goplus.py) = ~6 minutes,
# leaving real headroom inside collect.yml's job timeout (bumped alongside
# this collector -- see .github/workflows/collect.yml).
MAX_GOPLUS_CALLS_PER_RUN = 60


def fetch_pools() -> list[dict]:
    body = get_json(POOLS_API_URL)
    return body.get("data", [])


def _title(pool: dict, red_flags: list[str]) -> str:
    return f"{pool.get('symbol')} on {pool.get('chain')} ({pool.get('project')}): {len(red_flags)} security finding(s)"


def _topic_key(flagged_addresses: list[str]) -> str:
    # First flagged token's address -- cooldown suppresses re-notifying about
    # the SAME risky token if it later shows up in a different pool too.
    return flagged_addresses[0] if flagged_addresses else ""


def collect() -> int:
    load_dotenv()  # no-op in CI (no .env there); picks up local .env when run directly
    conn = get_conn()
    inserted = 0
    try:
        with run_log(conn, "collect_gems_security") as state:
            source_id = get_or_create_source(conn, CATEGORY, SOURCE_NAME, "api", {"url": POOLS_API_URL})

            pools = fetch_pools()
            state["details"]["fetched"] = len(pools)

            qualifying = [
                p for p in pools
                if MIN_TVL_USD <= (p.get("tvlUsd") or 0) < MAX_TVL_USD_FOR_SCREENING
                and not p.get("outlier")
                and p.get("pool")
                and p.get("underlyingTokens")
            ]
            qualifying.sort(key=lambda p: p.get("tvlUsd") or 0, reverse=True)
            state["details"]["qualifying"] = len(qualifying)

            # Batch cache reads per chain (see pipeline.goplus.bulk_cache_lookup's
            # docstring for why this matters at this scale) before doing any
            # per-pool work at all.
            tokens_by_chain: dict[str, set[str]] = defaultdict(set)
            for pool in qualifying:
                chain = pool.get("chain")
                for t in (pool.get("underlyingTokens") or []):
                    if t and t.lower() != goplus.NATIVE_TOKEN_ADDRESS and not _is_known_major(chain, t):
                        tokens_by_chain[chain].add(t.lower())

            cache_by_chain = {
                chain: goplus.bulk_cache_lookup(conn, chain, list(addrs))
                for chain, addrs in tokens_by_chain.items()
            }

            items = []
            fresh_calls = 0
            deferred_budget = 0
            deferred_unmapped_chain = 0

            for pool in qualifying:
                chain = pool.get("chain")
                tokens = [
                    t.lower() for t in (pool.get("underlyingTokens") or [])
                    if t and t.lower() != goplus.NATIVE_TOKEN_ADDRESS and not _is_known_major(chain, t)
                ]
                if not tokens:
                    continue  # nothing left to check -- e.g. an all-major pair like WBTC-USDC
                chain_cache = cache_by_chain.get(chain, {})
                if (chain or "").lower() not in goplus.CHAIN_NAME_TO_GOPLUS_ID:
                    deferred_unmapped_chain += 1
                    continue

                cache_misses = [t for t in tokens if t not in chain_cache]
                if cache_misses and fresh_calls + len(cache_misses) > MAX_GOPLUS_CALLS_PER_RUN:
                    # Would blow this run's budget -- defer the WHOLE pool to
                    # a later cycle rather than partially screen it (a pool
                    # scored on only half its tokens is exactly the kind of
                    # incomplete-but-silent-about-it picture this category
                    # exists to avoid).
                    deferred_budget += 1
                    continue

                per_token = []
                for addr in tokens:
                    if addr in chain_cache:
                        result = chain_cache[addr]
                    else:
                        result = goplus.get_token_security(conn, chain, addr)
                        fresh_calls += 1
                    per_token.append({"address": addr, **goplus.evaluate(result)})

                if not any(t["has_red_flag"] for t in per_token):
                    continue  # the whole editorial point: no finding, no post

                red_flags, triggered_fields, tokens_checked, tokens_unchecked = [], [], [], []
                for t in per_token:
                    prefix = f"{t['address'][:8]}...: " if len(per_token) > 1 else ""
                    red_flags.extend(prefix + f for f in t["red_flags"])
                    triggered_fields.extend(t["triggered_fields"])
                    (tokens_checked if t["checked_at_all"] else tokens_unchecked).append(t["address"])

                flagged_addresses = [t["address"] for t in per_token if t["has_red_flag"]]
                payload = {
                    "title": _title(pool, red_flags),
                    "topic_key": _topic_key(flagged_addresses),
                    "pool_id": pool["pool"],
                    "project": pool.get("project"),
                    "chain": chain,
                    "symbol": pool.get("symbol"),
                    "tvl_usd": pool.get("tvlUsd") or 0,
                    "apy": pool.get("apy"),
                    "red_flags": red_flags,
                    "triggered_fields": triggered_fields,
                    "tokens_checked": tokens_checked,
                    "tokens_unchecked": tokens_unchecked,
                    "flagged_token_addresses": flagged_addresses,
                }
                items.append((pool["pool"], payload))

            # Same-protocol/product dominance filter -- see
            # PROTOCOL_DOMINANCE_THRESHOLD's comment. Applied once, after the
            # full cycle's candidates are known, since dominance is a
            # property of the WHOLE cycle's output, not any single pool.
            # Checks address/project/naming-template keys together (a token
            # or protocol can trip more than one).
            key_occurrences = Counter(
                key for _, payload in items for key in _dominance_keys(payload)
            )
            dominant_keys = {k for k, n in key_occurrences.items() if n >= PROTOCOL_DOMINANCE_THRESHOLD}
            dominance_excluded = 0
            if dominant_keys:
                kept = []
                for external_id, payload in items:
                    if _dominance_keys(payload) & dominant_keys:
                        dominance_excluded += 1
                    else:
                        kept.append((external_id, payload))
                items = kept
                logger.info(
                    "gems_security: excluded %d pool(s) this cycle -- dominated by "
                    "token/project/template appearing in >=%d independent pools "
                    "(structural, not a per-pool red flag): %s",
                    dominance_excluded, PROTOCOL_DOMINANCE_THRESHOLD, sorted(dominant_keys),
                )

            state["details"]["fresh_goplus_calls"] = fresh_calls
            state["details"]["deferred_budget"] = deferred_budget
            state["details"]["deferred_unmapped_chain"] = deferred_unmapped_chain
            state["details"]["dominance_excluded"] = dominance_excluded
            state["details"]["red_flagged"] = len(items)

            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items)
            state["details"]["inserted"] = inserted

            logger.info(
                "gems_security: fetched=%d qualifying=%d fresh_calls=%d red_flagged=%d inserted=%d "
                "deferred_budget=%d deferred_unmapped_chain=%d dominance_excluded=%d",
                len(pools), len(qualifying), fresh_calls, len(items), inserted,
                deferred_budget, deferred_unmapped_chain, dominance_excluded,
            )
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = collect()
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
