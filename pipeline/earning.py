"""earning_opportunities -- the deterministic core (Phase 5, 2026-11-15).

Anything a reader can take part in to earn money or get something of value: giveaways, free games, signup and
account bonuses, free credits, hackathon prizes, bounties, airdrops. Earn Stack's category (a global, non-crypto
audience), written to by Telegram channels (t.me/s/) and RSS/API sources; Discord comes later.

Everything here is a PURE function of text and stored facts -- no network, no database -- so the gate and both
scores can be tested on real candidates and re-run without side effects. The collectors do the fetching
(RDAP domain age, Safe Browsing, URLhaus, GoPlus) and store the results in the item's payload; the scorer only reads
the payload.

THE RULES (operator-approved 2026-11-15):
  1. A deterministic GATE runs first and excludes outright: seed-phrase / private-key requests, connect-wallet-to-claim,
     and any upfront payment to take part (a fee, "gas to claim", "send X to receive Y"). Capital you deposit and keep
     (a bank-bonus deposit) is not a fee: it is the VALUE score's `capital` component.
  2. TRUST and VALUE are two separate numbers and are NEVER blended. trust = checkable facts about the source and the
     offer; value = what it is worth a reader's time. Selection is trust >= 40 AND value >= 40 AND expected payout >= $10 (unknown passes), ordered by value
     (trust is a gate, not a ranking dimension). `scores.score` is set to the VALUE only; trust lives in
     score_breakdown, and every card shows both.
  3. Trust is facts, not judgment: source tier, independent corroboration, whether terms are stated (deadline /
     eligibility / payout), domain age (RDAP), Safe Browsing, URLhaus, GoPlus for a token contract. The "is this
     real" call is the operator's, by hand. (Two checks that need a search API -- "does the official domain confirm
     it" and "scam reports found" -- were dropped on purpose; see BACKLOG.)
  4. Cross-link: an absurd payout-for-nothing ratio PENALISES TRUST, it does not just look attractive -- the same
     lesson as the 462% APY pools.
  5. Referral (operator overrides 2026-11-16 and 2026-11-17): a referral program the reader can earn from adds up to REFERRAL_VALUE_MAX (12) points to VALUE; a stated per-referral earning ("$15 per friend")
     is read as the offer's PAYOUT when the offer states no other; multi-level wording (downline, MLM) is a WARNING LABEL on the card with no score effect. There is NO recruitment penalty and NO referral-promotion
     detection: they were the assistant's recommendation, the operator overrode them, and they must not be restored. Referral parameters are still stripped from every stored link (the operator adds his own).
"""

import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pipeline import prizes  # noqa: E402  (award kinds, prize structure, skill -- pure functions)

TRUST_FLOOR = 40.0
VALUE_FLOOR = 40.0
# A THIRD hard floor, not a weight (operator 2026-11-16): value 40 is reachable with ZERO payout, because a free, global, no-KYC offer earns ~50
# points from friction alone (capital 100, prereq 100, geo 100). An expected payout under $10 surfaces nothing. Unknown passes (see expected_payout_ok).
EXPECTED_PAYOUT_FLOOR = 10.0

# ----------------------------------------------------------------------------- sources

# tier 1 = the offer's own platform announcing it (an official API); tier 2 = a curated or structured aggregator
# with editorial or data discipline; tier 3 = an open aggregator / Telegram channel anyone can post to.
SOURCES = {
    "devpost": {"name": "Devpost", "tier": 2, "kind": "api", "default_type": "hackathon"},
    "doctorofcredit": {"name": "Doctor of Credit", "tier": 2, "kind": "rss", "default_type": "bank_bonus"},
    # Platforms that pay for work or skill (added 2026-11-16, keyless JSON, verified from Railway). Tier 2 like Devpost: a platform that hosts
    # third-party sponsors' prizes, not the sponsor itself.
    "superteam": {"name": "Superteam Earn", "tier": 2, "kind": "api", "default_type": "bounty"},
    "zindi": {"name": "Zindi", "tier": 2, "kind": "api", "default_type": "hackathon"},
    "immunefi": {"name": "Immunefi audit competitions", "tier": 2, "kind": "api", "default_type": "bounty"},
    # Exchange campaigns and Galxe (operator 2026-11-17; KYC is not a blocker). Tier 1 = the exchange's own official API; Galxe is tier 3 (anyone can create a campaign) and keeps only
    # campaigns with a STATED reward. Earn Stack candidates, separate from exchange_listings.
    "binance_campaigns": {"name": "Binance campaigns", "tier": 1, "kind": "text_api", "default_type": "giveaway"},
    "kucoin_campaigns": {"name": "KuCoin campaigns", "tier": 1, "kind": "text_api", "default_type": "giveaway"},
    "galxe": {"name": "Galxe", "tier": 3, "kind": "text_api", "default_type": "airdrop"},
    # Official exchange Telegram channels (tier 2: the organisation's own account), campaign posts only (collectors.earning_opportunities.CAMPAIGN_ONLY_SOURCES)
    "tg:OKXAnnouncements": {"name": "OKX Announcements (Telegram)", "tier": 2, "kind": "telegram", "default_type": "giveaway"},
    "tg:Bybit_Announcements": {"name": "Bybit Announcements (Telegram)", "tier": 2, "kind": "telegram", "default_type": "giveaway"},
    "tg:airdropfind": {"name": "Airdrop Finder (Telegram)", "tier": 3, "kind": "telegram", "default_type": "airdrop"},
    "tg:airdrops_io": {"name": "AIRDROPS.IO (Telegram)", "tier": 3, "kind": "telegram", "default_type": "airdrop"},
    "tg:incrypted_airdrops": {"name": "Incrypted Airdrops (Telegram)", "tier": 3, "kind": "telegram", "default_type": "airdrop"},
    "tg:criptinho": {"name": "Criptinho (Telegram)", "tier": 3, "kind": "telegram", "default_type": "airdrop"},
    # Added 2026-11-17 (operator list from third-party research, each verified FROM RAILWAY first: fresh posts, 0.1-1.3 a day). Tier 3: open channels anyone can post to.
    "tg:airdropinspector": {"name": "Airdrop Inspector (Telegram)", "tier": 3, "kind": "telegram", "default_type": "airdrop"},
    "tg:AirdropDetective": {"name": "Airdrop Detective (Telegram)", "tier": 3, "kind": "telegram", "default_type": "airdrop"},
    "tg:airdropking_io": {"name": "Airdrop King (Telegram)", "tier": 3, "kind": "telegram", "default_type": "airdrop"},
    "tg:LegitAirdrops": {"name": "Legit Airdrops (Telegram)", "tier": 3, "kind": "telegram", "default_type": "airdrop"},
    "tg:cryptoairdrops": {"name": "Crypto Airdrops (Telegram)", "tier": 3, "kind": "telegram", "default_type": "airdrop"},
}
# SCOPE RULE (operator ruling 2026-11-15): money INTO the reader's pocket, not money SAVED. A discount is a cheaper purchase and a free
# game is a free purchase -- neither is earning. REMOVED, not filtered: Epic Games Store, GamerPower, OzBargain, Dealabs, MyDealz and the
# three free-games Telegram channels (EpicSteamGames, FreeGamesNews, r_FreeGamesOnSteam) no longer have collectors, so no calls are spent
# on them. In scope: airdrops, hackathons / bounties, giveaways with cash or crypto prizes, signup and deposit bonuses, paid beta
# testing, bug bounties, paid research and surveys, and cashback that pays out in money (never via a referral).
# Anything that still slips in from an in-scope source (a "free credit", a freebie) is gated by NON_INCOME_TYPES below.
NON_INCOME_TYPES = {"free_game", "freebie", "credits"}

# Used only to CORROBORATE (a campaign they also announce raises trust) and never as a source of opportunities: they
# overlap exchange_listings and Crypto Wall Street, and the same event must not reach two channels by two paths.
CORROBORATION_ONLY_TELEGRAM = ["OKXAnnouncements", "Bybit_Announcements", "CoinMarketCapAnnouncements"]

