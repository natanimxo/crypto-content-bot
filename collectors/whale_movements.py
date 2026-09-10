"""Whale movements collector — watches known exchange hot wallets for large
native-ETH and ERC-20 transfers (Phase 2, 2026-09-10).

Deliberately watches known EXCHANGE wallets, not speculative "whale"
addresses — there's no reliable free source for "who are the individual
whales," but exchange hot/cold wallets are well-documented and unambiguous
(this is also how public whale-alert trackers actually work: flag large flows
in/out of known exchanges, not subjective wallet-tagging). See
config/category_config.yaml's whale_movements section for the full scoring/
design rationale and operator sign-off.

Runs standalone (`python -m collectors.whale_movements`) or via the collect
workflow. Every raw item carries the exact fields pipeline/score.py's
whale_movements scorer needs, fetched once here so scoring stays a pure,
network-free function over stored payload (same architecture as defi_yields).
"""

import logging
import os
import sys
import time

from dotenv import load_dotenv

from pipeline.db import get_conn
from pipeline.http import get_json
from pipeline.run_log import run_log
from pipeline.score import load_category_config
from pipeline.store import get_or_create_source, insert_raw_items_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CATEGORY = "whale_movements"
SOURCE_NAME = "etherscan_whale_watch"
# Etherscan's V1 endpoint (api.etherscan.io/api) is deprecated — live-discovered
# 2026-09-10, first real call against a funded key returned "You are using a
# deprecated V1 endpoint, switch to Etherscan API V2". V2 is a unified multi-
# chain endpoint requiring an explicit chainid param (1 = Ethereum mainnet).
ETHERSCAN_URL = "https://api.etherscan.io/v2/api"
ETHERSCAN_CHAIN_ID = 1
DEFILLAMA_PRICE_URL = "https://coins.llama.fi/prices/current/"

# Etherscan free tier is rate-limited to a few calls/sec — this is a floor
# between consecutive calls, not a queue, but keeps a straight-line loop of
# ~30-40 calls/cycle safely under that without needing real request pacing.
ETHERSCAN_CALL_DELAY_SECONDS = 0.35

# Verified 2026-09-10 via Etherscan's own address-label metadata (cross-checked
# through live web search against Etherscan's page titles, not recalled from
# memory alone — getting a checksum wrong here would mean silently watching
# the wrong wallet). Easy to extend: add entries here, no code change needed
# elsewhere. Lowercase — Etherscan's API returns addresses lowercase too, and
# all comparisons in this module assume that.
WATCHLIST = [
    {"address": "0xf977814e90da44bfa03b6295a0616a897441acec", "exchange": "Binance"},
    {"address": "0x631fc1ea2270e98fbd9d92658ece0f5a269aa161", "exchange": "Binance"},
    {"address": "0x71660c4005ba85c37ccec55d0c4493e66fe775d3", "exchange": "Coinbase"},
    {"address": "0xb5d85cbf7cb3ee0d56b3bb207d5fc4b82f43f511", "exchange": "Coinbase"},
    {"address": "0xf30ba13e4b04ce5dc4d254ae5fa95477800f0eb0", "exchange": "Kraken"},
    {"address": "0xcc282e2004428939ee5149a9e7872f0b4d5d5ec7", "exchange": "Kraken"},
    {"address": "0x4b4e14a3773ee558b6597070797fd51eb48606e5", "exchange": "OKX"},
    {"address": "0x4e7b110335511f662fdbb01bf958a7844118c0d4", "exchange": "OKX"},
]
WATCHLIST_BY_ADDRESS = {w["address"]: w for w in WATCHLIST}

TXLIST_PAGE_SIZE = 100
DEFAULT_MIN_USD = 2_000_000  # fallback if category_config.collect_min_usd is unset

# Live bug, 2026-09-10: fetching "the most recent 100 transactions" from
# Etherscan has NO implicit recency guarantee for a low-activity address — a
# wallet with under 100 total (or under-100-since) transactions can have that
# window reach back YEARS. Confirmed live: two OKX withdrawals from
# 2023-06-21 got collected today as if they'd just happened (the wallet's
# real recent activity was almost entirely dust/spam-token noise, so its
# actual top-100 by index still reached back three years for the last
# "real" transfer). 24h is generous over the 4h collection cadence — a
# single missed/delayed cycle still won't lose anything — while still
# enforcing genuine freshness for what's meant to be a live monitor, not a
# historical archive.
WHALE_MAX_AGE_HOURS = 24


