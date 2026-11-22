"""Prize structure for earning_opportunities (operator 2026-11-16): guaranteed vs raffle vs competition, shown honestly.

WHY THIS EXISTS. A EUR 25,000 top-3 competition was shown as "expected 176" (pool / entrants). That arithmetic only describes a RAFFLE, where every entry has the same chance.
In a competition the prizes go to the best entries, so the average per entrant is not what anyone gets: the reader needs the prize STRUCTURE and the ODDS.

  guaranteed -- paid to everyone who completes it (bank bonus, cashback, an airdrop for an eligible wallet, a fixed-pay job): the stated amount (x a prior for
                airdrops, see earning.payout_probability);
  raffle     -- a random draw among entrants (giveaways): pool / entrants is meaningful, so it is the only kind that gets an "expected" amount and the $10 floor;
  competition-- ranked / judged prizes (hackathons, bounties with 1st/2nd/3rd, Kaggle-style contests, audit contests): prize structure and odds are SHOWN, no expected
                amount is computed and the expected-payout floor does not apply.

NEVER RELABEL OR CONVERT CURRENCY: every amount shown to the operator or a reader is in the ORIGINAL currency, as stated (EUR stays EUR). The dollar-equivalent
numbers used internally for scoring thresholds are never printed.

Everything here is a pure function of the stored payload.
"""

import math
import re

AWARD_KINDS = ("guaranteed", "raffle", "competition")

# FX used ONLY for internal thresholds (never shown). Same table as earning._FX.
_FX = {"$": 1.0, "usd": 1.0, "€": 1.08, "eur": 1.08, "£": 1.27, "gbp": 1.27, "aud": 0.65, "cad": 0.73, "r$": 0.18, "brl": 0.18, "inr": 0.012, "₹": 0.012}

_ORIG_RE = re.compile(
    r"(?:(?P<cur1>R\$|US\$|A\$|C\$|\$|€|£|₹)\s?(?P<n1>(?:\d{1,3}(?:[  ,]\d{3})+|\d+)(?:\.\d+)?)(?P<k1>[kKmM])?"
    r"|(?P<n2>(?:\d{1,3}(?:[  ,]\d{3})+|\d+)(?:\.\d+)?)(?P<k2>[kKmM])?\s?(?P<cur2>USD|EUR|GBP|AUD|CAD|INR|BRL|USDT|USDC)"
    r"|(?P<n3>(?:\d{1,3}(?:[  ,]\d{3})+|\d+)(?:\.\d+)?)(?P<k3>[kKmM])?\s?(?P<cur3>[$€£])(?!\w))", re.IGNORECASE)


def parse_original(text: str) -> tuple[float, str, str] | None:
    """(amount, symbol, suffix) of the FIRST money amount in the text, exactly as stated: ("$40 000 USD") -> (40000.0, "$", "USD"); ("€20 000 EUR") -> (20000.0, "€", "EUR").
    Never converted. `symbol` is "" when only a code is given."""
    m = _ORIG_RE.search(text or "")
    if not m:
        return None
    n = m.group("n1") or m.group("n2") or m.group("n3")
    k = (m.group("k1") or m.group("k2") or m.group("k3") or "").lower()
    n = re.sub(r"[  ,]", "", n)
    try:
        v = float(n) * {"k": 1e3, "m": 1e6}.get(k, 1)
    except ValueError:
        return None
    sym = (m.group("cur1") or m.group("cur3") or "").strip()
    code = (m.group("cur2") or "").upper()
    tail = re.search(r"\b(USD|EUR|GBP|USDC|USDT|USDG)\b", (text or "")[m.end():m.end() + 6], re.IGNORECASE)
    return v, sym, code or (tail.group(1).upper() if tail else "")


def to_usd(amount: float, symbol: str, suffix: str = "") -> float:
    """Internal-only dollar equivalent for thresholds."""
    key = (symbol or suffix or "$").lower()
    if key in ("usdc", "usdt", "usdg", "dai"):
        return amount
    return amount * _FX.get(key, 1.0)