TELEGRAM_HANDLES = [k[3:] for k, v in SOURCES.items() if v["kind"] == "telegram"]

# ----------------------------------------------------------------------------- links

_TRACKING = {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "igshid", "yclid", "_ga", "ref_src"}
_REFERRAL = {"ref", "referral", "referrer", "ref_code", "refcode", "referral_code", "r", "rid", "aff", "affiliate", "aff_id",
             "invite", "invite_code", "invitecode", "inviter", "via", "rcode", "sponsor", "partner", "tag", "subid", "irclickid", "clickid"}
_REFERRAL_PATH_RE = re.compile(r"/(?:r|ref|refer|referral|invite|invites|join)/[^/?#]+", re.IGNORECASE)
_SOCIAL_AGGREGATOR_HOSTS = ("t.me", "telegram.me", "telegram.org", "twitter.com", "x.com", "facebook.com", "instagram.com",
                            "youtube.com", "youtu.be", "reddit.com", "discord.gg", "discord.com", "bit.ly", "t.co")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def is_referral_link(url: str) -> bool:
    """Display-only: did the link carry a referral parameter or path? Never an input to any score."""
    if not url:
        return False
    p = urlparse(url)
    if _REFERRAL_PATH_RE.search(p.path or ""):
        return True
    return any(k.lower() in _REFERRAL for k, _ in parse_qsl(p.query, keep_blank_values=True))


def normalize_url(url: str) -> str:
    """Host lower-cased without www., tracking and REFERRAL parameters dropped, fragment dropped, no trailing slash."""
    p = urlparse((url or "").strip())
    host = (p.netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    keep = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in _TRACKING and k.lower() not in _REFERRAL]
    path = _REFERRAL_PATH_RE.sub("", p.path or "").rstrip("/")
    return urlunparse((p.scheme or "https", host, path, "", urlencode(keep), ""))


def domain_of(url: str) -> str | None:
    host = (urlparse(url or "").netloc or "").lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    return host or None


def registrable_domain(host: str | None) -> str | None:
    """Last two labels (three for common two-part public suffixes) -- good enough for an RDAP lookup key."""
    if not host:
        return None
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "gov", "ac"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def pick_landing_url(links: list[str]) -> str | None:
    """The first link that is not social or a link-shortener; the landing page a reader would actually act on."""
    for link in links:
        host = domain_of(link) or ""
        if not any(host == h or host.endswith("." + h) for h in _SOCIAL_AGGREGATOR_HOSTS):
            return link
    return None


def find_urls(text: str) -> list[str]:
    return [u.rstrip(".,;:!?") for u in _URL_RE.findall(text or "")]


# ----------------------------------------------------------------------------- the gate

_SEED_RE = re.compile(r"seed ?phrase|recovery ?phrase|secret recovery|mnemonic|\b(?:12|24)[- ]word|private ?key|keystore|import (?:your )?wallet", re.IGNORECASE)
_WALLET_CLAIM_RE = re.compile(
    r"connect (?:your |a |the )?(?:crypto |web3 )?wallet|wallet ?connect|link (?:your )?wallet (?:to|and) claim|"
    r"claim (?:your |the )?(?:airdrop|tokens?|reward|drop)s?[^.\n]{0,40}(?:connect|wallet)|(?:connect|wallet)[^.\n]{0,40}claim", re.IGNORECASE)
_FEE_RE = re.compile(
    r"(?:pay|send|deposit|transfer)\s+(?:a\s+|the\s+|some\s+)?(?:small\s+|tiny\s+|minimal\s+)?(?:gas|network|activation|registration|processing|"
    r"unlock|verification|withdrawal|entry|admin|release|insurance|tax|customs|shipping and handling)\s+(?:fees?|charges?|costs?)|"
    r"(?:gas|activation|registration|processing|unlock|verification|withdrawal|entry|participation|claim|release)\s+fee\s+(?:is |are )?(?:required|needed|to (?:claim|receive|withdraw|participate|unlock))|"
    r"(?:entry|registration|participation|activation|claiming) fee\b|"
    r"\bsend\s+(?:\d[\d.,]*\s*)?(?:eth|btc|usdt|usdc|bnb|sol|tokens?|coins?)\b[^.\n]{0,50}\b(?:to receive|to get|to claim|and (?:receive|get)|back)\b|"
    r"\b(?:double|2x|10x|triple) your (?:crypto|eth|btc|usdt|bnb|sol|coins?|deposit|money)\b|"
    r"\bpay\s+(?:\$|€|£)?\d[\d.,]*\s*(?:usd|usdt|eur|gbp)?\s+(?:to (?:claim|receive|unlock|participate|enter|join|withdraw)|and (?:you|get|receive|win))", re.IGNORECASE)


def not_income_reason(type_: str) -> str | None:
    """Why an item is out of scope as 'not income' (it saves the reader money; it does not pay them), or None."""
    return "not income: saves money, does not pay it (free game / freebie / credit)" if type_ in NON_INCOME_TYPES else None


# The narrow wallet rule applies ONLY to offers HOSTED on a vetted platform itself (operator 2026-11-16): the platform's own listing, written by a sponsor
# the platform vets. It does NOT apply to an aggregator or a channel RELAYING someone else's text (airdrops_io, incrypted, airdropfind, criptinho -- all
# tier 3 -- nor to Doctor of Credit, a curated blog, nor to Devpost): for those the broad rule stays. On a hosted platform listing "connect your wallet and
# grab your referral link" or "... and sign a message" is a step, not the drainer pattern; a connect-to-CLAIM / connect-to-RECEIVE construction still
# trips everywhere. The trigger is the SOURCE ID, not the tier: a new tier-2 source never inherits the relaxation by accident.
GATE_NARROW_WALLET_SOURCES = frozenset({"superteam", "zindi", "immunefi"})
_WALLET_CLAIM_NARROW_RE = re.compile(
    r"(?:connect|link)\s+(?:your |a |the )?(?:crypto |web3 )?wallet\s+(?:to|and|in order to|so (?:you|that))\s+(?:\w+\s+){0,2}(?:claim|receive|collect|redeem|withdraw)|"
    r"claim\s+(?:your |the )?(?:airdrop|tokens?|reward|drop|prize)s?[^.\n]{0,40}(?:connect|wallet)|"
    r"(?:connect|wallet)[^.\n]{0,40}\b(?:to claim|to receive|to collect|to redeem|claim your|receive your|collect your)", re.IGNORECASE)


def gate(text: str, source: str | None = None) -> list[str]:
    """Reasons this item is excluded before any scoring; [] means it passes. Deterministic and boring on purpose."""
    reasons = []
    if _SEED_RE.search(text or ""):
        reasons.append("asks for a seed phrase / private key / wallet import")
    wallet_re = _WALLET_CLAIM_NARROW_RE if source in GATE_NARROW_WALLET_SOURCES else _WALLET_CLAIM_RE
    if wallet_re.search(text or ""):
        reasons.append("connect-wallet-to-claim")
    if _FEE_RE.search(text or ""):
        reasons.append("upfront payment to take part")
    return reasons


# ----------------------------------------------------------------------------- extraction

_FX = {"$": 1.0, "usd": 1.0, "us$": 1.0, "€": 1.08, "eur": 1.08, "£": 1.27, "gbp": 1.27, "aud": 0.65, "a$": 0.65, "cad": 0.73,
       "c$": 0.73, "r$": 0.18, "brl": 0.18, "inr": 0.012, "₹": 0.012, "rs": 0.012}
_AMOUNT_RE = re.compile(
    r"(?P<up>up to |worth |over |\+ ?)?(?:(?P<cur1>R\$|US\$|A\$|C\$|\$|€|£|₹|USD |EUR |GBP |AUD |CAD |INR |Rs\.? ?)(?P<n1>(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d[\d,]*)(?:\.\d+)?)(?P<k1>[kKmM])?"
    r"|(?P<n2>(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d[\d,]*)(?:\.\d+)?)(?P<k2>[kKmM])? ?(?P<cur2>USD|EUR|GBP|AUD|CAD|INR|BRL|USDT|USDC)"
    r"|(?P<n3>(?:\d{1,3}(?:[  ]\d{3})+|\d[\d,]*)(?:\.\d+)?)(?P<k3>[kKmM])?\s?(?P<cur3>[$€£])(?!\w))", re.IGNORECASE)   # "50$" (Russian / Portuguese style: the sign AFTER the number)


