"""Whale Alert (official Telegram channel @whale_alert_io) -> whale_movements (operator 2026-11-16).

Adds what Etherscan cannot see: BTC and the other chains (the post's Details link names the chain and carries the transaction hash). Its transfer posts are STRUCTURED --
    "850 $BTC (54,730,605 USD) transferred from unknown wallet to #Binance"
    "1,100 $BTC (70,785,025 USD) transferred from #Ceffu to unknown wallet"
so they are parsed DETERMINISTICALLY (no model): amount, symbol, dollar value, the two sides. TRANSFERS ONLY: mints and burns ("250,000,000 $USDC minted at USDC Treasury") and the
channel's own news posts ("Read Analysis" links to whale-alert.io/stories) are skipped, and so is a transfer with NO exchange on either side (unknown wallet -> unknown wallet): the
whale_movements model is "money moving into or out of an exchange" and a post that cannot say which exchange cannot be written in it.

Tier: the channel is Whale Alert's own account (an official account of the data provider) -> recorded as tier 2; the other whale source (Etherscan wallet watch) is tier 1 data. Both are
labels in the payload; scoring is the existing whale scorer, unchanged.

DEDUPE BY TX HASH, both ways: a transaction this collector sees that Etherscan already stored is skipped, and collectors/whale_movements.py drops any Etherscan move whose hash this
collector already stored. A transaction is one event however many sources report it.
"""

import logging
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from collectors import whale_movements as WM
from pipeline import tg_preview
from pipeline.db import dict_cursor, get_conn
from pipeline.run_log import run_log
from pipeline.score import load_category_config
from pipeline.store import get_or_create_source, insert_raw_items_batch

logger = logging.getLogger(__name__)

CATEGORY = "whale_movements"
SOURCE_NAME = "whale_alert_telegram"
HANDLE = "whale_alert_io"
SOURCE_TIER = 2
HOLD_ALL_CANDIDATES = False
STALE_HOURS = 24            # a channel whose newest post is older than this is dead, not quiet (it posts ~13 a day)

# label (lower-cased, '#' and spaces removed) -> the exchange name the whale watchlist already uses
EXCHANGE_LABELS = {
    "binance": "Binance", "coinbase": "Coinbase", "coinbaseinstitutional": "Coinbase", "kraken": "Kraken", "okx": "OKX", "okex": "OKX", "bybit": "Bybit",
    "bitfinex": "Bitfinex", "kucoin": "KuCoin", "gemini": "Gemini", "bitstamp": "Bitstamp", "cryptocom": "Crypto.com", "htx": "HTX", "huobi": "HTX",
    "gateio": "Gate.io", "gate": "Gate.io", "bitget": "Bitget", "mexc": "MEXC", "upbit": "Upbit", "bithumb": "Bithumb", "robinhood": "Robinhood",
}

_TRANSFER_RE = re.compile(
    r"^\W*(?P<amount>\d[\d,]*(?:\.\d+)?)\s+[#$]*(?P<sym>[A-Za-z0-9]+)\s+\((?P<usd>\d[\d,]*(?:\.\d+)?)\s+USD\)\s+transferred\s+from\s+(?P<frm>.+?)\s+to\s+(?P<to>.+?)\s*(?:Details)?\s*$",
    re.IGNORECASE | re.DOTALL)
_LINK_RE = re.compile(r"whale-alert\.io/transaction/(?P<chain>[a-z0-9_-]+)/(?P<hash>[A-Za-z0-9]+)")


def _label(raw: str) -> tuple[str, str | None, bool]:
    """(clean text, exchange name or None, is_unknown_wallet). '#Coinbase Institutional' -> ('Coinbase Institutional', 'Coinbase', False)."""
    text = re.sub(r"\s+", " ", raw.replace("#", "").strip())
    key = re.sub(r"[^a-z0-9]", "", text.lower())
    unknown = key.startswith("unknown")
    return text, EXCHANGE_LABELS.get(key), unknown


def parse_post(text: str, links: list[str]) -> dict | None:
    """The transfer in one Whale Alert post, or None for anything that is not a transfer (a mint, a burn, a news post)."""
    t = (text or "").replace("\n", " ").strip()
    m = _TRANSFER_RE.match(t)
    link = next((_LINK_RE.search(l) for l in links or [] if _LINK_RE.search(l)), None)
    if not m or not link:
        return None
    frm, from_ex, from_unknown = _label(m.group("frm"))
    to, to_ex, to_unknown = _label(m.group("to"))
    return {"amount": float(m.group("amount").replace(",", "")), "symbol": m.group("sym").upper(), "value_usd": float(m.group("usd").replace(",", "")),
            "from_label": frm, "from_exchange": from_ex, "from_unknown": from_unknown, "to_label": to, "to_exchange": to_ex, "to_unknown": to_unknown,
            "chain": link.group("chain"), "tx_hash": link.group("hash")}


