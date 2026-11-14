"""What each category tells the writer about its own items: the FACTS, what a
post about them must COVER, and the GUARDRAILS no channel voice may bend.

Split out of pipeline/write_post.py on 2026-11-14 (operator direction: "the
channel owns voice, the category supplies only facts"). Before this, every
category's prompt builder interleaved three different things -- the facts of the
item, the voice to write them in (category_config.voice / prompt_notes) and the
output format -- so a news story landed on Crypto Wall Street and on Alpha Edge
through the same prompt and read identically. Now:

  * facts          -- the real data about this item (this module)
  * content_brief  -- what a post about it must contain, in voice-neutral terms
  * guardrails     -- the never-rules; ALWAYS win over a channel's voice
  * voice          -- who is talking, how long, what shape (pipeline/channel_voice.py,
                      config/channel_voices.yaml)

Guardrails live here in code, not in category_config, on purpose: each one is
paired with a structural check in pipeline/write_post.py (overclaim, reproduction,
neutrality, speculation guards) and none is an operator-tunable knob -- an
operator who wants to loosen one should have to change the check next to it too.

The `facts_*` helpers return the exact "Facts about ..." block the legacy
per-category prompt builders in write_post.py embed -- those builders call these
same helpers, so the two prompt paths cannot drift (legacy stays for any channel
without a voice profile, e.g. CoinCraft until Phase 5).
"""

from dataclasses import dataclass
from typing import Callable


def facts_defi_yields(p: dict) -> str:
    return f"""Facts about this pool:
- Protocol: {p.get('project')}
- Chain: {p.get('chain')}
- Pool: {p.get('symbol')}
- APY: {p.get('apy')}% (base {p.get('apy_base')}%, reward {p.get('apy_reward')}%)
- TVL: ${p.get('tvl_usd', 0):,.0f}
- 7d APY change: {p.get('apy_pct_7d')}
- IL risk flag: {p.get('il_risk')}
- Stablecoin pair: {p.get('stablecoin')}"""


def whale_counterparty_note(p: dict) -> str:
    # 3-way, not binary (2026-09-10 -- live bug: an unlabeled-but-clearly-not-
    # retail counterparty was being described to the LLM as "an external
    # wallet," and it wrote posts calling it exactly that, when it's very
    # likely institutional infrastructure we just haven't identified by name).
    if p.get("counterparty_is_exchange"):
        return f"the counterparty is another exchange ({p.get('counterparty_exchange_name')})"
    if p.get("counterparty_likely_institutional"):
        return (
            "the counterparty is a high-activity wallet with an enormous on-chain "
            "transaction history — almost certainly institutional or automated "
            "infrastructure, not an individual holder, even though we haven't identified "
            "exactly which entity it is. Do NOT call it 'an external wallet' or imply it's "
            "a retail/individual holder."
        )
    return "the counterparty is an external wallet"


def facts_whale_movements(p: dict) -> str:
    verb = "withdrew" if p.get("direction") == "outflow" else "deposited"
    return f"""Facts about this transfer:
- Exchange: {p.get('exchange')}
- Direction: {verb} (relative to the exchange wallet)
- Amount: {p.get('amount')} {p.get('symbol')}
- USD value: ${p.get('value_usd', 0):,.0f}
- Counterparty: {whale_counterparty_note(p)}"""


def facts_wire_story(p: dict) -> str:
    """news and macro_news -- identical block."""
    return f"""Facts about this story (from a wire excerpt, summarize in your own words --
never quote more than a short phrase, and never copy the excerpt's sentence
structure):
- Headline: {p.get('title')}
- Source excerpt: {p.get('description')}
- Outlet: {p.get('source')}"""


def jobs_salary_note(p: dict) -> str:
    salary_max = p.get("salary_max") or 0
    return f"${p.get('salary_min') or 0:,.0f}-${salary_max:,.0f}" if salary_max else "not disclosed"


def facts_job_listing(p: dict) -> str:
    """web3_jobs and startup_jobs -- identical block."""
    return f"""Facts about this listing:
- Role: {p.get('position')}
- Company: {p.get('company')}
- Salary: {jobs_salary_note(p)}
- Location: {p.get('location') or 'remote (no geographic restriction stated)'}
- Tags: {', '.join(p.get('tags') or [])}"""