def _to_number(n: str) -> float:
    """'4 000' -> 4000, '1,500' -> 1500, '10.000' -> 10000 (dot thousands: exactly three digits after each dot), '1.50' -> 1.5."""
    n = re.sub(r"[\s\u00a0]", "", n)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", n):
        n = n.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+,\d{1,2}", n):
        n = n.replace(",", ".")
    else:
        n = n.replace(",", "")
    return float(n)


def extract_amounts_usd(text: str) -> list[float]:
    out = []
    for m in _AMOUNT_RE.finditer(text or ""):
        cur = (m.group("cur1") or m.group("cur2") or m.group("cur3") or "$").strip().lower().rstrip(".")
        n = m.group("n1") or m.group("n2") or m.group("n3")
        k = (m.group("k1") or m.group("k2") or m.group("k3") or "").lower()
        try:
            v = _to_number(n)
        except ValueError:
            continue
        v *= {"k": 1e3, "m": 1e6}.get(k, 1)
        rate = _FX.get(cur, 1.0 if cur in ("usdt", "usdc") else None)
        if rate is None:
            continue
        out.append(round(v * rate, 2))
    return out


_REWARD_BEFORE_RE = re.compile(r"(?:win|won|prize|prizes|reward|rewards|bonus|earn|earning|get|receive|receiving|credit|credits|gift ?card|voucher|cash ?back|worth|valued? at|up to|free|claim|giveaway|pool of|total of|приз\w*|наград\w*|бонус\w*|получ\w*|выигра\w*|раздел\w*|пул\w*|pr[eê]mio\w*|recompensa\w*|b[oô]nus|ganhe|ganhar|receba|receber|giving away|give away|разыгрыва\w*|забира\w*|заб[её]рем|сумму|pr[eê]mio total|pool de|sorteio de|каждому\w*|каждого|cada)(?:\W+\w{1,9}){0,2}\W{0,3}[^\w$€£]{0,25}$", re.IGNORECASE)
_REWARD_AFTER_RE = re.compile(r"^\s{0,2}(?:/\w+ )?(?:bonus|credits?|reward|rewards|prize|prizes|gift ?card|voucher|cash ?back|in (?:prizes|rewards|credits|tokens|bonuses)|worth|value|giveaway|airdrop|free)|бонус\w*|наград\w*|приз\w*|в токен\w+|pr[eê]mio\w*|b[oô]nus|recompensa\w*|em pr[eê]mios|shared|distributed|up for grabs|split|pool|each|per (?:person|winner|user)|разыгрываем|за каждого|cada|por pessoa", re.IGNORECASE)
_NOT_REWARD_RE = re.compile(r"raise[ds]?|привлек\w*|раунд\w*|инвестиц\w*|оценк\w*|капитализац\w*|объ[её]м\w*|стоимост\w*|levantou|rodada|avalia[cç][aã]o|pre[cç]o|funding|valuation|tvl|market ?cap|\bfdv\b|\bround\b|series [a-d]\b|volume|investors?|\bprice[ds]?\b|\bcosts?\b|msrp|\brrp\b|\bwas \W|\bonly \W|\bjust \W|\bsave\b|saving|\boff\b|per month|/mo\b|a month|subscription|deposit|minimum|balance|spend|purchase|\bbuy\b|\bfrom \W|\bnow \W|\bfor \W|instead of", re.IGNORECASE)


def extract_reward_usd(text: str, type_: str = "other") -> float | None:
    """The amount a reader RECEIVES, not any dollar figure in the text. An amount counts only with a reward cue next
    to it (win / prize / bonus / get / credit / worth / up to ...) and no cost, price or funding cue (raised, TVL, price,
    deposit, spend ...). Without this an airdrop news post mentioning "$20M raised" scored a $20 million payout.
    Figures of $1M or more are not a per-person reward outside a hackathon (they are pools): ignored, i.e. unknown."""
    t = text or ""
    best = None
    for m in _AMOUNT_RE.finditer(t):
        before, after = t[max(0, m.start() - 30):m.start()], t[m.end():m.end() + 24]
        around = t[max(0, m.start() - 30):m.end() + 24]
        if _NOT_REWARD_RE.search(before) or _NOT_REWARD_RE.search(after[:16]):
            continue
        if not (_REWARD_BEFORE_RE.search(before) or _REWARD_AFTER_RE.search(after)):
            continue
        vals = extract_amounts_usd(m.group(0))
        if not vals:
            continue
        v = vals[0]
        if v >= 1_000_000 and type_ != "hackathon":
            continue
        best = v if best is None else max(best, v)
    return best


# ---- What KIND of amount is it? (operator, 2026-11-15: "the cross-penalty is the most aggressive rule in the system and it fires
# on a value-derived signal -- any misread of value silently destroys trust"). The payout-for-nothing penalty may fire ONLY on an
# amount that is unambiguously what ONE reader receives (or a stated maximum -- a deliberate choice, see BACKLOG). A pool, a total, an
# amount split among N winners, or anything unclear is recorded as such and can neither inflate value nor cost trust.
_POOL_RE = re.compile(
    r"\bpool\b|\bprize fund\b|\btotal (?:rewards?|prizes?|prize|pool|of|payout|value)\b|\b(?:in|worth of) (?:prizes?|rewards?|tokens?|bonuses|total)\b|"
    r"\bshare[ds]? (?:among|between|by|with)\b|\bsplit\b|\bdistribut\w+|\bup for grabs\b|\bgiv(?:e|ing) away\b.{0,25}\b(?:among|between|to)\b|\bto be shared\b|"
    r"призов\w+ фонд\w*|\bфонд\w* наград\w*|\bпул\b|общ\w+ (?:пул|фонд|сумм\w+)|раздел\w+|разыгр\w+|поровну|между (?:всеми|участник\w+|победител\w+)|\bна сумму\b|в наградах|в призах|\bвсего\b|"
    r"pr[eê]mio total|premia[cç][aã]o|\bpool de\b|distribu[ií]\w+|dividid\w+|\bdividir\b|sorte\w+ (?:entre|para)|em pr[eê]mios|em recompensas|\bsorteio\b", re.IGNORECASE)
_WINNERS_RE = re.compile(
    r"\b(?:for|to|among|between|across)\s+(?:the\s+)?(?:top\s+)?\d[\d,]*\s+(?:winners?|lucky|users?|participants?|people|traders?|creators?|holders?)\b|"
    r"\b\d[\d,]*\s+winners?\b|\d[\d\s]*\s*победител\w+|\bдля\s+\d[\d\s]*\s+(?:человек|участник\w+|пользовател\w+)|"
    r"\b\d[\d.]*\s+(?:vencedores?|ganhadores?|sortead\w+)|\bpara\s+\d[\d.]*\s+(?:vencedores?|pessoas|usu[aá]rios)", re.IGNORECASE)
_PER_PERSON_RE = re.compile(
    r"\beach\b|\bper (?:person|winner|user|participant|referral|account)\b|\bevery (?:user|winner|participant)\b|\ba piece\b|"
    r"\bкаждому\b|\bкаждого\b|\bна каждого\b|\bза каждого\b|\bпо \$|\bcada\b|\bpor (?:pessoa|usu[aá]rio|conta|indica[cç][aã]o)\b", re.IGNORECASE)
_MAX_BEFORE_RE = re.compile(r"(?:up to|upto|as much as|до|вплоть до|at[eé]|ate|hasta|m[aá]x(?:imum|imo)?|макс\w*)\W{0,3}[^\w$€£]{0,6}$", re.IGNORECASE)


