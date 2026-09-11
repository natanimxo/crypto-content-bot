"""Final post writing (Section 8, step 2; redesigned 2026-09-10). Only ever
called on operator-approved items.

The LLM's job is narrowed to prose only — it returns structured JSON
(title / narrative / why_it_matters), not raw HTML or a full post. Everything
structural (history blockquote, risk flag, source link, hashtags, the single
leading emoji) is computed deterministically in pipeline/post_format.py from
real data, then assembled around the model's prose. See post_format.py's
module docstring for the reliability rationale.

During a category's benchmark trial (Section 4.3), generates both a DeepSeek
and a Sonnet variant of the PROSE, then assembles each through the identical
deterministic pipeline — history/risk/source/hashtags never differ between
variants, only the writing does, so the comparison isolates writing quality.
"""

import difflib
import logging
import os
import re
from datetime import datetime, timezone

from pipeline import entities as entity_lib
from pipeline import llm, post_format
from pipeline.db import dict_cursor
from pipeline.http import get_json
from pipeline.score import load_category_config

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 5

# Per-category prompt builders — the LLM's prose-only job. defi_yields is the
# only one wired for the MVP; add one function per new category as it's built,
# same registry pattern as score.py's scorers and template.py's triage renderers.
PROMPT_BUILDERS = {}

# Per-category deterministic element computers (history_line/risk_line/
# source_name) — the non-LLM structural half of every post.
POST_COMPUTERS = {}


def register_prompt_builder(category: str):
    def deco(fn):
        PROMPT_BUILDERS[category] = fn
        return fn
    return deco


def register_post_computer(category: str):
    def deco(fn):
        POST_COMPUTERS[category] = fn
        return fn
    return deco


def get_topic_history(conn, category: str, topic_key: str, exclude_raw_item_id: int, limit: int = HISTORY_LIMIT):
    if not topic_key:
        return []
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT payload, collected_at FROM raw_items
               WHERE category = %s AND payload->>'topic_key' = %s AND id != %s
               ORDER BY collected_at DESC LIMIT %s""",
            (category, topic_key, exclude_raw_item_id, limit),
        )
        return cur.fetchall()


def _get_region_profile(conn, channel: str) -> str:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT region_profile FROM channel_config WHERE channel = %s", (channel,))
        row = cur.fetchone()
    return (row["region_profile"] if row else "default") or "default"


@register_prompt_builder("defi_yields")
def build_defi_yields_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    p = raw_item["payload"]

    us_note = (
        "\nThis is a US-audience channel — keep financial framing conservative and "
        "factual; never phrase anything as a recommendation to deposit funds."
        if region_profile == "us" else ""
    )

    return f"""You are writing prose for a crypto DeFi content channel. Voice: {cfg.get('voice', 'educational')}.
{cfg.get('prompt_notes', '')}{us_note}

Facts about this pool:
- Protocol: {p.get('project')}
- Chain: {p.get('chain')}
- Pool: {p.get('symbol')}
- APY: {p.get('apy')}% (base {p.get('apy_base')}%, reward {p.get('apy_reward')}%)
- TVL: ${p.get('tvl_usd', 0):,.0f}
- 7d APY change: {p.get('apy_pct_7d')}
- IL risk flag: {p.get('il_risk')}
- Stablecoin pair: {p.get('stablecoin')}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these
three string fields:
{{
  "title": "one specific, informative title — NOT a category label. E.g.
    'Solana WSOL-USDC pool jumps to 214% APY', never 'New DeFi Opportunity'.
    No emoji — one is added separately.",
  "narrative": "2-3 sentences: what happened, with the key numbers stated
    inline (not a bullet list). Plain prose.",
  "why_it_matters": "EXACTLY one sentence (not two) on the mechanic behind the
    number — real fees vs. emissions, lockup, demand driver. Teach, don't hype."
}}

