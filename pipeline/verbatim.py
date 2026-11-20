"""We Talk A Lot's post builder (operator 2026-11-20): the card carries the SOURCE'S EXACT WORDS, in order -- not rewritten, not shortened, not summarised.

What this module does, and the one thing it must never do:
  * FORMATS only: the episode header from a pool at the top, the title in bold, 2-3 niche hashtags on the line below the title, paragraph breaks where the text naturally divides, clean
    spacing after full stops, an optional <tg-spoiler> around the payoff, and a source line. All of that is CODE.
  * DeepSeek is used for exactly two things and is handed no way to change a word: (1) where the paragraph breaks go -- it sees the source's sentences NUMBERED and returns the indices where a
    new paragraph starts, and the code slices the ORIGINAL text there; (2) the hashtags. If the model fails, code breaks paragraphs every few sentences and uses the category's default tags.
  * VERIFIES, always: the title and body as they appear in the finished post, with tags and whitespace removed, must equal the title and body as the source gave them, character for character.
    If anything differs the operator gets the ORIGINAL TEXT UNFORMATTED instead, and the card says so. The check runs on every item; it is cheap and it is the whole guarantee.
  * The copy guard (write_post._find_reproduction_issues) is NOT used on this channel: the text is the source's own words by design.

"Source's exact words" means the text as EXTRACTED: HTML tags and entities removed and whitespace collapsed (that is reading, not editing); nothing else is touched.

The episode-header pool, the poll pairs and the default hashtags live in config/wtal.yaml (the operator edits it).
"""

import html
import json
import logging
import os
import re

import yaml

logger = logging.getLogger(__name__)

CHANNEL = "we_talk_a_lot"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "wtal.yaml")

# categories whose final sentence (or a joke's delivery) is wrapped in <tg-spoiler> by default (operator: "spoilers, for every category")
SPOILER_DEFAULT = True
# a body with this many sentences or more, in a single block, is offered to the model for paragraph breaks
MIN_SENTENCES_FOR_BREAKS = 4
FALLBACK_SENTENCES_PER_PARAGRAPH = 3
MAX_PARAGRAPHS = 5
CARD_ORIGINAL_MAX_CHARS = 1100

