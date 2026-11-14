"""Shared GoPlus Token Security API client (gems_security category + defi_yields
retrofit, 2026-09-11). One place for rate-limiting, caching, chain mapping, and
red-flag interpretation, so both categories read the same signal the same way.

Verified live before building against it, same discipline as RemoteOK/Etherscan:
`GET https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses=...`
works keyless, no auth header needed, real data (checked WETH -- clean -- and a
real newly-collected pool's token, earnETH -- also clean, but see the schema
note below on the field-consistency finding).

RATE LIMIT (undocumented in GoPlus's own docs -- measured empirically,
2026-09-11, three separate burst tests to failure): keyless/no-key access from
one IP allows ~10 requests before returning `{"code": 4029, "message": "too
many requests"}`, recovering after ~30-46 seconds. GOPLUS_REQUEST_INTERVAL_SECONDS
below is set well under that ceiling (never bursts) rather than trying to ride
the burst-then-cooldown edge -- simpler to reason about and doesn't risk a
mistimed retry stacking two categories' calls into the same window.

FIELD-CONSISTENCY FINDING (important, drives everything in `evaluate` below):
GoPlus's response schema is NOT consistent per token. WETH's response included
is_honeypot/is_mintable/hidden_owner/can_take_back_ownership; earnETH's response
-- a real, legitimate token -- omitted all four entirely, not as "0" but as
genuinely absent keys. A missing field must never be read as "checked and
clean" -- it's coded as unknown everywhere in this module, and the deterministic
risk_line callers build must say so explicitly rather than staying silent
(silence reads as "passed" -- operator direction 2026-09-11).
"""

import json
import logging
import re
import time
from collections import Counter

from pipeline.db import dict_cursor
from pipeline.http import get_json

logger = logging.getLogger(__name__)

API_URL_TEMPLATE = "https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
NATIVE_TOKEN_ADDRESS = "0x0000000000000000000000000000000000000000"

# Sustainable pace, well under the measured ~10-per-30-45s ceiling -- see module
# docstring. Enforced as a minimum gap between real API calls (cache hits are
# free and don't touch this), via a simple last-call timestamp rather than a
# token-bucket -- this module only ever serves one collector process at a time
# (a single GitHub Actions job), so there's no concurrent-caller case to guard.
GOPLUS_REQUEST_INTERVAL_SECONDS = 6.0
_last_call_at = 0.0

# RESPONSE-CODE HANDLING, fixed 2026-11-14 after a live log review showed
# gems_security inserting nothing for weeks while "almost every GoPlus call"
# errored. Three separate defects, all in how a response was interpreted:
#
# 1. Only `code == 1` was accepted. GoPlus's docs define code 2 as "Partial
#    data obtained. The complete data can be requested again in about 15
#    seconds", and in practice returns `code=3, message="OK"` with 15-16 of the
#    usual 19-34 fields for some tokens (code 3 is NOT in GoPlus's published
#    status table; observed live on Base tokens 2026-11-14, data present every
#    time). The old code threw all of that away AND never cached it, so those
#    tokens were re-requested -- and discarded again -- on every cycle, forever.
#    Now: codes 2 and 3 with data present are USED (fields that are missing are
#    already read as "unknown" by evaluate(), never as clean), tagged
#    `_goplus_code` in the cached JSON, and cached only PARTIAL_RESULT_TTL_DAYS
#    so a completed analysis is picked up soon instead of 14 days later.
# 2. Non-address "token" ids were sent to the API. DefiLlama's underlyingTokens
#    sometimes holds `coingecko:usd-coin` or a Solana base58 string instead of
#    a contract address; GoPlus answers every one with `code=5000 system error`.
#    They can never succeed, so they now short-circuit to "unchecked" with NO
#    API call (is_checkable_address).
# 3. A failed lookup was never remembered, so the SAME failing token was
#    re-requested once per pool containing it (and, via the collector's own
#    accounting, charged against the per-run call budget each time). A failure
#    is now remembered for the rest of the process (UNAVAILABLE_BACKOFF_SECONDS).
PARTIAL_CODES = {2, 3}
PARTIAL_RESULT_TTL_DAYS = 1
PARTIAL_MARKER = "_goplus_code"
UNAVAILABLE_BACKOFF_SECONDS = 6 * 3600
_unavailable_until: dict[tuple[int, str], float] = {}

# Per-process outcome counters -- collectors copy these into run_logs so a
# degraded GoPlus shows up as a number instead of staying invisible.
STATS: Counter = Counter()

_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")