Do not mention scores or numbers-out-of-100 — those are internal, never shown
to the reader. Never phrase anything as investment advice ("you should
deposit here") — describe what the data shows. No emoji anywhere in your
output. Keep the combined narrative + why_it_matters under ~70 words total —
the whole post (including a header/history/risk/source/hashtags that are
added separately, not by you) targets roughly 400-700 characters.
"""


# apy_pct_7d is handed to the LLM as a fact and is very likely referenced in
# the narrative's prose (e.g. "+79pp over the past seven days") — that figure
# comes from DefiLlama's own long-running data, a source entirely independent
# of when WE started collecting (2026-09-08). Our own "last tracked" blockquote
# must span AT LEAST that same window, or it can flatly contradict the
# narrative it sits right next to — live bug, 2026-09-10: a same-day snapshot
# rendered as "Last tracked earlier today at 0.00% APY", directly contradicting
# a narrative that said "+79.44pp over the past seven days" one paragraph
# above it. Right now this means nearly all history is too young to render —
# that's correct: omitting the blockquote is always safer than a misleading
# one (Section 1's "memory" differentiator must never become misinformation).
HISTORY_MIN_AGE_DAYS = 7
HISTORY_MIN_APY_DELTA_PP = 5.0
HISTORY_MIN_TVL_DELTA_PCT = 20.0


def _valid_history_point(payload: dict) -> bool:
    """Reject a history snapshot with a null/zero/missing APY or TVL — almost
    always a first-ever-collected artifact (an uninitialized/missing reading),
    not a genuine prior value. Live bug, 2026-09-10 (see HISTORY_MIN_AGE_DAYS
    comment above): an early snapshot's apy=0.00 got rendered as if real."""
    apy = payload.get("apy")
    tvl = payload.get("tvl_usd")
    return apy is not None and apy > 0 and tvl is not None and tvl > 0


def _compute_defi_yields_history_line(raw_item: dict, history: list) -> str | None:
    if not history:
        return None
    oldest = history[-1]  # DESC order (most recent first) -> last = oldest in window
    oldest_p = oldest["payload"]

    if not _valid_history_point(oldest_p):
        return None
    if not (raw_item.get("collected_at") and oldest.get("collected_at")):
        return None

    days_ago = (raw_item["collected_at"] - oldest["collected_at"]).days
    # Validation pass: never let the blockquote's own claimed recency be able
    # to contradict the narrative's 7-day-change framing.
    if days_ago < HISTORY_MIN_AGE_DAYS:
        return None

    old_apy, old_tvl = oldest_p["apy"], oldest_p["tvl_usd"]
    new_apy = raw_item["payload"].get("apy") or 0.0
    new_tvl = raw_item["payload"].get("tvl_usd") or 0.0

    apy_delta_pp = abs(new_apy - old_apy)
    tvl_delta_pct = abs(new_tvl - old_tvl) / old_tvl * 100 if old_tvl else 0.0
    if apy_delta_pp < HISTORY_MIN_APY_DELTA_PP and tvl_delta_pct < HISTORY_MIN_TVL_DELTA_PCT:
        return None  # not materially different -- would be noise, not memory

    return (
        f"Last tracked {days_ago}d ago at {old_apy:.2f}% APY (TVL ${old_tvl:,.0f}) — "
        f"now {new_apy:.2f}% APY (TVL ${new_tvl:,.0f})."
    )


@register_post_computer("defi_yields")
def compute_defi_yields_elements(conn, raw_item: dict, history: list) -> dict:
    """Everything here is a fact lookup or a fixed rule — never invented prose.
    See post_format.py's module docstring for why this is deliberately not
    the LLM's job."""
    p = raw_item["payload"]

    history_line = _compute_defi_yields_history_line(raw_item, history)

    risk_reasons = []
    il_risk = (p.get("il_risk") or "").lower()
    stablecoin = bool(p.get("stablecoin"))
    apy = p.get("apy") or 0.0
    apy_reward = p.get("apy_reward") or 0.0
    apy_base = p.get("apy_base") or 0.0
    if il_risk == "yes" and not stablecoin:
        risk_reasons.append("impermanent loss risk is flagged")
    if apy > 100:
        risk_reasons.append("APY is far above typical stable-yield norms")
    if apy_reward and apy_base == 0:
        risk_reasons.append("yield is entirely emissions-driven, not real fees")

    # GoPlus retrofit, 2026-09-11 -- read the SAME verdict pipeline/score.py
    # already computed (stashed in score_breakdown['goplus']) rather than
    # re-querying, so scoring and the delivered post can never disagree.
    # Operator direction: an unchecked token must say so explicitly, never
    # stay silent -- silence reads as "passed".
    goplus_eval = (raw_item.get("score_breakdown") or {}).get("goplus") or {}
    if goplus_eval.get("has_red_flag"):
        risk_reasons.append(
            "GoPlus flagged the underlying token: " + "; ".join(goplus_eval["red_flags"])
        )
    elif goplus_eval.get("tokens_unchecked"):
        risk_reasons.append(
            "token contract security could not be verified for this pool "
            "(unsupported chain or no GoPlus data) — not the same as a clean result"
        )

    risk_line = ("; ".join(risk_reasons) + ".").capitalize() if risk_reasons else None

    # No hyperlinks (2026-09-10, operator direction) — plain-text attribution only.
    source_name = "DefiLlama" if p.get("pool_id") else None

    return {"history_line": history_line, "risk_line": risk_line, "source_name": source_name}


@register_prompt_builder("whale_movements")
def build_whale_movements_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    p = raw_item["payload"]
    verb = "withdrew" if p.get("direction") == "outflow" else "deposited"
    # 3-way, not binary (2026-09-10 — live bug: an unlabeled-but-clearly-not-
    # retail counterparty was being described to the LLM as "an external
    # wallet," and it wrote posts calling it exactly that, when it's very
    # likely institutional infrastructure we just haven't identified by name).
    if p.get("counterparty_is_exchange"):
        counterparty_note = f"the counterparty is another exchange ({p.get('counterparty_exchange_name')})"
    elif p.get("counterparty_likely_institutional"):
        counterparty_note = (
            "the counterparty is a high-activity wallet with an enormous on-chain "
            "transaction history — almost certainly institutional or automated "
            "infrastructure, not an individual holder, even though we haven't identified "
            "exactly which entity it is. Do NOT call it 'an external wallet' or imply it's "
            "a retail/individual holder."
        )
    else:
        counterparty_note = "the counterparty is an external wallet"

    us_note = (
        "\nThis is a US-audience channel — keep financial framing conservative and "
        "factual; never phrase anything as investment advice or a price prediction."
        if region_profile == "us" else ""
    )

    return f"""You are writing prose for a crypto market-summary Telegram channel. Voice: {cfg.get('voice', 'market_summary')}.
{cfg.get('prompt_notes', '')}{us_note}

Facts about this transfer:
- Exchange: {p.get('exchange')}
- Direction: {verb} (relative to the exchange wallet)
- Amount: {p.get('amount')} {p.get('symbol')}
- USD value: ${p.get('value_usd', 0):,.0f}
- Counterparty: {counterparty_note}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these
three string fields:
{{
  "title": "one specific, informative title naming the exchange and the
    approximate dollar amount — NOT a generic label. E.g. '$4.2M USDC
    withdrawn from Binance', never 'Whale Alert'. No emoji.",
  "narrative": "1-2 sentences: what moved, with the key numbers inline.
    Plain prose.",
  "why_it_matters": "EXACTLY one sentence on the likely market read (e.g.
    accumulation vs. distribution signal) — state it as a typical
    interpretation, not a certainty or a directive."
}}

Do not mention scores or internal categorization. Never state a price
prediction or tell the reader what to do with their own funds. No emoji
anywhere in your output. Keep the combined narrative + why_it_matters under
~60 words — the whole post (header/history/risk/source/hashtags added
separately, not by you) targets roughly 400-700 characters.
"""


WHALE_BACKFILL_SCAN_LIMIT = 20  # prior txs to check EACH of native-ETH and ERC-20 (40 total) on a wallet's FIRST flagged move
WHALE_DORMANCY_DAYS = 14         # matches the plan's dormancy-vs-repeat-mover framing threshold
# Same price-confidence gate as collectors/whale_movements.py — kept as a
# duplicate constant rather than a cross-module import, matching how the
# rest of this category's shared reasoning already lives in per-module
# comments (e.g. the V2 API deprecation note) rather than a shared config.
MIN_PRICE_CONFIDENCE = 0.8
MAX_PRICE_AGE_HOURS = 24
# V2: same deprecation fix as collectors/whale_movements.py — see that file's
# ETHERSCAN_URL comment for the discovery story.
ETHERSCAN_URL = "https://api.etherscan.io/v2/api"
ETHERSCAN_CHAIN_ID = 1
DEFILLAMA_HISTORICAL_PRICE_URL = "https://coins.llama.fi/prices/historical/"


def _whale_history_frame(exchange: str, direction: str, value_usd: float, days_ago: float) -> str:
    verb = "withdrew from" if direction == "outflow" else "deposited to"
    if days_ago >= WHALE_DORMANCY_DAYS:
        return f"Dormant since — this wallet's last comparable move was {days_ago:.0f}d ago, when it {verb} {exchange} (${value_usd:,.0f})."
    return f"Also {verb} {exchange} {days_ago:.0f}d ago (${value_usd:,.0f}) — repeat activity from this wallet."


def _compute_whale_own_history_line(raw_item: dict, history: list) -> str | None:
    """Uses OUR OWN accumulated raw_items — cheap, no extra API calls. Unlike
    defi_yields, there's no external N-day reference window to avoid
    contradicting here, so any validated prior flagged move counts as real
    memory regardless of recency (same null/zero defensive validation still
    applies — never build a history line from a missing/malformed prior value)."""
    if not history:
        return None
    prior = history[0]  # DESC order -> most recent prior flagged move
    prior_p = prior["payload"]
    prior_value = prior_p.get("value_usd")
    if not prior_value or prior_value <= 0:
        return None
    if not (raw_item.get("collected_at") and prior.get("collected_at")):
        return None

    days_ago = (raw_item["collected_at"] - prior["collected_at"]).total_seconds() / 86400
    return _whale_history_frame(prior_p.get("exchange"), prior_p.get("direction"), prior_value, days_ago)


def _get_eth_price_historical(timestamp: int) -> float | None:
    resp = get_json(f"{DEFILLAMA_HISTORICAL_PRICE_URL}{timestamp}/coingecko:ethereum")
    coin = resp.get("coins", {}).get("coingecko:ethereum")
    return coin["price"] if coin else None


def _get_token_price_historical(contract_address: str, timestamp: int) -> float | None:
    """None means 'don't trust this enough to publish a dollar figure' — see
    collectors/whale_movements.py's _get_token_price_usd for the same check on
    the live-collection path and the reasoning (operator direction 2026-09-10).
    For a historical lookup, staleness is measured differently: DefiLlama's
    own `timestamp` on the returned point tells us how far its NEAREST actual
    data is from the timestamp we asked for — a large gap means it's
    extrapolating for a thinly-traded token, not truly pricing that moment."""
    key = f"ethereum:{contract_address.lower()}"
    resp = get_json(f"{DEFILLAMA_HISTORICAL_PRICE_URL}{timestamp}/{key}")
    coin = resp.get("coins", {}).get(key)
    if not coin or coin.get("price") is None:
        return None

    confidence = coin.get("confidence")
    if confidence is not None and confidence < MIN_PRICE_CONFIDENCE:
        logger.info("Skipping historical %s@%d -- low DefiLlama confidence (%.2f < %.2f)",
                    contract_address, timestamp, confidence, MIN_PRICE_CONFIDENCE)
        return None

    point_ts = coin.get("timestamp")
    if point_ts is not None and abs(point_ts - timestamp) > MAX_PRICE_AGE_HOURS * 3600:
        gap_h = abs(point_ts - timestamp) / 3600
        logger.info("Skipping historical %s@%d -- nearest DefiLlama data point is %.1fh away (> %dh)",
                    contract_address, timestamp, gap_h, MAX_PRICE_AGE_HOURS)
        return None

    return coin["price"]


def _backfill_whale_history(conn, raw_item: dict) -> str | None:
    """Real on-chain lookup for this wallet's actual prior large transfer,
    even from before we started tracking it — operator direction 2026-09-10:
    'this is the one category where real memory works on day one... Etherscan
    can pull a wallet's actual prior large transfers even from before we
    started collecting.' Runs ONCE per wallet (only when there's no
    accumulated raw_items history yet, i.e. this is the first time we've
    flagged it) — every subsequent move for the same wallet reuses the
    cheaper, already-accumulated history via _compute_whale_own_history_line
    instead.

    Scans BOTH native-ETH and ERC-20 history (extended 2026-09-10 — live
    evidence settled it: a real watched Binance wallet's native-ETH activity
    turned out to be 100% zero-value dust, with its actual economic activity
    entirely in tokens; an ETH-only backfill was completely blind to it, not
    just incomplete). Candidates from both are merged and checked in true
    chronological order, most recent first, so the first qualifying hit is
    genuinely the most recent real prior move regardless of which type it was.
    """
    p = raw_item["payload"]
    watched_address = p.get("watched_address")
    current_ts = p.get("timestamp")
    current_tx_hash = p.get("tx_hash")
    if not watched_address or not current_ts:
        return None

    api_key = os.environ.get("ETHERSCAN_API_KEY")
    if not api_key:
        return None  # can't backfill without a live key -- omit rather than fail the whole post

    try:
        cfg = load_category_config(conn, "whale_movements")
        min_usd = float(cfg.get("collect_min_usd") or 2_000_000)
        native_resp = get_json(
            ETHERSCAN_URL,
            params={
                "chainid": ETHERSCAN_CHAIN_ID,
                "module": "account", "action": "txlist", "address": watched_address,
                "startblock": 0, "endblock": 99999999,
                "page": 1, "offset": WHALE_BACKFILL_SCAN_LIMIT, "sort": "desc",
                "apikey": api_key,
            },
        )
        token_resp = get_json(
            ETHERSCAN_URL,
            params={
                "chainid": ETHERSCAN_CHAIN_ID,
                "module": "account", "action": "tokentx", "address": watched_address,
                "page": 1, "offset": WHALE_BACKFILL_SCAN_LIMIT, "sort": "desc",
                "apikey": api_key,
            },
        )
    except Exception:
        logger.warning("Backfill lookup failed for %s -- omitting history", watched_address)
        return None

    # (tx, is_token) pairs from both sources, merged into true chronological
    # order so the first qualifying candidate really is the most recent one.
    candidates = [(tx, False) for tx in (native_resp.get("result") or [])]
    candidates += [(tx, True) for tx in (token_resp.get("result") or [])]
    candidates.sort(key=lambda pair: int(pair[0].get("timeStamp", 0) or 0), reverse=True)

    for tx, is_token in candidates:
        if tx.get("hash") == current_tx_hash:
            continue  # skip the transfer we're writing about
        try:
            ts = int(tx.get("timeStamp", 0))
        except (TypeError, ValueError):
            continue
        if ts >= current_ts:
            continue  # only genuinely PRIOR transactions

        if is_token:
            try:
                decimals = int(tx.get("tokenDecimal") or 18)
                amount = int(tx["value"]) / (10 ** decimals)
            except (KeyError, ValueError):
                continue
            contract = tx.get("contractAddress")
            if not contract:
                continue
            price = _get_token_price_historical(contract, ts)
        else:
            try:
                amount = int(tx["value"]) / 1e18
            except (KeyError, ValueError):
                continue
            price = _get_eth_price_historical(ts)

        if price is None:
            continue  # can't value it reliably -- skip rather than guess
        value_usd = amount * price
        if value_usd < min_usd:
            continue

        days_ago = (current_ts - ts) / 86400
        direction = "outflow" if (tx.get("from") or "").lower() == watched_address else "inflow"
        return _whale_history_frame(p.get("exchange"), direction, value_usd, days_ago)

    return None  # no qualifying prior move in the scanned window -- omit, don't overclaim


@register_post_computer("whale_movements")
def compute_whale_movements_elements(conn, raw_item: dict, history: list) -> dict:
    """Everything here is a fact lookup or a fixed rule — never invented
    prose. See post_format.py's module docstring for why this is
    deliberately not the LLM's job."""
    history_line = _compute_whale_own_history_line(raw_item, history)
    if history_line is None and not history:
        history_line = _backfill_whale_history(conn, raw_item)

    p = raw_item["payload"]
    risk_line = None
    if not p.get("counterparty_is_exchange"):
        sent_count = p.get("counterparty_sent_tx_count")
        first_tx_ts = p.get("counterparty_first_tx_ts")
        tx_ts = p.get("timestamp")
        if sent_count is not None and sent_count < 5:
            risk_line = "Counterparty wallet has very little on-chain history — treat as a lower-confidence signal."
        elif first_tx_ts is not None and tx_ts is not None and (tx_ts - first_tx_ts) < 7 * 86400:
            risk_line = "Counterparty wallet was created within the past week."

    # No hyperlinks (2026-09-10, operator direction) — plain-text attribution only.
    return {"history_line": history_line, "risk_line": risk_line, "source_name": "Etherscan"}


@register_prompt_builder("web3_jobs")
def build_web3_jobs_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    p = raw_item["payload"]
    salary_max = p.get("salary_max") or 0
    salary_note = (
        f"${p.get('salary_min') or 0:,.0f}-${salary_max:,.0f}" if salary_max else "not disclosed"
    )

    return f"""You are writing prose for a web3/crypto jobs Telegram channel. Voice: {cfg.get('voice', 'opportunity_framed')}.
{cfg.get('prompt_notes', '')}

Facts about this listing:
- Role: {p.get('position')}
- Company: {p.get('company')}
- Salary: {salary_note}
- Location: {p.get('location') or 'remote (no geographic restriction stated)'}
- Tags: {', '.join(p.get('tags') or [])}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these
three string fields:
{{
  "title": "one specific title naming the role and company — NOT a generic
    label. E.g. 'Senior Solidity Engineer role open at Chainlink', never
    'New Web3 Job'. No emoji.",
  "narrative": "1-2 sentences: what the role is and who it's for. Plain prose.",
  "why_it_matters": "EXACTLY one sentence on what makes this worth a second
    look — the comp, the company, or the scope of the role. No hype."
}}

Do not mention scores or internal categorization. Never overstate or
editorialize beyond what the facts above actually say — if salary isn't
disclosed, say so plainly rather than guessing or hyping the opportunity. No
emoji anywhere in your output. Keep the combined narrative + why_it_matters
under ~60 words — the whole post targets roughly 400-700 characters.
"""


@register_post_computer("web3_jobs")
def compute_web3_jobs_elements(conn, raw_item: dict, history: list) -> dict:
    """No history_line, deliberately (operator direction 2026-09-10): a job
    posting isn't a recurring signal the way a wallet's past moves or a
    pool's yield trend are — "this company posted a job before" isn't
    memory a reader benefits from, unlike "this wallet did X last month".
    The honest answer for this category is "not much" and forcing a
    blockquote here would be padding, exactly what Section 1's differentiator
    is supposed to avoid. No risk_line either, for the same "don't force it"
    reasoning — credibility concerns are already scored (pipeline/score.py),
    not re-litigated in prose."""
    return {"history_line": None, "risk_line": None, "source_name": "RemoteOK"}


@register_prompt_builder("startup_jobs")
def build_startup_jobs_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    """Same shape as build_web3_jobs_prompt -- builder audience, not crypto,
    per collectors/startup_jobs.py's inverse relevance gate."""
    p = raw_item["payload"]
    salary_max = p.get("salary_max") or 0
    salary_note = (
        f"${p.get('salary_min') or 0:,.0f}-${salary_max:,.0f}" if salary_max else "not disclosed"
    )

    return f"""You are writing prose for a startup/tech jobs Telegram channel (audience:
founders, indie hackers, people job-hunting at startups -- not a crypto
audience). Voice: {cfg.get('voice', 'opportunity_framed')}.
{cfg.get('prompt_notes', '')}

Facts about this listing:
- Role: {p.get('position')}
- Company: {p.get('company')}
- Salary: {salary_note}
- Location: {p.get('location') or 'remote (no geographic restriction stated)'}
- Tags: {', '.join(p.get('tags') or [])}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these
three string fields:
{{
  "title": "one specific title naming the role and company — NOT a generic
    label. No emoji.",
  "narrative": "1-2 sentences: what the role is and who it's for. Plain prose.",
  "why_it_matters": "EXACTLY one sentence on what makes this worth a second
    look — the comp, the company, or the scope of the role. No hype."
}}

Do not mention scores or internal categorization. Never overstate or
editorialize beyond what the facts above actually say — if salary isn't
disclosed, say so plainly rather than guessing or hyping the opportunity. No
crypto framing or financial language. No emoji anywhere in your output. Keep
the combined narrative + why_it_matters under ~60 words — the whole post
targets roughly 400-700 characters.
"""


@register_post_computer("startup_jobs")
def compute_startup_jobs_elements(conn, raw_item: dict, history: list) -> dict:
    """Same reasoning as compute_web3_jobs_elements -- no history_line, no
    risk_line."""
    return {"history_line": None, "risk_line": None, "source_name": "RemoteOK"}


# Enforcement mechanism for "never overstate what a security check proves"
# (operator direction 2026-09-11, gems_security + defi_yields retrofit: "the
# write step must never imply more certainty than the data supports, and I
# want to see how you're enforcing that, not just an instruction in a
# prompt"). Checked against what the LLM actually returned, not just
# requested in the prompt -- prompts get ignored; a code-level guard doesn't.
# Not category-scoped on purpose: cheap to run everywhere, and a category
# that doesn't touch GoPlus data has nothing to trip it on anyway.
BANNED_OVERCLAIM_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"\bis (?:completely |totally |100% )?safe\b",
    r"\bis legit\b",
    r"\bis (?:not )?a scam\b",
    r"\bguaranteed\b",
    r"\brisk[- ]free\b",
    r"\bverified safe\b",
    r"\byou can trust\b",
    r"\bproven (?:safe|legit)\b",
    r"\bno risk\b",
    r"\bdefinitely (?:safe|risky)\b",
    r"\bcertainly (?:safe|risky)\b",
]]