def fmt(amount: float, symbol: str = "$", suffix: str = "", short: bool = False) -> str:
    """'EUR 12,000' as the ORIGINAL currency. short=True abbreviates thousands (12k) for a prize ladder. A code-only currency is appended ('20,000 EUR')."""
    if short and amount >= 1000:
        v = amount / 1000
        body = f"{v:.1f}".rstrip("0").rstrip(".") + "k"
    else:
        body = f"{amount:,.0f}"
    if symbol:
        return f"{symbol}{body}" + (f" {suffix}" if suffix and not short and suffix.upper() not in ("USD", "EUR", "GBP") else "")
    return f"{body} {suffix}".strip()


def award_kind(payload: dict) -> str:
    """guaranteed | raffle | competition. A collector that read a STRUCTURED field sets payload['award_kind'] and it wins; otherwise it is derived from the type."""
    k = (payload or {}).get("award_kind")
    if k in AWARD_KINDS:
        return k
    t = (payload or {}).get("type")
    if t == "giveaway":
        return "raffle"
    if t == "hackathon" or (payload or {}).get("payout_kind") == "ev":
        return "competition"
    return "guaranteed"


def prize_info(payload: dict) -> dict | None:
    """The stored prize structure (collector-built), or None."""
    pr = (payload or {}).get("prize")
    return pr if isinstance(pr, dict) and pr.get("pool") else None


def top_prize_usd(payload: dict) -> float | None:
    """Internal-only: the largest single prize in dollars, or (with no ladder) the pool / winners, or pool / 3 as a stated prior. For the value score, never shown."""
    pr = prize_info(payload)
    if not pr:
        pool = (payload or {}).get("pool_usd")
        return (pool / 3.0) if pool else None
    sym, suf = pr.get("symbol", ""), pr.get("suffix", "")
    tiers = pr.get("tiers") or []
    if tiers:
        return to_usd(max(tiers), sym, suf)
    winners = pr.get("winners")
    return to_usd(pr["pool"], sym, suf) / (winners if winners else 3)


# A competition whose biggest prize is under this (dollar equivalent, internal only) is a soft-floor failure: it is stored, scored and listed in the daily digest, never a
# card. WITHOUT any size floor a $125 hackathon cleared every floor (a free, global competition earns ~50 value points from friction alone) -- the weakness the expected-payout
# floor was built for, which no longer applies to competitions (operator 2026-11-16). This floor is the assistant's choice, not a ruling: one constant to change.
COMPETITION_MIN_TOP_PRIZE_USD = 100.0


def competition_payout_component(payload: dict) -> float:
    """0-65 for the VALUE score of a competition: the size of the biggest prize on a log scale from $100 (0) to $100,000 (65), so a competition can never outscore a
    guaranteed payout of the same size (you have to WIN it) and a tiny prize earns nothing. No expected amount, no division by entrants."""
    top = top_prize_usd(payload)
    if not top or top <= COMPETITION_MIN_TOP_PRIZE_USD:
        return 0.0
    return round(min(65.0, 65.0 * math.log10(top / COMPETITION_MIN_TOP_PRIZE_USD) / 3.0), 1)


def _winner_count(pr: dict) -> int | None:
    if pr.get("winners"):
        return int(pr["winners"])
    tiers = pr.get("tiers") or []
    if tiers:
        return len(tiers) + int((pr.get("bonus") or {}).get("count") or 0)
    return None


