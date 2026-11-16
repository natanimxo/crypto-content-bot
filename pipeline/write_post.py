"""Final post writing (Section 8, step 2; redesigned 2026-09-10). Only ever
called on operator-approved items.

The LLM's job is narrowed to prose only — it returns structured JSON
(title / narrative / why_it_matters), not raw HTML or a full post. Everything
structural (history blockquote, risk flag, source link, hashtags, the single
leading emoji) is computed deterministically in pipeline/post_format.py from
real data, then assembled around the model's prose. See post_format.py's
module docstring for the reliability rationale.

"""

import difflib
import logging
import os
import re
from datetime import datetime, timezone

from pipeline import entities as entity_lib
from pipeline import channel_voice, llm, post_format
from pipeline.db import dict_cursor
from pipeline.http import get_json
from pipeline.post_facts import (
    FACT_SPECS, WHALE_READ_CONVENTION, facts_defi_yields, facts_earning_opportunities, facts_exchange_listings, facts_gems_security, facts_hacks_exploits,
    facts_job_listing, listing_record_window,
    facts_tool_launches,
    facts_whale_movements, facts_wire_story,
)
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

{facts_defi_yields(p)}

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
    # Facts (incl. the 3-way counterparty note and its live-bug history) moved to
    # pipeline/post_facts.py 2026-11-14 so the channel-voice path shares them.

    us_note = (
        "\nThis is a US-audience channel — keep financial framing conservative and "
        "factual; never phrase anything as investment advice or a price prediction."
        if region_profile == "us" else ""
    )

    return f"""You are writing prose for a crypto market-summary Telegram channel. Voice: {cfg.get('voice', 'market_summary')}.
{cfg.get('prompt_notes', '')}{us_note}

{facts_whale_movements(p)}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these
three string fields:
{{
  "title": "one specific, informative title naming the exchange and the
    approximate dollar amount — NOT a generic label. E.g. '$4.2M USDC
    withdrawn from Binance', never 'Whale Alert'. No emoji.",
  "narrative": "1-2 sentences: what moved, with the key numbers inline.
    Plain prose.",
  "why_it_matters": "EXACTLY one sentence on the likely market read, stated as
    a typical interpretation, not a certainty or a directive. {WHALE_READ_CONVENTION}"
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


# Shared JSON-schema block for web3_jobs/startup_jobs (2026-09-15, operator
# direction) -- why_it_matters made OPTIONAL for these two categories
# specifically, not every category (news/macro_news/gems_security keep it
# mandatory in their own prompts -- implication IS the point there).
#
# What prompted this: a real generated startup_jobs post's second paragraph
# just restated the salary and remote status from paragraph one, then
# paraphrased the job description -- padding, because a straightforward
# listing often genuinely has no implication to draw. The old prompt forced
# one anyway ("EXACTLY one sentence... the comp, the company, or the scope
# of the role" as fallback material), which is exactly what produced the
# restatement once nothing about comp/company/scope was actually notable.
#
# Also fixes a second real, related problem in the same generated post:
# circular narrative phrasing ("X is hiring a Social Comms person to handle
# social media" -- restates the title, adds nothing). Both problems share
# one root cause (the prompt gave the model nothing concrete to reach for
# beyond the raw facts already listed above it), so both are fixed here
# together with concrete criteria and a negative example, not just "try
# harder" language.
_JOBS_SCHEMA_BLOCK = """Return ONLY a JSON object (no markdown fence, no commentary) with exactly these
three string fields:
{{
  "title": "one specific title naming the role and company — NOT a generic
    label. No emoji.",
  "narrative": "1-2 sentences: what the role actually involves and who it's
    for. Must add context the title doesn't already give — what the company
    does, why this role is opening, or what's distinctive about the work.
    Never just restate the role/company named in the title in different
    words — e.g. '{company} is hiring a {position} to handle social media'
    says nothing the title didn't already say. If the listing itself gives
    you nothing more specific than the title, describe the actual day-to-day
    scope from the tags/description instead of paraphrasing the job title.",
  "why_it_matters": "EXACTLY one sentence on what makes THIS SPECIFIC
    listing worth a second look — ONLY if something genuinely clears that
    bar: unusually strong comp for the role/level, a notable or recognizable
    company, or a real signal about the market (e.g. a rare fully-remote
    senior opening, a company hiring aggressively). If nothing here clears
    that bar, return an empty string \\"\\" instead — do not restate the
    salary, location, or role already covered in the narrative, and do not
    manufacture significance. A plain, unremarkable listing with an empty
    why_it_matters is the correct, expected output for most listings here,
    not a failure to try harder."
}}

Do not mention scores or internal categorization. Never overstate or
editorialize beyond what the facts above actually say — if salary isn't
disclosed, say so plainly rather than guessing or hyping the opportunity.
Do not restate a fact from the narrative in why_it_matters as if it were new
insight — a repeated statement is not significance. No emoji anywhere in
your output. Keep the combined narrative + why_it_matters (when present)
under ~60 words — the whole post targets roughly 400-700 characters, shorter
when why_it_matters is empty."""


@register_prompt_builder("web3_jobs")
def build_web3_jobs_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    p = raw_item["payload"]
    schema = _JOBS_SCHEMA_BLOCK.format(company=p.get("company"), position=p.get("position"))

    return f"""You are writing prose for a web3/crypto jobs Telegram channel. Voice: {cfg.get('voice', 'opportunity_framed')}.
{cfg.get('prompt_notes', '')}

{facts_job_listing(p)}

{schema}
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
    per collectors/startup_jobs.py's inverse relevance gate. Shares
    _JOBS_SCHEMA_BLOCK's optional-why_it_matters + anti-circularity design
    (see that constant's comment for the real generated post that prompted
    it) -- only the surrounding framing (audience, no-crypto-language rule)
    differs from web3_jobs."""
    p = raw_item["payload"]
    schema = _JOBS_SCHEMA_BLOCK.format(company=p.get("company"), position=p.get("position"))

    return f"""You are writing prose for a startup/tech jobs Telegram channel (audience:
founders, indie hackers, people job-hunting at startups -- not a crypto
audience). Voice: {cfg.get('voice', 'opportunity_framed')}.
{cfg.get('prompt_notes', '')}

{facts_job_listing(p)}

{schema}

No crypto framing or financial language anywhere in your output.
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
def _prose_text(parsed: dict) -> str:
    """Every piece of LLM prose in a parsed write, for the structural guards.
    The legacy shape is title/narrative/why_it_matters; the channel-voice shape
    (2026-11-14) is title + a `paragraphs` list. Both are covered so no guard
    silently stops checking when a channel moves to a voice profile."""
    parts = [str(parsed.get(k, "")) for k in ("title", "narrative", "why_it_matters")]
    parts += [str(x) for x in (parsed.get("paragraphs") or [])]
    return " ".join(parts)


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
    text = _prose_text(parsed)
    return [p.pattern for p in BANNED_OVERCLAIM_PATTERNS if p.search(text)]


