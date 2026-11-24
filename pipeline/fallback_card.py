"""The plain FACTS card for an earning_opportunities item whose draft could not be written (operator 2026-11-17), and the SHORT card for an item with nothing but a title and an amount (operator 2026-11-22).

The writer keeps its guards against invented facts and copied text. When a draft still fails them after its retries, the operator must not be left with a failure message and no
post: this builds a card straight from the stored facts -- no model, nothing invented -- that he can post as it is or fix with the Edit button. It is ready-to-send HTML like a
written post and carries NO URL and NO referral wording (he adds his own link when he forwards).

A fact that is not known is LEFT OUT: there is no "not stated" line, no "the steps are on the offer's page". That the card is a plain facts card, or that its terms are unverified, is said on the OPERATOR'S
header line only (bot/approval_poller.py), never in the text he forwards.

Pure functions of the stored payload and score breakdown.
"""

import datetime as _dt
import html

from pipeline import post_facts, prizes

_GEO = {"worldwide": "Open worldwide", "multi": "Open in several regions", "single": "Open in ONE country only"}


def _day(iso: str | None) -> str | None:
    try:
        d = _dt.datetime.fromisoformat(iso)
        return f"{d.strftime('%b')} {d.day}, {d.year}"
    except (TypeError, ValueError):
        return None


def reward_text(p: dict) -> str:
    """The reward as the offer states it, in its ORIGINAL currency; "" when nothing states one."""
    e = p.get("enriched") or {}
    line = prizes.prize_line(p)
    if line:
        return line
    shown = lambda key, usd: p.get(key) or (f"about ${usd:,.0f} (a dollar equivalent)" if usd else "")
    if e.get("reward") and not post_facts.has_referral(e["reward"]):       # a reward read off a referral page is not this offer's reward
        return e["reward"]
    if p.get("token_reward"):
        t = p["token_reward"]
        return f"{t.get('amount', 0):,.0f} {t.get('token', 'tokens')} (paid in a token, not a stablecoin)"
    if p.get("payout_usd") is not None:
        kind = {"max": "Up to", "worth": "Worth", "ev": "Raffle prize pool: expected about"}.get(p.get("payout_kind"), "")
        return f"{kind} {shown('payout_display', p['payout_usd'])}".strip()
    if p.get("pool_usd"):
        return f"A pool of {shown('pool_display', p['pool_usd'])} shared among participants"
    return ""


def title_of(p: dict) -> str:
    return ((p.get("enriched") or {}).get("title_en") or p.get("title") or "Earning opportunity").strip()[:140]


def plain_facts_card(payload: dict, breakdown: dict | None = None, display_name: str = "Earn Stack", gate_reasons: list[str] | None = None) -> str:
    """Ready-to-send Telegram HTML: only the facts that are known."""
    p, b = payload or {}, breakdown or {}
    e = p.get("enriched") or {}
    lines = [f"🎁 <b>{html.escape(title_of(p))}</b>", ""]
    reward = reward_text(p)
    if reward:
        lines.append(f"<b>Reward:</b> {html.escape(reward)}")
    deadline = _day(p.get("deadline")) or e.get("deadline")
    if deadline:
        lines.append(f"<b>Deadline:</b> {html.escape(deadline)}")
    who = "; ".join(c for c in (e.get("eligibility") or "").split("; ") if c and not post_facts.has_referral(c)) or _GEO.get(p.get("geo"))
    if who:
        lines.append(f"<b>Who can take part:</b> {html.escape(who)}")
    cap = p.get("capital_usd")
    page_cost = None if post_facts.has_referral(e.get("cost")) else e.get("cost")
    cost = page_cost or (f"About ${cap:,.0f} of your own money is asked for (a deposit)" if cap and cap > 0 else "")
    if cost:
        lines.append(f"<b>Cost:</b> {html.escape(cost)}")
    if e.get("minimums") and not post_facts.has_referral(e["minimums"]):
        lines.append(f"<b>Minimum:</b> {html.escape(e['minimums'])}")
    reqs = [x.replace("_", " ") for x in (p.get("prereqs") or [])]
    if b.get("kyc") is True and "kyc" not in reqs:
        reqs.append("kyc")
    if p.get("skill"):
        reqs.append("professional skill: " + ", ".join(p["skill"]))
    if reqs:
        lines.append(f"<b>Requirements:</b> {html.escape(', '.join(reqs))}")
    for c in [c for c in (e.get("conditions") or []) if not post_facts.has_referral(c)][:4]:
        lines.append(f"• {html.escape(c)}")
    steps = [s for s in (e.get("steps") or []) if not post_facts.has_referral(s)][:6]          # "NO referral wording" (module docstring): a step or condition about a referral program stays out
    if steps:
        lines.append("")
        lines += [f"{i}. {html.escape(s)}" for i, s in enumerate(steps, 1)]
    labels = []
    if (b.get("referral") or {}).get("multi_level"):
        labels.append("⚠ multi-level wording")
    for r in gate_reasons or ((p.get("gate") or {}).get("reasons") or []):
        labels.append("🔴 " + r)
    if labels:
        lines += ["", html.escape(" · ".join(labels))]
    lines += ["", f"Source: {html.escape(str(p.get('source_name') or p.get('source') or ''))}"]
    return "\n".join(lines)