def _find_overclaims(parsed: dict) -> list[str]:
    text = " ".join(str(parsed.get(k, "")) for k in ("title", "narrative", "why_it_matters"))
    return [p.pattern for p in BANNED_OVERCLAIM_PATTERNS if p.search(text)]


# Copyright guard (news category, 2026-09-12) -- extends the SAME checked-
# write mechanism built for gems_security's overclaim guard, rather than a
# parallel one, per operator direction ("the way you did the gems
# banned-phrase guard"). Only ever invoked with a real source_excerpt
# (news); every other category passes None and pays nothing for this check.
#
# Worth being explicit about the actual risk surface: this system only ever
# collects an RSS teaser excerpt (collectors/news.py), never a scraped full
# article body -- there is no complete article text anywhere in this system
# to closely paraphrase the structure of in the first place. This check is
# real defense-in-depth against reproducing even that excerpt, not the only
# thing standing between this category and a copyright problem.
_QUOTE_SPAN_RE = re.compile(r"[\"“]([^\"”]{1,600})[\"”]")
REPRODUCTION_NGRAM_SIZE = 8       # 8+ consecutive words verbatim = reproduction, not paraphrase
REPRODUCTION_QUOTE_MAX_WORDS = 25  # a "short attributed quote" beyond this reads as excerpt reproduction
REPRODUCTION_SIMILARITY_THRESHOLD = 0.6  # overall structural closeness, even without an exact long run