def extract_reward(text: str, type_: str = "other") -> tuple[float | None, str | None]:
    """(amount, kind), kind one of:
       'stated'  -- what one reader receives ("a $500 gift card", "$250 bonus", "каждому по $50")  -> may be penalised
       'max'     -- a stated maximum ("up to $15,000", "до $15 000")                              -> may be penalised (deliberate)
       'pool'    -- a total shared among participants ("$50,000 prize pool", "разделяем $70 000")  -> never penalised, never a payout
       'unclear' -- an amount tied to N winners, where per-person vs total cannot be told         -> never penalised, never a payout
    Precedence: an explicit per-person cue wins; then pool/total; then a winners count; then a maximum; else stated."""
    amount = extract_reward_usd(text, type_)
    if amount is None:
        return None, None
    t = text or ""
    for m in _AMOUNT_RE.finditer(t):
        vals = extract_amounts_usd(m.group(0))
        if not (vals and abs(vals[0] - amount) < 0.5):
            continue
        before, after = t[max(0, m.start() - 45):m.start()], t[m.end():m.end() + 45]
        around = before + m.group(0) + after
        if (m.group("up") or "").strip().lower() == "up to" and not _PER_PERSON_RE.search(after[:25]) and not _POOL_RE.search(around) and not _WINNERS_RE.search(around):
            return amount, "max"
        if _PER_PERSON_RE.search(after[:25]) or _PER_PERSON_RE.search(before[-14:]):
            return amount, "stated"
        if _POOL_RE.search(around):
            return amount, "pool"
        if _WINNERS_RE.search(around):
            return amount, "unclear"
        if _MAX_BEFORE_RE.search(before[-18:]):
            return amount, "max"
        return amount, "stated"
    return amount, "stated"


# ---- Roundups are not offers (operator ruling 2026-11-15): "a reader can't participate in 'Daily Roundup'". A digest of several
# projects was being scored as ONE offer, taking the first linked project's domain facts -- the same class of error as scoring
# airdrops.io's domain instead of the project's. Detected on the title (and first line): such a post is stored held, with the reason, and
# never becomes a candidate. A single offer inside a roundup reaches us through the project's own channel or a second source.
_ROUNDUP_RE = re.compile(
    r"round-?up|\bdigest\b|\brecap\b|\bradar\b|weekly (?:radar|digest|recap|summary|update)|daily (?:radar|digest|recap|summary|update)|"
    r"\b\d+\s+(?:\w+\s+){0,2}(?:airdrops|projects|campaigns|tasks|opportunities)\b|\btop[- ]\d+\b|\blist of\b|\bour picks\b|"
    r"подборк\w*|отжимк\w*|дайджест\w*|итоги (?:недели|дня|месяца)|\bобзор\w*|\bсводк\w*|"
    r"resumo (?:semanal|di[aá]rio|da semana)|\bcompila[cç][aã]o\b|\bsemanal\b.*\bresumo\b|\bdestaques\b", re.IGNORECASE)


def is_roundup(title: str, text: str = "") -> bool:
    """True for a digest / roundup / list of several projects (or an article about one, "Обзор биржи"): not a single opportunity."""
    first_line = (text or "").strip().split("\n")[0][:140]
    return bool(_ROUNDUP_RE.search(title or "") or _ROUNDUP_RE.search(first_line))


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DEADLINE_CUE = r"(?:until|till|ends?|ending|expires?|expiring|deadline|valid (?:until|through)|through|by|before|only)"
_DATE_TEXT_RE = re.compile(
    rf"{_DEADLINE_CUE}[^0-9a-z]{{0,6}}(?:on )?(?P<d1>\d{{1,2}})(?:st|nd|rd|th)? (?P<m1>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:,? (?P<y1>20\d\d))?|"
    rf"{_DEADLINE_CUE}[^0-9a-z]{{0,6}}(?:on )?(?P<m2>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (?P<d2>\d{{1,2}})(?:st|nd|rd|th)?(?:,? (?P<y2>20\d\d))?|"
    rf"{_DEADLINE_CUE}[^0-9a-z]{{0,6}}(?P<iso>20\d\d-\d\d-\d\d)|"
    rf"{_DEADLINE_CUE}[^0-9a-z]{{0,6}}(?P<m3>\d{{1,2}})/(?P<d3>\d{{1,2}})(?:/(?P<y3>20\d\d|\d\d))?", re.IGNORECASE)
_RANGE_END_RE = re.compile(r"(?P<m>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}\s*[-–]\s*(?P<d>\d{1,2})(?:,? (?P<y>20\d\d))?", re.IGNORECASE)
# "Oct 30 - Dec 04, 2026": the END of a two-month range
_RANGE2_RE = re.compile(r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}\s*[-–]\s*(?P<m>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (?P<d>\d{1,2})(?:,? (?P<y>20\d\d))?", re.IGNORECASE)
# "(11/14 Only)": the cue comes AFTER the date
_ONLY_RE = re.compile(r"(?P<m>\d{1,2})/(?P<d>\d{1,2})\s*only", re.IGNORECASE)


# ---- Multilingual term detection (operator ruling 2026-11-15: "0 of 34 Russian items scored any 'terms stated' -- that is a
# detector gap, not an absence of terms"). Russian and Portuguese patterns for the three things "terms stated" asks: a
# deadline, who can take part, and what is paid. Deterministic on purpose; if recall on real items stays poor, a narrow cheap
# LLM extraction (structured output, Python decides what it means) is the sanctioned fallback.
_RU_MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}
_PT_MONTHS = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
_RU_MONTH_RE = r"(?:январ[яь]|феврал[яь]|марта?|апрел[яь]|ма[яй]|июн[яь]|июл[яь]|август[а]?|сентябр[яь]|октябр[яь]|ноябр[яь]|декабр[яь])"
_PT_MONTH_RE = r"(?:janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)"
_RU_DEADLINE_RE = re.compile(
    r"(?:до|по|дедлайн|заканчива\w+|законч\w+|завершит\w+|окончани\w+|срок\w*|успе\w+)\W{0,10}(?:до\W{0,3})?(?P<d>\d{1,2})\s+(?P<m>" + _RU_MONTH_RE + r")(?:\s+(?P<y>20\d\d))?|"
    r"(?:до|по|дедлайн|срок\w*)\W{0,6}(?P<d2>\d{1,2})\.(?P<m2>\d{1,2})(?:\.(?P<y2>20\d\d|\d\d))?", re.IGNORECASE)
_PT_DEADLINE_RE = re.compile(
    r"(?:at[eé]|prazo|termina|encerra\w*|v[aá]lido at[eé]|acaba)\W{0,12}(?:dia\s+)?(?P<d>\d{1,2})(?:\s+de\s+|\s+)(?P<m>" + _PT_MONTH_RE + r")(?:\s+de\s+(?P<y>20\d\d))?|"
    r"(?:at[eé]|prazo|termina|encerra\w*)\W{0,8}(?:dia\s+)?(?P<d2>\d{1,2})/(?P<m2>\d{1,2})(?:/(?P<y2>20\d\d|\d\d))?", re.IGNORECASE)


def _deadline_multilingual(text: str) -> tuple[int, int, int | None] | None:
    """(month, day, year-or-None) from a Russian or Portuguese deadline phrase, or None."""
    for rx, months in ((_RU_DEADLINE_RE, _RU_MONTHS), (_PT_DEADLINE_RE, _PT_MONTHS)):
        m = rx.search(text or "")
        if not m:
            continue
        if m.group("m"):
            mo = months.get(m.group("m").lower()[:3].replace("ç", "c"))
            return (mo, int(m.group("d")), int(m.group("y")) if m.group("y") else None) if mo else None
        mo, d = int(m.group("m2")), int(m.group("d2"))
        y = int(m.group("y2")) if m.group("y2") else None
        if y is not None and y < 100:
            y += 2000
        if 1 <= mo <= 12:
            return (mo, d, y)
    return None


