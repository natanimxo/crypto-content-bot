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

import logging
import os
import re

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


def _generate_checked_write(conn, category: str, prompt: str, *, model_override: str | None = None) -> str:
    """llm.generate_write, but refuses to let an overclaiming draft through.
    One retry with a stricter reminder appended to the SAME prompt, then a
    hard failure rather than silently shipping it -- same "never silently
    ship a malformed post" philosophy as post_format.parse_llm_json. A
    RuntimeError here surfaces as a failed write, not a bad post the
    operator has to catch by reading carefully."""
    raw = llm.generate_write(conn, category, prompt, model_override=model_override)
    hits = _find_overclaims(post_format.parse_llm_json(raw))
    if hits:
        logger.warning("write_post: overclaim language detected for category=%s, retrying once: %s", category, hits)
        stricter_prompt = prompt + (
            "\n\nSTRICT REMINDER: your previous draft used language claiming or implying "
            "certainty this data doesn't support (words like 'safe', 'legit', 'guaranteed', "
            "'risk-free', 'no risk'). Do not use any such language anywhere in your output. "
            "State only what the specific checks found, nothing more."
        )
        raw = llm.generate_write(conn, category, stricter_prompt, model_override=model_override)
        hits = _find_overclaims(post_format.parse_llm_json(raw))
        if hits:
            raise RuntimeError(
                f"write_post: category={category} still overclaimed certainty after one retry "
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
    raw = _generate_checked_write(conn, category, prompt)
    return _assemble(conn, category, raw_item, history, raw)


def generate_post_variants(conn, category: str, channel: str, raw_item: dict) -> dict:
    """Dual-variant write for a benchmark trial (Section 4.3). Always compares
    DeepSeek against Sonnet, regardless of which one write_model currently points
    at, so the trial data is comparable across the whole trial window. Both
    variants go through the identical deterministic assembly (same history/
    risk/source/hashtags) — only the LLM prose differs between them."""
    prompt, history = _build_prompt_and_history(conn, category, channel, raw_item)
    raw_deepseek = _generate_checked_write(conn, category, prompt, model_override="deepseek-v4-flash")
    raw_sonnet = _generate_checked_write(conn, category, prompt, model_override="claude-sonnet-5")
    return {
        "deepseek-v4-flash": _assemble(conn, category, raw_item, history, raw_deepseek),
        "claude-sonnet-5": _assemble(conn, category, raw_item, history, raw_sonnet),
    }
