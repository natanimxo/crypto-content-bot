"""Zero-LLM triage rendering (Section 8): for categories whose source data is
already structured numbers, the "summary" is just formatting those numbers well —
no model call, no cost, no latency. Register one function per category; category
config points `triage_model: template` at whichever categories qualify.
"""

TEMPLATES = {}


def register_template(category: str):
    def deco(fn):
        TEMPLATES[category] = fn
        return fn
    return deco


def render(category: str, raw_item: dict) -> str:
    if category not in TEMPLATES:
        raise RuntimeError(
            f"No template registered for category '{category}' — either register one "
            f"in pipeline/llm_providers/template.py or point its triage_model at a real model."
        )
    return TEMPLATES[category](raw_item)


@register_template("defi_yields")
def render_defi_yields(raw_item: dict) -> str:
    p = raw_item["payload"]
    apy = p.get("apy") or 0.0
    apy_base = p.get("apy_base") or 0.0
    apy_reward = p.get("apy_reward") or 0.0
    tvl = p.get("tvl_usd") or 0.0
    pct7d = p.get("apy_pct_7d")

    parts = [f"{p.get('project')} {p.get('symbol')} on {p.get('chain')}: {apy:.2f}% APY"]
    if apy_reward:
        parts.append(f"({apy_base:.2f}% base + {apy_reward:.2f}% reward)")
    parts.append(f"TVL ${tvl:,.0f}")
    if pct7d is not None:
        direction = "up" if pct7d >= 0 else "down"
        parts.append(f"{direction} {abs(pct7d):.1f}pp over 7d")
    if p.get("il_risk") == "yes" and not p.get("stablecoin"):
        parts.append("IL risk flagged")
    return " · ".join(parts)


@register_template("whale_movements")
def render_whale_movements(raw_item: dict) -> str:
    p = raw_item["payload"]
    verb = "withdrawn from" if p.get("direction") == "outflow" else "deposited to"
    parts = [
        f"${p.get('value_usd', 0):,.0f} ({p.get('amount', 0):,.2f} {p.get('symbol', '?')}) "
        f"{verb} {p.get('exchange')}"
    ]
    if p.get("counterparty_is_exchange"):
        parts.append(f"counterparty: {p.get('counterparty_exchange_name')} (exchange)")
    else:
        sent = p.get("counterparty_sent_tx_count")
        if sent is not None and sent < 5:
            parts.append("counterparty: low-activity wallet")
    return " · ".join(parts)


@register_template("tool_launches")
def render_tool_launches(raw_item: dict) -> str:
    p = raw_item["payload"]
    if p.get("source") == "hackernews":
        parts = [p.get("title") or "", f"{p.get('points')} pts, {p.get('comments')} comments (HN)"]
        if p.get("also_trending_on_github"):
            parts.append("also trending on GitHub")
    else:
        parts = [p.get("title") or "", f"{p.get('stars_today')} stars today (GitHub)"]
        if p.get("also_shown_on_hn"):
            parts.append("also on HN")
    return " · ".join(parts)


@register_template("news")
def render_news(raw_item: dict) -> str:
    p = raw_item["payload"]
    parts = [p.get("title") or "", f"— {p.get('source')}"]
    if p.get("also_covered_by"):
        parts.append(f"(+{len(p['also_covered_by'])} other outlet(s))")
    return " ".join(parts)


@register_template("gems_security")
def render_gems_security(raw_item: dict) -> str:
    p = raw_item["payload"]
    red_flags = p.get("red_flags") or []
    parts = [
        f"{p.get('symbol')} on {p.get('chain')} ({p.get('project')}), TVL ${p.get('tvl_usd', 0):,.0f}",
        f"{len(red_flags)} finding(s): " + "; ".join(red_flags[:2]) + ("..." if len(red_flags) > 2 else ""),
    ]
    if p.get("tokens_unchecked"):
        parts.append(f"{len(p['tokens_unchecked'])} token(s) unchecked")
    return " · ".join(parts)


@register_template("web3_jobs")
def render_web3_jobs(raw_item: dict) -> str:
    p = raw_item["payload"]
    parts = [f"{p.get('position')} at {p.get('company')}"]
    salary_max = p.get("salary_max") or 0
    if salary_max:
        salary_min = p.get("salary_min") or 0
        parts.append(f"${salary_min:,.0f}-${salary_max:,.0f}" if salary_min else f"up to ${salary_max:,.0f}")
    else:
        parts.append("salary undisclosed")
    location = p.get("location")
    if location:
        parts.append(location)
    return " · ".join(parts)


@register_template("startup_jobs")
def render_startup_jobs(raw_item: dict) -> str:
    p = raw_item["payload"]
    parts = [f"{p.get('position')} at {p.get('company')}"]
    salary_max = p.get("salary_max") or 0
    if salary_max:
        salary_min = p.get("salary_min") or 0
        parts.append(f"${salary_min:,.0f}-${salary_max:,.0f}" if salary_min else f"up to ${salary_max:,.0f}")
    else:
        parts.append("salary undisclosed")
    location = p.get("location")
    if location:
        parts.append(location)
    return " · ".join(parts)
