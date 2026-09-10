"""Deterministic, per-category scoring — zero LLM calls (Section 7). An item's
score never crosses category lines: each category has its own weights, its own
scorer function, and its own threshold in category_config.

Adding a new category later means writing one function and registering it with
@register_scorer("category_name") — nothing else in this file changes.

Scorer signature: `scorer(conn, raw_item_id, payload) -> breakdown_dict`.
"Deterministic" means same-inputs-in-same-outputs-out and zero LLM calls — it
does NOT mean no DB access. whale_movements' novelty component (Phase 2,
2026-09-10) needs to compare against this category's own recent history (a
cooldown-style lookup), which payload alone can't answer — conn/raw_item_id
are there for scorers that need that. defi_yields ignores both.
"""

import json
import math
import time

from psycopg2.extras import execute_values

from pipeline.db import dict_cursor

SCORERS = {}


def register_scorer(category: str):
    def deco(fn):
        SCORERS[category] = fn
        return fn
    return deco


def load_category_config(conn, category: str) -> dict:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT * FROM category_config WHERE category = %s", (category,))
        row = cur.fetchone()
    if not row:
        raise RuntimeError(
            f"No category_config row for '{category}' — run scripts/seed_config.py first."
        )
    return row


def category_config_exists(conn, category: str) -> bool:
    """Cheap existence check, so callers that iterate a channel's categories
    (bot/notify.py) can skip categories that are listed for a future build phase
    but don't have a collector/scorer/config wired up yet, instead of crashing."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT 1 FROM category_config WHERE category = %s", (category,))
        return cur.fetchone() is not None


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _log_scale(value: float, floor: float, ceiling: float,
                out_min: float = 0.0, out_max: float = 100.0) -> float:
    """Maps value logarithmically from [floor, ceiling] to [out_min, out_max],
    clamped outside that range. Useful for dollar-value-style impact scores
    where a floor->10x move should matter a lot more than a 10x->11x one."""
    if value <= floor:
        return out_min
    if value >= ceiling:
        return out_max
    frac = (math.log10(value) - math.log10(floor)) / (math.log10(ceiling) - math.log10(floor))
    return out_min + frac * (out_max - out_min)


@register_scorer("defi_yields")
def score_defi_yields(conn, raw_item_id: int, payload: dict) -> dict:
    """Breakdown components, each 0-100. See config/category_config.yaml for the
    weights applied to these, and prompt_notes for how "impact" is meant to read
    (mechanics, not hype).

    TODO(scoring): live-observed on 2026-09-09 — very high/spiking APY (e.g.
    200%+ apy with 100+ percentage-point apy_pct_7d jumps, see raydium-amm
    WSOL-USDC and pepeteam-swaves SWAVES in that run's real candidates) currently
    scores as highly as a healthy, stable yield: `impact` saturates at 33%+ APY
    with no ceiling-awareness, and `novelty` explicitly rewards a big 7d swing
    with no sense of direction or plausibility. In practice this pattern usually
    means unsustainable token emissions, not a real opportunity — the two should
    probably be distinguished. Candidate fix: a penalty when apy is far above
    the category's own rolling median/percentile (not a fixed cutoff, since
    "high" is relative to market conditions) — needs a rolling stat over recent
    raw_items, not just the single payload. Deliberately NOT implemented yet —
    the MVP notify/approve/write/publish loop needs to be fully validated first
    before touching scoring weights.
    """
    apy = payload.get("apy") or 0.0
    apy_base = payload.get("apy_base") or 0.0
    apy_reward = payload.get("apy_reward") or 0.0
    apy_pct_7d = payload.get("apy_pct_7d")
    tvl = payload.get("tvl_usd") or 0.0
    il_risk = (payload.get("il_risk") or "").lower()
    stablecoin = bool(payload.get("stablecoin"))

    # Impact: how large the yield itself is, log-scaled so a 8%->16% move matters
    # more than a 60%->68% one at the extreme end.
    impact = _clamp(apy * 3.0)  # 33%+ APY saturates this component

    # Novelty: a real week-over-week swing is more worth reading about than a
    # yield that's been flat forever. No history yet (apy_pct_7d missing) reads
    # as moderately novel rather than zero, so a brand-new pool isn't penalized.
    if apy_pct_7d is None:
        novelty = 50.0
    else:
        novelty = _clamp(abs(apy_pct_7d) * 8.0)

    # Credibility: reward-token-driven APY is far less trustworthy than base
    # (real fee) APY, and TVL is the market's own vote of confidence.
    base_share = apy_base / apy if apy else 0.0
    credibility = _clamp(base_share * 60.0 + min(tvl, 20_000_000) / 20_000_000 * 40.0)

    # Actionability: penalize pools DefiLlama itself flags as high IL risk unless
    # they're a stablecoin pair, where IL risk is close to moot.
    if il_risk == "yes" and not stablecoin:
        actionability = 30.0
    else:
        actionability = 85.0
    if apy_reward and apy_base == 0:
        # 100% emissions-driven yield — actionable only for very fast movers.
        actionability = _clamp(actionability - 25.0)

    return {
        "impact": round(impact, 1),
        "novelty": round(novelty, 1),
        "credibility": round(credibility, 1),
        "actionability": round(actionability, 1),
    }


# whale_movements impact scale: $2M (the collection floor, category_config.
# collect_min_usd) reads as barely-impactful, $50M+ single transfers (rare but
# real for major exchange wallets) saturate. Not DB-driven off collect_min_usd
# itself — these are the scorer's own internal calibration constants, same as
# defi_yields' "33%+ APY saturates impact" being a code constant, not config.
WHALE_IMPACT_FLOOR_USD = 2_000_000
WHALE_IMPACT_CEILING_USD = 50_000_000
WHALE_NOVELTY_CEILING_HOURS = 72  # 3 days since a similar move -> full novelty


def _whale_novelty(conn, raw_item_id: int, payload: dict) -> float:
    """How long since we last flagged a move for this SAME exchange+direction
    pair — a big Binance-inflow flagged yesterday makes another same-size
    Binance-inflow today less novel; the first flagged move in days for a
    given exchange/direction scores high. Requires a DB lookup (see module
    docstring) since payload alone can't know about other raw_items."""
    exchange = payload.get("exchange")
    direction = payload.get("direction")
    if not exchange or not direction:
        return 50.0

    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT r.collected_at,
                      (SELECT MAX(r2.collected_at) FROM raw_items r2
                       WHERE r2.category = r.category AND r2.id != r.id
                         AND r2.payload->>'exchange' = r.payload->>'exchange'
                         AND r2.payload->>'direction' = r.payload->>'direction'
                      ) AS prior_collected_at
               FROM raw_items r WHERE r.id = %s""",
            (raw_item_id,),
        )
        row = cur.fetchone()

    if not row or not row["prior_collected_at"]:
        return 100.0  # first flagged move we've ever seen for this exchange+direction pair

    hours_gap = (row["collected_at"] - row["prior_collected_at"]).total_seconds() / 3600
    return round(_clamp(hours_gap / WHALE_NOVELTY_CEILING_HOURS * 100.0), 1)


def _whale_credibility(payload: dict) -> float:
    """Counterparty wallet establishment (age + sent-tx count), NOT the
    exchange side — operator direction 2026-09-10: every watchlist address is
    curated, so 'is the exchange side legitimate' would be a near-constant
    that doesn't discriminate between items. The counterparty genuinely
    varies: a transfer touching a long-lived, active wallet is a more
    credible 'real economic activity' signal than one touching a brand-new,
    one-off address (higher exploit/mixer/wash-trade risk)."""
    if payload.get("counterparty_is_exchange"):
        return 90.0  # another watchlisted, well-established entity

    first_tx_ts = payload.get("counterparty_first_tx_ts")
    tx_ts = payload.get("timestamp")
    if first_tx_ts is not None and tx_ts is not None:
        age_days = max(0.0, (tx_ts - first_tx_ts) / 86400)
        age_score = min(age_days / 365.0, 1.0) * 100.0
    else:
        age_score = 20.0  # unknown age -- treat cautiously, not as an automatic zero

    sent_tx_count = payload.get("counterparty_sent_tx_count")
    count_score = min(sent_tx_count / 100.0, 1.0) * 100.0 if sent_tx_count is not None else 20.0

    return round(age_score * 0.6 + count_score * 0.4, 1)


@register_scorer("whale_movements")
def score_whale_movements(conn, raw_item_id: int, payload: dict) -> dict:
    """Breakdown components, each 0-100. See config/category_config.yaml's
    whale_movements section for the full weighting rationale — this shape is
    deliberately NOT copied from defi_yields (operator direction 2026-09-10):
    impact/novelty are weighted equally (0.30 each, vs. defi_yields' 0.35/0.25)
    since 'is this unusual' carries as much story as 'how big' for a whale
    move, and credibility is redefined entirely (counterparty establishment,
    not exchange-side legitimacy — see _whale_credibility)."""
    value_usd = payload.get("value_usd") or 0.0

    impact = round(_log_scale(value_usd, WHALE_IMPACT_FLOOR_USD, WHALE_IMPACT_CEILING_USD), 1)
    novelty = _whale_novelty(conn, raw_item_id, payload)
    credibility = _whale_credibility(payload)

    # Actionability, 3 tiers (2026-09-10 — was binary, missed the live bug
    # where an unlabeled-but-clearly-institutional counterparty was scored
    # exactly like a genuine retail wallet): a single-exchange in/out against
    # a genuine external wallet is a clear directional signal (deposit =
    # possible sell pressure, withdrawal = possible accumulation); a
    # CONFIRMED exchange<->exchange transfer is an ambiguous market read
    # (internal rebalancing); an unlabeled-but-clearly-not-retail counterparty
    # (see collectors/whale_movements.py's INSTITUTIONAL_SENT_TX_THRESHOLD)
    # sits between the two — probably not a real directional signal, but not
    # confirmed internal reshuffling either.
    if payload.get("counterparty_is_exchange"):
        actionability = 40.0
    elif payload.get("counterparty_likely_institutional"):
        actionability = 55.0
    else:
        actionability = 85.0

    return {
        "impact": impact,
        "novelty": novelty,
        "credibility": credibility,
        "actionability": round(actionability, 1),
    }


# web3_jobs weights are NOT copied from either prior category (operator
# direction 2026-09-10) — "big number = important" doesn't apply to a job
# listing. Each of the four components maps to one of the factors the
# operator named: impact=compensation quality, novelty=freshness (a genuine
# early opportunity vs. one that's been sitting for weeks), credibility=
# listing legitimacy (web3 job boards see real scam volume), actionability=
# remote-accessibility + apply-ability. credibility/actionability weighted
# highest (0.30 each) since legitimacy and "can a global reader actually
# take this" matter more here than compensation or freshness alone.
WEB3_JOBS_SALARY_FLOOR = 30_000
WEB3_JOBS_SALARY_CEILING = 150_000
WEB3_JOBS_NOVELTY_DECAY_DAYS = 14  # full novelty at 0 days old, zero by this age


@register_scorer("web3_jobs")
def score_web3_jobs(conn, raw_item_id: int, payload: dict) -> dict:
    """Breakdown components, each 0-100. Pure function of payload — no DB
    lookup needed (unlike whale_movements' novelty), since RemoteOK already
    hands us a real posting timestamp to score freshness from directly."""
    salary_max = payload.get("salary_max") or 0
    if salary_max <= 0:
        # Undisclosed reads as moderate, not zero -- most legitimate listings
        # on this feed don't disclose salary (live-verified: ~2% do), so
        # treating silence as disqualifying would filter out most real jobs.
        impact = 30.0
    else:
        impact = round(_log_scale(salary_max, WEB3_JOBS_SALARY_FLOOR, WEB3_JOBS_SALARY_CEILING), 1)

    epoch = payload.get("epoch")
    if epoch is None:
        novelty = 50.0
    else:
        age_days = max(0.0, (time.time() - epoch) / 86400)
        novelty = round(_clamp(100.0 - (age_days / WEB3_JOBS_NOVELTY_DECAY_DAYS) * 100.0), 1)

    # Credibility: cheap, deterministic legitimacy signals -- no LLM judgment
    # about whether a listing "sounds real". A logo, a focused (not spam-
    # bloated) tag list, and a substantive description are all things a
    # thin/scam listing typically lacks. Live-observed: spam-tagged listings
    # in this feed had 30-40 unrelated tags; genuine ones had under a dozen,
    # topically coherent.
    credibility = 40.0
    if payload.get("company_logo"):
        credibility += 30.0
    tag_count = len(payload.get("tags") or [])
    if 1 <= tag_count <= 12:
        credibility += 15.0
    if (payload.get("description_length") or 0) > 200:
        credibility += 15.0
    credibility = round(_clamp(credibility), 1)

    # Actionability: RemoteOK listings are remote by definition, so the real
    # differentiator is whether the role is geographically OPEN (any global
    # reader could apply) vs. region-restricted despite being "remote" (e.g.
    # a GCC-only role requiring Arabic + local market knowledge -- live-
    # observed in this feed) -- plus a basic apply-ability gate.
    apply_url = payload.get("apply_url")
    location = (payload.get("location") or "").strip().lower()
    if not apply_url:
        actionability = 10.0
    elif not location or "world" in location or "anywhere" in location:
        actionability = 90.0
    else:
        actionability = 60.0

    return {
        "impact": impact,
        "novelty": novelty,
        "credibility": credibility,
        "actionability": round(actionability, 1),
    }


def score_new_items(conn, category: str) -> int:
    """Score every raw_item in this category that doesn't have a score row yet.
    Returns the number scored."""
    if category not in SCORERS:
        raise RuntimeError(f"No scorer registered for category '{category}'")
    scorer = SCORERS[category]
    weights = load_category_config(conn, category)["score_weights"]

    with dict_cursor(conn) as cur:
        cur.execute(
            """SELECT r.id, r.payload FROM raw_items r
               LEFT JOIN scores s ON s.raw_item_id = r.id
               WHERE r.category = %s AND s.id IS NULL""",
            (category,),
        )
        unscored = cur.fetchall()

    if not unscored:
        return 0

    values = []
    for row in unscored:
        breakdown = scorer(conn, row["id"], row["payload"])
        total = sum(weights[k] * breakdown[k] for k in weights)
        values.append((row["id"], category, round(total, 1), json.dumps(breakdown)))

    # One round trip for the whole batch, not one INSERT per row — a category
    # with thousands of unscored items (e.g. defi_yields' first run) would
    # otherwise take one network round trip per row against Supabase, which is
    # what actually risks timing out collect.yml's job limit (Section 12).
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO scores (raw_item_id, category, score, score_breakdown) VALUES %s""",
            values,
            template="(%s, %s, %s, %s)",
            page_size=500,
        )
    conn.commit()
    return len(values)