def _etherscan_get(params: dict) -> dict:
    api_key = os.environ.get("ETHERSCAN_API_KEY")
    if not api_key:
        raise RuntimeError("ETHERSCAN_API_KEY is not set")
    time.sleep(ETHERSCAN_CALL_DELAY_SECONDS)
    body = get_json(ETHERSCAN_URL, params={**params, "chainid": ETHERSCAN_CHAIN_ID, "apikey": api_key})
    if params.get("module") == "proxy":
        # Live bug, 2026-09-10: proxy-module calls (eth_getTransactionCount
        # etc.) are plain JSON-RPC responses ({"jsonrpc":...,"result":...}) —
        # no "status"/"message" fields at all. The account-module check below
        # was flagging every single one as a false-alarm "non-OK response:
        # None", pure log noise (the actual result was always fine — verified
        # by cross-checking real counterparty_sent_tx_count values landed
        # correctly in stored payloads despite the spurious warnings).
        if "error" in body:
            logger.warning("Etherscan proxy error for action=%s: %s", params.get("action"), body["error"])
        return body
    # Etherscan (account module) returns status="0" for BOTH real errors and
    # "no results" — message is what actually distinguishes them.
    if body.get("status") not in ("1", 1) and body.get("message") != "No transactions found":
        logger.warning("Etherscan non-OK response for action=%s: %s", params.get("action"), body.get("message"))
    return body


def _get_eth_price_usd() -> float:
    resp = get_json(f"{DEFILLAMA_PRICE_URL}coingecko:ethereum")
    return resp["coins"]["coingecko:ethereum"]["price"]


def _get_token_price_usd(contract_address: str) -> float | None:
    key = f"ethereum:{contract_address.lower()}"
    resp = get_json(f"{DEFILLAMA_PRICE_URL}{key}")
    coin = resp.get("coins", {}).get(key)
    return coin["price"] if coin else None


def _fetch_native_txs(address: str) -> list[dict]:
    body = _etherscan_get({
        "module": "account", "action": "txlist", "address": address,
        "startblock": 0, "endblock": 99999999,
        "page": 1, "offset": TXLIST_PAGE_SIZE, "sort": "desc",
    })
    return body.get("result") or []


def _fetch_token_txs(address: str) -> list[dict]:
    body = _etherscan_get({
        "module": "account", "action": "tokentx", "address": address,
        "page": 1, "offset": TXLIST_PAGE_SIZE, "sort": "desc",
    })
    return body.get("result") or []


def _counterparty_establishment(address: str) -> dict:
    """First-tx age + sent-tx count (nonce) — a cheap 'is this wallet real and
    established' signal, only fetched for transfers that already cleared
    collect_min_usd (keeps call volume low). Feeds the credibility score
    (Section 7) — deliberately about the COUNTERPARTY side, not the exchange
    side (operator direction 2026-09-10: "every watchlist address is curated"
    would make exchange-side credibility a near-constant that doesn't
    discriminate between items; the counterparty genuinely varies per transfer).
    """
    first_tx_resp = _etherscan_get({
        "module": "account", "action": "txlist", "address": address,
        "page": 1, "offset": 1, "sort": "asc",
    })
    results = first_tx_resp.get("result") or []
    first_tx_ts = int(results[0]["timeStamp"]) if results else None

    nonce_resp = _etherscan_get({
        "module": "proxy", "action": "eth_getTransactionCount", "address": address, "tag": "latest",
    })
    nonce_hex = nonce_resp.get("result")
    sent_tx_count = int(nonce_hex, 16) if nonce_hex else None

    return {"first_tx_ts": first_tx_ts, "sent_tx_count": sent_tx_count}


def _is_recent(tx: dict, now_ts: int) -> bool:
    try:
        tx_ts = int(tx.get("timeStamp", 0))
    except (TypeError, ValueError):
        return False
    return (now_ts - tx_ts) <= WHALE_MAX_AGE_HOURS * 3600


def _title(exchange: str, direction: str, amount: float, symbol: str, value_usd: float) -> str:
    verb = "withdrawn from" if direction == "outflow" else "deposited to"
    return f"${value_usd:,.0f} ({amount:,.2f} {symbol}) {verb} {exchange}"