def _find_reproduction_issues(parsed: dict, source_excerpt: str | None) -> list[str]:
    if not source_excerpt:
        return []
    text = " ".join(str(parsed.get(k, "")) for k in ("title", "narrative", "why_it_matters"))

    issues = []
    unquoted = text
    for m in _QUOTE_SPAN_RE.finditer(text):
        quote = m.group(1)
        if len(quote.split()) > REPRODUCTION_QUOTE_MAX_WORDS:
            issues.append(f"quoted span is {len(quote.split())} words -- too long to read as a short attributed quote")
        unquoted = unquoted.replace(m.group(0), " ", 1)

    src_words = re.findall(r"\w+", source_excerpt.lower())
    gen_words = re.findall(r"\w+", unquoted.lower())
    n = REPRODUCTION_NGRAM_SIZE
    src_ngrams = {tuple(src_words[i:i + n]) for i in range(max(0, len(src_words) - n + 1))}
    if any(tuple(gen_words[i:i + n]) in src_ngrams for i in range(max(0, len(gen_words) - n + 1))):
        issues.append(f"reproduces {n}+ consecutive words verbatim from the source outside a marked quote")

    ratio = difflib.SequenceMatcher(None, unquoted.lower(), source_excerpt.lower()).ratio()
    if ratio > REPRODUCTION_SIMILARITY_THRESHOLD:
        issues.append(f"closely mirrors the source's structure (similarity {ratio:.2f})")

    return issues


