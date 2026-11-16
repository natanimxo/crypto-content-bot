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

import re
from dataclasses import dataclass
from typing import Callable


# The one definition of how to read an exchange transfer, shared by the channel-voice brief below and the
# legacy whale prompt in write_post.py. 2026-11-14: both used to say only "state the likely market read
# (e.g. accumulation vs. distribution)" and the model inverted it -- a Kraken withdrawal came out "read as
# distribution" in the first channel-voice test. Stating the convention removes the guess.
WHALE_READ_CONVENTION = (
    "The convention: a WITHDRAWAL from an exchange (coins leaving) is typically read as accumulation or "
    "reduced sell pressure; a DEPOSIT to an exchange is typically read as possible sell pressure. For a "
    "stablecoin, report the movement and give no directional read."
)


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


def facts_hacks_exploits(p: dict) -> str:
    tvl = p.get("protocol_tvl_usd")
    tvl_note = f"${tvl:,.0f}" if tvl is not None else "not tracked by DefiLlama"
    returned = p.get("returned_funds")
    recovery_note = f"${returned:,.0f} recovered" if returned else "no funds recovered as of this report"
    return f"""Facts about this incident (confirmed by DefiLlama's own incident tracker, not your judgment):
- Protocol/platform: {p.get('protocol_name')}
- Amount lost: ${p.get('amount_usd', 0):,.0f}
- Chain: {p.get('chain') or 'not specified'}
- DefiLlama's own classification: {p.get('classification') or 'unclassified'}
- Technique (DefiLlama's own categorization): {p.get('technique') or 'not specified'}
- This protocol's current tracked TVL: {tvl_note}
- Recovery status: {recovery_note}"""


def listing_record_window(p: dict) -> str | None:
    """'Mar 2026' -- how far back EVERY other tracked exchange's record reaches (the latest of their start dates).
    This is the window a "no earlier listing" statement is true for, and it can be short: KuCoin's announcement API
    only reaches back to 2026-03, so a token KuCoin listed in 2025 does not appear in the record at all. A post may
    never say "no earlier listing" without saying "since <this>" (pipeline/write_post.py checks it)."""
    import calendar
    since = p.get("record_since") or {}
    starts = [v for k, v in since.items() if k != p.get("exchange") and v]
    if not starts:
        return None
    year, month = max(starts).split("-")[:2]
    return f"{calendar.month_abbr[int(month)]} {year}"