# Copyright guard (news category, 2026-09-12; corrected 2026-09-12 after a
# macro_news finding, see below) -- extends the SAME checked-write mechanism
# built for gems_security's overclaim guard, rather than a parallel one, per
# operator direction ("the way you did the gems banned-phrase guard"). Runs
# for every category via generate_post's generic `source_excerpt =
# payload.get("description")` line -- it isn't actually news-only, it's just
# that most categories' payloads have no "description" key, so it naturally
# no-ops (None in, [] out) for them. news and macro_news both populate a
# real description, so both get a real check.
#
# CORRECTED, operator direction 2026-09-12: this comment used to claim the
# architecture itself was doing protective work -- "this system only ever
# collects an RSS teaser excerpt, never a scraped full article body -- there
# is no complete article text anywhere in this system to closely paraphrase
# the structure of in the first place." That was true of news's 6 feeds
# specifically (live-checked: max real description length 373 chars across
# theblock/decrypt/blockworks/thedefiant/protos; CoinDesk's is empty, headline
# -only by design) but was never an architectural guarantee -- it was a fact
# about what those particular feeds happen to put in their RSS `summary`
# field, which is external and could change without any code here changing.
# macro_news proved this directly: its Axios feed delivers full article
# bodies (live-measured, 1000-3400+ chars/item) through the EXACT SAME
# feedparser/`summary` mechanism news uses. Same collection method, opposite
# risk profile -- "RSS-only" was never what was protecting anything. The
# code-level check below is what's actually load-bearing, for every category
# that reaches it, regardless of source length.
#
# And the check itself needed a real fix, not just a documentation update,
# once tested against a genuinely long source (see BACKLOG.md's "Copyright
# guard" entry for the full test sequence): the original similarity check
# used `SequenceMatcher(None, draft, source).ratio()`, which is 2*M /
# (len(draft)+len(source)) -- a fixed amount of verbatim overlap gets
# diluted almost to nothing once `source` is a 2500+ char article instead of
# a ~100-500 char teaser (measured: a real 90-word verbatim lift from a
# 2565-char source scored ratio=0.067, nowhere near the 0.6 threshold, purely
# because of the long denominator, not because the copying was subtle).
# Replaced with `_content_overlap_fraction`, which measures matched
# characters as a fraction of the DRAFT's own length, not the combined
# length -- source length stops mattering, which is the property this
# needed. Re-verified after the fix: the same verbatim lift now scores 1.0
# (caught); a genuine synonym-substituted paraphrase of the same passage
# still scores low (0.17, NOT caught) and a legitimate own-words summary
# scores 0.0 (correctly not caught) -- see that function's docstring for
# what this can and cannot see. Two realistic partial-reproduction drafts
# (half verbatim/half original commentary; one ~20-word verbatim run inside
# an otherwise-original draft) scored 0.37-0.38, which is what
# REPRODUCTION_OVERLAP_THRESHOLD is set just below.
#
# FLOOR, NOT GUARANTEE -- same honesty as _find_neutrality_violations. This
# check reliably catches verbatim-or-near-verbatim lifting, at any source
# length now. It does NOT and structurally cannot catch a genuine semantic
# paraphrase (same meaning, different words, same structure) -- that's a
# character-diff heuristic's real limit, not a bug; catching that would need
# semantic/embedding comparison, a materially different (and non-zero-LLM-
# call) approach, not attempted here.
_QUOTE_SPAN_RE = re.compile(r"[\"“]([^\"”]{1,600})[\"”]")
REPRODUCTION_NGRAM_SIZE = 8       # 8+ consecutive words verbatim = reproduction, not paraphrase
REPRODUCTION_QUOTE_MAX_WORDS = 25  # a "short attributed quote" beyond this reads as excerpt reproduction
REPRODUCTION_MIN_BLOCK_CHARS = 15  # ignore trivial few-character coincidental matches
REPRODUCTION_OVERLAP_THRESHOLD = 0.3  # fraction of the DRAFT's own text made of >=15-char runs shared with the source


def _content_overlap_fraction(gen_text: str, source_text: str,
                               min_block_len: int = REPRODUCTION_MIN_BLOCK_CHARS) -> float:
    """What fraction of gen_text is made of substantial (>=min_block_len
    char) contiguous runs that also appear in source_text -- normalized
    against gen_text's OWN length, not the combined length, so a long
    source doesn't dilute a real match (see the module comment above for
    the live bug this replaced)."""
    if not gen_text:
        return 0.0
    sm = difflib.SequenceMatcher(None, gen_text, source_text)
    matched = sum(block.size for block in sm.get_matching_blocks() if block.size >= min_block_len)
    return matched / len(gen_text)


def _find_reproduction_issues(parsed: dict, source_excerpt: str | None) -> list[str]:
    """Reproduction guard, measured on EXPRESSION rather than facts (2026-11-14, operator
    direction): copyright protects how something is said, not that "U.S. spot crypto ETFs"
    pulled in "$1.55 billion". Entities (figures, tickers/acronyms, proper nouns -- see
    entities.entity_surface_forms, derived from the source excerpt) are masked out of BOTH the
    draft and the source first, then both checks run on what remains:

      1. a run of 8+ consecutive identical NON-entity words (entity placeholders bridge a run so
         a lift cannot hide by sprinkling names through it, but do not count toward its length)
      2. the share of the draft's non-entity characters made of 15+ char runs lifted from the
         source (denominator excludes masked characters, so it no longer penalises a short,
         fact-dense post for stating its facts)

    The thresholds are UNCHANGED (8 words, 0.30). Before this the same two checks ran on raw
    text and made terse, numeric posts -- a 200-350 char Wall Street post -- fail on the
    entity names and figures any accurate post must repeat. Whether masking lets real lifting
    through is tested, not assumed: BACKLOG.md ("Reproduction guard: entity masking").

    Quoted spans are handled exactly as before (<=25 words, excluded from the checks)."""
    if not source_excerpt:
        return []
    text = _prose_text(parsed)

    issues = []
    unquoted = text
    for m in _QUOTE_SPAN_RE.finditer(text):
        quote = m.group(1)
        if len(quote.split()) > REPRODUCTION_QUOTE_MAX_WORDS:
            issues.append(f"quoted span is {len(quote.split())} words -- too long to read as a short attributed quote")
        unquoted = unquoted.replace(m.group(0), " ", 1)

    forms = entity_lib.entity_surface_forms(source_excerpt)
    gen_masked = entity_lib.mask_forms(unquoted, forms)
    src_masked = entity_lib.mask_forms(source_excerpt, forms)
    ph = entity_lib.MASK_PLACEHOLDER

    run = entity_lib.masked_verbatim_run(gen_masked, src_masked, REPRODUCTION_NGRAM_SIZE)
    if run:
        shown = " ".join("[...]" if t == ph else t for t in run)
        issues.append(f"reproduces {REPRODUCTION_NGRAM_SIZE}+ consecutive words verbatim from the source "
                      f"outside a marked quote (\"{shown}\") -- reword that passage in your own words "
                      f"(names and figures may be repeated; the wording around them may not)")

    denom = sum(1 for c in gen_masked if c != ph)
    if denom:
        sm = difflib.SequenceMatcher(None, gen_masked, src_masked, autojunk=False)
        matched = 0
        for b in sm.get_matching_blocks():
            block = gen_masked[b.a:b.a + b.size]
            real = sum(1 for c in block if c != ph)
            if b.size >= REPRODUCTION_MIN_BLOCK_CHARS and real >= REPRODUCTION_MIN_BLOCK_CHARS:
                matched += real
        overlap = matched / denom
        if overlap > REPRODUCTION_OVERLAP_THRESHOLD:
            runs = _overlap_runs(gen_masked, src_masked)
            issues.append(
                f"substantial verbatim overlap with the source ({overlap:.2f} of the draft's wording, must be "
                f"at most {REPRODUCTION_OVERLAP_THRESHOLD:.2f}) -- reword these runs, which appear word for "
                f"word in the source: " + "; ".join(f'"{r.replace(ph, "[...]")}"' for r in runs)
            )
    return issues


def _find_reproduction_issues_unmasked(parsed: dict, source_excerpt: str | None) -> list[str]:
    """The reproduction guard exactly as it was before entity masking (same thresholds, raw text).
    Kept ONLY so the masking change can be tested against it; nothing in the pipeline calls it."""
    if not source_excerpt:
        return []
    text = _prose_text(parsed)

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
    hit = next((tuple(gen_words[i:i + n]) for i in range(max(0, len(gen_words) - n + 1))
                if tuple(gen_words[i:i + n]) in src_ngrams), None)
    if hit:
        issues.append(f"reproduces {n}+ consecutive words verbatim from the source outside a marked quote "
                      f"(\"{' '.join(hit)}...\") -- reword that passage")

    overlap = _content_overlap_fraction(unquoted.lower(), source_excerpt.lower())
    if overlap > REPRODUCTION_OVERLAP_THRESHOLD:
        # Name the exact runs so a retry can actually act on it -- "too much overlap"
        # alone left a terse draft unable to tell WHICH phrases to reword (2026-11-14).
        runs = _overlap_runs(unquoted.lower(), source_excerpt.lower())
        issues.append(
            f"substantial verbatim overlap with the source ({overlap:.2f} of the draft, must be at most "
            f"{REPRODUCTION_OVERLAP_THRESHOLD:.2f}) -- reword these runs, which appear word for word in "
            f"the source: " + "; ".join(f'"{r}"' for r in runs)
        )

    return issues


