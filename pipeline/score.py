"""Deterministic, per-category scoring — zero LLM calls (Section 7). An item's
score never crosses category lines: each category has its own weights, its own
scorer function, and its own threshold in category_config.

Adding a new category later means writing one function and registering it with
@register_scorer("category_name") — nothing else in this file changes.
"""

import json

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


@register_scorer("defi_yields")
def score_defi_yields(payload: dict) -> dict:
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
        breakdown = scorer(row["payload"])
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