def facts_exchange_listings(p: dict) -> str:
    import datetime as _dt

    def day(iso: str | None) -> str:
        """'Nov 4' -- a wire dispatch does not print ISO dates."""
        try:
            d = _dt.datetime.fromisoformat(iso)
            return f"{d.strftime('%b')} {d.day}"
        except (TypeError, ValueError):
            return ""

    me = p.get("exchange")
    others = [n for n in ("Binance", "OKX", "Bybit", "KuCoin") if n != me]
    prior = p.get("prior_listings") or []
    if prior:
        prior_line = "YES -- " + "; ".join(
            f"{x['exchange']} (announced {day(x.get('announced_at'))}"
            f"{', an earlier announcement by this same exchange' if x.get('exchange_key') == p.get('exchange_key') else ''})"
            for x in prior)
    else:
        window = listing_record_window(p)
        prior_line = (f"NO -- no earlier listing of this token appears in the record of the other tracked exchanges "
                      f"({', '.join(others)}). That is a statement about the record, not proof it is listed nowhere else"
                      + (f": those records only go back to {window}, so a listing before then would not appear. "
                         f"Whenever the post says no earlier listing appears, it MUST say 'since {window}' right "
                         f"beside it." if window else "."))
    since = p.get("record_since") or {}
    record = ", ".join(f"{k} from {v}" for k, v in since.items()) or "unknown"
    # Derived from the same record, and the reason a thin announcement still has something true to say: how many of
    # the four tracked exchanges list it now, and how long after the first one this listing came.
    distinct = {x["exchange_key"] for x in prior} | {p.get("exchange_key")}
    standing = f"listed on {len(distinct)} of the 4 tracked exchanges once this listing is counted"
    if prior:
        try:
            gap = (_dt.datetime.fromisoformat(p["published_at"]) - _dt.datetime.fromisoformat(prior[0]["announced_at"])).total_seconds() / 86400
            when = ("the same day as" if gap < 1 else f"{int(gap)} days after")
            standing += f"; this announcement came {when} the first tracked one ({prior[0]['exchange']}, {day(prior[0].get('announced_at'))})"
        except (KeyError, TypeError, ValueError):
            pass
    else:
        standing = "this is the one tracked exchange with a listing of it in the record (1 of the 4)"
    start = p.get("trading_start")
    trading_line = f"\n- Trading opens: {day(start)} {start[11:16]} UTC, as stated in the announcement" if start else ""
    flags = p.get("flags") or []
    flag_line = f"\n- The exchange's own labels for it: {'; '.join(flags)}" if flags else ""
    name, sym = p.get("token_name"), p.get("symbol")
    token = f"{name} ({sym})" if name and name.upper() != (sym or "").upper() else f"{sym}"
    return f"""Facts about this listing (the exchange's own announcement, plus the record we keep of every spot listing announced by Binance, OKX, Bybit and KuCoin):
- Exchange: {me}
- Token: {token}
- Announced: {day(p.get('published_at'))}, {(p.get('published_at') or '')[11:16]} UTC{trading_line}{flag_line}
- ALREADY LISTED ON ANOTHER TRACKED EXCHANGE (our own record -- the one thing the announcement does not tell a reader): {prior_line}
- Where that leaves it: {standing}
- Background for you, NOT for the post: the record goes back to {record}, so a listing before those dates would not appear in it."""


MAX_FACT_STEPS = 3
STRUCTURED_SOURCES = {"superteam", "zindi", "immunefi"}      # platforms whose API gives the amount, token and deadline as fields
# A participation step (or an eligibility clause) that is about a referral / promo code is never handed to the writer: the operator adds referral
# links by hand and no one else's code goes out under his name (the guard rejects referral language anyway, so a post built from such a step could
# only fail). Found on the Superteam "Raenest" bounty, whose own steps say "sign up using code: community" and "include your unique referral code".
_REFERRAL_STEP_RE = re.compile(r"referral|referrer|refer a |invite (?:code|link)|promo(?:tional)? code|affiliate|(?:using|with|use) (?:the )?code\b|\bcode:", re.IGNORECASE)


def _shorten(text: str | None, limit: int) -> str | None:
    """Cut at a clause boundary under `limit` characters; None stays None."""
    if not text or len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("; ", ", ", " - ", ". "):
        i = cut.rfind(sep)
        if i > limit * 0.5:
            return cut[:i] + " (more on the page)"
    return cut.rsplit(" ", 1)[0] + " (more on the page)"