def _overlap_runs(gen_text: str, source_text: str, limit: int = 4,
                  min_block_len: int = REPRODUCTION_MIN_BLOCK_CHARS) -> list[str]:
    """The longest (>=min_block_len char) runs gen_text shares with source_text --
    what _content_overlap_fraction counted, for naming them in a retry message."""
    sm = difflib.SequenceMatcher(None, gen_text, source_text, autojunk=False)
    ph = entity_lib.MASK_PLACEHOLDER  # masked texts: only real (non-placeholder) characters count
    blocks = sorted((b for b in sm.get_matching_blocks()
                     if b.size >= min_block_len
                     and sum(1 for c in gen_text[b.a:b.a + b.size] if c != ph) >= min_block_len),
                    key=lambda b: -b.size)
    return [gen_text[b.a:b.a + b.size].strip() for b in blocks[:limit]]


# Political neutrality guard (macro_news, 2026-09-12) -- extends the SAME
# checked-write mechanism as the gems_security overclaim guard and the news
# copyright guard, per operator direction: "enforce this the way you did the
# gems overclaim guard and the news copyright check -- a code-level check on
# generated output, not just prompt wording."
#
# IMPORTANT, and the operator was explicit about this (2026-09-12): these
# three checks are a FLOOR, not a guarantee. They catch loaded VOCABULARY,
# which is real -- but bias in macro coverage lives mostly in SELECTION and
# FRAMING (whose casualties get numbers, whose action reads as "response"
# vs. "escalation", which side's stated reasons get repeated and which
# don't) -- and no regex sees any of that. This module makes no claim that
# passing these checks makes a macro_news post safe or neutral. The
# operator's own approval step is what's actually doing that work; these
# checks exist to catch the specific, code-catchable failure mode (loaded
# language, unattributed motive-assertion, direct policy advocacy) that a
# careful human reviewer might still miss on a fast read, not to replace
# that reviewer's judgment about selection and framing.
#
# Sub-check 3 (prescriptive-advocacy) was explicitly flagged by the operator
# as false-positive-risky before being trusted: "the Fed should raise
# rates" is advocacy; "analysts expect the Fed will need to raise rates" is
# attributed reporting -- a bare regex on "needs to" catches both, and a
# false positive here means a legitimate post never generates (RuntimeError,
# not a soft warning). See _find_neutrality_violations' docstring for the
# sentence-level attribution check this uses to tell the two apart, and
# BACKLOG.md for the real-draft test run this was checked against before
# being wired in.
_LOADED_CHARACTERIZATION_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"\bauthoritarian\b", r"\bcorrupt regime\b", r"\bbrutal crackdown\b",
    r"\bdesperate (?:attempt|move)\b", r"\bradical agenda\b", r"\bextremist\b",
    r"\billegitimate government\b", r"\brogue (?:state|nation)\b", r"\bpropaganda\b",
    r"\bwarmongering\b", r"\breckless\b", r"\bfailed policy\b",
]]

_UNATTRIBUTED_MOTIVE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"\bis trying to distract from\b", r"\bin an attempt to cling to power\b",
    r"\bis desperate to\b", r"\bwants? to undermine\b", r"\bcrackdown on\b",
    r"\bis using .{0,40} as a pretext\b",
]]

# Actor + prescriptive-modal, checked per-sentence so an ATTRIBUTION cue in
# the SAME sentence (a third party's view being reported, not the writer's
# own recommendation) exempts it -- see _find_neutrality_violations.
_PRESCRIPTIVE_ACTOR_MODAL_RE = re.compile(
    r"\b(the fed|the federal reserve|lawmakers|congress|the government|regulators|"
    r"the administration|policymakers|the central bank)\b.{0,30}?"
    r"\b(should|must|needs? to|ought to)\b",
    re.IGNORECASE,
)
_ATTRIBUTION_CUE_RE = re.compile(
    r"\b(analysts?|economists?|traders?|investors?|officials?|expects?|expected|"
    r"believe[sd]?|forecast(?:s|ed)?|predicts?|predicted|according to|says?|said)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _find_neutrality_violations(parsed: dict) -> list[str]:
    """Three sub-checks, tested against real generated drafts before being
    trusted (2026-09-12, operator direction -- see BACKLOG.md's "Political
    neutrality guard" entry for the full test run and its results):

    1/2. Loaded characterization ("authoritarian", "corrupt regime") and
    unattributed motive-assertion ("is trying to distract from") -- these
    have NO attribution exemption, deliberately: a subjective motive claim
    or a loaded label is the writer's own editorializing regardless of who
    they attribute the underlying fact to ("officials say the government is
    trying to distract from the scandal" is still OUR characterization of
    intent, just laundered through a source).

    3. Prescriptive advocacy ("the Fed should raise rates") DOES have an
    attribution exemption, and needs one to avoid the exact false-positive
    the operator named before this was trusted: "the Fed should raise
    rates" is the writer's own advocacy; "analysts expect the Fed will need
    to raise rates" is the writer reporting a THIRD PARTY's forecast. Same
    modal language, different actor making the claim -- attribution
    genuinely changes what's being asserted here in a way it doesn't for
    #1/#2. Checked per-sentence: an actor+modal pattern is only flagged if
    that SAME sentence has no attribution cue nearby (analysts/economists/
    say/expect/according to/etc.).

    KNOWN LIMIT, not fixed: the attribution check is cue-PRESENCE, not real
    parsing -- "the Fed should raise rates, according to no one in
    particular" would NOT be flagged, because the cue is there even though
    it isn't doing real attribution work. Narrow enough that no draft has
    hit it live yet, but worth naming rather than pretending the exemption
    is bulletproof."""
    text = _prose_text(parsed)
    issues = [p.pattern for p in _LOADED_CHARACTERIZATION_PATTERNS if p.search(text)]
    issues += [p.pattern for p in _UNATTRIBUTED_MOTIVE_PATTERNS if p.search(text)]

    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if _PRESCRIPTIVE_ACTOR_MODAL_RE.search(sentence) and not _ATTRIBUTION_CUE_RE.search(sentence):
            issues.append(f"prescriptive advocacy, unattributed: {sentence.strip()!r}")
    return issues


# Speculation guard (hacks_exploits, 2026-09-30) -- same checked-write
# mechanism as the gems_security overclaim guard and the macro_news
# neutrality guard, per operator direction: "same certainty discipline as
# gems -- report what's confirmed, don't speculate on cause or blame."
# DefiLlama's /hacks data has no attacker-identity field and no motive field
# at all -- anything the model says about WHO did it or WHY is invented, not
# reported. Classification/technique ARE safe to state as fact (DefiLlama's
# own categorization of what happened, not our speculation about why), so
# this only bans naming/blaming an unconfirmed party and hedge-words that
# smuggle in an unconfirmed claim as if it were established.
_SPECULATION_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"\ballegedly\b", r"\ballegations?\b", r"\bapparently\b", r"\brumor(?:ed)?\b",
    r"\bsuspected? (?:to be|of|is|was)\b", r"\bit'?s (?:believed|thought|speculated)\b",
    r"\b(?:probably|likely|possibly|may have|might have|could have) (?:been )?"
    r"(?:an? )?(?:inside job|rug pull|intentional|malicious insiders?)\b",
    r"\bthe (?:attacker|hacker|thief)('s)? (?:motive|goal|intention|identity)\b",
    r"\bteam (?:is|was|are|were) (?:likely|probably|suspected)\b",
]]


def _find_speculation_issues(parsed: dict) -> list[str]:
    """Checked against title/narrative/why_it_matters, same fields every
    other structural guard in this module checks."""
    text = _prose_text(parsed)
    return [p.pattern for p in _SPECULATION_PATTERNS if p.search(text)]


# Reader-advice guard (2026-11-14, channel-voice path). Found by the first macro_news-on-Hustle
# generation: Hustle's second paragraph is "what to do about it", and macro_news's guardrail says the
# answer is what it MEANS for the reader or what to WATCH -- never an instruction. The model wrote
# "plan for higher costs on any credit you rely on". The prompt alone did not hold the line, so this
# checks the output, same as every other guardrail. Deliberately narrow: an advisory modal aimed at
# the reader ("you should/must/need to"), or a sentence that OPENS with an advisory imperative.
# "Watch ...", "Track ...", "Note ..." are NOT advice and are allowed. A floor, not a guarantee --
# it cannot see advice phrased as a statement ("now is the time to lock in rates").
_ADVICE_MODAL_RE = re.compile(
    r"\byou(?:'d| would| might| may| could)? (?:should|must|need to|ought to|want to|better)\b"
    r"|\b(?:you(?:'d| would)? )?(?:might|may) want to\b|\bit(?:'s| is) (?:wise|smart|prudent) to\b",
    re.IGNORECASE)