def _build_item(tx: dict, watched_address: str, exchange: str, symbol: str,
                 value_usd: float, amount: float, is_token: bool) -> tuple[str, dict] | None:
    from_addr = (tx.get("from") or "").lower()
    to_addr = (tx.get("to") or "").lower()

    if from_addr == watched_address:
        direction = "outflow"   # watched exchange wallet SENDING -- e.g. a customer withdrawal
        counterparty = to_addr
    elif to_addr == watched_address:
        direction = "inflow"    # watched exchange wallet RECEIVING -- e.g. a customer deposit
        counterparty = from_addr
    else:
        return None  # shouldn't happen given how we fetched this tx, but don't guess if it does
    if not counterparty:
        return None

    counterparty_entry = WATCHLIST_BY_ADDRESS.get(counterparty)
    establishment = _counterparty_establishment(counterparty)

    tx_hash = tx.get("hash")
    if not tx_hash:
        return None
    # (tx_hash, watched_address, direction) rather than tx_hash alone: an
    # exchange<->exchange transfer touches two watchlist entries, and each is
    # collected from that wallet's own txlist/tokentx call — this keeps both
    # perspectives distinct rather than colliding on the UNIQUE constraint.
    external_id = f"{tx_hash}:{watched_address}:{direction}"

    payload = {
        "title": _title(exchange, direction, amount, symbol, value_usd),
        "topic_key": watched_address,   # groups THIS wallet's own flagged-move history
        "tx_hash": tx_hash,
        "watched_address": watched_address,
        "exchange": exchange,
        "direction": direction,   # 'inflow' | 'outflow', relative to the watched exchange wallet
        "counterparty": counterparty,
        "counterparty_is_exchange": counterparty_entry is not None,
        "counterparty_exchange_name": counterparty_entry["exchange"] if counterparty_entry else None,
        "counterparty_first_tx_ts": establishment["first_tx_ts"],
        "counterparty_sent_tx_count": establishment["sent_tx_count"],
        "symbol": symbol,
        "amount": amount,
        "value_usd": value_usd,
        "is_token": is_token,
        "timestamp": int(tx["timeStamp"]),
    }
    return (external_id, payload)


def collect() -> int:
    load_dotenv()  # no-op in CI (no .env there); picks up local .env when run directly
    conn = get_conn()
    try:
        with run_log(conn, "collect_whale_movements") as state:
            cfg = load_category_config(conn, CATEGORY)
            min_usd = float(cfg.get("collect_min_usd") or DEFAULT_MIN_USD)

            source_id = get_or_create_source(
                conn, CATEGORY, SOURCE_NAME, "api", {"watchlist_size": len(WATCHLIST)}
            )

            eth_price = _get_eth_price_usd()
            token_price_cache: dict[str, float | None] = {}

            items = []
            fetched_count = 0
            stale_skipped = 0
            now_ts = int(time.time())

            for entry in WATCHLIST:
                address = entry["address"]
                exchange = entry["exchange"]

                for tx in _fetch_native_txs(address):
                    fetched_count += 1
                    if not _is_recent(tx, now_ts):
                        stale_skipped += 1
                        continue
                    try:
                        value_eth = int(tx["value"]) / 1e18
                    except (KeyError, ValueError):
                        continue
                    value_usd = value_eth * eth_price
                    if value_usd < min_usd:
                        continue
                    item = _build_item(tx, address, exchange, "ETH", value_usd, value_eth, is_token=False)
                    if item:
                        items.append(item)

                for tx in _fetch_token_txs(address):
                    fetched_count += 1
                    if not _is_recent(tx, now_ts):
                        stale_skipped += 1
                        continue
                    try:
                        decimals = int(tx.get("tokenDecimal") or 18)
                        raw_value = int(tx["value"])
                    except (KeyError, ValueError):
                        continue
                    token_amount = raw_value / (10 ** decimals)
                    contract = tx.get("contractAddress")
                    if not contract:
                        continue
                    if contract not in token_price_cache:
                        token_price_cache[contract] = _get_token_price_usd(contract)
                    price = token_price_cache[contract]
                    if price is None:
                        continue  # can't value it reliably -- skip rather than guess
                    value_usd = token_amount * price
                    if value_usd < min_usd:
                        continue
                    item = _build_item(tx, address, exchange, tx.get("tokenSymbol") or "?",
                                        value_usd, token_amount, is_token=True)
                    if item:
                        items.append(item)

            state["details"]["fetched"] = fetched_count
            state["details"]["stale_skipped"] = stale_skipped
            state["details"]["notable"] = len(items)

            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items)
            state["details"]["inserted"] = inserted
            logger.info("whale_movements: fetched=%d notable=%d inserted=%d",
                        fetched_count, len(items), inserted)
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = collect()
    logger.info("Done. %d new raw_items inserted.", n)
    sys.exit(0)