def is_checkable_address(addr: str | None) -> bool:
    """True only for a real EVM contract address -- the one id shape GoPlus's
    numeric-chain-id endpoint can answer."""
    return bool(addr) and bool(_EVM_ADDRESS_RE.match(addr.lower()))


def _is_partial(result) -> bool:
    return isinstance(result, dict) and result.get(PARTIAL_MARKER) in PARTIAL_CODES


def _is_fresh(result, checked_at, cache_ttl_days: float) -> bool:
    ttl = min(cache_ttl_days, PARTIAL_RESULT_TTL_DAYS) if _is_partial(result) else cache_ttl_days
    return (time.time() - checked_at.timestamp()) / 86400 < ttl

# DefiLlama's `chain` field is a free-text name; GoPlus's endpoint wants a
# numeric chain_id. Mapped for the chains that actually show up with real
# volume above defi_yields' $100k TVL floor (live-checked 2026-09-11: Ethereum,
# Solana, Base, Arbitrum, BSC, Polygon, Monad, OP Mainnet, Hyperliquid L1,
# Avalanche were the top 10 by qualifying pool count) -- Solana/Monad/
# Hyperliquid L1 aren't on GoPlus's EVM-style numeric-chain_id endpoint (Solana
# has a separate, differently-shaped Beta endpoint we're not integrating here;
# Monad and Hyperliquid L1 aren't listed in GoPlus's supported chain_id enum at
# all as of this writing). An unmapped chain is skipped and logged, never
# guessed at -- same "known list, logged skip, never silent" pattern as
# whale_movements' KNOWN_EXCHANGE_ADDRESSES and web3_jobs' KNOWN_WEB3_COMPANIES.
CHAIN_NAME_TO_GOPLUS_ID = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "optimism": 10,
    "op mainnet": 10,
    "base": 8453,
    "avalanche": 43114,
    "fantom": 250,
    "cronos": 25,
    "gnosis": 100,
    # Added 2026-11-14. The Sept note above said Monad wasn't in GoPlus's chain
    # enum; it is now (GoPlus /supported_chains lists 46 chains). Each id below
    # was verified live with a real pool token returning code=1 and data, using
    # DefiLlama's chain NAME as it appears in the yields feed. Together these
    # were ~265 qualifying pools previously deferred as "unmapped". Checked and
    # NOT added: Scroll and Unichain (supported by GoPlus, but the one real
    # token tried on each returned no data -- not enough evidence), Hyperliquid
    # L1 / Flare / Katana (not supported by GoPlus at all), Solana/Sui/TON/etc.
    # (non-EVM; Solana has a separate endpoint with a different response schema
    # -- feature work, not a mapping fix).
    "monad": 143,
    "plasma": 9745,
    "robinhood chain": 4663,
    "mantle": 5000,
    "sonic": 146,
    "linea": 59144,
    "berachain": 80094,
}

# The fields that actually matter for a red-flag determination, each with a
# plain-language explanation used verbatim in the deterministic risk_line (see
# evaluate()). Deliberately a short, load-bearing list, not every field GoPlus
# returns (e.g. is_proxy and is_anti_whale are informational, not inherently
# bad, and are left out of this set on purpose -- a legitimate upgradeable
# protocol is routinely a proxy).
#
# Shape: field -> (is_flag_fn, description). is_flag_fn takes the raw string
# value GoPlus returns for that field and decides if it's a red flag.
def _is_truthy_flag(value) -> bool:
    return value == "1"


def _high_percent(threshold: float):
    def check(value) -> bool:
        try:
            return float(value) > threshold
        except (TypeError, ValueError):
            return False
    return check


CRITICAL_FIELDS = {
    "is_honeypot": (_is_truthy_flag, "this contract is flagged as a honeypot -- tokens can be bought but not sold"),
    "cannot_sell_all": (_is_truthy_flag, "holders cannot sell their full balance"),
    "cannot_buy": (_is_truthy_flag, "the contract blocks normal buys"),
    "hidden_owner": (_is_truthy_flag, "the contract has a hidden owner not visible in standard checks"),
    "can_take_back_ownership": (_is_truthy_flag, "ownership that was renounced can reportedly be reclaimed"),
    "is_mintable": (_is_truthy_flag, "the owner can mint new tokens, diluting holders at will"),
    "transfer_pausable": (_is_truthy_flag, "the owner can pause all transfers"),
    "selfdestruct": (_is_truthy_flag, "the contract has a self-destruct function"),
    "slippage_modifiable": (_is_truthy_flag, "the owner can change buy/sell tax after the fact"),
    "personal_slippage_modifiable": (_is_truthy_flag, "the owner can set a different tax per wallet"),
    "is_blacklisted": (_is_truthy_flag, "the contract can blacklist specific wallets from trading"),
    "honeypot_with_same_creator": (_is_truthy_flag, "this creator has deployed a honeypot before"),
    "is_open_source": (lambda v: v == "0", "the contract's source code is not verified/public"),
    "owner_percent": (_high_percent(0.30), "the owner wallet holds over 30% of total supply"),
    "creator_percent": (_high_percent(0.30), "the creator wallet holds over 30% of total supply"),
    "buy_tax": (_high_percent(0.10), "buy tax is over 10%"),
    "sell_tax": (_high_percent(0.10), "sell tax is over 10%"),
}