_ADVICE_IMPERATIVE_RE = re.compile(
    r"^\W*(?:(?:plan|prepare|brace|consider|budget|hedge|lock|invest|avoid|stock|"
    r"revisit|renegotiate|diversify|borrow|refinance|accelerate|adjust|factor|price in|set aside|"
    r"make sure|be sure|don't|do not)\b"
    # These verbs are also NOUNS that open a news sentence: "Increase in oil prices lifted costs", "Review of the
    # tariff schedule begins Monday", "Spend on data centers rose 12%", "Build-out of the grid", "Sell-off in
    # bonds" (2026-11-15 audit) -- so they only count as an instruction when not used that way.
    r"|(?:buy|sell|hold|cut|raise|reduce|increase|review|save|spend|delay|build)\b(?!-)"
    r"(?!\s+(?:in|of|on|from|by|at|to|for|across|among|is|was|are|were|has|have|had|will|would|could|may|might|can)\b))",
    re.IGNORECASE)
READER_ADVICE_CHECKED_CATEGORIES = {"macro_news"}


def _find_reader_advice(parsed: dict) -> list[str]:
    issues = []
    # Each field (title, then each paragraph) is checked on its own. Joined into one string, the first sentence
    # of paragraph 1 follows the title with no sentence break, so "Plan for higher costs..." was never at the
    # START of a sentence and slipped through (2026-11-15 audit -- the guard's imperative test was dead for it).
    pieces = [str(parsed.get(k, "")) for k in ("title", "narrative", "why_it_matters")]
    pieces += [str(x) for x in (parsed.get("paragraphs") or [])]
    sentences = [sent for piece in pieces for sent in _SENTENCE_SPLIT_RE.split(piece)]
    for sentence in sentences:
        s = sentence.strip()
        # An advisory imperative may follow a colon or dash ("Watch rates: plan for ...") -- check each clause.
        clauses = re.split(r"[:;—]\s+|\s--\s|,\s+(?:so|and|then|but)\s+", s)
        if _ADVICE_MODAL_RE.search(s) or any(_ADVICE_IMPERATIVE_RE.match(c) for c in clauses):
            issues.append(f"it tells the reader what to do (\"{s[:70]}\") -- say what it means for them or what to "
                          f"watch instead; never an instruction")
    return issues


# Exchange-listings guard (2026-11-15, operator-approved: "no price -- a stale number reads as a call, and we have no
# guardrail that makes that safe"; and "the prior-listing fact must be prominent, not a footnote"). Both are checked
# on the output, not just requested: a listing post is rejected if it talks price or performance, claims a debut
# the four-exchange record cannot support, or buries (or omits) the prior-listing fact.
_LISTING_PRICE_RE = re.compile(
    r"\b(?:price|prices|pump\w*|rall(?:y|ies|ied)|surg\w*|jump\w*|soar\w*|moon\w*|bullish|bearish|upside|"
    r"spik\w*|gains?|expected to (?:rise|climb|gain))\b", re.IGNORECASE)
_LISTING_ABSOLUTE_RE = re.compile(
    r"\b(?:first[- ]ever|debut\w*|brand[- ]new|newly (?:created|launched|minted|listed)|new (?:token|coin|asset)|"
    r"only (?:exchange|venue|tracked)|sole)\b", re.IGNORECASE)
_LISTING_NEGATION_RE = re.compile(r"\b(?:no|not|none|neither|never|without)\b", re.IGNORECASE)
_LISTING_NO_PRIOR_RE = re.compile(r"\b(?:not previously listed|no (?:earlier|prior|previous) listing|no earlier)\b", re.IGNORECASE)
_LISTING_ALREADY_RE = re.compile(
    r"(?<!not )(?<!never )(?<!n't )\b(?:already (?:listed|trad\w+)|previously listed|earlier listed)\b", re.IGNORECASE)
LISTING_CHECKED_CATEGORIES = {"exchange_listings"}


def _find_listing_issues(parsed: dict, payload: dict) -> list[str]:
    title = str(parsed.get("title", ""))
    paragraphs = [str(x) for x in (parsed.get("paragraphs") or [])]
    text = " ".join([title] + paragraphs)
    issues = []
    m = _LISTING_PRICE_RE.search(text)
    if m:
        issues.append(f"it talks about price or performance (\"{m.group(0)}\") -- facts only: exchange, token, timing and the "
                      f"prior-listing record, nothing about price or what the listing might do")
    m = _LISTING_ABSOLUTE_RE.search(text)
    if m:
        issues.append(f"\"{m.group(0)}\" claims more than the four-exchange record shows -- say 'not previously listed on the "
                      f"other tracked exchanges' instead")
    # "First" is only a risk when the record shows NO earlier listing (then it reads as "first anywhere"). When it
    # does show one, "the first tracked announcement was Binance's" is simply the record's own order of events.
    for sentence in ([] if payload.get("prior_listings") else _SENTENCE_SPLIT_RE.split(text)):
        if re.search(r"\bfirst\b", sentence, re.IGNORECASE) and not (
                re.search(r"\btracked\b", sentence, re.IGNORECASE) and re.search(r"\bsince\b", sentence, re.IGNORECASE)):
            issues.append("a sentence says 'first' without 'tracked' and 'since <month year>' -- the record covers four "
                          "exchanges and a short period only, so drop 'first' or say 'first among the tracked exchanges since "
                          "<month year>'")
            break
    if not payload.get("trading_start") and re.search(r"\btrading (?:opens?|will open|starts?|begins?)\b|\bopens? (?:for )?trading\b", text, re.IGNORECASE):
        issues.append("the announcement did not state when trading opens -- say nothing about trading time")
    first_para = paragraphs[0] if paragraphs else ""
    head = " ".join([title] + _SENTENCE_SPLIT_RE.split(first_para)[:2])
    prior = payload.get("prior_listings") or []
    if prior and _LISTING_NO_PRIOR_RE.search(text):
        issues.append("the facts say the token WAS already listed on another tracked exchange, but the post says no earlier "
                      "listing exists -- say which exchange(s) listed it earlier, and drop 'no earlier / not previously listed'")
    if not prior and _LISTING_ALREADY_RE.search(text):
        issues.append("the facts say NO earlier listing appears among the other tracked exchanges, but the post says the token "
                      "was already listed -- say that no earlier listing appears")
    if prior:
        missing = [x["exchange"] for x in prior if x["exchange"].lower() not in text.lower()]
        if missing:
            issues.append(f"the token was already listed on {', '.join(missing)} (tracked record) -- name every earlier exchange")
        elif not any(x["exchange"].lower() in head.lower() for x in prior):
            issues.append("state the earlier listing in the title or the first two sentences -- it is the point of the post, "
                          "not a footnote")
    else:
        if not (re.search(r"\btracked\b", head, re.IGNORECASE) and _LISTING_NEGATION_RE.search(head)):
            issues.append("state in the title or the first two sentences that no earlier listing appears among the tracked "
                          "exchanges (use the word 'tracked' and a plain negative) -- it is the point of the post")
        window = listing_record_window(payload)
        if window and not (re.search(r"\bsince\b", text, re.IGNORECASE) and window.lower() in text.lower()):
            issues.append(f"the record is short: say 'since {window}' beside the claim that no earlier listing appears, so it "
                          f"can never be read as 'never listed anywhere'")
    return issues


# earning_opportunities output guard (Phase 5, 2026-11-15). The CoinCraft post has to be genuinely ACTIONABLE, which is the
# one place a model's habit of inventing a plausible step is dangerous: a wrong step wastes a reader's time or sends them
# somewhere they should not go. Checked on the output, not just requested: no invented steps, no amounts the facts do not
# contain, the deadline stated when there is one, and no referral language (the operator adds referral links by hand).
_EARN_REFERRAL_RE = re.compile(r"\b(?:referral|refer(?:ral)? (?:code|link)|refer a friend|invite (?:code|link|friends?)|use (?:my|our) (?:link|code)|promo code|affiliate)\b", re.IGNORECASE)
_EARN_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_EARN_STEP_LINE_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$", re.MULTILINE)
EARNING_CHECKED_CATEGORIES = {"earning_opportunities"}


