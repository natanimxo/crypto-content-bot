"""The plain FACTS card for an earning_opportunities item whose draft could not be written (operator 2026-11-17).

The writer keeps its guards against invented facts and copied text. When a draft still fails them after its retries, the operator must not be left with a failure message and no
post: this builds a card straight from the stored facts -- no model, nothing invented -- that he can post as it is or fix with the Edit button. It is ready-to-send HTML like a
written post, carries NO URL and NO referral wording (he adds his own link when he forwards), and says in one line that it is a facts card.

Pure function of the stored payload and score breakdown.
"""

import datetime as _dt
import html

from pipeline import prizes

_GEO = {"worldwide": "Open worldwide", "multi": "Open in several regions (check the offer's page for which)", "single": "Open in ONE country only"}


def _day(iso: str | None) -> str | None:
    try:
        d = _dt.datetime.fromisoformat(iso)
        return f"{d.strftime('%b')} {d.day}, {d.year}"
    except (TypeError, ValueError):
        return None


def _reward(p: dict) -> str:
    line = prizes.prize_line(p)
    if line:
        return line
    shown = lambda key, usd: p.get(key) or (f"about ${usd:,.0f} (a dollar equivalent)" if usd else "")
    if p.get("token_reward"):
        t = p["token_reward"]
        return f"{t.get('amount', 0):,.0f} {t.get('token', 'tokens')} (paid in a token, not a stablecoin)"
    if p.get("payout_usd") is not None:
        kind = {"max": "Up to", "worth": "Worth", "ev": "Raffle prize pool: expected about"}.get(p.get("payout_kind"), "")
        return f"{kind} {shown('payout_display', p['payout_usd'])}".strip()
    if p.get("pool_usd"):
        return f"A pool of {shown('pool_display', p['pool_usd'])} shared among participants (what one person receives is not stated)"
    return "Reward not stated"


def plain_facts_card(payload: dict, breakdown: dict | None = None, display_name: str = "Earn Stack", gate_reasons: list[str] | None = None) -> str:
    """Ready-to-send Telegram HTML."""
    p, b = payload or {}, breakdown or {}
    title = html.escape((p.get("title") or "Earning opportunity").strip()[:140])
    lines = [f"🎁 <b>{title}</b>", "", f"<b>Reward:</b> {html.escape(_reward(p))}"]
    if _day(p.get("deadline")):
        lines.append(f"<b>Deadline:</b> {_day(p['deadline'])}")
    lines.append(f"<b>Who can take part:</b> {html.escape(_GEO.get(p.get('geo'), 'Countries not stated'))}")
    cap = p.get("capital_usd")
    cost = "Nothing is stated as required up front" if cap == 0 else (f"About ${cap:,.0f} of your own money is asked for (a deposit)" if cap else "What it costs is not stated")
    lines.append(f"<b>Cost:</b> {html.escape(cost)}")
    reqs = [x.replace("_", " ") for x in (p.get("prereqs") or [])]
    if b.get("kyc") is True and "kyc" not in reqs:
        reqs.append("kyc")
    if p.get("skill"):
        reqs.append("professional skill: " + ", ".join(p["skill"]))
    if reqs:
        lines.append(f"<b>Requirements:</b> {html.escape(', '.join(reqs))}")
    steps = [s for s in ((p.get("enriched") or {}).get("steps") or [])][:5]
    if steps:
        lines.append("")
        lines += [f"{i}. {html.escape(s)}" for i, s in enumerate(steps, 1)]
    else:
        lines += ["", "The steps are on the offer's page."]
    labels = []
    if (b.get("referral") or {}).get("multi_level"):
        labels.append("⚠ multi-level wording")
    for r in gate_reasons or ((p.get("gate") or {}).get("reasons") or []):
        labels.append("🔴 " + r)
    if labels:
        lines += ["", html.escape(" · ".join(labels))]
    lines += ["", f"Source: {html.escape(str(p.get('source_name') or p.get('source') or ''))}", "", "<i>Plain facts card: the writer could not produce a checked draft. Edit it or post it as it is.</i>"]
    return "\n".join(lines)
