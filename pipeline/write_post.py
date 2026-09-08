"""Final post writing (Section 8, step 2). Only ever called on operator-approved
items. Builds the fixed three-part prompt (what happened → why it matters → what
to watch), feeds in related history from raw_items so the "memory" differentiator
(Section 1, #1) is explicit in the output, and — during a category's benchmark
trial (Section 4.3) — generates both a DeepSeek and a Sonnet variant so the
operator can compare real output instead of picking a model on assumption.
"""

from pipeline import llm
from pipeline.db import dict_cursor
from pipeline.score import load_category_config

HISTORY_LIMIT = 5

# Per-category prompt builders. defi_yields is the only one wired for the MVP;
# add one function per new category as it's built, same pattern as score.py's
# scorer registry and template.py's triage registry.
PROMPT_BUILDERS = {}


def register_prompt_builder(category: str):
    def deco(fn):
        PROMPT_BUILDERS[category] = fn
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


@register_prompt_builder("defi_yields")
def build_defi_yields_prompt(cfg: dict, raw_item: dict, history: list) -> str:
    p = raw_item["payload"]

    history_lines = "\n".join(
        f"- {h['collected_at']}: {h['payload'].get('apy', '?')}% APY, "
        f"TVL ${h['payload'].get('tvl_usd', 0):,.0f}"
        for h in history
    ) or "(first time we've seen this pool — no prior history)"

    return f"""You are writing one post for the "Crypto Notebook" Telegram channel.
Voice: {cfg.get('voice', 'educational')}. {cfg.get('prompt_notes', '')}

Structure, exactly three short parts, no headers/labels in the output:
1. What happened — the concrete numbers below, stated plainly.
2. Why it matters — the mechanic behind the number (real fees vs. emissions,
   lockup, IL exposure, TVL trend). Teach, don't hype.
3. What to watch — one concrete thing a reader could check next (e.g. whether
   the base-fee share holds up, whether TVL keeps growing or is being farmed
   and dumped).

Facts:
- Protocol: {p.get('project')}
- Chain: {p.get('chain')}
- Pool: {p.get('symbol')}
- APY: {p.get('apy')}% (base {p.get('apy_base')}%, reward {p.get('apy_reward')}%)
- TVL: ${p.get('tvl_usd', 0):,.0f}
- 7d APY change: {p.get('apy_pct_7d')}
- IL risk flag: {p.get('il_risk')}
- Stablecoin pair: {p.get('stablecoin')}

History for this same pool (most recent first, may be empty):
{history_lines}

Score breakdown for context (do not mention scores/numbers-out-of-100 in the post
itself, they're for your judgment of what to emphasize, not for the reader):
{raw_item.get('score_breakdown')}

Write only the final post text. Keep it under ~130 words. No markdown headers, no
"Part 1/2/3" labels — just three short, well-separated paragraphs. Never phrase
anything as investment advice ("you should deposit here") — describe what the
data shows.
"""


def build_prompt(conn, category: str, raw_item: dict) -> str:
    if category not in PROMPT_BUILDERS:
        raise NotImplementedError(f"No prompt builder registered for category '{category}'")
    cfg = load_category_config(conn, category)
    topic_key = (raw_item["payload"] or {}).get("topic_key")
    history = get_topic_history(conn, category, topic_key, raw_item["raw_item_id"])
    return PROMPT_BUILDERS[category](cfg, raw_item, history)


def generate_post(conn, category: str, raw_item: dict) -> str:
    """Single-variant write, using whatever write_model is currently configured."""
    prompt = build_prompt(conn, category, raw_item)
    return llm.generate_write(conn, category, prompt)


def generate_post_variants(conn, category: str, raw_item: dict) -> dict:
    """Dual-variant write for a benchmark trial (Section 4.3). Always compares
    DeepSeek against Sonnet, regardless of which one write_model currently points
    at, so the trial data is comparable across the whole trial window."""
    prompt = build_prompt(conn, category, raw_item)
    return {
        "deepseek-v4-flash": llm.generate_write(conn, category, prompt, model_override="deepseek-v4-flash"),
        "claude-sonnet-5": llm.generate_write(conn, category, prompt, model_override="claude-sonnet-5"),
    }