def facts_tool_launches(p: dict) -> str:
    if p.get("source") == "hackernews":
        facts = (
            f"- Launch: {p.get('title')}\n"
            f"- Discussed on Hacker News: {p.get('points')} points, {p.get('comments')} comments\n"
            f"- Link: {p.get('url')}"
        )
        cross_note = "\nNote: this also appeared on GitHub Trending the same day." if p.get("also_trending_on_github") else ""
    else:
        facts = (
            f"- Repository: {p.get('title')}\n"
            f"- Trending on GitHub: {p.get('stars_today')} stars today (language: {p.get('language') or 'unspecified'})\n"
            f"- Description: {p.get('description') or 'none given'}"
        )
        cross_note = "\nNote: this was also posted to Hacker News the same day." if p.get("also_shown_on_hn") else ""
    return f"""Facts about this launch:
{facts}{cross_note}"""


def facts_gems_security(p: dict) -> str:
    red_flags = p.get("red_flags") or []
    tokens_unchecked = p.get("tokens_unchecked") or []
    unchecked_note = (
        f"\nNote: {len(tokens_unchecked)} of this pool's underlying token(s) could not be "
        f"checked at all (unsupported chain or no GoPlus data) -- do not imply they're clean, "
        f"say plainly that they weren't checked."
        if tokens_unchecked else ""
    )
    return f"""Facts about this pool (already verified by an automated check, not your judgment):
- Protocol: {p.get('project')}
- Chain: {p.get('chain')}
- Pool: {p.get('symbol')}
- TVL: ${p.get('tvl_usd', 0):,.0f}
- Specific security findings from GoPlus's Token Security API:
{chr(10).join(f'  - {flag}' for flag in red_flags)}{unchecked_note}"""


@dataclass(frozen=True)
class FactSpec:
    audience: str | None          # who the CATEGORY's content is about, when not the channel's default crypto readership
    facts: Callable[[dict], str]
    content_brief: str            # what a post must cover -- no tone, no length, no structure
    guardrails: str               # never-rules; override any channel voice instruction
    us_guardrail: str | None = None   # extra rule on region_profile 'us' channels (kept from the legacy builders)
    # Where a channel's paragraph ROLE would invite what the guardrails forbid, the category replaces
    # that role (1-based paragraph number -> new role text) instead of relying on the model to
    # reconcile them -- guardrails beat voice, applied at prompt-design level. Only ever used for
    # a role that would otherwise ask for advice; the channel's tone/length/person still apply.
    role_overrides: dict[int, str] | None = None


_NO_SCORES = "Never mention scores or internal categorization."