# Principled split, 2026-09-11 -- this recurred four times (is_mintable,
# Syrup, osETH, Pendle) before the actual generalizable axis became clear
# enough to encode, rather than reactively demoting one field at a time
# forever. Checked and ruled out first: holder_count as a legitimacy proxy
# (a real genuine finding -- a Base memecoin GoPlus's own honeypot
# SIMULATION caught red-handed -- had 93,372 holders, MORE than any of the
# false-positive-prone tokens it was being compared against, which had
# 26-76,532; holder count doesn't separate these at all).
#
# What actually separates every real case seen so far is the KIND of
# evidence the field represents, not which specific field or token it is:
#
# - BEHAVIORAL fields are things GoPlus directly tested or directly
#   measured: is_honeypot/cannot_sell_all/cannot_buy are live buy-then-sell
#   SIMULATIONS (near-ground-truth, not an inference), and owner_percent/
#   creator_percent/buy_tax/sell_tax/is_open_source are plain facts read
#   straight off the contract (a concentration percentage, a tax rate,
#   whether the source is even readable at all). These can gate a candidate
#   on their own.
# - CAPABILITY fields only mean an admin COULD do something -- they say
#   nothing about whether it's disclosed, expected, or ever used. Minting is
#   the designed, correct behavior of every liquid-staking/yield-
#   tokenization receipt token (it mints on every new stake/deposit,
#   verified live on rETH/tBTC/kBTC and every Pendle PT/YT/SY token this
#   session). Pause/blacklist capability is a standard, disclosed feature of
#   regulated/compliant stablecoins (verified live: Cronos-bridged USDT/USDC
#   flagged hidden_owner+is_mintable+transfer_pausable -- exactly how a
#   centralized stablecoin is supposed to work, not a rug indicator).
#   honeypot_with_same_creator conflates shared deployer/factory
#   infrastructure (normal for established protocols) with a repeat
#   scammer (verified live on BlackRock's BUIDL fund). These fields are
#   still reported if they fire (real signal, just weak alone), but can
#   never be the SOLE reason a candidate clears the gate.
#
# This is the encoding the operator asked for "beyond maintaining exclusion
# lists" -- it classifies by what KIND of claim the underlying GoPlus field
# is actually making, so a new liquid-staking token, a new compliant
# stablecoin, or a new Pendle-style tokenization product doesn't need its
# own hand-added exclusion the next time one shows up. KNOWN_MAJOR_TOKENS
# (collectors/gems_security.py) still exists as a narrower, separate belt
# for specific well-known contracts -- this fixes the general pattern
# instead of only the specific instances already seen.
NON_GATING_FIELDS = {
    "hidden_owner", "can_take_back_ownership", "is_mintable",
    "transfer_pausable", "selfdestruct", "slippage_modifiable",
    "personal_slippage_modifiable", "is_blacklisted", "honeypot_with_same_creator",
}


def _rate_limited_get(url: str, params: dict) -> dict:
    global _last_call_at
    wait = GOPLUS_REQUEST_INTERVAL_SECONDS - (time.time() - _last_call_at)
    if wait > 0:
        time.sleep(wait)
    _last_call_at = time.time()
    return get_json(url, params=params)


def _cache_get(conn, chain_id: int, contract_address: str):
    with dict_cursor(conn) as cur:
        cur.execute(
            "SELECT result, checked_at FROM token_security_cache WHERE chain_id = %s AND contract_address = %s",
            (chain_id, contract_address),
        )
        return cur.fetchone()


def _cache_put(conn, chain_id: int, contract_address: str, result: dict | None):
    with dict_cursor(conn) as cur:
        cur.execute(
            """INSERT INTO token_security_cache (chain_id, contract_address, result, checked_at)
               VALUES (%s, %s, %s, now())
               ON CONFLICT (chain_id, contract_address)
               DO UPDATE SET result = EXCLUDED.result, checked_at = EXCLUDED.checked_at""",
            (chain_id, contract_address, json.dumps(result) if result is not None else None),
        )
    conn.commit()


