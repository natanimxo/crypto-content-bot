"""Shared entity extraction for the `news` category (2026-09-11) -- one place
for pulling tickers/proper-noun phrases/figures out of a headline+excerpt,
used for two genuinely different jobs that both need it:

1. Same-cycle cross-outlet dedup (collectors/news.py) -- comparing fingerprint
   overlap between items from DIFFERENT sources collected in the same pass,
   to catch five outlets covering one event under five different headlines.
2. Cross-category data connection (pipeline/write_post.py) -- matching a news
   item's tickers against our OWN recent whale_movements/defi_yields data,
   the one piece of context a generic news article structurally cannot offer
   (operator direction 2026-09-11: "give it real attention... if it works
   well, it's the strongest version of the spec's Section 1 memory idea in
   any category so far").

Deliberately deterministic, no LLM -- matches every other category's scoring/
dedup philosophy. This is real-world-lossy by nature (keyword/regex
extraction, not semantic understanding) and is biased toward PRECISION over
recall throughout: missing a genuine match costs a reader one skipped bonus
fact; a false match risks wrongly merging two distinct stories or attaching
an unrelated "our own data" fact to the wrong article, which is a worse
failure mode (see collectors/news.py's dedup comment for the concrete
reasoning).
"""

import re

# Tickers most likely to actually show up in OUR OWN whale_movements/
# defi_yields data (the cross-category connection's real target), plus the
# handful of majors any crypto news cycle references constantly regardless.
# Maintained like every other "known-entity" list in this codebase
# (KNOWN_EXCHANGE_ADDRESSES, KNOWN_WEB3_COMPANIES, KNOWN_MAJOR_TOKENS) --
# hand-extend as real news cycles surface a frequently-mentioned ticker
# that's missing, common-knowledge judgment, no address-style rigor needed.
KNOWN_TICKERS = {
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC", "POL",
    "LINK", "UNI", "AAVE", "LTC", "BCH", "ATOM", "ARB", "OP", "SUI", "APT",
    "NEAR", "FTM", "INJ", "TIA", "SEI", "RUNE", "GRT", "SAND", "AXS", "MANA",
    "CRV", "MKR", "SNX", "COMP", "LDO", "RPL", "PEPE", "SHIB", "WIF", "BONK",
    "USDT", "USDC", "DAI", "USDE", "TUSD", "FDUSD", "WBTC", "WETH", "STETH",
    "BNB", "TRX", "TON", "ICP", "FIL", "HBAR", "VET", "ALGO", "XLM", "XTZ",
}

# Live bug, first test against real headlines 2026-09-11: "Bitcoin, ether
# rise as inflation data..." extracted ZERO entities -- KNOWN_TICKERS only
# has the symbol form (BTC), but a spelled-out coin name in prose is at
# least as common in real headlines as the raw ticker. Mapped here so both
# forms normalize to the same entity (case-insensitive match on the name,
# unlike the ticker set itself which stays case-sensitive to avoid "IT"/
# "OR"-style false positives on short symbols).
COIN_NAME_TO_TICKER = {
    "bitcoin": "BTC", "ethereum": "ETH", "ether": "ETH", "solana": "SOL",
    "ripple": "XRP", "cardano": "ADA", "dogecoin": "DOGE", "avalanche": "AVAX",
    "polkadot": "DOT", "polygon": "MATIC", "chainlink": "LINK", "uniswap": "UNI",
    "litecoin": "LTC", "cosmos": "ATOM", "arbitrum": "ARB", "optimism": "OP",
    "near protocol": "NEAR", "tether": "USDT", "binance coin": "BNB",
    "tron": "TRX", "toncoin": "TON", "stellar": "XLM", "shiba inu": "SHIB",
}

# 2+ consecutive Title-Case words -- catches named protocols/companies/people
# not in KNOWN_TICKERS (e.g. "Sam Bankman-Fried", "Bitcoin Suisse"). Allows a
# hyphen WITHIN a word (Bankman-Fried) without treating the hyphen as a word
# boundary. Deliberately run ONLY against description/excerpt text, never
# headlines -- live-verified these feeds Title Case every word of a
# headline as a style choice ("Bitcoin Golden Cross Flickers Off as
# Rate-Hike Bets Firm Up" isn't 5 named entities, it's headline styling),
# which would make this regex fire on essentially any 2+-word run in a
# title and produce pure noise. Descriptions are consistently normal
# sentence case across every feed checked, so real proper nouns are the
# only thing that trips it there.
_PROPER_NOUN_RE = re.compile(r"\b([A-Z][a-zA-Z'.-]+(?:\s+[A-Z][a-zA-Z'.-]+){1,3})\b")