def _earn_words(text: str) -> set[str]:
    stop = {"the", "and", "you", "your", "for", "with", "that", "this", "then", "into", "from", "will", "are", "can", "any", "all"}
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if w not in stop}


def _find_earning_issues(parsed: dict, payload: dict) -> list[str]:
    from pipeline import earning
    title = str(parsed.get("title", ""))
    paragraphs = [str(x) for x in (parsed.get("paragraphs") or [])]
    text = "\n".join([title] + paragraphs)
    facts = facts_earning_opportunities(payload)
    issues = []
    if _EARN_REFERRAL_RE.search(text):
        issues.append("it mentions a referral / invite / promo code -- the operator adds referral links by hand; remove it")
    if _EARN_URL_RE.search(text):
        issues.append("it contains a URL -- the link is added when the post is forwarded; say 'the link' instead")
    headline = payload.get("headline_max_usd")
    if headline and any(abs(a - headline) < 0.5 for a in earning.extract_amounts_usd(title)):
        issues.append(f"the title leads with the listing's headline maximum (${headline:,.0f}); that is a ceiling, not the reward -- lead with the reward figure in the facts")
    allowed = set(earning.extract_amounts_usd(facts))
    for amount in earning.extract_amounts_usd(text):
        if not any(abs(amount - a) < 0.5 for a in allowed):
            issues.append(f"the amount ${amount:,.0f} is not in the facts -- use only amounts the facts give")
            break
    if re.search(r"\bthe facts\b|\bour (?:data|records|system)\b|\bthe pipeline\b|\bautomatically collected\b", text, re.IGNORECASE):
        issues.append("it talks about 'the facts' / the system's data -- the reader must never see how the post was built; say 'the page' or 'the link'")
    if any(len(re.findall(r"(?:^|\s)\d+[.)]\s", line)) > 1 for line in text.split("\n")):
        issues.append("put each numbered step on its own line (1. on one line, 2. on the next)")
    steps = (payload.get("enriched") or {}).get("steps") or []
    step_lines = _EARN_STEP_LINE_RE.findall(text)
    if step_lines and not steps:
        issues.append("the facts have NO steps available, but the post lists numbered steps -- write none and say the steps are on the offer's page")
    elif step_lines:
        fact_words = _earn_words(" ".join(steps))
        for line in step_lines:
            if len(_earn_words(line) & fact_words) < 2:
                issues.append(f"step \"{line[:60]}\" is not one of the steps in the facts -- use only the facts' steps")
                break
        if len(step_lines) > len(steps) + 1:
            issues.append("it lists more steps than the facts contain -- never add steps")
    deadline = payload.get("deadline")
    if deadline:
        try:
            d = datetime.fromisoformat(deadline)
            label = f"{d.strftime('%b')} {d.day}"
            if label.lower() not in text.lower() and d.strftime("%B").lower() + f" {d.day}" not in text.lower():
                issues.append(f"the deadline ({label}) is in the facts but not in the post -- state it")
        except ValueError:
            pass
    return issues


def _generate_checked_write(conn, category: str, prompt: str, *, model_override: str | None = None,
                             source_excerpt: str | None = None, check_neutrality: bool = False,
                             check_no_speculation: bool = False) -> str:
    """llm.generate_write, but refuses to let an overclaiming, (when
    source_excerpt is given) reproduced, or (when check_neutrality is set)
    politically-loaded draft through. One retry with a stricter reminder
    appended to the SAME prompt, then a hard failure rather than silently
    shipping it -- same "never silently ship a malformed post" philosophy as
    post_format.parse_llm_json. A RuntimeError here surfaces as a failed
    write, not a bad post the operator has to catch by reading carefully."""
    def _check(raw: str) -> list[str]:
        parsed = post_format.parse_llm_json(raw)
        hits = _find_overclaims(parsed) + _find_reproduction_issues(parsed, source_excerpt)
        if check_neutrality:
            hits += _find_neutrality_violations(parsed)
        if check_no_speculation:
            hits += _find_speculation_issues(parsed)
        return hits

    raw = llm.generate_write(conn, category, prompt, model_override=model_override)
    hits = _check(raw)
    if hits:
        logger.warning("write_post: guard violation for category=%s, retrying once: %s", category, hits)
        stricter_prompt = prompt + (
            "\n\nSTRICT REMINDER: your previous draft either (a) used language claiming or "
            "implying certainty this data doesn't support (words like 'safe', 'legit', "
            "'guaranteed', 'risk-free', 'no risk'), (b) reproduced or too-closely paraphrased "
            "the source material instead of summarizing it in your own words, (c) used "
            "loaded characterizations of a political actor, asserted a government's motive "
            "without attribution, or told a government/policymaker what it should do, or (d) "
            "speculated about who was responsible for an incident or why, or hedged an "
            "unconfirmed claim as if it were established ('allegedly', 'apparently', "
            "'probably an inside job'). Fix all that apply: state only what the data supports, "
            "write your own summary, report what happened and its plausible implications "
            "without taking a side or characterizing anyone's motives, never recommend a "
            "course of action to a policymaker or reader, and never name or imply who caused "
            "an incident or why -- describe only what's confirmed."
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

    return f"""You are writing prose for a crypto DeFi security/education channel. Voice: {cfg.get('voice', 'educational')}.
{cfg.get('prompt_notes', '')}

{facts_gems_security(p)}

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


@register_prompt_builder("hacks_exploits")
def build_hacks_exploits_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    """Certainty discipline is structural here, not just a tone request
    (operator direction, 2026-09-30, same as gems_security): the prompt hands
    the LLM only what DefiLlama itself confirms -- amount, classification,
    chain, technique, whether funds were recovered -- and there is
    structurally no attacker-identity or motive field anywhere in this
    payload for the model to draw on, so any such claim is invented, not
    reported. check_no_speculation (pipeline/write_post.py's
    _generate_checked_write) is the code-level enforcement; this prompt is
    the first layer, not the only one."""
    p = raw_item["payload"]

    return f"""You are writing prose for a crypto security/education channel. Voice: {cfg.get('voice', 'educational')}.
{cfg.get('prompt_notes', '')}

{facts_hacks_exploits(p)}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these
three string fields:
{{
  "title": "one specific title naming the protocol and the dollar amount --
    e.g. 'Protocol X loses $2.3M in oracle manipulation exploit'. No emoji.",
  "narrative": "2-3 sentences: what happened, on this specific incident, using
    ONLY the facts above. State the classification/technique as DefiLlama's
    own finding, not your own theory. Never name or imply who was
    responsible or why -- there is no such confirmed information here.",
  "why_it_matters": "EXACTLY one sentence on why this specific incident is
    worth knowing about -- the scale, or what it exposes about this
    technique/protocol category in general. Not a generic 'always DYOR'
    platitude."
}}

Hard rules, no exceptions: never speculate on who caused this or why; never
use hedge-words that smuggle in an unconfirmed claim ("allegedly",
"apparently", "probably an inside job", "suspected to be"); never say a
recovery is "likely" or "unlikely" beyond the recovery status given above;
never tell the reader what to do with their own money. No emoji anywhere in
your output. Keep the combined narrative + why_it_matters under ~70 words.
"""


@register_post_computer("hacks_exploits")
def compute_hacks_exploits_elements(conn, raw_item: dict, history: list) -> dict:
    """No history_line -- a confirmed past incident isn't a recurring signal
    the way a wallet's past moves are (same reasoning gems_security/
    web3_jobs already use). risk_line states the confirmed facts plainly, same
    "unknown is never coded as clear" posture as gems_security's own
    risk_line: an untracked protocol says so, it doesn't go silent."""
    p = raw_item["payload"]
    tvl = p.get("protocol_tvl_usd")
    parts = [f"DefiLlama: ${p.get('amount_usd', 0):,.0f} lost, classified as {p.get('classification') or 'unclassified'}"]
    if p.get("chain"):
        parts.append(f"chain: {p['chain']}")
    parts.append(f"protocol TVL: ${tvl:,.0f}" if tvl is not None else "protocol TVL not tracked by DefiLlama")
    if p.get("returned_funds"):
        parts.append(f"${p['returned_funds']:,.0f} recovered")
    risk_line = "; ".join(parts) + "."

    return {"history_line": None, "risk_line": risk_line, "source_name": "DefiLlama"}