_ELIGIBILITY_RE = re.compile(
    r"\b(?:eligib|resident|18\+|21\+|must be|requires?|required)|"
    r"только для|жител|резидент|необходим|требует|нужно|для пользовател|доступн\w+ (?:в|для)|участв\w+ могут|верификаци|\bкус\b|"
    r"apenas para|somente para|residentes|requisito|necess[aá]rio|precisa|[eé] preciso|dispon[ií]vel (?:para|em)|maiores de 18|verifica[cç][aã]o", re.IGNORECASE)
_PAYOUT_UNIT_RE = re.compile(
    r"\d[\d\s.,]*\s*(?:tokens?|токен\w*|points?|поинт\w*|очк\w*|\bxp\b|usdt|usdc|usd|bnb|eth|sol)(?!\w)|"
    r"\d[\d\s.,]*\s*(?:\$|€|£|%)|"
    r"(?:\$|€|£)\s?\d[\d\s.,]*", re.IGNORECASE)
_PAYOUT_CUE_RE = re.compile(
    r"reward|prize|bonus|airdrop|pool|earn|win\b|giveaway|credit|"
    r"награ\w+|приз\w*|бонус\w*|пул\b|раздел\w+|разыгр\w+|получ\w+|дроп\w*|аирдроп\w*|"
    r"pr[eê]mio|recompensa|b[oô]nus|ganhe|ganhar|receba|sorteio|distribui\w+", re.IGNORECASE)


def has_eligibility(text: str, geo: str | None = None) -> bool:
    """Does the text say who can take part (or does it state a geography)?"""
    return (geo not in (None, "unknown")) or bool(_ELIGIBILITY_RE.search(text or ""))


def payout_stated(text: str) -> bool:
    """Does the text state WHAT is paid, in a currency or a token/point quantity, next to a reward word -- in any of the
    three languages? (The USD value may be unknowable for a token amount; the fact that a payout is stated still counts.)"""
    t = text or ""
    return bool(_PAYOUT_UNIT_RE.search(t) and _PAYOUT_CUE_RE.search(t))


