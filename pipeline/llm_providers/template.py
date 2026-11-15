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


@register_template("macro_news")
def render_macro_news(raw_item: dict) -> str:
    p = raw_item["payload"]
    parts = [p.get("title") or "", f"— {p.get('source')}"]
    if p.get("also_covered_by"):
        parts.append(f"(+{len(p['also_covered_by'])} other outlet(s))")
    return " ".join(parts)


@register_template("hacks_exploits")
def render_hacks_exploits(raw_item: dict) -> str:
    p = raw_item["payload"]
    tvl = p.get("protocol_tvl_usd")
    parts = [
        f"{p.get('protocol_name')}: ${p.get('amount_usd', 0):,.0f} lost ({p.get('classification') or 'unclassified'})",
        f"TVL ${tvl:,.0f}" if tvl is not None else "TVL not tracked",
    ]
    if p.get("chain"):
        parts.append(p["chain"])
    return " · ".join(parts)


@register_template("exchange_listings")
def render_exchange_listings(raw_item: dict) -> str:
    """The digest line (operator, 2026-11-15: "which exchange, and how many of the four already list it -- a first
    Binance listing and a fourth-exchange KuCoin listing should be distinguishable before I open anything").
    Leads with a colour dot and the position -- 1st/2nd/3rd/4th of the four tracked exchanges to list the token --
    then who already lists it, so the signal reads at a glance. When nobody else does, the window of that claim is
    shown too (the record is short), so a 'FIRST' is never read as 'first anywhere'."""
    from pipeline.post_facts import listing_record_window
    p = raw_item["payload"]
    me = p.get("exchange_key")
    earlier = []
    for x in p.get("prior_listings") or []:
        if x.get("exchange_key") != me and x["exchange"] not in earlier:
            earlier.append(x["exchange"])
    position = len(earlier) + 1
    dot = {1: "🟢", 2: "🟡"}.get(position, "⚪")
    ordinal = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(position, f"{position}th")
    if earlier:
        detail = "already on " + ", ".join(earlier)
    else:
        window = listing_record_window(p)
        detail = f"none of the other 3 list it in our record{f' since {window}' if window else ''}"
    token = p.get("token_name")
    token = f"{token} ({p.get('symbol')})" if token and token.upper() != (p.get("symbol") or "").upper() else f"{p.get('symbol')}"
    parts = [f"{dot} {p.get('exchange')} · {ordinal} of 4 tracked ({detail})", token]
    if p.get("trading_start"):
        parts.append(f"trading opens {p['trading_start'][:16].replace('T', ' ')} UTC")
    return " · ".join(parts)


@register_template("earning_opportunities")
def render_earning_opportunities(raw_item: dict) -> str:
    """The digest line: what it is, from where, and the checkable facts a reader of the digest needs before opening
    anything (the two scores are printed by the notifier). Facts only -- the operator does the 'is this real' call."""
    p = raw_item["payload"]
    parts = [f"{p.get('type', 'other').replace('_', ' ')}: {(p.get('title') or '')[:90]}", f"{p.get('source_name')} (tier {p.get('source_tier')})"]
    if p.get("payout_usd") is not None:
        kind = {"ev": "expected ", "worth": "worth ", "stated": "stated "}.get(p.get("payout_kind"), "")
        parts.append(f"{kind}${p['payout_usd']:,.0f}")
    elif p.get("pool_usd"):
        parts.append(f"shared pool ${p['pool_usd']:,.0f}")
    else:
        parts.append("payout not stated")
    cap = p.get("capital_usd")
    parts.append("cost unknown" if cap is None else "no cost" if cap == 0 else f"needs ${cap:,.0f}")
    if p.get("deadline"):
        parts.append(f"ends {p['deadline'][:10]}")
    parts.append({"worldwide": "worldwide", "multi": "several regions", "single": "ONE country"}.get(p.get("geo"), "region not stated"))
    if p.get("corroborators"):
        parts.append(f"also in {len(p['corroborators'])} other source(s)")
    age = p.get("domain_age_days")
    if age is not None and age < 90:
        parts.append(f"landing domain only {age}d old")
    if p.get("prereqs"):
        parts.append("requires " + "/".join(x.replace("_", " ") for x in p["prereqs"]))
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