_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?…])["”’)\]]*\s+(?=["“‘(\[]?[A-Z0-9])')
_NO_SPACE_AFTER_STOP_RE = re.compile(r"([a-z0-9][.!?])([A-Z])")
_TAG_RE = re.compile(r"^#[A-Za-z][A-Za-z0-9]{3,24}$")
_GENERIC_TAGS = {"#funny", "#lol", "#memes", "#meme", "#viral", "#humor", "#humour", "#fun", "#news", "#joke", "#jokes", "#haha", "#comedy"}
_ALLOWED_EDIT_TAGS = ("tg-spoiler", "b", "i", "u", "s", "code")


# ------------------------------------------------------------------ config
_CFG_CACHE: dict | None = None


def load_config() -> dict:
    global _CFG_CACHE
    if _CFG_CACHE is None:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _CFG_CACHE = yaml.safe_load(f) or {}
    return _CFG_CACHE


def reset_config_cache() -> None:
    global _CFG_CACHE
    _CFG_CACHE = None


# ------------------------------------------------------------------ text helpers
def extract_text(raw: str | None) -> str:
    """HTML -> plain text: tags removed (block tags become paragraph breaks), entities unescaped, whitespace collapsed within each paragraph. Returns paragraphs separated by a blank line."""
    s = raw or ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p\s*>|</div\s*>|</li\s*>|</h[1-6]\s*>", "\n\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    paras = [re.sub(r"[ \t\r\f\v ​]+", " ", p).strip() for p in re.split(r"\n\s*\n|\n{2,}", s)]
    paras = [re.sub(r"\s*\n\s*", " ", p) for p in paras if p]
    return "\n\n".join(paras)


def clean_spacing(text: str) -> str:
    """'clean spacing after full stops': collapse runs of spaces and add the missing space in 'end.Next'. Characters other than whitespace are never touched."""
    t = re.sub(r"[ \t ]+", " ", text or "").strip()
    return _NO_SPACE_AFTER_STOP_RE.sub(r"\1 \2", t)


def strip_ws(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def split_sentences(paragraph: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(paragraph.strip()) if p.strip()]
    return parts or ([paragraph.strip()] if paragraph.strip() else [])


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def escape_keep_tags(text: str) -> str:
    """The operator's typed text -> Telegram HTML: everything is escaped EXCEPT the few formatting tags he may type by hand (<tg-spoiler>, <b>, <i>, <u>, <s>, <code>)."""
    out = html.escape(text or "", quote=False)
    for tag in _ALLOWED_EDIT_TAGS:
        out = out.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return out


def visible_text(post_html: str) -> str:
    """The characters a reader sees: tags removed, entities unescaped."""
    return html.unescape(re.sub(r"<[^>]+>", "", post_html or ""))


# ------------------------------------------------------------------ the model's two jobs
def _hashtag_prompt(label: str, title: str, body: str, sentences: list[str] | None) -> str:
    numbered = ""
    ask_breaks = ""
    if sentences:
        numbered = "\nSENTENCES (numbered from 0):\n" + "\n".join(f"{i}: {s}" for i, s in enumerate(sentences)) + "\n"
        ask_breaks = (' and "paragraph_starts": the 0-based sentence numbers where a NEW paragraph begins, in increasing order, starting with 0, at most 4 paragraphs, '
                      "breaking only where the text naturally changes subject or beat")
    return f"""You are formatting one post for a Telegram humor channel. The post is the source's own text and you must not change, add or remove any word: you only choose formatting.

Category: {label}
TITLE: {title or "(none)"}
TEXT: {body[:1500] if not sentences else "(see the numbered sentences)"}{numbered}
Return ONLY a JSON object with "hashtags": 2 or 3 niche hashtags for this category and this text (letters and digits only, 4 to 25 characters each, nothing generic like #funny #lol #memes #viral){ask_breaks}.
Example: {{"hashtags": ["#BizarreHistory", "#PopeDrama"]{', "paragraph_starts": [0, 2]' if sentences else ''}}}"""


def valid_hashtags(tags) -> list[str]:
    out = []
    for t in tags if isinstance(tags, list) else []:
        t = str(t).strip()
        if not t.startswith("#"):
            t = "#" + t
        t = re.sub(r"[^#A-Za-z0-9]", "", t)
        if _TAG_RE.match(t) and t.lower() not in _GENERIC_TAGS and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out[:3]


def valid_starts(starts, n: int) -> list[int] | None:
    """Paragraph-start indices the model returned, validated; None when unusable."""
    try:
        s = [int(x) for x in starts]
    except (TypeError, ValueError):
        return None
    s = sorted(set(x for x in s if 0 <= x < n))
    if not s:
        return None
    if s[0] != 0:
        s = [0] + s
    return s[:MAX_PARAGRAPHS] if len(s) >= 2 else None


def ask_model(conn, category: str, label: str, title: str, body: str, sentences: list[str] | None) -> dict:
    """{'hashtags': [...], 'paragraph_starts': [...]|None}. Any failure returns {} and the code falls back -- formatting must never block a card."""
    from pipeline import llm, post_format
    try:
        raw = llm.generate_write(conn, category, _hashtag_prompt(label, title, body, sentences))
        parsed = post_format.parse_llm_json(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        logger.exception("verbatim: the model could not be used for %s -- falling back to code defaults", category)
        return {}


def code_starts(n: int) -> list[int]:
    return list(range(0, n, FALLBACK_SENTENCES_PER_PARAGRAPH))[:MAX_PARAGRAPHS]


def paragraphs_from_starts(sentences: list[str], starts: list[int]) -> list[str]:
    bounds = starts + [len(sentences)]
    return [" ".join(sentences[bounds[i]:bounds[i + 1]]) for i in range(len(starts))]


# ------------------------------------------------------------------ headers, hashtags, polls
def pick_header(category: str, seed: int) -> str | None:
    """A header from the category's pool. `seed` is a counter (how many posts the category has had), so the pool cycles through all its headers before repeating one, in a fixed shuffled order."""
    import random
    pool = list((load_config().get("headers") or {}).get(category) or [])
    if not pool:
        return None
    random.Random(f"wtal:{category}").shuffle(pool)
    return pool[seed % len(pool)]


def default_hashtags(category: str) -> list[str]:
    return list((load_config().get("hashtag_defaults") or {}).get(category) or [])


def poll_for(category: str, seed: int) -> dict | None:
    cfg = load_config().get("polls") or {}
    pool = list(cfg.get(category) or []) + list(cfg.get("default") or [])
    return pool[seed % len(pool)] if pool else None


def category_settings(category: str) -> dict:
    return (load_config().get("categories") or {}).get(category) or {}


# ------------------------------------------------------------------ building the post
def _spoiler_wrap(paragraphs: list[str], mode: str) -> list[str]:
    """Return paragraph HTML (already escaped) with the payoff inside <tg-spoiler>. mode: 'last_sentence' | 'last_paragraph' | 'none'."""
    html_paras = [esc(p) for p in paragraphs]
    if mode == "none" or not paragraphs:
        return html_paras
    if mode == "last_paragraph":
        if len(paragraphs) >= 2:
            html_paras[-1] = f"<tg-spoiler>{html_paras[-1]}</tg-spoiler>"
        return html_paras
    sents = split_sentences(paragraphs[-1])
    total = sum(len(split_sentences(p)) for p in paragraphs)
    if total >= 2 and len(sents) >= 1:
        head = " ".join(sents[:-1])
        last = sents[-1]
        html_paras[-1] = (esc(head) + " " if head else "") + f"<tg-spoiler>{esc(last)}</tg-spoiler>"
    return html_paras


def source_line(category: str, payload: dict) -> str:
    name = (payload.get("source_name") or "").strip()
    if not name:
        return ""
    template = category_settings(category).get("source_line") or "Source: {source}"
    return template.format(source=name)


def original_text(payload: dict) -> str:
    """The item as the source gave it (title, then body): what the card shows below the draft, and what the exact-match check compares against."""
    parts = [payload.get("title") or "", payload.get("body") or ""]
    return "\n\n".join(p for p in parts if p).strip()


def exact_match(payload: dict, post_title_html: str | None, post_body_paragraph_html: list[str]) -> bool:
    title_ok = strip_ws(visible_text(post_title_html or "")) == strip_ws(payload.get("title") or "")
    body_ok = strip_ws(visible_text("".join(post_body_paragraph_html))) == strip_ws(payload.get("body") or "")
    return title_ok and body_ok


def unformatted(payload: dict) -> str:
    """The fallback: the source's text with nothing added or arranged -- no header, no tags, no spoiler."""
    return esc(original_text(payload))


def build(conn, category: str, raw_item: dict, *, use_model: bool = True, seed: int | None = None) -> dict:
    """{'text': post HTML, 'ok': bool (False = the exact-words check failed and `text` is the unformatted original), 'original_html': the block the card shows below the draft, 'tags': [...]}."""
    payload = dict(raw_item.get("payload") or {})
    cfg = category_settings(category)
    label = cfg.get("label") or category
    title = clean_spacing(payload.get("title") or "")
    parts = [clean_spacing(p) for p in (payload.get("body_parts") or []) if p and p.strip()]
    if not parts and payload.get("body"):
        parts = [clean_spacing(p) for p in str(payload["body"]).split("\n\n") if p.strip()]
    body_joined = "\n\n".join(parts)
    # what the check compares against: the item EXACTLY as the source gave it (not the spacing-cleaned copy), so a cleaning bug cannot hide
    original = dict(payload, title=payload.get("title") or "", body=payload.get("body") or "\n\n".join(p for p in (payload.get("body_parts") or []) if p))

    # paragraph breaks: keep the source's own paragraphs; offer one long block to the model
    sentences = None
    if len(parts) == 1 and len(split_sentences(parts[0])) >= MIN_SENTENCES_FOR_BREAKS:
        sentences = split_sentences(parts[0])
    model = ask_model(conn, category, label, title, body_joined, sentences) if use_model else {}
    tags = valid_hashtags(model.get("hashtags")) or default_hashtags(category)
    if sentences:
        starts = valid_starts(model.get("paragraph_starts"), len(sentences)) or code_starts(len(sentences))
        parts = paragraphs_from_starts(sentences, starts)

    spoiler_mode = cfg.get("spoiler", "last_sentence" if SPOILER_DEFAULT else "none")
    body_html = _spoiler_wrap(parts, spoiler_mode) if parts else []
    title_html = f"<b>{esc(title)}</b>" if title else None

    ok = exact_match(original, title_html, body_html)
    original_html = original_block(original)
    if not ok:
        logger.error("verbatim: EXACT-WORDS CHECK FAILED for category=%s title=%r -- sending the original unformatted", category, title[:80])
        return {"text": unformatted(original), "ok": False, "original_html": original_html, "tags": tags}

    if seed is None:
        seed = _category_counter(conn, category)
    header = pick_header(category, seed)
    tag_line = esc(" ".join(tags)) if tags else ""
    src = source_line(category, payload)
    blocks = []
    if header:
        blocks.append(esc(header))
    top = []
    if title_html:
        top.append(title_html)
    if tag_line and title_html:
        top.append(tag_line)                                    # hashtags on the line below the title
    if top:
        blocks.append("\n".join(top))
    blocks.extend(body_html)
    if tag_line and not title_html:
        blocks.append(tag_line)                                 # no title: the tags close the post, above the source line
    if src:
        blocks.append(esc(src))
    return {"text": "\n\n".join(blocks), "ok": True, "original_html": original_html, "tags": tags}


def _category_counter(conn, category: str) -> int:
    """How many cards the category has had so far: the header pool advances by one per card."""
    if conn is None:
        return 0
    from pipeline.db import dict_cursor
    with dict_cursor(conn) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM notifications nf JOIN raw_items r ON r.id = ANY(nf.candidate_raw_item_ids) WHERE r.category = %s", (category,))
        return int(cur.fetchone()["n"] or 0)


def original_block(payload: dict) -> str:
    """The original item, as fetched, for the approval card: source, text (cut for the card), link."""
    text = original_text(payload)
    if len(text) > CARD_ORIGINAL_MAX_CHARS:
        text = text[:CARD_ORIGINAL_MAX_CHARS].rstrip() + " [cut for the card]"
    src = payload.get("source_name") or "source"
    link = payload.get("source_url") or payload.get("link") or ""
    return f"<b>Original</b> ({esc(src)})\n{esc(text)}" + (f"\n{esc(link)}" if link else "")