FACT_SPECS: dict[str, FactSpec] = {
    "defi_yields": FactSpec(
        audience=None,
        facts=facts_defi_yields,
        content_brief=(
            "State the key numbers (APY, TVL, the 7-day move) inline, and explain the "
            "mechanic behind the headline APY -- real fees vs. token emissions, lockups, "
            "impermanent-loss exposure, what is driving demand."
        ),
        guardrails=(
            "Describe what the numbers show; never phrase anything as investment advice or "
            "a recommendation to deposit funds, on any channel. No hype language, no "
            "manufactured urgency. " + _NO_SCORES
        ),
        us_guardrail=(
            "This is a US-audience channel — keep financial framing conservative and "
            "factual; never phrase anything as a recommendation to deposit funds."
        ),
    ),
    "whale_movements": FactSpec(
        audience=None,
        facts=facts_whale_movements,
        content_brief=(
            "Name the exchange and the approximate dollar amount, say which way it moved, "
            "and state the typical market read of that kind of move as an interpretation, "
            "not a certainty. The convention: a WITHDRAWAL from an exchange (coins leaving) "
            "is typically read as accumulation or reduced sell pressure; a DEPOSIT to an "
            "exchange is typically read as possible sell pressure. For a stablecoin, report "
            "the movement and give no directional read."
        ),
        guardrails=(
            "Never state a price prediction or tell the reader what to do with their own "
            "funds. Present the market read as a typical interpretation, never a certainty "
            "or a directive. " + _NO_SCORES
        ),
        us_guardrail=(
            "This is a US-audience channel — keep financial framing conservative and "
            "factual; never phrase anything as investment advice or a price prediction."
        ),
    ),
    "news": FactSpec(
        audience=None,
        facts=facts_wire_story,
        content_brief=(
            "Explain what happened and why it matters -- its significance or implication "
            "going forward -- in your own words. Never restate the headline. If there is "
            "genuinely nothing to add beyond the story itself, say what to watch next."
        ),
        guardrails=(
            "Summarize in your own words: never quote more than a short, clearly attributed "
            "phrase and never copy the excerpt's sentences or structure (a code-level check "
            "rejects 8+ consecutive verbatim words). Never state a price prediction or tell "
            "the reader what to do with their own funds. " + _NO_SCORES
        ),
    ),
    "web3_jobs": FactSpec(
        audience=None,
        facts=facts_job_listing,
        content_brief=(
            "Say what the role actually involves and who it is for -- beyond what the "
            "title already says (what the company does, why the role is open, the real "
            "day-to-day scope). State pay and location plainly. Point out what makes this "
            "listing worth a look ONLY if something genuinely does; otherwise just give the "
            "practical details."
        ),
        guardrails=(
            "Never inflate a listing beyond what the facts say; if salary is not disclosed, "
            "say so plainly instead of implying it is competitive. Never restate the title "
            "as if it were new information, and never manufacture significance. No hype "
            "language, no manufactured urgency. " + _NO_SCORES
        ),
    ),
    "startup_jobs": FactSpec(
        audience=("founders, indie hackers, people job-hunting at startups -- not a crypto "
                  "audience"),
        facts=facts_job_listing,
        content_brief=(
            "Say what the role actually involves and who it is for -- beyond what the "
            "title already says. State pay and location plainly. Point out what makes this "
            "listing worth a look ONLY if something genuinely does; otherwise just give the "
            "practical details."
        ),
        guardrails=(
            "Never inflate a listing beyond what the facts say; if salary is not disclosed, "
            "say so plainly. Never restate the title as if it were new information, and never "
            "manufacture significance. No crypto framing or financial language anywhere. No "
            "hype language, no manufactured urgency. " + _NO_SCORES
        ),
    ),
    "tool_launches": FactSpec(
        audience=("founders, indie hackers, people evaluating new tools -- not a crypto "
                  "audience"),
        facts=facts_tool_launches,
        content_brief=(
            "Say what the tool does and who it is for, what problem it solves or what is "
            "genuinely new about it, and how much traction it actually has."
        ),
        guardrails=(
            "Never inflate a launch beyond what the facts say: modest traction must read as "
            "modest. No crypto framing, no financial language. No hype language, no "
            "manufactured urgency. " + _NO_SCORES
        ),
    ),
    "macro_news": FactSpec(
        audience=("founders, indie hackers, people who want to understand how world events "
                  "affect their own economic situation -- not a crypto audience, not a "
                  "general news audience"),
        facts=facts_wire_story,
        content_brief=(
            "Say what happened in your own words, then the PLAUSIBLE ECONOMIC IMPLICATION "
            "for a reader -- prices, rates, jobs, costs, markets. Where a section asks what "
            "the reader should DO, answer with what it means for them or what to watch; "
            "never an instruction."
        ),
        # Hustle's second paragraph is "what to do about it". For macro news that invites exactly
        # what the guardrail forbids (first live generation: "plan for higher costs on any credit
        # you rely on"), so the role is replaced here, not left for the model to reconcile.
        role_overrides={2: (
            "What it means for you: the plausible effect on the reader's costs, demand or markets, "
            "and what to watch next -- a statement about the world, written to the reader, never an "
            "instruction or a recommendation of any action."
        )},
        guardrails=(
            "HARD RULES. Report what happened and its plausible economic implications. Never "
            "advocate a position, never tell a government/policymaker/reader what should "
            "happen, never characterize any government's or actor's MOTIVES ('in a desperate "
            "attempt to...', 'trying to distract from...') — describe actions, don't assign "
            "intent. Never use loaded characterizations of a political actor ('authoritarian', "
            "'corrupt regime', 'radical', 'extremist', 'regime') — use neutral, specific, "
            "factual language regardless of which side or country is involved. If two sides "
            "dispute what happened or why, say so plainly rather than adopting either side's "
            "framing as settled fact. Never state a price prediction or tell the reader what to "
            "do with their own money. No crypto framing, no financial-advice language. "
            "Summarize in your own words; never copy the excerpt's sentences or structure. "
            + _NO_SCORES
        ),
    ),
    "gems_security": FactSpec(
        audience=None,
        facts=facts_gems_security,
        content_brief=(
            "State plainly what the automated check found on this specific pool, then "
            "explain the GENERAL pattern behind the finding -- what it enables and why it "
            "matters -- and how a reader could check the same thing themselves on any token. "
            "Say plainly which tokens were not checked at all."
        ),
        guardrails=(
            "HARD RULES, no exceptions: you are reporting specific automated findings, not "
            "rendering a safety judgment. Never write 'safe', 'legit', 'a scam', "
            "'guaranteed', 'risk-free', 'verified safe', or any equivalent verdict language. "
            "Never say or imply a token is fine, trustworthy, or worth buying/depositing "
            "into, even by omission, and never add certainty the check does not have. Never "
            "tell the reader what to do with their own money. " + _NO_SCORES
        ),
    ),
}