def prize_line(payload: dict, ladder: list[float] | None = None) -> str | None:
    """Prize STRUCTURE and ODDS for a competition, in the original currency, e.g.
         'Top 3 share EUR 25,000 (EUR 12k / EUR 8k / EUR 5k) · ~142 entrants · 3 winners'
    Parts that are not known are left out, never guessed. None for anything that is not a competition."""
    if award_kind(payload) != "competition":
        return None
    pr = prize_info(payload)
    if not pr:
        return None
    sym, suf = pr.get("symbol", ""), pr.get("suffix", "")
    tiers = list(ladder or pr.get("tiers") or [])
    bonus = pr.get("bonus") or {}
    winners = _winner_count({**pr, "tiers": tiers}) if (tiers or pr.get("winners")) else None
    pool = fmt(pr["pool"], sym, suf)
    if tiers and len(tiers) <= 6:
        ladder_txt = " / ".join(fmt(t, sym, suf, short=True) for t in tiers)
        if bonus.get("count"):
            ladder_txt += f", plus {bonus['count']} x {fmt(bonus['amount'], sym, suf, short=True)}"
        head = f"Top {len(tiers)} share {pool} ({ladder_txt})"
    elif winners == 1:
        head = f"One prize: {pool}"
    elif winners:
        head = f"{winners} prizes share {pool}"
    else:
        head = f"{pool} shared among the winners"
    parts = [head]
    if pr.get("entrants") is not None:
        parts.append(f"~{pr['entrants']:,} entrants")
    if winners and (tiers and len(tiers) <= 6):
        parts.append(f"{winners} winner{'s' if winners != 1 else ''}")
    return " · ".join(parts)


_LADDER_RE = re.compile(r"(?:\b(\d)(?:st|nd|rd|th)\b|\b(first|second|third|fourth|fifth)\b)[^$€£\d]{0,25}((?:R\$|US\$|\$|€|£)\s?\d[\d ,.]*(?:\s?[kK])?)", re.IGNORECASE)
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}


def parse_ladder(text: str) -> list[float] | None:
    """Prize amounts by place read from a page's own wording ('1st place $2,500 USD, 2nd $1,500, 3rd $1,000') -> [2500.0, 1500.0, 1000.0], in the original
    currency, only when places 1..n are all present in order. None otherwise (never guessed)."""
    found = {}
    for m in _LADDER_RE.finditer(text or ""):
        place = int(m.group(1)) if m.group(1) else _ORD[m.group(2).lower()]
        got = parse_original(m.group(3))
        if got and place not in found:
            found[place] = got[0]
    if not found or sorted(found) != list(range(1, len(found) + 1)) or len(found) < 2:
        return None
    return [found[i] for i in sorted(found)]


# ---- "skill required" (operator 2026-11-16): professional expertise (ML, engineering, security) -- shown as a fact and it lowers the effort component.
_SKILL_PATTERNS = (
    ("machine learning", re.compile(r"machine learning|deep learning|neural network|data science|\bLLM\b|computer vision|\bNLP\b|train(?:ing)? (?:a |your )?model|predictive model|\bAI agents?\b|\bdatasets?\b", re.IGNORECASE)),
    ("software engineering", re.compile(r"smart contract|solidity|\bRust\b|solana program|build (?:a |an )?(?:working|production)|\bdeploy\w*|\bSDK\b|\bAPI integration|integrat\w+ (?:the |our |with )?\w*\s?(?:API|SDK|protocol)|github repo|repository|full[- ]stack|backend|open[- ]source (?:code|tool)|production onboarding", re.IGNORECASE)),
    ("security", re.compile(r"audit|vulnerabilit|exploit|penetration test|security research|proof[- ]of[- ]concept|bug bount", re.IGNORECASE)),
)
SOURCE_SKILL_DEFAULT = {"zindi": ["machine learning"], "immunefi": ["security"]}


def skill_domains(text: str, source: str | None = None) -> list[str]:
    out = list(SOURCE_SKILL_DEFAULT.get(source or "", []))
    for name, rx in _SKILL_PATTERNS:
        if name not in out and rx.search(text or ""):
            out.append(name)
    return out


def display_for(text: str, usd_value: float | None) -> str | None:
    """The amount in `text` whose dollar equivalent is `usd_value`, formatted in its ORIGINAL currency ('EUR 500' stays EUR). None when no amount in the text
    matches (the caller then falls back to a plain USD figure only if the offer really is in dollars -- it never relabels)."""
    if not usd_value:
        return None
    for m in _ORIG_RE.finditer(text or ""):
        got = parse_original(m.group(0))
        if got and abs(to_usd(got[0], got[1], got[2]) - usd_value) < max(0.6, usd_value * 0.005):
            return fmt(got[0], got[1], got[2])
    return None