@register_prompt_builder("exchange_listings")
def build_exchange_listings_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    """Legacy-shape prompt. Exchange listings only ever go to a channel with a voice profile (Crypto Wall Street),
    so the channel-voice path in generate_post_with_report writes them and this is a fallback for a channel without
    one -- kept so a category never raises NotImplementedError. The rules it carries are the FactSpec's."""
    p = raw_item["payload"]
    spec = FACT_SPECS["exchange_listings"]
    return f"""You are writing prose for a crypto market channel. Voice: {cfg.get('voice', 'market desk')}.
{cfg.get('prompt_notes', '')}

{facts_exchange_listings(p)}

WHAT THE POST MUST COVER: {spec.content_brief}

{spec.guardrails}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these three string fields:
{{
  "title": "one specific title naming the exchange and the token. No emoji.",
  "narrative": "2 sentences: the listing, then whether the token was already listed on another tracked exchange.",
  "why_it_matters": "EXACTLY one sentence on what the prior-listing record tells a reader. Not a prediction."
}}
"""


@register_post_computer("exchange_listings")
def compute_exchange_listings_elements(conn, raw_item: dict, history: list) -> dict:
    """No history_line / context_line / risk_line: the prior-listing fact lives IN the post body (it is the point of
    the post), not in a blockquote a terse channel would drop."""
    p = raw_item["payload"]
    return {"history_line": None, "context_line": None, "risk_line": None,
            "source_name": f"{p.get('exchange')} announcement"}


@register_prompt_builder("earning_opportunities")
def build_earning_opportunities_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    """Legacy-shape fallback. CoinCraft writes this category through its channel voice profile; this exists so the
    category never raises NotImplementedError. The rules it carries are the FactSpec's."""
    p = raw_item["payload"]
    spec = FACT_SPECS["earning_opportunities"]
    return f"""You are writing a short, practical post for a global audience. Voice: {cfg.get('voice', 'how_to')}.
{cfg.get('prompt_notes', '')}

{facts_earning_opportunities(p)}

WHAT THE POST MUST COVER: {spec.content_brief}

{spec.guardrails}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these three string fields:
{{
  "title": "what the reader gets and the action, concrete. No emoji.",
  "narrative": "2-3 sentences: what it is, who can take part, the deadline.",
  "why_it_matters": "EXACTLY one sentence on the catch: what it costs or requires."
}}
"""


@register_post_computer("earning_opportunities")
def compute_earning_opportunities_elements(conn, raw_item: dict, history: list) -> dict:
    """No history / context / risk line: the catch is a paragraph of the post, written from the facts."""
    return {"history_line": None, "context_line": None, "risk_line": None, "source_name": raw_item["payload"].get("source_name")}


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

{facts_wire_story(p)}

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


# Cross-category name extraction, one entry per category this connection
# check can run against -- each returns a single normalized candidate NAME
# string to word-boundary-match against the OTHER category's text, or None
# if this row doesn't offer one. tool_launches/startup_jobs added 2026-09-12
# for macro_news (operator-approved plan: "same notified-only, named-entity
# bar as the crypto version") -- neither has a clean single "project"-style
# slug field the way defi_yields/gems_security do, so their name comes from
# running the SAME shared entity extractor (pipeline/entities.py) already
# used everywhere else in this codebase, just handed the row's own title
# (and company, for startup_jobs) as the "description" argument specifically
# -- extract_entities() only pulls proper-noun PHRASES from description, not
# title, because news headlines are stylistically Title-Cased throughout
# (see that module's docstring); tool_launches/startup_jobs titles are
# natural HN/GitHub/job-board text, not headline-styled, so this reuses the
# existing phrase extractor for its intended purpose rather than working
# around it.
def _primary_phrase_name(text: str | None) -> str | None:
    if not text:
        return None
    ent = entity_lib.extract_entities("", text)
    phrases = sorted(ent["phrases"], key=len, reverse=True)
    return phrases[0].lower() if phrases else None


CROSS_CATEGORY_NAME_EXTRACTORS = {
    "whale_movements": lambda p: (p.get("exchange") or "").strip().lower() or None,
    "defi_yields": lambda p: _normalize_project(p.get("project")),
    "gems_security": lambda p: _normalize_project(p.get("project")),
    "tool_launches": lambda p: _primary_phrase_name(p.get("title")),
    "startup_jobs": lambda p: _primary_phrase_name(f"{p.get('company') or ''} {p.get('title') or ''}"),
}


def _cross_category_fact(category: str, p: dict, when: str) -> str:
    if category == "whale_movements":
        verb = "withdrew from" if p.get("direction") == "outflow" else "deposited to"
        return (
            f"a ${p.get('value_usd', 0):,.0f} {p.get('symbol')} transfer {verb} "
            f"{p.get('exchange')}, {when}, in our own whale tracking"
        )
    elif category == "defi_yields":
        return (
            f"{p.get('project')} {p.get('symbol')} is currently yielding "
            f"{p.get('apy')}% APY (TVL ${p.get('tvl_usd', 0):,.0f}) in our own DeFi tracking"
        )
    elif category == "gems_security":
        return (
            f"a GoPlus security finding on {p.get('symbol')} ({p.get('project')}) "
            f"we flagged {when}: {'; '.join(p.get('red_flags') or [])}"
        )
    elif category == "tool_launches":
        source_label = "Hacker News" if p.get("source") == "hackernews" else "GitHub Trending"
        return f"\"{p.get('title')}\", which we surfaced {when} in our own {source_label} tracking"
    else:  # startup_jobs
        return f"a {p.get('position')} role at {p.get('company')} we surfaced {when} in our own job listings"


def _find_cross_category_connection(conn, text: str, categories: tuple[str, ...],
                                     lookback_hours: int = None, log_label: str = "news") -> str | None:
    """The piece operator direction 2026-09-11/12 asked for real attention
    on: does this item connect to OUR OWN data from other categories in a
    way that's genuinely informative, not just coincidental? Originally
    built for news (whale_movements/defi_yields/gems_security) and
    generalized 2026-09-12 for macro_news (tool_launches/startup_jobs) --
    same mechanism, parameterized `categories` instead of a hardcoded tuple,
    since the two channels' "our own data" sets are entirely different.

    This went through two real, live-caught failures before landing here --
    worth understanding both, since the fix is a direct response to each:

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
    NAME the text names (protocol/exchange for news, product/company name
    for macro_news), not a generic shared ticker/keyword -- "Spark"
    appearing in both the headline and our own tracked Spark pools is a
    real, specific connection. And per operator direction, require the
    matched row to have been ACTUALLY NOTIFIED (not merely scored above
    threshold) -- a materially higher, less gameable bar than a raw score
    comparison, since it means the connection is always to something
    already judged genuinely worth attention, not a score technicality.

    This is deliberately narrower than the multi-signal-corroboration idea
    also discussed (e.g. a token showing both a notable exchange outflow
    AND a notable yield move together telling one coherent story) -- that
    would mean correlating two independently-notified facts with each
    other, a materially bigger feature than name-matching one item against
    one notified row. Not built this pass; flagged as a real follow-on, not
    silently dropped. "Rare and real beats frequent and coincidental"
    (operator direction) -- this fires far less often than the ticker-
    matching version did, which is the intended outcome, not a regression
    to fix.
    """
    lookback_hours = lookback_hours or CROSS_CATEGORY_LOOKBACK_HOURS
    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT r.category, r.payload, n.sent_at
               FROM raw_items r
               JOIN notifications n ON r.id = ANY(n.candidate_raw_item_ids)
               WHERE r.category = ANY(%s)
                 AND n.sent_at > now() - (%s || ' hours')::interval
               ORDER BY n.sent_at DESC LIMIT 200""",
            (list(categories), lookback_hours),
        )
        rows = cur.fetchall()

    # Visible, not silent (operator direction 2026-09-12): almost nothing
    # will be notified yet with everything held for Hetzner, so this will
    # correctly return None constantly for a while -- log which case it is,
    # so "correctly omitted, nothing notified yet" stays distinguishable
    # from "quietly broken" once there's real notified history to check
    # against.
    if not rows:
        logger.info("%s: cross-category lookup -- no notified %s rows in the last %dh",
                     log_label, "/".join(categories), lookback_hours)
        return None

    text_lower = (text or "").lower()
    for row in rows:
        p = row["payload"]
        extractor = CROSS_CATEGORY_NAME_EXTRACTORS.get(row["category"])
        name = extractor(p) if extractor else None
        if not name or len(name) < _PROJECT_NAME_MIN_LENGTH:
            continue
        if not re.search(rf"\b{re.escape(name)}\b", text_lower):
            continue

        age_hours = (datetime.now(timezone.utc) - row["sent_at"]).total_seconds() / 3600
        when = "earlier today" if age_hours < 20 else f"{age_hours / 24:.0f}d ago"
        return _cross_category_fact(row["category"], p, when)

    logger.info("%s: cross-category lookup -- %d notified row(s) in the last %dh, "
                 "none named in this item's text", log_label, len(rows), lookback_hours)
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
    context_line = _find_cross_category_connection(
        conn, news_text, categories=("whale_movements", "defi_yields", "gems_security"),
    )

    return {
        "history_line": history_line,
        "risk_line": None,
        "source_name": p.get("source") or None,
        "context_line": context_line,
    }


@register_prompt_builder("tool_launches")
def build_tool_launches_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    p = raw_item["payload"]
    return f"""You are writing prose for a builder/startup-tools Telegram channel (audience:
founders, indie hackers, people evaluating new tools -- not a crypto audience).
Voice: {cfg.get('voice', 'opportunity_framed')}.
{cfg.get('prompt_notes', '')}