def _generate_checked_write(conn, category: str, prompt: str, *, model_override: str | None = None,
                             source_excerpt: str | None = None) -> str:
    """llm.generate_write, but refuses to let an overclaiming OR (when
    source_excerpt is given) reproduced draft through. One retry with a
    stricter reminder appended to the SAME prompt, then a hard failure
    rather than silently shipping it -- same "never silently ship a
    malformed post" philosophy as post_format.parse_llm_json. A
    RuntimeError here surfaces as a failed write, not a bad post the
    operator has to catch by reading carefully."""
    def _check(raw: str) -> list[str]:
        parsed = post_format.parse_llm_json(raw)
        return _find_overclaims(parsed) + _find_reproduction_issues(parsed, source_excerpt)

    raw = llm.generate_write(conn, category, prompt, model_override=model_override)
    hits = _check(raw)
    if hits:
        logger.warning("write_post: guard violation for category=%s, retrying once: %s", category, hits)
        stricter_prompt = prompt + (
            "\n\nSTRICT REMINDER: your previous draft either (a) used language claiming or "
            "implying certainty this data doesn't support (words like 'safe', 'legit', "
            "'guaranteed', 'risk-free', 'no risk') or (b) reproduced or too-closely paraphrased "
            "the source material instead of summarizing it in your own words. Fix both: state "
            "only what the data supports, and write your own summary -- a short, clearly quoted "
            "and attributed phrase is fine, reproducing sentences or structure is not."
        )
        raw = llm.generate_write(conn, category, stricter_prompt, model_override=model_override)
        hits = _check(raw)
        if hits:
            raise RuntimeError(
                f"write_post: category={category} still failed the output guard after one retry "
                f"({hits}) -- refusing to publish rather than ship it"
            )
    return raw