def extract_deadline(text: str, now: datetime) -> datetime | None:
    """The stated deadline as an end-of-day UTC datetime, or None. A date without a year is taken as the next
    occurrence, never a past one more than a week old."""
    text = text or ""
    m = _DATE_TEXT_RE.search(text)
    y = mo = d = None
    if m:
        if m.group("iso"):
            y, mo, d = (int(x) for x in m.group("iso").split("-"))
        elif m.group("m1"):
            mo, d, y = _MONTHS[m.group("m1").lower()[:3]], int(m.group("d1")), int(m.group("y1")) if m.group("y1") else None
        elif m.group("m2"):
            mo, d, y = _MONTHS[m.group("m2").lower()[:3]], int(m.group("d2")), int(m.group("y2")) if m.group("y2") else None
        elif m.group("m3"):
            mo, d = int(m.group("m3")), int(m.group("d3"))
            y = int(m.group("y3")) if m.group("y3") else None
            if y is not None and y < 100:
                y += 2000
            if mo > 12 and d <= 12:          # day/month order
                mo, d = d, mo
    else:
        r = _RANGE2_RE.search(text) or _RANGE_END_RE.search(text)
        o = _ONLY_RE.search(text)
        if r:
            mo, d, y = _MONTHS[r.group("m").lower()[:3]], int(r.group("d")), int(r.group("y")) if r.group("y") else None
        elif o:
            mo, d = int(o.group("m")), int(o.group("d"))
    if not (mo and d):
        ml = _deadline_multilingual(text)
        if ml:
            mo, d, y = ml
    if not (mo and d and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    try:
        if y is None:
            y = now.year
            cand = datetime(y, mo, d, 23, 59, tzinfo=timezone.utc)
            if cand < now - timedelta(days=7):
                cand = datetime(y + 1, mo, d, 23, 59, tzinfo=timezone.utc)
            return cand
        return datetime(y, mo, d, 23, 59, tzinfo=timezone.utc)
    except ValueError:
        return None


_TYPE_RULES = [
    ("bank_bonus", r"\b(?:checking|savings|brokerage|bank|account|credit card|card|invest(?:ing|ment)?|ira|529) (?:bonus|offer|promo)|\bbonus\b.*\b(?:direct deposit|checking|account)\b|sign[- ]?up bonus"),
    ("hackathon", r"hackathon|buildathon|competition|contest|challenge|prize pool|devpost"),
    ("bounty", r"bug ?bounty|bounty|immunefi|hackerone|vulnerabilit"),
    ("airdrop", r"airdrop|retrodrop|points? program|testnet|token (?:launch|claim)|claim (?:your )?(?:tokens?|drop)"),
    ("free_game", r"free (?:game|games|to keep|dlc|pc game|steam|epic|gog|key)|\b100% off\b|giveaway.*(?:steam|epic|gog|key)|loot|free on (?:steam|epic|gog|prime)"),
    ("credits", r"free credits?|\bcredits?\b.*\b(?:free|bonus|\$)|api credits|free tier|trial credits|startup credits"),
    ("cashback", r"cash ?back|rebate|% back"),
    ("giveaway", r"giveaway|sweepstakes?|raffle|win (?:a|an|\$|up)|enter to win"),
    ("freebie", r"\bfree\b|gratis|gratuit|kostenlos|freebie|sample"),
]


def classify_type(text: str, default: str | None = None) -> str:
    """Strong keyword rules first. 'freebie' (the bare word "free") is weak: it never overrides a source's own default
    type (an airdrop channel's post that says "free" is still an airdrop)."""
    t = (text or "").lower()
    for name, pat in _TYPE_RULES:
        if re.search(pat, t):
            if name == "freebie" and default and default != "freebie":
                return default
            return name
    return default or "other"


_TITLE_NOISE = {"the", "a", "an", "is", "are", "free", "giveaway", "key", "keys", "steam", "epic", "games", "game", "store", "gog", "on", "for", "to",
                "get", "your", "claim", "now", "bundle", "pack", "dlc", "and", "of", "in", "with", "itch", "io", "indiegala", "stove", "prime", "gaming",
                "limited", "time", "offer", "airdrop", "new"}


def title_key(title: str, type_: str) -> str:
    """Stable key for 'the same offer announced twice' -- Epic announcing a game and GamerPower relaying it, two channels
    posting the same campaign. Significant words only, order-free."""
    words = sorted({w for w in re.findall(r"[a-z0-9]{3,}", (title or "").lower()) if w not in _TITLE_NOISE})[:5]
    return f"{type_}:{'-'.join(words)}"


_WORLDWIDE_RE = re.compile(r"worldwide|world[- ]wide|global(?:ly)?|all countries|international(?:ly)?|anywhere in the world|open to (?:all|everyone)|no (?:geo|region)[- ]?(?:restriction|limit)", re.IGNORECASE)
_SINGLE_COUNTRY_RE = re.compile(
    r"\b(?:(?:us|u\.s\.|usa|uk|u\.k\.|canada|canadian|australia|australian|germany|german|france|french|india|indian|nigeria|brazil|italy|spain|ireland|japan)[ -](?:only|residents?|customers?|citizens?)|"
    r"(?:only )?(?:available|open|valid|eligible) (?:to|for|in) (?:the )?(?:us|u\.s\.|usa|uk|u\.k\.|canada|australia|germany|france|india|residents of [a-z ]+)|"
    r"residents? of (?:the )?[a-z ]{2,20} only|(?:us|uk|au|ca|eu) only)\b", re.IGNORECASE)
_MULTI_RE = re.compile(r"\b(?:eu|europe|european union|asia|latam|latin america|africa|nordics?|(?:\w+, ){2,}\w+ (?:and|&) \w+ (?:residents|only|countries))\b", re.IGNORECASE)


def geo_breadth(text: str, source_default: str | None = None) -> str:
    """'worldwide' | 'multi' | 'single' | 'unknown' -- worldwide beats single-country however large the single country."""
    t = text or ""
    if _SINGLE_COUNTRY_RE.search(t):
        return "single"
    if _WORLDWIDE_RE.search(t):
        return "worldwide"
    if _MULTI_RE.search(t):
        return "multi"
    return source_default or "unknown"


_KYC_RE = re.compile(r"\bkyc\b|identity verification|verify your identity|government id|passport|selfie|ssn|social security", re.IGNORECASE)
_HARD_PULL_RE = re.compile(r"hard (?:credit )?(?:pull|inquiry)", re.IGNORECASE)
_SOFT_PULL_RE = re.compile(r"soft (?:credit )?(?:pull|inquiry)|soft check", re.IGNORECASE)
_DIRECT_DEPOSIT_RE = re.compile(r"direct deposit(?! not required)", re.IGNORECASE)
_NO_DD_RE = re.compile(r"direct deposit not required|no direct deposit", re.IGNORECASE)
_CAPITAL_RE = re.compile(
    r"(?:deposit|fund(?:ed|ing)?|maintain|minimum (?:balance|deposit)|balance of|invest|contribut(?:e|ion)|spend|purchase|buy)\D{0,25}"
    r"(?P<cur>US\$|\$|€|£)\s?(?P<n>\d[\d,]*(?:\.\d+)?)(?P<k>[kK])?", re.IGNORECASE)
_FREE_RE = re.compile(r"\b(?:free|no (?:purchase|deposit|payment|cost) (?:necessary|required)|100% off|\$0|0\.00)\b", re.IGNORECASE)
_STEP_WORDS_RE = re.compile(r"\b(?:follow|retweet|repost|join (?:our|the)|like|comment|tag|invite|share|subscribe|complete (?:the )?tasks?|quests?)\b", re.IGNORECASE)


def extract_prereqs(text: str) -> list[str]:
    t = text or ""
    out = []
    if _KYC_RE.search(t):
        out.append("kyc")
    if _HARD_PULL_RE.search(t):
        out.append("hard_credit_pull")
    elif _SOFT_PULL_RE.search(t):
        out.append("soft_credit_pull")
    if _DIRECT_DEPOSIT_RE.search(t) and not _NO_DD_RE.search(t):
        out.append("direct_deposit")
    return out


_RECURRING_FEE_RE = re.compile(r"(?:US\$|\$|€|£)\s?(?P<n>\d[\d,]*(?:\.\d+)?)\s*(?:/|per |a |each )\s*(?P<per>month|mo\b|year|yr\b|annum)", re.IGNORECASE)


_CAPITAL_TRAIL_RE = re.compile(
    r"(?:deposit|fund(?:ed|ing)?|maintain|minimum (?:balance|deposit)|balance of|invest|contribut(?:e|ion)|spend|purchase|buy|"
    r"депозит\w*|внес\w+|внести|пополни\w*|вложи\w*|dep[oó]sit\w*|invista|aporte)\D{0,25}"
    r"(?P<n>\d[\d ,.]*\d|\d)\s?(?P<k>[kKкК])?\s?(?:\$|€|£)(?!\w)", re.IGNORECASE)      # "deposit 500$": the sign AFTER the number


def extract_capital_usd(text: str, type_: str) -> float | None:
    """Money the reader must put in (and, for a bonus, usually gets back): 0 for a free offer, the largest stated
    deposit/spend otherwise, None when it is not stated and the type usually needs some (a bank bonus)."""
    amounts = []
    for m in _CAPITAL_RE.finditer(text or ""):
        try:
            v = float(m.group("n").replace(",", "")) * (1000 if m.group("k") else 1)
        except ValueError:
            continue
        amounts.append(v)
    for m in _CAPITAL_TRAIL_RE.finditer(text or ""):
        try:
            amounts.append(_to_number(m.group("n")) * (1000 if m.group("k") else 1))
        except ValueError:
            continue
    # A recurring fee the offer needs ("requires a $39.99 per month account") is money in, even though it is not a deposit:
    # three months for a monthly fee (the usual minimum), one year's for an annual one.
    for m in _RECURRING_FEE_RE.finditer(text or ""):
        try:
            v = float(m.group("n").replace(",", ""))
        except ValueError:
            continue
        amounts.append(v * (3 if m.group("per").lower().startswith(("month", "mo")) else 1))
    if amounts:
        return max(amounts)
    if type_ in ("free_game", "giveaway", "airdrop", "freebie", "credits", "bounty", "hackathon"):
        return 0.0
    if _FREE_RE.search(text or ""):
        return 0.0
    return None


_EFFORT_DEFAULT_MIN = {"free_game": 3, "freebie": 5, "giveaway": 6, "credits": 10, "cashback": 10, "airdrop": 30, "bank_bonus": 90,
                        "hackathon": 600, "bounty": 600, "other": 20}


def estimate_effort_minutes(text: str, type_: str, prereqs: list[str]) -> int:
    minutes = _EFFORT_DEFAULT_MIN.get(type_, 20)
    steps = len(_STEP_WORDS_RE.findall(text or ""))
    minutes += 2 * min(steps, 8)
    if "kyc" in prereqs:
        minutes += 15
    if "direct_deposit" in prereqs:
        minutes += 30
    return minutes


def payout_probability(type_: str, payload: dict) -> float:
    """Chance an eligible participant actually receives the payout. PRIORS, by type -- stated as such."""
    if type_ in ("free_game", "freebie", "credits"):
        return 1.0
    if type_ in ("bank_bonus", "cashback"):
        return 0.8
    if type_ == "airdrop":
        return 0.3
    if type_ == "bounty":
        return 0.1
    if type_ == "giveaway":
        w, e = payload.get("winners"), payload.get("entrants")
        return min(1.0, max(0.001, w / e)) if w and e else 0.02
    return 0.1


# ----------------------------------------------------------------------------- referral program (operator override 2026-11-16)

# "Can the reader invite others and earn from it?" -- detected in the OFFER'S TEXT, clause by clause, in en / ru / pt. Deliberately not
# matched: the poster's own code ("use my referral code": that is a promo for the poster, not something the reader earns from), negations
# ("no referral needed"), and generic "we invite you".
_REFERRAL_CUE = (
    r"\breferrals\b|\breferral\b(?! (?:code|link|id)\b)|\brefer(?:s|ring)? (?:a |your |any )?(?:friends?|others?|someone|people|users?)\b|\brefer[- ]?a[- ]?friend\b|"
    r"\binvit(?:e|es|ing) (?:a |your |any )?(?:friends?|others?|someone|people|new users?|users?)\b|\binvite (?:&|and) earn\b|"
    r"\b(?:each|every|per) (?:friend|referral|invitee|person (?:you )?(?:refer|invite))|\bbring(?:ing)? (?:a )?friends?\b|\baffiliate (?:program|commission|link)\b|"
    r"реферал\w*|пригласи\w* (?:друг\w*|знаком\w*|пользовател\w*)|приглаш[её]нн\w+|за приглашени\w+|приглаш\w+ друз\w+|"
    r"\bза (?:каждого )?друг\w*|\bкаждого друга|приведи \w+ друг\w*|"
    r"\bindica[cç][aã]o\b|\bindique (?:um|uma|seus?|suas?|amigos?|e ganhe)\b|\bindicar (?:um|uma|amigos?)\b|\bconvide (?:amigos?|algu[eé]m)\b|\b(?:cada|por) (?:amigo|indicado)\b|\bprograma de afiliad\w+")
_REFERRAL_CUE_RE = re.compile(_REFERRAL_CUE, re.IGNORECASE)
_REFERRAL_NEG_RE = re.compile(r"(?:\bno|\bwithout|\bnot|\bnon|\bsem|\bбез|\bне)[- ]?(?:\w+ )?$", re.IGNORECASE)
_AMT_LEAD = r"(?:(?:R\$|[$€£])?\s?\d[\d\s.,]*\s?(?:[$€£]|usd\w*|eur)?\s{0,2}(?:for |per )?)?"      # "... and $25 for each friend": the amount may precede the cue
_CLAUSE_SPLIT_RE = re.compile(r"[\n;•·]+|(?<=[.!?])\s+|(?:,\s*|\s+)(?:and|plus|&|\+|и|e|y)\s+(?=" + _AMT_LEAD + r"(?:" + _REFERRAL_CUE + r"))|,\s+(?=(?:" + _REFERRAL_CUE + r"))", re.IGNORECASE)


def _is_referral_clause(clause: str) -> bool:
    m = _REFERRAL_CUE_RE.search(clause)
    return bool(m) and not _REFERRAL_NEG_RE.search(clause[max(0, m.start() - 14):m.start()])


def split_referral(text: str) -> tuple[str, list[str]]:
    """(the text WITHOUT its referral clauses, the referral clauses). The base text is what the payout is read from: a referral
    bonus must never be mistaken for the offer's own payout, or 'referral earnings exceed the base payout' could never be told."""
    clauses = [c for c in _CLAUSE_SPLIT_RE.split(text or "") if c and c.strip()]
    ref = [c for c in clauses if _is_referral_clause(c)]
    base = " ".join(c for c in clauses if not _is_referral_clause(c))
    return base, ref


def extract_referral(text: str) -> dict:
    """{'available': bool, 'amount_usd': what one referral pays the reader (None when no figure is stated: a percentage or nothing),
    'multi_level': multi-level / downline wording present -- a WARNING LABEL on the card, no score effect}."""
    _, ref = split_referral(text)
    multi = has_multilevel(text)
    if not ref and not multi:
        return {"available": False, "amount_usd": None, "multi_level": False}
    amounts = [a for c in ref for a in extract_amounts_usd(c) if a < 1_000_000]
    return {"available": True, "amount_usd": max(amounts) if amounts else None, "multi_level": multi}


# Multi-level wording is the clearest TEXTUAL signal of a pyramid structure (operator, 2026-11-16): recruitment-shaped on its own, whatever
# the amounts say. Unambiguous terms count anywhere in the text; the bare word "levels" is too common ("complete 3 levels") so it counts only
# inside a referral clause, or as a counted level / tier of referrals when the text has a referral program.
_MULTILEVEL_ANY_RE = re.compile(
    r"\bdownlines?\b|\bupline\b|\bmulti-?level\b|\bmlm\b|\bnetwork marketing\b|\buni-?level\b|\bbinary (?:plan|tree|commission)\b|"
    r"многоуровнев\w*|даунлайн\w*|\bmultin[ií]vel\b|\bmulti-?n[ií]veis\b", re.IGNORECASE)
_MULTILEVEL_LEVELS_RE = re.compile(r"\blevels?\b|уров(?:ень|ня|ни|ней|нях|нем)\b|\bn[ií]veis\b|\bn[ií]vel\b", re.IGNORECASE)
_MULTILEVEL_COUNTED_RE = re.compile(
    r"(?:\d|two|three|four|five|second|third|[2-9](?:nd|rd|th))[- ](?:levels?|tiers?)\b|\b(?:levels?|tiers?) ?[2-9]\b|\b(?:levels?|tiers?) (?:of|deep)\b|"
    r"[2-9]\s?(?:уров|n[ií]vei)\w*", re.IGNORECASE)


def has_multilevel(text: str) -> bool:
    t = text or ""
    if _MULTILEVEL_ANY_RE.search(t):
        return True
    _, ref = split_referral(t)
    if any(_MULTILEVEL_LEVELS_RE.search(c) for c in ref):
        return True
    return bool(ref) and bool(_MULTILEVEL_COUNTED_RE.search(t))


# ---- Token-denominated rewards (operator 2026-11-16): "689,655 BAOBAO" is not a dollar figure. A reward paid in anything but a stablecoin is an
# UNCLEAR amount: it is never priced (pricing it needs a token price, i.e. a valuation call, a different kind of claim from any other this
# system makes) and the item's VALUE is capped at TOKEN_REWARD_VALUE_CAP. NOTE the cap is below VALUE_FLOOR: such an item is recorded and
# scored but never reaches the operator.
STABLECOINS = {"usd", "usdc", "usdt", "usdg", "dai", "pyusd", "fdusd", "usde", "tusd", "busd", "usdp", "gusd", "lusd"}
TOKEN_REWARD_VALUE_CAP = 30.0


def is_stablecoin(token: str | None) -> bool:
    return (token or "").strip().lower() in STABLECOINS


REFERRAL_VALUE_MAX = 12.0           # points of the 100-point value score a referral program can ever add ("around 10-15%")
# (There is NO recruitment penalty: it was the assistant's recommendation, the operator overrode it 2026-11-17 and it must not be restored. See BACKLOG.)


_REFERRAL_MAX_RE = re.compile(r"(?:up to|upto|до|até|ate|hasta)\W{0,3}[^\w$€£]{0,6}[$€£\d]", re.IGNORECASE)


def referral_payout(text: str) -> tuple[float, str, str] | None:
    """(amount in dollar equivalent, 'stated' | 'max', the clause) for the first referral clause that states an amount ("$15 per friend", "up to $15 for each invited friend"). Operator
    2026-11-17: a stated per-referral earning counts as PAYOUT, scored like any other -- used by the collector only when the offer states no other payout."""
    _, clauses = split_referral(text)
    for c in clauses:
        amounts = extract_amounts_usd(c)
        if amounts and amounts[0] < 1_000_000:
            return amounts[0], ("max" if _REFERRAL_MAX_RE.search(c) else "stated"), c
    return None


# ---- "could potentially make the channel owner money" (operator 2026-11-17): a little extra VALUE when the reader is asked to bring money to a platform (a deposit), the offer is an
# exchange / broker / bank sign-up, or it has a referral program -- the channel owner can earn from those, so they are worth surfacing a bit sooner. A deposit is NEVER a reason to block.
MONETIZABLE_BONUS = 5.0
_MONETIZABLE_TYPES = {"bank_bonus", "cashback"}


def monetizable(payload: dict) -> bool:
    cap = payload.get("capital_usd")
    return bool((cap and cap > 0) or (payload.get("type") in _MONETIZABLE_TYPES) or referral_info(payload)["available"])


def referral_info(payload: dict) -> dict:
    """Availability (the TEXT describes a program the reader can earn from), the stated per-referral amount, and `multi_level` (a WARNING LABEL on the card only: no score effect).
    A referral code on the source's own link (`link_code`) is only an inference and is never scored."""
    ref = payload.get("referral") or {}
    return {"available": bool(ref.get("available")), "link_code": bool(payload.get("referral_link")), "amount_usd": ref.get("amount_usd"), "multi_level": bool(ref.get("multi_level"))}


def referral_component(payload: dict) -> float:
    """0-100: how much a referral program is worth to the reader. Nothing for no program, nothing for a recruitment-shaped one (a
    warning, not a feature), a modest 50 for a program with no stated figure, up to 100 for a large stated per-referral payout."""
    info = referral_info(payload)
    if not info["available"]:
        return 0.0
    return max(50.0, _log_scale(info["amount_usd"])) if info["amount_usd"] else 50.0


# ----------------------------------------------------------------------------- scoring

def _log_scale(x: float, lo: float = 1.0, hi: float = 1000.0) -> float:
    if x is None or x <= lo:
        return 0.0
    return min(100.0, 100.0 * math.log10(x / lo) / math.log10(hi / lo))


def expected_payout_usd(payload: dict) -> float | None:
    """Probability-adjusted payout. None when no payout is stated -- unknown is never coded as high."""
    # A COMPETITION (ranked / judged prizes) has no expected amount: pool / entrants only describes a raffle (operator 2026-11-16). Its odds and prize
    # structure are SHOWN (prizes.prize_line); the expected-payout floor does not apply to it.
    if prizes.award_kind(payload) == "competition":
        return None
    nominal = payload.get("payout_usd")
    if nominal is None:
        return None
    if payload.get("payout_kind") == "ev":          # a RAFFLE's expected value (pool / entrants)
        return nominal
    return nominal * payout_probability(payload.get("type") or "other", payload)


def value_components(payload: dict) -> dict:
    t = payload.get("type") or "other"
    ev = expected_payout_usd(payload)
    if prizes.award_kind(payload) == "competition":
        payout = prizes.competition_payout_component(payload)    # size of the biggest prize, capped at 65: you have to WIN it
    elif ev is None:
        payout = 20.0 if t in ("airdrop", "giveaway") else 25.0   # unknown payout: capped low, never coded as high
    else:
        payout = _log_scale(ev)
    cap = payload.get("capital_usd")
    if cap is None:
        capital = 50.0                                           # not stated and normally needed: neutral, not clear
    elif cap <= 0:
        capital = 100.0
    elif cap <= 10:
        capital = 70.0
    elif cap <= 50:
        capital = 45.0
    elif cap <= 250:
        capital = 20.0
    else:
        capital = 5.0
    minutes = payload.get("effort_minutes") or _EFFORT_DEFAULT_MIN.get(t, 20)
    effort = 100.0 if minutes <= 2 else 80.0 if minutes <= 10 else 55.0 if minutes <= 30 else 30.0 if minutes <= 120 else 10.0
    if payload.get("skill"):                       # professional expertise (ML, engineering, security) needed: the effort is worth less to most readers
        effort = round(effort * 0.5, 1)
    prereq = 100.0
    for p in payload.get("prereqs") or []:
        prereq -= {"kyc": 25, "hard_credit_pull": 35, "soft_credit_pull": 10, "direct_deposit": 20}.get(p, 10)
    prereq = max(0.0, prereq)
    geo = {"worldwide": 100.0, "multi": 70.0, "single": 35.0}.get(payload.get("geo"), 55.0)
    return {"payout": round(payout, 1), "capital": capital, "effort": effort, "prereq": prereq, "geo": geo}


VALUE_WEIGHTS = {"payout": 0.35, "capital": 0.25, "effort": 0.15, "prereq": 0.10, "geo": 0.15}
_TIER_POINTS = {1: 40.0, 2: 28.0, 3: 14.0}


def payout_for_nothing_penalty(payload: dict) -> float:
    """The cross-link. A big nominal payout for next to no capital or effort from a source nobody vets is the shape of a
    scam, so it costs TRUST. Official (tier 1) sources are exempt. Ratio = nominal payout / (capital + 1 + 0.25/min)."""
    nominal = payload.get("payout_usd")
    # Only a text-claimed per-person amount or a stated maximum. A structured "worth" (GamerPower's retail value), an expected
    # value, a pool or an unclear amount are never grounds to cut trust: a misread of VALUE must not silently destroy TRUST.
    if not nominal or payload.get("payout_kind") not in ("stated", "max") or payload.get("source_tier") == 1:
        return 0.0
    # "For nothing" must be KNOWN. If the text carries another figure of at least half the nominal that was NOT read as a reward -- a
    # price, a deposit, a fee (e.g. a $5,500 solar battery deal with "$3,500 worth" of extras) -- the offer is not demonstrably free, the
    # capital in the ratio is unknown rather than zero, and cutting trust on it would be the same misread-value-destroys-trust bug.
    if (payload.get("other_amount_max") or 0.0) >= 0.5 * nominal:
        return 0.0
    minutes = payload.get("effort_minutes") or _EFFORT_DEFAULT_MIN.get(payload.get("type") or "other", 20)
    ratio = nominal / ((payload.get("capital_usd") or 0.0) + 1.0 + 0.25 * minutes)
    if nominal >= 1000 and ratio > 300:
        return 50.0
    if nominal >= 100 and ratio > 100 and payload.get("source_tier") == 3:
        return 30.0
    return 0.0


def trust_components(payload: dict) -> dict:
    tier = _TIER_POINTS.get(payload.get("source_tier"), 14.0)
    corro = min(20.0, 10.0 * len(payload.get("corroborators") or []))
    terms = payload.get("terms") or {}
    stated = 5.0 * sum(1 for k in ("deadline", "eligibility", "payout") if terms.get(k))
    age = payload.get("domain_age_days")
    if age is None:
        domain = 6.0                            # unknown is neither old nor new
    elif age >= 365:
        domain = 15.0
    elif age >= 90:
        domain = 10.0
    elif age >= 30:
        domain = 4.0
    else:
        domain = 0.0
    rep = payload.get("reputation") or {}
    clean = sum(1 for k in ("safe_browsing", "urlhaus") if rep.get(k) == "clean")
    reputation = {2: 10.0, 1: 6.0, 0: 4.0}[clean]
    gp = (payload.get("goplus") or {}).get("status")
    if gp == "unchecked":
        reputation -= 10.0                      # a token contract we could not check: unknown is never coded as clear
    penalty = payout_for_nothing_penalty(payload)
    return {"source_tier": tier, "corroboration": corro, "terms_stated": stated, "domain_age": domain,
            "reputation": reputation, "payout_for_nothing_penalty": penalty}


def score_earning(payload: dict) -> dict:
    """Breakdown stored in scores.score_breakdown. `value` and `trust` are separate and never combined; the
    component dicts exist so a card can show WHY. pipeline/score.py sets scores.score = value (weights value 1, trust 0)."""
    vc = value_components(payload)
    base = round(sum(VALUE_WEIGHTS[k] * vc[k] for k in VALUE_WEIGHTS), 1)
    bonus = round(REFERRAL_VALUE_MAX / 100.0 * referral_component(payload), 1)      # 0 .. REFERRAL_VALUE_MAX points, added on top
    mon_bonus = MONETIZABLE_BONUS if monetizable(payload) else 0.0
    value = round(min(100.0, base + bonus + mon_bonus), 1)
    uncapped = value
    if payload.get("token_reward"):
        value = min(value, TOKEN_REWARD_VALUE_CAP)        # a non-stablecoin token reward is an unclear amount, never priced (operator 2026-11-16)
    tc = trust_components(payload)
    trust = round(max(0.0, min(100.0, sum(v for k, v in tc.items() if not k.endswith("penalty")) - sum(v for k, v in tc.items() if k.endswith("penalty")))), 1)
    kyc = True if "kyc" in (payload.get("prereqs") or []) else (False if payload.get("kyc_known") else None)    # None = unknown, never "no"
    ep = expected_payout_usd(payload)
    top = prizes.top_prize_usd(payload) if prizes.award_kind(payload) == "competition" else None
    out = {"value": value, "trust": trust, "value_base": base, "referral_bonus": bonus, "referral": referral_info(payload), "kyc": kyc,
           "monetizable": mon_bonus > 0, "expected_payout_usd": round(ep, 2) if ep is not None else None, "award_kind": prizes.award_kind(payload),
           "prize_line": prizes.prize_line(payload), "top_prize_usd": round(top, 2) if top else None, "skill": list(payload.get("skill") or []),
           "value_components": vc, "trust_components": tc}
    if payload.get("token_reward"):
        out["value_capped_by"] = {"reason": "token-denominated reward (not a stablecoin): unclear amount, never priced", "cap": TOKEN_REWARD_VALUE_CAP,
                                  "value_uncapped": uncapped, "token": payload["token_reward"].get("token")}
    return out


def expected_payout_ok(breakdown: dict) -> bool:
    """The third hard floor (operator 2026-11-16): the probability-adjusted payout must be at least EXPECTED_PAYOUT_FLOOR. UNKNOWN passes (no
    figure is not a fail) -- its payout component is already scored low (20-25), so it has to earn its way through on other merits."""
    if breakdown.get("award_kind") == "competition":       # no expected amount for a competition; its own size floor instead (prizes.COMPETITION_MIN_TOP_PRIZE_USD)
        top = breakdown.get("top_prize_usd")
        return top is None or float(top) >= prizes.COMPETITION_MIN_TOP_PRIZE_USD
    if breakdown.get("award_kind") != "raffle":            # guaranteed payouts (a $2 deposit bonus) are never cut by an expected-payout floor (operator 2026-11-17)
        return True
    ep = breakdown.get("expected_payout_usd")
    return ep is None or float(ep) >= EXPECTED_PAYOUT_FLOOR


def passes_floors(breakdown: dict) -> bool:
    return (breakdown.get("trust", 0) >= TRUST_FLOOR and breakdown.get("value", 0) >= VALUE_FLOOR and expected_payout_ok(breakdown))