def facts_earning_opportunities(p: dict) -> str:
    import datetime as _dt

    def day(iso: str | None) -> str | None:
        try:
            d = _dt.datetime.fromisoformat(iso)
            return f"{d.strftime('%b')} {d.day}"
        except (TypeError, ValueError):
            return None

    enriched = p.get("enriched") or {}
    steps = [s for s in (enriched.get("steps") or []) if not _REFERRAL_STEP_RE.search(s)]
    extra_steps = max(0, len(steps) - MAX_FACT_STEPS)
    steps = [_shorten(x, 80).replace(" (more on the page)", "") for x in steps[:MAX_FACT_STEPS]]       # a 400-650 character post cannot carry a paragraph per step
    dropped_referral = len(enriched.get("steps") or []) - len(steps) - extra_steps > 0
    reward = _shorten(enriched.get("reward"), 120)
    if not reward and p.get("payout_kind") == "ev" and p.get("pool_usd"):
        reward = (f"a prize pool of ${p['pool_usd']:,.0f} shared among the winners only -- most participants win nothing "
                  f"(average per participant: about ${p['payout_usd']:,.0f})")
    elif not reward and p.get("payout_usd") is not None:
        kind = {"worth": "worth", "stated": "stated as"}.get(p.get("payout_kind"), "stated as")
        reward = f"{kind} ${p['payout_usd']:,.0f}"
    elif not reward and p.get("pool_usd"):
        reward = f"a pool of ${p['pool_usd']:,.0f} shared among participants; what one person receives is not stated"
    elif not reward and p.get("unclear_usd"):
        reward = f"an amount of ${p['unclear_usd']:,.0f} is mentioned, but it is not clear whether it is per person or a total shared among winners"
    cap = p.get("capital_usd")
    cost = enriched.get("cost") or ("nothing is stated as required up front" if cap == 0 else
                                    (f"about ${cap:,.0f} of your own money is needed" if cap else "not stated"))
    # A structured platform's own deadline (an ISO timestamp from its API) is authoritative: the page text shows the same instant in the fetcher's
    # local time ("14 December 07:29" for Dec 13 23:59 UTC), and the guard checks the post against the structured date -- preferring the page made
    # that guard impossible to satisfy (Zindi Dis-Chem, 2026-11-16).
    deadline = ((day(p.get("deadline")) if p.get("source") in STRUCTURED_SOURCES else None) or enriched.get("deadline") or day(p.get("deadline")) or "not stated")
    geo = {"worldwide": "open worldwide", "multi": "open in several regions (check the page for which)", "single": "open in ONE country only"}.get(p.get("geo"), "countries not stated")
    where = _shorten("; ".join(c for c in re.split(r"\s*;\s*", enriched.get("eligibility") or "") if c and not _REFERRAL_STEP_RE.search(c)), 170) or geo
    reqs = ", ".join(x.replace("_", " ") for x in (p.get("prereqs") or [])) or "none stated"
    if steps:
        steps_block = "\n".join(f"  {i}. {s}" for i, s in enumerate(steps, 1))
        if extra_steps or dropped_referral:     # the writer has a 400-650 character budget: a long ladder of steps cannot fit, and a referral step is the operator's to add
            steps_block += "\n  (the offer has further steps -- say the rest are on the offer's page; never write them yourself)"
    else:
        steps_block = "  NOT AVAILABLE -- the offer's own page could not be read. Write NO steps; tell the reader the steps are on the offer's page."
    return f"""Facts about this opportunity (collected automatically; the reader's own checks still apply):
- What: {p.get('title')}
- Kind: {(p.get('type') or 'other').replace('_', ' ')}
- Posted by: {p.get('source_name')}
- Reward: {reward or 'not stated'}
- What it costs or requires: {cost}
- Deadline: {deadline}
- Who can take part: {where}
- Other requirements: {reqs}
- Participation steps ({'from the offer itself' if steps else 'none available'}):
{steps_block}"""


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
    # Whether the facts contain a real FIGURE a title could carry. None = detect (any digit in the facts). A category
    # whose facts hold only dates and counts (exchange_listings) sets False, so a voice that wants a figure in the
    # title is told there is none instead of being failed on every draft for lacking one.
    title_figures: bool | None = None


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
            "not a certainty. " + WHALE_READ_CONVENTION
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
    "hacks_exploits": FactSpec(
        audience=None,
        facts=facts_hacks_exploits,
        content_brief=(
            "State what happened on this specific incident using ONLY the facts above, giving the "
            "classification and technique as DefiLlama's own finding. Then explain the GENERAL "
            "mechanism behind that kind of failure -- what the weakness is and why it can be "
            "exploited -- so a reader understands the pattern, not just this one loss."
        ),
        # A channel whose last paragraph says "how to spot it yourself ... on any pool or token" would
        # be asking for token/pool checks on an incident that has neither. The category restates the
        # role for incidents; the channel's tone, length and person still apply.
        role_overrides={3: (
            "How to spot it yourself: what a reader can look at on ANY protocol to see whether it has "
            "this kind of exposure (public audit reports, admin and key controls, oracle sources, "
            "upgrade permissions) -- where to look and what to look for. Never a verdict on any named "
            "protocol, and never an instruction about what to do with money."
        )},
        guardrails=(
            "HARD RULES, no exceptions: report only what the facts confirm. Never speculate on who "
            "caused this or why -- no attacker identity or motive, nothing about an inside job. Never "
            "use hedge-words that smuggle in an unconfirmed claim ('allegedly', 'apparently', "
            "'suspected to be'). Never say a recovery is likely or unlikely beyond the recovery status "
            "given. Never tell the reader what to do with their own money. " + _NO_SCORES
        ),
    ),
    "exchange_listings": FactSpec(
        audience=None,
        facts=facts_exchange_listings,
        content_brief=(
            "Say which exchange listed which token and, only if the facts give it, when trading opens (otherwise say "
            "nothing about trading time). The post's title or its first two sentences MUST also say whether the token was "
            "already listed on another tracked exchange. If the facts say YES: name each exchange and when it announced, "
            "and never also say 'no earlier listing' or 'not previously listed'. If the facts say NO: say that no earlier "
            "listing appears among the other tracked exchanges SINCE the month the facts give -- the window is part of the "
            "claim, because the record is short and a reader must never take it for 'never listed anywhere'. That prior-listing fact is the point of the post, not a "
            "footnote: it is the one thing a reader cannot get from the announcement itself. After it, say where that leaves "
            "the token (how many of the four tracked exchanges list it now, and how long after the first one this "
            "announcement came) -- real, derived facts, never filler. Do not recite the record's start dates. Write "
            "dates like 'Nov 4'. Report an exchange label (Seed Tag, World Premiere) only as the "
            "exchange's own label."
        ),
        guardrails=(
            "HARD RULES, no exceptions: facts only. No price, price move, market cap, volume or trading performance, and "
            "nothing that predicts or implies one (no 'pump', 'rally', 'surge', 'expected to rise', 'upside'). Never call "
            "this a token's first listing anywhere, its debut, or brand new -- the record covers four exchanges and a "
            "limited period. Say 'no earlier listing among the other tracked exchanges since <month year>' ONLY when the "
            "facts say NO, using the word 'tracked' and the window the facts give. Never write 'first', 'new', 'newly' or "
            "'only' about the token itself. Never say what the project does or whether it is good. Never tell the "
            "reader to buy, trade or position. " + _NO_SCORES
        ),
        # The market-desk paragraph role says "lead with the figures"; a listing has none, so the category states
        # what this dispatch leads with instead (the channel's tone, length and person still apply).
        role_overrides={1: (
            "The whole dispatch in one tight paragraph of one to three short sentences: which exchange listed which "
            "token; then -- within the first two sentences -- whether the token was already listed on another tracked "
            "exchange (name each one and when it announced) or that no earlier listing appears among the tracked "
            "exchanges; then when trading opens, only if the facts give it."
        )},
        title_figures=False,
    ),
    "earning_opportunities": FactSpec(
        audience=("a global, geographically diverse audience of everyday people looking to earn or get something of value -- "
                  "not a crypto audience, not US-only"),
        facts=facts_earning_opportunities,
        content_brief=(
            "Say what the reader gets and who can take part (countries, requirements, deadline) exactly as the facts give them. "
            "Then the steps: ONLY the steps in the facts, numbered, one action each -- if the facts say no steps are available, "
            "write none and say the steps are on the offer's page. Then the catch: what it costs or requires and any limit the "
            "facts state. Where a fact is missing, say plainly that it is not stated."
        ),
        guardrails=(
            "HARD RULES, no exceptions: use only what the facts state -- never invent a step, amount, deadline, country or "
            "requirement. Never promise an outcome ('you will earn', 'guaranteed', 'free money'); say 'up to', 'if selected' or "
            "'eligible' as the facts do, and treat a giveaway as a chance, not a payment. No hype, no urgency, no 'don't miss'. "
            "Never include a URL, a referral link or an invite code (the link is added when the post is forwarded); refer to "
            "'the link'. Never say whether the offer is safe or legitimate -- say what the facts state. " + _NO_SCORES
        ),
        title_figures=False,
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