@register_prompt_builder("gems_security")
def build_gems_security_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    """Educational voice, built into the prompt structurally, not left as a
    tone request (operator direction 2026-09-11): the reader should come away
    better at spotting this PATTERN themselves, not just told a verdict about
    this one token. The LLM is deliberately never asked for a safety verdict
    at all -- only to explain a finding Python already determined (see
    compute_gems_security_elements) -- and is handed the found/unchecked
    split explicitly so it can't imply more than what was actually checked."""
    p = raw_item["payload"]
    red_flags = p.get("red_flags") or []
    tokens_unchecked = p.get("tokens_unchecked") or []

    unchecked_note = (
        f"\nNote: {len(tokens_unchecked)} of this pool's underlying token(s) could not be "
        f"checked at all (unsupported chain or no GoPlus data) -- do not imply they're clean, "
        f"say plainly that they weren't checked."
        if tokens_unchecked else ""
    )

    return f"""You are writing prose for a crypto DeFi security/education channel. Voice: {cfg.get('voice', 'educational')}.
{cfg.get('prompt_notes', '')}

Facts about this pool (already verified by an automated check, not your judgment):
- Protocol: {p.get('project')}
- Chain: {p.get('chain')}
- Pool: {p.get('symbol')}
- TVL: ${p.get('tvl_usd', 0):,.0f}
- Specific security findings from GoPlus's Token Security API:
{chr(10).join(f'  - {flag}' for flag in red_flags)}{unchecked_note}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these
three string fields:
{{
  "title": "one specific title naming the ACTUAL finding — e.g. 'XYZ pool's
    token has a hidden owner who can still mint', never a vague 'Watch out
    for this token'. No emoji.",
  "narrative": "2-3 sentences: state plainly what the check found, on this
    specific pool. Do not add certainty the check itself doesn't have — you
    are reporting a finding, not delivering a verdict.",
  "why_it_matters": "EXACTLY one sentence explaining the GENERAL pattern
    behind this specific finding, and how a reader could check for the same
    thing themselves on any token. This is the teaching moment — the reader
    should leave better at spotting this pattern, not just informed about
    this one token."
}}

Hard rules, no exceptions: never write "safe", "legit", "a scam", "guaranteed",
"risk-free", "verified safe", or any equivalent verdict language — you are
reporting specific automated findings, not rendering a safety judgment. Never
say or imply a token is fine, trustworthy, or worth buying/depositing into,
even by omission. Never tell the reader what to do with their own money. No
emoji anywhere in your output. Keep the combined narrative + why_it_matters
under ~70 words — the whole post targets roughly 400-700 characters.
"""


@register_post_computer("gems_security")
def compute_gems_security_elements(conn, raw_item: dict, history: list) -> dict:
    """No history_line -- a security finding isn't a recurring signal the way
    a wallet's past moves are (same reasoning web3_jobs used to omit one).
    risk_line here is NOT optional or conditional the way it is for every
    other category -- this category only ever posts when there IS a finding,
    so risk_line always exists, and it explicitly separates what was found
    from what couldn't be checked (operator direction 2026-09-11: an
    unchecked token must say so, never stay silent -- silence reads as
    "passed")."""
    p = raw_item["payload"]
    red_flags = p.get("red_flags") or []
    tokens_unchecked = p.get("tokens_unchecked") or []

    parts = ["GoPlus Token Security: " + "; ".join(red_flags)]
    if tokens_unchecked:
        parts.append(
            f"{len(tokens_unchecked)} underlying token(s) in this pool could not be checked "
            f"at all — not the same as a clean result"
        )
    risk_line = ". ".join(parts) + "."

    return {"history_line": None, "risk_line": risk_line, "source_name": "GoPlus Token Security API"}


@register_prompt_builder("news")
def build_news_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    """The write step's real job here (operator direction 2026-09-11: "the
    spec's premise is posts worth reading late -- a post that rephrases the
    article adds nothing"): explain SIGNIFICANCE, never restate the
    headline. Enforced structurally too, not just requested -- see
    _generate_checked_write's reproduction guard, which checks the actual
    returned prose against the source excerpt, not just the prompt wording."""
    # No coordination with compute_news_elements' history_line/context_line
    # here, deliberately -- same pattern every other category already uses
    # (compare build_defi_yields_prompt / compute_defi_yields_elements):
    # the LLM's prose is written with no knowledge of the deterministic
    # blockquotes that get appended around it afterward, so those elements
    # stay guaranteed-accurate regardless of what the model does with the
    # facts it WAS given below.
    p = raw_item["payload"]

    return f"""You are writing prose for a crypto market-summary Telegram channel. Voice: {cfg.get('voice', 'market_summary')}.
{cfg.get('prompt_notes', '')}

Facts about this story (from a wire excerpt, summarize in your own words --
never quote more than a short phrase, and never copy the excerpt's sentence
structure):
- Headline: {p.get('title')}
- Source excerpt: {p.get('description')}
- Outlet: {p.get('source')}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these
three string fields:
{{
  "title": "one specific title in your own words — not a copy of the
    headline above. No emoji.",
  "narrative": "1-2 sentences: what happened, in your own words, summarized
    from the excerpt — never a close paraphrase of its structure or wording.",
  "why_it_matters": "EXACTLY one sentence on the SIGNIFICANCE or implication
    — what this means going forward, not a restatement of the narrative in
    different words. If you genuinely cannot say anything beyond what the
    narrative already covers, say what to watch for next instead."
}}

Do not mention scores or internal categorization. Never state a price
prediction or tell the reader what to do with their own funds. No emoji
anywhere in your output. Keep the combined narrative + why_it_matters under
~70 words — the whole post targets roughly 400-700 characters.
"""


def _news_topic_overlap_ok(current_entities: dict, candidate_payload: dict, threshold: float) -> bool:
    candidate_entities = {
        "tickers": set(candidate_payload.get("tickers") or []),
        "phrases": set(candidate_payload.get("phrases") or []),
        "figures": set(candidate_payload.get("figures") or []),
    }
    return entity_lib.fingerprint_overlap(current_entities, candidate_entities) >= threshold


# Live bug, first end-to-end test 2026-09-12: originally set to 0.3 on the
# theory that continuation should be LOOSER than same-cycle dedup (a
# developing story drifts further from earlier coverage than same-day
# multi-outlet coverage of one event does). Real test caught this being
# wrong: 0.3 is exactly fingerprint_overlap's ticker-only floor (pipeline/
# entities.py), so it provided NO actual filtering beyond "shares a
# ticker" -- a real run produced "we covered this developing story" linking
# a Spark/OKX USDT vault story to an unrelated Tether private-credit-fund
# story, whose only connection was both mentioning USDT. A "developing
# story" claim is a STRONGER, more visible editorial statement to a reader
# than silently merging same-cycle duplicates -- it deserves a HIGHER bar,
# not a lower one. Raised to require more than ticker-only agreement
# (ticker+figure, ticker+phrase, or figure+phrase all clear this).
NEWS_CONTINUATION_OVERLAP_THRESHOLD = 0.65

# How far back to look for a cross-category connection -- our own
# whale_movements/defi_yields data has to be genuinely recent to be a fair
# "also happening right now" fact, not a stale coincidence.
CROSS_CATEGORY_LOOKBACK_HOURS = 72


# Words too short/generic to safely word-boundary-match against free news
# prose without real risk of a coincidental hit (unlike a protocol slug's
# first segment, which is usually distinctive enough on its own).
_PROJECT_NAME_MIN_LENGTH = 3