def to_payload(parsed: dict, post_time: datetime, post_url: str) -> dict | None:
    """Map one parsed transfer onto the whale_movements payload the existing scorer reads. None when no exchange is on either side."""
    fe, te = parsed["from_exchange"], parsed["to_exchange"]
    if not fe and not te:
        return None
    if te:                                   # into an exchange: a deposit (both sides exchanges: attributed to the receiving one, the sender is the counterparty)
        exchange, direction = te, "inflow"
        cp_label, cp_ex, cp_unknown = parsed["from_label"], fe, parsed["from_unknown"]
    else:
        exchange, direction = fe, "outflow"
        cp_label, cp_ex, cp_unknown = parsed["to_label"], te, parsed["to_unknown"]
    return {
        "title": WM._title(exchange, direction, parsed["amount"], parsed["symbol"], parsed["value_usd"]),
        "topic_key": f"whale_alert:{exchange.lower()}",
        "source": "whale_alert", "source_tier": SOURCE_TIER, "chain": parsed["chain"], "tx_hash": parsed["tx_hash"].lower() if parsed["chain"] == "ethereum" else parsed["tx_hash"],
        "watched_address": None, "exchange": exchange, "direction": direction,
        "counterparty": cp_label, "counterparty_is_exchange": cp_ex is not None, "counterparty_exchange_name": cp_ex,
        "counterparty_likely_institutional": bool(cp_label and not cp_unknown and cp_ex is None),     # a labelled non-exchange entity (a custodian, a protocol): not a retail wallet
        "counterparty_first_tx_ts": None, "counterparty_sent_tx_count": None,
        "symbol": parsed["symbol"], "amount": parsed["amount"], "value_usd": parsed["value_usd"], "is_token": parsed["symbol"] not in ("BTC", "ETH"),
        "timestamp": int(post_time.timestamp()), "published_at": post_time.isoformat(), "source_url": post_url,
    }


def collect() -> int:
    load_dotenv()
    conn = get_conn()
    try:
        with run_log(conn, "collect_whale_alert") as state:
            cfg = load_category_config(conn, CATEGORY)
            min_usd = float(cfg.get("collect_min_usd") or WM.DEFAULT_MIN_USD)
            posts = tg_preview.fetch_posts(HANDLE)
            now = datetime.now(timezone.utc)
            newest_age_h = (now - posts[-1]["time"]).total_seconds() / 3600
            state["details"].update({"posts": len(posts), "newest_age_h": round(newest_age_h, 1)})
            if newest_age_h > STALE_HOURS:
                raise RuntimeError(f"@{HANDLE} is stale: newest post {newest_age_h:.0f}h old")
            with dict_cursor(conn) as cur:
                cur.execute("SELECT DISTINCT lower(payload->>'tx_hash') h FROM raw_items WHERE category = %s AND payload->>'tx_hash' IS NOT NULL AND collected_at > now() - interval '3 days'", (CATEGORY,))
                known = {r["h"] for r in cur.fetchall()}
            stats = {"not_transfer": 0, "no_exchange": 0, "below_min": 0, "stale": 0, "duplicate_tx": 0}
            items = []
            for p in posts:
                parsed = parse_post(p["text"], p["links"])
                if not parsed:
                    stats["not_transfer"] += 1; continue
                if (now - p["time"]).total_seconds() > WM.WHALE_MAX_AGE_HOURS * 3600:
                    stats["stale"] += 1; continue
                if parsed["value_usd"] < min_usd:
                    stats["below_min"] += 1; continue
                payload = to_payload(parsed, p["time"], f"https://t.me/{p['post']}")
                if not payload:
                    stats["no_exchange"] += 1; continue
                if payload["tx_hash"].lower() in known:
                    stats["duplicate_tx"] += 1; continue
                items.append((f"whale_alert:{payload['chain']}:{payload['tx_hash']}", payload))
            source_id = get_or_create_source(conn, CATEGORY, SOURCE_NAME, "telegram", {"handle": HANDLE})
            inserted = insert_raw_items_batch(conn, source_id, CATEGORY, items, held=HOLD_ALL_CANDIDATES)
            state["details"].update({**stats, "candidates": len(items), "inserted": inserted})
            logger.info("whale_alert: posts=%d %s candidates=%d inserted=%d", len(posts), stats, len(items), inserted)
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(0 if collect() >= 0 else 1)
