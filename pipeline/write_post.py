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

from pipeline import llm, post_format
from pipeline.db import dict_cursor
from pipeline.score import load_category_config

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
def compute_defi_yields_elements(raw_item: dict, history: list) -> dict:
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
    risk_line = ("; ".join(risk_reasons) + ".").capitalize() if risk_reasons else None

    # No hyperlinks (2026-09-10, operator direction) — plain-text attribution only.
    source_name = "DefiLlama" if p.get("pool_id") else None

    return {"history_line": history_line, "risk_line": risk_line, "source_name": source_name}


def _assemble(conn, category: str, raw_item: dict, history: list, llm_raw_output: str) -> str:
    cfg = load_category_config(conn, category)
    parsed = post_format.parse_llm_json(llm_raw_output)
    computer = POST_COMPUTERS.get(category)
    elements = computer(raw_item, history) if computer else {}

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
    raw = llm.generate_write(conn, category, prompt)
    return _assemble(conn, category, raw_item, history, raw)


def generate_post_variants(conn, category: str, channel: str, raw_item: dict) -> dict:
    """Dual-variant write for a benchmark trial (Section 4.3). Always compares
    DeepSeek against Sonnet, regardless of which one write_model currently points
    at, so the trial data is comparable across the whole trial window. Both
    variants go through the identical deterministic assembly (same history/
    risk/source/hashtags) — only the LLM prose differs between them."""
    prompt, history = _build_prompt_and_history(conn, category, channel, raw_item)
    raw_deepseek = llm.generate_write(conn, category, prompt, model_override="deepseek-v4-flash")
    raw_sonnet = llm.generate_write(conn, category, prompt, model_override="claude-sonnet-5")
    return {
        "deepseek-v4-flash": _assemble(conn, category, raw_item, history, raw_deepseek),
        "claude-sonnet-5": _assemble(conn, category, raw_item, history, raw_sonnet),
    }