# A few real `project` values (pipeline/score.py's data, see the live query
# behind this design) whose first hyphen-segment would be wrong or too
# generic to use as-is -- same "small, hand-maintained override" shape as
# collectors/gems_security.py's KNOWN_MAJOR_TOKENS, kept minimal since most
# project slugs normalize correctly automatically (see _normalize_project).
_PROJECT_NAME_OVERRIDES = {
    "stake-dao-yield": "stakedao",
    "project-0": None,  # a real but non-identifying placeholder value seen in the data
}


def _normalize_project(project_slug: str | None) -> str | None:
    """defi_yields/gems_security `project` values are versioned slugs
    ("uniswap-v3", "aave-v3", "raydium-amm") -- this recovers the actual
    brand name a news article would use ("uniswap", "aave", "raydium") by
    taking the slug's leading segment, with a small override list for the
    handful of real values (live-queried, not guessed) where that's wrong.
    Deliberately NOT a hand-curated protocol name list -- the set of names
    worth checking is exactly whatever we've actually collected, so it
    never goes stale as new protocols show up in defi_yields' feed."""
    if project_slug is None:
        return None
    if project_slug in _PROJECT_NAME_OVERRIDES:
        return _PROJECT_NAME_OVERRIDES[project_slug]
    name = project_slug.split("-")[0].strip().lower()
    return name if len(name) >= _PROJECT_NAME_MIN_LENGTH else None


def _find_cross_category_connection(conn, news_text: str) -> str | None:
    """The piece operator direction 2026-09-11/12 asked for real attention
    on: does this news item connect to OUR OWN whale_movements/defi_yields/
    gems_security data in a way that's genuinely informative, not just
    coincidental? This went through two real, live-caught failures before
    landing here -- worth understanding both, since the fix is a direct
    response to each:

    1. Ticker-overlap matching surfaced a 0.01% APY pool with nothing to do
       with the actual story -- they shared "USDT" and nothing else. Fixed
       (partially) by requiring the matched row to have independently
       cleared its own category's review_threshold.
    2. That fix still surfaced a real, independently-notable pool (a 43.5%
       APY pair at the TVL floor -- also flagged in BACKLOG.md as a live
       instance of defi_yields' known APY-spike scoring gap) that was STILL
       unrelated to the actual news story. The deeper problem (operator
       direction): "a shared ticker isn't a connection, even a notable
       one." USDT appears in hundreds of unrelated products; sharing it
       proves nothing about relatedness.

    The fix that actually addresses the root cause: match on the specific
    PROTOCOL or EXCHANGE NAME the news text names, not the generic ticker
    -- "Spark" appearing in both the headline and our own tracked Spark
    pools is a real, specific connection; "USDT" appearing in both proves
    only that both involve a widely-used stablecoin. And per operator
    direction, require the matched row to have been ACTUALLY NOTIFIED (not
    merely scored above threshold) -- a materially higher, less gameable
    bar than a raw score comparison, since it means the connection is
    always to something already judged genuinely worth attention, not a
    score technicality.

    This is deliberately narrower than the multi-signal-corroboration idea
    also discussed (e.g. a token showing both a notable exchange outflow
    AND a notable yield move together telling one coherent story) -- that
    would mean correlating two independently-notified facts with each
    other, a materially bigger feature than name-matching one news item
    against one notified row. Not built this pass; flagged as a real
    follow-on, not silently dropped. "Rare and real beats frequent and
    coincidental" (operator direction) -- this fires far less often than
    the ticker-matching version did, which is the intended outcome, not a
    regression to fix.
    """
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT r.category, r.payload, n.sent_at
               FROM raw_items r
               JOIN notifications n ON r.id = ANY(n.candidate_raw_item_ids)
               WHERE r.category IN ('whale_movements', 'defi_yields', 'gems_security')
                 AND n.sent_at > now() - (%s || ' hours')::interval
               ORDER BY n.sent_at DESC LIMIT 200""",
            (CROSS_CATEGORY_LOOKBACK_HOURS,),
        )
        rows = cur.fetchall()

    # Visible, not silent (operator direction 2026-09-12): almost nothing
    # will be notified yet with everything held for Hetzner, so this will
    # correctly return None constantly for a while -- log which case it is,
    # so "correctly omitted, nothing notified yet" stays distinguishable
    # from "quietly broken" once there's real notified history to check
    # against.
    if not rows:
        logger.info("news: cross-category lookup -- no notified whale_movements/defi_yields/"
                     "gems_security rows in the last %dh", CROSS_CATEGORY_LOOKBACK_HOURS)
        return None

    text_lower = (news_text or "").lower()
    for row in rows:
        p = row["payload"]
        if row["category"] == "whale_movements":
            name = (p.get("exchange") or "").strip().lower()
        else:  # defi_yields / gems_security
            name = _normalize_project(p.get("project"))
        if not name or len(name) < _PROJECT_NAME_MIN_LENGTH:
            continue
        if not re.search(rf"\b{re.escape(name)}\b", text_lower):
            continue

        age_hours = (datetime.now(timezone.utc) - row["sent_at"]).total_seconds() / 3600
        when = "earlier today" if age_hours < 20 else f"{age_hours / 24:.0f}d ago"

        if row["category"] == "whale_movements":
            verb = "withdrew from" if p.get("direction") == "outflow" else "deposited to"
            return (
                f"a ${p.get('value_usd', 0):,.0f} {p.get('symbol')} transfer {verb} "
                f"{p.get('exchange')}, {when}, in our own whale tracking"
            )
        elif row["category"] == "defi_yields":
            return (
                f"{p.get('project')} {p.get('symbol')} is currently yielding "
                f"{p.get('apy')}% APY (TVL ${p.get('tvl_usd', 0):,.0f}) in our own DeFi tracking"
            )
        else:  # gems_security
            return (
                f"a GoPlus security finding on {p.get('symbol')} ({p.get('project')}) "
                f"we flagged {when}: {'; '.join(p.get('red_flags') or [])}"
            )

    logger.info("news: cross-category lookup -- %d notified row(s) in the last %dh, "
                 "none named in this item's text", len(rows), CROSS_CATEGORY_LOOKBACK_HOURS)
    return None


@register_post_computer("news")
def compute_news_elements(conn, raw_item: dict, history: list) -> dict:
    """Two genuinely deterministic value-adds, both optional and omitted
    honestly when absent (operator direction 2026-09-11): history_line for
    a real developing-story continuation, context_line for a cross-category
    connection to our OWN whale_movements/defi_yields data -- the strongest
    version of Section 1's "memory the reader doesn't have" idea in any
    category so far, since it's the one thing a reader genuinely cannot get
    from the article itself."""
    p = raw_item["payload"]
    current_entities = {
        "tickers": set(p.get("tickers") or []),
        "phrases": set(p.get("phrases") or []),
        "figures": set(p.get("figures") or []),
    }

    history_line = None
    for prior in history:
        if _news_topic_overlap_ok(current_entities, prior["payload"], NEWS_CONTINUATION_OVERLAP_THRESHOLD):
            days_ago = (raw_item["collected_at"] - prior["collected_at"]).days if raw_item.get("collected_at") and prior.get("collected_at") else None
            when = f"{days_ago}d ago" if days_ago is not None else "previously"
            history_line = f"We covered this developing story {when}: \"{prior['payload'].get('title')}\""
            break

    news_text = f"{p.get('title') or ''} {p.get('description') or ''}"
    context_line = _find_cross_category_connection(conn, news_text)

    return {
        "history_line": history_line,
        "risk_line": None,
        "source_name": p.get("source") or None,
        "context_line": context_line,
    }


@register_prompt_builder("tool_launches")
def build_tool_launches_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    p = raw_item["payload"]
    if p.get("source") == "hackernews":
        facts = (
            f"- Launch: {p.get('title')}\n"
            f"- Discussed on Hacker News: {p.get('points')} points, {p.get('comments')} comments\n"
            f"- Link: {p.get('url')}"
        )
        cross_note = "\nNote: this also appeared on GitHub Trending the same day." if p.get("also_trending_on_github") else ""
    else:
        facts = (
            f"- Repository: {p.get('title')}\n"
            f"- Trending on GitHub: {p.get('stars_today')} stars today (language: {p.get('language') or 'unspecified'})\n"
            f"- Description: {p.get('description') or 'none given'}"
        )
        cross_note = "\nNote: this was also posted to Hacker News the same day." if p.get("also_shown_on_hn") else ""

    return f"""You are writing prose for a builder/startup-tools Telegram channel (audience:
founders, indie hackers, people evaluating new tools -- not a crypto audience).
Voice: {cfg.get('voice', 'opportunity_framed')}.
{cfg.get('prompt_notes', '')}