{facts_tool_launches(p)}

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


@register_prompt_builder("macro_news")
def build_macro_news_prompt(cfg: dict, region_profile: str, raw_item: dict, history: list) -> str:
    """Hustle to Million's macro category -- reuses news.py's collection
    infrastructure unchanged (operator direction), but the editorial brief
    and voice are different: this is NOT "business news" or a general news
    feed, it's macro developments that plausibly affect a reader's economic
    situation, written with a hard political-neutrality requirement.

    Neutrality is enforced BOTH here (prompt instructions) AND structurally
    in _generate_checked_write's check_neutrality path (_find_neutrality_
    violations) -- the same "prompts get ignored, a code-level guard
    doesn't" reasoning as every other guard in this file. See that
    function's docstring for what the structural check does and does NOT
    catch (it's a floor, not a guarantee -- selection and framing bias is
    not something a regex can see; the operator's own approval step is what
    actually covers that)."""
    p = raw_item["payload"]

    return f"""You are writing prose for a macro-economics Telegram channel (audience:
founders, indie hackers, people who want to understand how world events affect
their own economic situation -- not a crypto audience, not a general news
audience). Voice: {cfg.get('voice', 'market_summary')}.
{cfg.get('prompt_notes', '')}

{facts_wire_story(p)}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly these
three string fields:
{{
  "title": "one specific title in your own words — not a copy of the
    headline above. No emoji.",
  "narrative": "1-2 sentences: what happened, in your own words, summarized
    from the excerpt — never a close paraphrase of its structure or wording.",
  "why_it_matters": "EXACTLY one sentence on the PLAUSIBLE ECONOMIC
    IMPLICATION for a reader — prices, rates, jobs, costs, markets — never a
    restatement of the narrative in different words."
}}

HARD RULES, no exceptions:
- Report what happened and its plausible economic implications. Never
  advocate a position, never tell a government/policymaker/reader what
  should happen, never characterize any government's or actor's MOTIVES
  ("in a desperate attempt to...", "trying to distract from...") — describe
  actions, don't assign intent.
- Never use loaded characterizations of a political actor ("authoritarian",
  "corrupt regime", "radical", "extremist", "regime") — use neutral,
  specific, factual language regardless of which side or country is
  involved.
- If two sides dispute what happened or why, say that plainly rather than
  adopting either side's framing as settled fact.
- Never state a price prediction or tell the reader what to do with their
  own money. No crypto framing, no financial-advice language.
- No emoji anywhere in your output. Keep the combined narrative +
  why_it_matters under ~70 words — the whole post (header/source/hashtags
  added separately, not by you) targets roughly 400-700 characters.
"""


@register_post_computer("macro_news")
def compute_macro_news_elements(conn, raw_item: dict, history: list) -> dict:
    """Same two deterministic value-adds as compute_news_elements, reused
    unchanged (operator direction): history_line for a genuine developing-
    story continuation (same entity-fingerprint mechanism/threshold,
    pipeline/entities.py), context_line for a cross-category connection --
    but pointed at tool_launches/startup_jobs (Hustle to Million's own other
    categories) instead of news's whale_movements/defi_yields/gems_security,
    via _find_cross_category_connection's generalized `categories` param."""
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

    macro_text = f"{p.get('title') or ''} {p.get('description') or ''}"
    context_line = _find_cross_category_connection(
        conn, macro_text, categories=("tool_launches", "startup_jobs"), log_label="macro_news",
    )

    source_name = MACRO_NEWS_SOURCE_DISPLAY_NAMES.get(p.get("source"), p.get("source"))
    return {
        "history_line": history_line,
        "risk_line": None,
        "source_name": source_name,
        "context_line": context_line,
    }


MACRO_NEWS_SOURCE_DISPLAY_NAMES = {
    "bbc_world": "BBC", "bbc_business": "BBC", "npr_economy": "NPR",
    "cnbc_economy": "CNBC", "federal_reserve": "Federal Reserve",
    "axios": "Axios", "ars_technica": "Ars Technica", "the_verge": "The Verge",
    "wired": "Wired",
}