# Regulatory/institutional acronyms worth tracking on their own -- short
# enough that a generic proper-noun regex would either miss them (single
# word) or drown in false positives if loosened to match any short
# all-caps run.
KNOWN_ACRONYMS = {"SEC", "CFTC", "DOJ", "IRS", "FBI", "FDIC", "OCC", "ECB", "IMF", "FATF"}

_DOLLAR_FIGURE_RE = re.compile(r"\$\s?[\d,]+(?:\.\d+)?\s?(?:[BMKbmk]illion|[BMK])?\b")
_PERCENT_FIGURE_RE = re.compile(r"\b\d+(?:\.\d+)?%")

# A capitalized word that's ALSO a common sentence-leading/generic word --
# excluded from the proper-noun match so "This Week" or "New York" (fine,
# real) don't get confused with headline-generic filler like "The Latest".
_GENERIC_LEADING_WORDS = {"The", "This", "That", "These", "Those", "A", "An"}


def extract_entities(title: str, description: str = "") -> dict:
    """Returns {"tickers": set[str], "phrases": set[str], "figures": set[str]}.
    Tickers/acronyms/figures are extracted from title+description combined
    (precise regex matches, unaffected by title-casing). Proper-noun
    PHRASES are extracted from description only -- see _PROPER_NOUN_RE's
    comment for why the title is unreliable for this specifically."""
    title = title or ""
    description = description or ""
    combined = f"{title} {description}"

    words = set(re.findall(r"\b[A-Z]{2,6}\b", combined))
    tickers = (words & KNOWN_TICKERS) | (words & KNOWN_ACRONYMS)
    lowered = combined.lower()
    for name, ticker in COIN_NAME_TO_TICKER.items():
        if re.search(rf"\b{re.escape(name)}\b", lowered):
            tickers.add(ticker)

    phrases = set()
    for m in _PROPER_NOUN_RE.finditer(description):
        phrase = m.group(1).strip()
        first_word = phrase.split()[0]
        if first_word in _GENERIC_LEADING_WORDS:
            continue
        phrases.add(phrase)

    figures = set(_DOLLAR_FIGURE_RE.findall(combined)) | set(_PERCENT_FIGURE_RE.findall(combined))

    return {"tickers": tickers, "phrases": phrases, "figures": figures}


def fingerprint_overlap(a: dict, b: dict) -> float:
    """0-1 similarity between two extract_entities() results, weighted toward
    tickers/figures (specific, low-ambiguity) over phrases (looser, more
    prone to coincidental overlap). Used for same-cycle cross-outlet dedup
    -- see collectors/news.py. Returns 0.0 if neither item has any tickers
    or figures at all (too little specific signal to claim a match either
    way -- biased toward NOT merging when uncertain, per this module's
    precision-over-recall stance)."""
    ticker_overlap = len(a["tickers"] & b["tickers"])
    figure_overlap = len(a["figures"] & b["figures"])
    phrase_overlap = len(a["phrases"] & b["phrases"])

    if not (a["tickers"] or a["figures"]) or not (b["tickers"] or b["figures"]):
        return 0.0

    # Two DIFFERENT stories about the same ticker are common (two unrelated
    # BTC price posts on the same day) -- ticker overlap alone is weak
    # evidence. A shared specific figure (a dollar amount, a percentage) or
    # a shared named phrase alongside a shared ticker is much stronger:
    # coincidentally reusing the exact same number is unlikely unless it's
    # the same underlying fact.
    score = 0.0
    if ticker_overlap:
        score += 0.3
    if figure_overlap:
        score += 0.45
    if phrase_overlap:
        score += 0.35
    return min(score, 1.0)


def primary_entity(entities: dict) -> str | None:
    """The single most stable identifier for 'what is this story primarily
    about' -- used as topic_key (pipeline/score.py, select_candidates.py's
    existing cooldown machinery). Deliberately coarser than
    fingerprint_overlap: this is 'same primary subject', not 'same specific
    event' -- cooldown_hours plus the status-change override (collectors/
    news.py) do the real work of distinguishing a recycled rehash from a
    genuine follow-up on that subject; topic_key alone was never going to
    carry that distinction reliably with keyword matching."""
    if entities["tickers"]:
        return sorted(entities["tickers"])[0]
    if entities["phrases"]:
        return sorted(entities["phrases"])[0].lower()
    return None