Facts about this launch:
{facts}{cross_note}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these
three string fields:
{{
  "title": "one specific title naming what the tool actually does — not a
    generic label. No emoji.",
  "narrative": "1-2 sentences: what it is and who it's for. Plain prose.",
  "why_it_matters": "EXACTLY one sentence on why a builder should take a
    second look — what problem it solves or what's genuinely new about it.
    No hype."
}}

Do not mention scores or internal categorization. Never inflate a launch
beyond what the facts say — if the traction is modest, say so plainly rather
than implying it's a bigger deal than the numbers show. No emoji anywhere in
your output. Keep the combined narrative + why_it_matters under ~60 words —
the whole post targets roughly 400-700 characters.
"""


@register_post_computer("tool_launches")
def compute_tool_launches_elements(conn, raw_item: dict, history: list) -> dict:
    """No history_line, same reasoning as web3_jobs (Section 1's differentiator
    is deliberately not forced onto a category where "we saw this before"
    isn't real memory -- each launch is its own event). No risk_line -- this
    category isn't a safety/warning category the way gems_security is."""
    p = raw_item["payload"]
    source_name = "Hacker News" if p.get("source") == "hackernews" else "GitHub Trending"
    return {"history_line": None, "risk_line": None, "source_name": source_name}


def _assemble(conn, category: str, raw_item: dict, history: list, llm_raw_output: str) -> str:
    cfg = load_category_config(conn, category)
    parsed = post_format.parse_llm_json(llm_raw_output)
    computer = POST_COMPUTERS.get(category)
    elements = computer(conn, raw_item, history) if computer else {}

    return post_format.assemble_post(
        emoji=cfg.get("emoji") or "",
        title=parsed["title"],
        narrative=parsed["narrative"],
        why_it_matters=parsed["why_it_matters"],
        history_line=elements.get("history_line"),
        risk_line=elements.get("risk_line"),
        source_name=elements.get("source_name"),
        hashtags=cfg.get("hashtags") or [],
        context_line=elements.get("context_line"),
    )


def _build_prompt_and_history(conn, category: str, channel: str, raw_item: dict) -> tuple[str, list]:
    if category not in PROMPT_BUILDERS:
        raise NotImplementedError(f"No prompt builder registered for category '{category}'")
    cfg = load_category_config(conn, category)
    region_profile = _get_region_profile(conn, channel)
    topic_key = (raw_item["payload"] or {}).get("topic_key")
    history = get_topic_history(conn, category, topic_key, raw_item["raw_item_id"])
    prompt = PROMPT_BUILDERS[category](cfg, region_profile, raw_item, history)
    return prompt, history


def generate_post(conn, category: str, channel: str, raw_item: dict) -> str:
    """Single-variant write, using whatever write_model is currently configured."""
    prompt, history = _build_prompt_and_history(conn, category, channel, raw_item)
    source_excerpt = (raw_item.get("payload") or {}).get("description")
    raw = _generate_checked_write(conn, category, prompt, source_excerpt=source_excerpt)
    return _assemble(conn, category, raw_item, history, raw)


def generate_post_variants(conn, category: str, channel: str, raw_item: dict) -> dict:
    """Dual-variant write for a benchmark trial (Section 4.3). Always compares
    DeepSeek against Sonnet, regardless of which one write_model currently points
    at, so the trial data is comparable across the whole trial window. Both
    variants go through the identical deterministic assembly (same history/
    risk/source/hashtags) — only the LLM prose differs between them."""
    prompt, history = _build_prompt_and_history(conn, category, channel, raw_item)
    source_excerpt = (raw_item.get("payload") or {}).get("description")
    raw_deepseek = _generate_checked_write(conn, category, prompt, model_override="deepseek-v4-flash", source_excerpt=source_excerpt)
    raw_sonnet = _generate_checked_write(conn, category, prompt, model_override="claude-sonnet-5", source_excerpt=source_excerpt)
    return {
        "deepseek-v4-flash": _assemble(conn, category, raw_item, history, raw_deepseek),
        "claude-sonnet-5": _assemble(conn, category, raw_item, history, raw_sonnet),
    }