def _assemble(conn, category: str, raw_item: dict, history: list, llm_raw_output: str) -> str:
    cfg = load_category_config(conn, category)
    parsed = post_format.parse_llm_json(llm_raw_output)
    computer = POST_COMPUTERS.get(category)
    elements = computer(conn, raw_item, history) if computer else {}

    return post_format.assemble_post(
        emoji=cfg.get("emoji") or "",
        title=parsed["title"],
        narrative=parsed["narrative"],
        # .get(...) or None, not parsed["why_it_matters"] -- as of 2026-09-15
        # this field is genuinely optional for some categories (see
        # post_format.assemble_post's docstring); an LLM that follows those
        # prompts' instructions returns "" when there's nothing to add, and
        # that should render as nothing, not an empty paragraph.
        why_it_matters=parsed.get("why_it_matters") or None,
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


# Categories whose written output gets checked against
# _find_neutrality_violations -- see that function's docstring and
# _generate_checked_write's check_neutrality param.
NEUTRALITY_CHECKED_CATEGORIES = {"macro_news"}

# Categories whose written output gets checked against
# _find_speculation_issues -- see that function's docstring and
# _generate_checked_write's check_no_speculation param.
SPECULATION_CHECKED_CATEGORIES = {"hacks_exploits"}


# earning_opportunities from the platform sources (Superteam, Zindi) carries a real steps list, a prize ladder and eligibility, so a draft lands a few
# characters over 650 more often than an airdrop post does; 4 of 6 such drafts failed at 5 retries on 2026-11-16 (+15 to +120 characters). More draws, not a longer limit.
VOICE_MAX_RETRIES_BY_CATEGORY = {"earning_opportunities": 9}
VOICE_MAX_RETRIES = 5  # after the first draft; each retry shows the rejected draft + the specific issues. 2 left ~1 in 12 short writes failing by a few characters; 3 left 3 of 15 Crypto Notebook gems writes (the longest, 600-900) 1-50 characters over, 2026-11-15


def _get_display_name(conn, channel: str) -> str:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT display_name FROM channel_config WHERE channel = %s", (channel,))
        row = cur.fetchone()
    return (row["display_name"] if row and row["display_name"] else channel)


def _generate_voiced_write(conn, category: str, prompt: str, check, *, model_override: str | None = None,
                            max_retries: int = VOICE_MAX_RETRIES) -> tuple[dict, int, list[list[str]]]:
    """The channel-voice counterpart of _generate_checked_write: same "never
    silently ship a draft that fails a check" rule, but the checks include the
    voice profile's length/structure/title/person rules, and a retry tells the
    model exactly what was wrong ("the post is 412 characters but must be
    200-350") rather than a generic reminder -- a length miss is fixed far more
    reliably with the number than without. `check(parsed) -> list[str]` runs
    the category guards plus the voice checks. Returns (parsed, attempts,
    issues_per_failed_attempt); raises RuntimeError after max_retries."""
    history: list[list[str]] = []
    current = prompt
    for attempt in range(1, max_retries + 2):
        raw = llm.generate_write(conn, category, current)
        try:
            parsed = post_format.parse_llm_json(raw)
            issues = check(parsed)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:  # malformed JSON / shape
            parsed, issues = None, [f"the reply was not the required JSON object ({type(exc).__name__}); "
                                    f"return ONLY the JSON object"]
        if not issues:
            return parsed, attempt, history
        history.append(issues)
        logger.warning("write_post: voice/guard issues for category=%s attempt=%d: %s", category, attempt, issues)
        # The rejected draft itself is shown (2026-11-15, found by the Crypto Notebook batch: 18 of 21
        # writes failed). Before, the retry carried only the issue list -- "cut about 725 characters" about
        # a draft the model could not see -- so each retry was a fresh draw with the same tendency to
        # overshoot (1475, 1454, 1536, 1382 chars against 600-900). Editing a visible draft converges.
        current = prompt + (
            "\n\nYOUR PREVIOUS DRAFT WAS REJECTED:\n" + str(raw).strip()
            + "\n\nRewrite it, fixing every one of these, and keep the guardrails:\n"
            + "\n".join(f"- {i}" for i in issues)
        )
    raise RuntimeError(
        f"write_post: category={category} still failed the voice/guard checks after {max_retries} retries "
        f"({history[-1]}) -- refusing to publish rather than ship it"
    )


def _record_earning_draft(conn, raw_item: dict, *, attempts: int | None, chars: int | None, ok: bool) -> None:
    """One run_logs row per PRODUCTION earning draft (job `earning_draft`): how many attempts it took, and whether it gave up. Read by
    `scripts/earning_report.py --drafts`. Purpose (operator 2026-11-16): if most cards need 4+ attempts the 400-650 band is too tight for offers
    with real steps and prize ladders -- widen the band rather than keep paying for retries. Never raises into the write path."""
    try:
        from pipeline.run_log import run_log
        p = raw_item.get("payload") or {}
        with run_log(conn, "earning_draft") as state:
            state["details"].update({"raw_item_id": raw_item.get("raw_item_id"), "source": p.get("source"), "attempts": attempts, "chars": chars, "ok": ok})
    except Exception:
        logger.exception("could not record the earning draft statistics")
        try:
            conn.rollback()
        except Exception:
            pass


def generate_post_with_report(conn, category: str, channel: str, raw_item: dict, *, profile=None) -> dict:
    """Channel-voice write. Returns {"text", "attempts", "retry_issues", "chars",
    "prompt", "channel"} -- generate_post() wraps this and returns just the text;
    scripts/voice_compare.py shows the rest. `profile` overrides the DB lookup
    (the compare script passes profiles straight from the YAML so a voice can be
    reviewed before it is seeded into production)."""
    spec = FACT_SPECS[category]
    if category == "earning_opportunities":
        # The how-to needs the offer's REAL steps: read from the landing page (or the source's own instructions) before
        # the prompt is built, stored on the item, and treated as the only source of steps.
        from pipeline import enrich_landing
        enrich_landing.enrich_item(conn, raw_item)
    cfg = load_category_config(conn, category)
    production = profile is None        # a caller-supplied profile is a review / test run: not counted in the draft statistics
    profile = profile or channel_voice.load_profile(conn, channel)
    if profile is None:
        raise RuntimeError(f"channel '{channel}' has no voice profile")
    region_profile = _get_region_profile(conn, channel)
    topic_key = (raw_item["payload"] or {}).get("topic_key")
    history = get_topic_history(conn, category, topic_key, raw_item["raw_item_id"])
    computer = POST_COMPUTERS.get(category)
    elements = computer(conn, raw_item, history) if computer else {}

    history_line = elements.get("history_line") if profile.keep_context_lines else None
    context_line = elements.get("context_line") if profile.keep_context_lines else None
    risk_line = elements.get("risk_line")
    emoji = cfg.get("emoji") or ""

    def assemble(title: str, paragraphs: list[str], *, final: bool) -> str:
        # `final=False` is the LENGTH measurement. A profile that excludes the evidence/risk line from its
        # range (Crypto Notebook) measures without it; the posted text always has it.
        measured_risk_line = None if (not final and profile.length_excludes_risk_line) else risk_line
        return post_format.assemble_post(
            emoji=emoji, title=title, narrative="", why_it_matters=None, paragraphs=paragraphs,
            history_line=history_line, risk_line=measured_risk_line,
            source_name=elements.get("source_name") if final else None,
            hashtags=(cfg.get("hashtags") or []) if (final and profile.hashtags) else [],
            context_line=context_line,
        )

    # Everything in the post the writer does NOT control (emoji, context lines, risk
    # flag) is measured once, up front, and subtracted from the length budget the
    # writer is given.
    overhead = channel_voice.measure(assemble("", [], final=False))
    prompt = channel_voice.build_prompt(profile, display_name=_get_display_name(conn, channel), spec=spec,
                                        region_profile=region_profile, raw_item=raw_item, overhead_chars=overhead)
    source_excerpt = (raw_item.get("payload") or {}).get("description")
    facts_have_figures = channel_voice._spec_has_figures(spec, spec.facts(raw_item["payload"]))

    def check(parsed: dict) -> list[str]:
        if not isinstance(parsed.get("paragraphs"), list):
            return channel_voice.find_voice_issues(profile, parsed, None, facts_have_figures)
        hits = _find_overclaims(parsed) + _find_reproduction_issues(parsed, source_excerpt)
        if category in NEUTRALITY_CHECKED_CATEGORIES:
            hits += _find_neutrality_violations(parsed)
        if category in SPECULATION_CHECKED_CATEGORIES:
            hits += _find_speculation_issues(parsed)
        if category in READER_ADVICE_CHECKED_CATEGORIES:
            hits += _find_reader_advice(parsed)
        if category in LISTING_CHECKED_CATEGORIES:
            hits += _find_listing_issues(parsed, raw_item["payload"])
        if category in EARNING_CHECKED_CATEGORIES:
            hits += _find_earning_issues(parsed, raw_item["payload"])
        chars = channel_voice.measure(assemble(str(parsed.get("title", "")), parsed["paragraphs"], final=False))
        return hits + channel_voice.find_voice_issues(profile, parsed, chars, facts_have_figures)

    try:
        parsed, attempts, retry_issues = _generate_voiced_write(conn, category, prompt, check,
                                                                 max_retries=VOICE_MAX_RETRIES_BY_CATEGORY.get(category, VOICE_MAX_RETRIES))
    except RuntimeError:
        if production and category == "earning_opportunities":
            _record_earning_draft(conn, raw_item, attempts=None, chars=None, ok=False)
        raise
    if production and category == "earning_opportunities":
        _record_earning_draft(conn, raw_item, attempts=attempts, chars=channel_voice.measure(assemble(parsed["title"], parsed["paragraphs"], final=False)), ok=True)
    text = assemble(parsed["title"], parsed["paragraphs"], final=True)
    return {
        "text": text, "attempts": attempts, "retry_issues": retry_issues, "prompt": prompt, "channel": channel,
        "chars": channel_voice.measure(assemble(parsed["title"], parsed["paragraphs"], final=False)),
    }


def generate_post(conn, category: str, channel: str, raw_item: dict) -> str:
    """Writes the post using whichever write_model the category is configured with.

    A channel with a voice profile (channel_config.voice_profile) and a category with
    a FactSpec is written through the channel-voice path -- the channel owns voice,
    the category supplies facts and guardrails. Anything else (CoinCraft until its
    Phase 5 profile, a category without a FactSpec) takes the legacy path below,
    unchanged."""
    if category in FACT_SPECS and channel_voice.load_profile(conn, channel) is not None:
        return generate_post_with_report(conn, category, channel, raw_item)["text"]
    prompt, history = _build_prompt_and_history(conn, category, channel, raw_item)
    source_excerpt = (raw_item.get("payload") or {}).get("description")
    raw = _generate_checked_write(
        conn, category, prompt, source_excerpt=source_excerpt,
        check_neutrality=category in NEUTRALITY_CHECKED_CATEGORIES,
        check_no_speculation=category in SPECULATION_CHECKED_CATEGORIES,
    )
    return _assemble(conn, category, raw_item, history, raw)