def bulk_cache_lookup(conn, chain_name: str, contract_addresses: list[str],
                       *, cache_ttl_days: int = 14) -> dict[str, dict | None]:
    """One DB round trip for many addresses on the SAME chain, instead of one
    per address -- essential for a bulk sweep across thousands of pools
    (collectors/gems_security.py qualifies ~6,800 pools above its TVL floor);
    Supabase network round-trip latency alone would make a naive per-token
    lookup design far too slow for a single job run, long before GoPlus's own
    rate limit ever became the bottleneck.

    Returns {address: cached_result} only for addresses with a FRESH
    (within cache_ttl_days) cache entry -- an address missing from the
    returned dict is a cache miss (never checked, or stale) and still needs
    get_token_security() for a real verdict."""
    chain_id = CHAIN_NAME_TO_GOPLUS_ID.get((chain_name or "").lower())
    if chain_id is None or not contract_addresses:
        return {}
    addrs = list({a.lower() for a in contract_addresses if a and a.lower() != NATIVE_TOKEN_ADDRESS})
    if not addrs:
        return {}
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT contract_address, result, checked_at FROM token_security_cache
               WHERE chain_id = %s AND contract_address = ANY(%s)""",
            (chain_id, addrs),
        )
        rows = cur.fetchall()
    return {
        row["contract_address"]: row["result"]
        for row in rows
        if _is_fresh(row["result"], row["checked_at"], cache_ttl_days)
    }


def would_need_fresh_call(conn, chain_name: str, contract_address: str, *, cache_ttl_days: int = 14) -> bool:
    """True if calling get_token_security on this (chain, address) right now
    would consume a real API call rather than being served from cache -- lets
    a bulk-sweep caller (collectors/gems_security.py) enforce its own
    per-run call budget without needing to duplicate get_token_security's
    cache/chain-mapping/native-address logic to find out."""
    if not contract_address or contract_address.lower() == NATIVE_TOKEN_ADDRESS:
        return False  # never calls the API at all for this address
    if not is_checkable_address(contract_address):
        return False  # not a contract address -- short-circuits, no call
    chain_id = CHAIN_NAME_TO_GOPLUS_ID.get((chain_name or "").lower())
    if chain_id is None:
        return False  # skipped, not called
    if _unavailable_until.get((chain_id, contract_address.lower()), 0) > time.time():
        return False  # failed earlier this process; not retried yet
    cached = _cache_get(conn, chain_id, contract_address.lower())
    if cached is None:
        return True
    return not _is_fresh(cached["result"], cached["checked_at"], cache_ttl_days)


def get_token_security(conn, chain_name: str, contract_address: str, *, cache_ttl_days: int = 14) -> dict | None:
    """Cache-first lookup of a single token's GoPlus security result. Returns
    the raw GoPlus field dict, or None if the chain isn't mapped, the address
    is the native-token placeholder, or GoPlus genuinely had no data for it --
    all three are "we don't have a verdict", handled identically by evaluate()
    below (never treated as "clean").

    Does NOT count against the rate-limit budget on a cache hit -- callers
    doing a bulk sweep (collectors/gems_security.py, the defi_yields retrofit)
    should check how many real calls they've made via the return value's
    absence from cache, not assume every call here costs an API request.
    """
    if not contract_address or contract_address.lower() == NATIVE_TOKEN_ADDRESS:
        return None
    chain_id = CHAIN_NAME_TO_GOPLUS_ID.get((chain_name or "").lower())
    if chain_id is None:
        logger.info("goplus: skipping unmapped chain %r for %s", chain_name, contract_address)
        return None
    if not is_checkable_address(contract_address):
        STATS["non_address_skipped"] += 1
        return None  # e.g. "coingecko:usd-coin" -- GoPlus can only answer 5000 for these

    contract_address = contract_address.lower()
    cached = _cache_get(conn, chain_id, contract_address)
    if cached is not None and _is_fresh(cached["result"], cached["checked_at"], cache_ttl_days):
        return cached["result"]

    key = (chain_id, contract_address)
    if _unavailable_until.get(key, 0) > time.time():
        return cached["result"] if cached is not None else None  # already failed this process

    body = _rate_limited_get(
        API_URL_TEMPLATE.format(chain_id=chain_id),
        {"contract_addresses": contract_address},
    )
    code = body.get("code")
    raw = body.get("result")
    result = raw.get(contract_address) if isinstance(raw, dict) else None

    if code == 1:
        STATS["ok"] += 1
        _cache_put(conn, chain_id, contract_address, result)
        return result

    if code in PARTIAL_CODES and result:
        STATS["partial"] += 1
        stored = {**result, PARTIAL_MARKER: code}
        _cache_put(conn, chain_id, contract_address, stored)
        return stored

    STATS["unavailable"] += 1
    STATS[f"unavailable_code_{code}"] += 1
    _unavailable_until[key] = time.time() + UNAVAILABLE_BACKOFF_SECONDS
    logger.warning("goplus: no usable response for %s@%s: code=%s message=%s",
                   contract_address, chain_id, code, body.get("message"))
    return cached["result"] if cached is not None else None  # stale cache beats nothing on a transient failure


def evaluate(result: dict | None) -> dict:
    """Turns a raw GoPlus result (or None) into a red-flag verdict. Every
    CRITICAL_FIELDS entry ends up in exactly one of two buckets -- red_flags
    (present and triggered) or unknown (missing entirely, including the
    result-is-None case) -- there is no third "checked and clean" bucket
    tracked per-field, because a field simply not being flagged isn't the same
    claim as a field being checked and cleared, and conflating the two is
    exactly the overstatement this category exists to avoid.

    has_red_flag is the binary gate collectors use to decide whether something
    is even a candidate -- true only if at least one GATING field triggered
    (NON_GATING_FIELDS can still appear in red_flags/triggered_fields for
    context, they just can't be the only reason something qualifies; see that
    set's comment for the live evidence behind this split). unknown_fields
    feeds the deterministic risk_line so a reader is told what wasn't
    checked, not left to assume "not mentioned" means "fine".
    """
    if result is None:
        return {
            "has_red_flag": False,
            "red_flags": [],
            "triggered_fields": [],
            "unknown_fields": list(CRITICAL_FIELDS.keys()),
            "checked_at_all": False,
        }

    red_flags = []
    triggered_fields = []
    unknown_fields = []
    has_gating_flag = False
    for field, (is_flag, description) in CRITICAL_FIELDS.items():
        if field not in result or result.get(field) in (None, ""):
            unknown_fields.append(field)
            continue
        if is_flag(result[field]):
            red_flags.append(description)
            triggered_fields.append(field)
            if field not in NON_GATING_FIELDS:
                has_gating_flag = True

    return {
        "has_red_flag": has_gating_flag,
        "red_flags": red_flags,
        "triggered_fields": triggered_fields,
        "unknown_fields": unknown_fields,
        "checked_at_all": True,
    }


def evaluate_pool(conn, chain_name: str, token_addresses: list[str]) -> dict:
    """Aggregates get_token_security + evaluate() across every underlying
    token of a pool/pair (native-currency placeholders are skipped
    automatically by get_token_security). Shared by both consumers of this
    module -- the defi_yields retrofit (pipeline/score.py) and the
    gems_security collector -- so a pool's tokens are only ever iterated and
    interpreted one way.

    has_red_flag is true if ANY token has one. tokens_checked/tokens_unchecked
    track which ADDRESSES got a real verdict at all (unmapped chain, no data,
    etc.) -- distinct from unknown_fields_by_token, which is about individual
    FIELDS being missing on a token that WAS otherwise checked. Both matter:
    a pool can have one fully-unchecked token and one token that was checked
    but with several unknown fields, and a caller building a risk_line needs
    to be able to say both things explicitly rather than going quiet on either.
    """
    per_token = []
    for addr in token_addresses:
        if not addr or addr.lower() == NATIVE_TOKEN_ADDRESS:
            continue
        result = get_token_security(conn, chain_name, addr)
        per_token.append({"address": addr.lower(), **evaluate(result)})

    red_flags = []
    triggered_fields = []
    for t in per_token:
        prefix = f"{t['address'][:8]}...: " if len(per_token) > 1 else ""
        red_flags.extend(prefix + f for f in t["red_flags"])
        triggered_fields.extend(t["triggered_fields"])

    return {
        "has_red_flag": any(t["has_red_flag"] for t in per_token),
        "red_flags": red_flags,
        "triggered_fields": triggered_fields,
        "tokens_checked": [t["address"] for t in per_token if t["checked_at_all"]],
        "tokens_unchecked": [t["address"] for t in per_token if not t["checked_at_all"]],
        "unknown_fields_by_token": {t["address"]: t["unknown_fields"] for t in per_token if t["checked_at_all"]},
    }
