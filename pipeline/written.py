"""We Talk A Lot, WRITTEN mode (operator 2026-11-21): for a category with `mode: written` in config/wtal.yaml, DeepSeek writes the whole post from the item's raw facts, in the operator's own system prompt below.
`mode: raw` (the default, and what jokes, shower thoughts and satire keep) is pipeline/verbatim.py: the source's exact words, formatted by code.

What the model does and does not do here:
  * It writes the post (Telegram bold as **x**, spoiler as ||x||) at temperature 0.9, or answers SKIP when the item is dry or boring. A SKIP drops the item (held, never deleted, with the reason on its payload, and the
    item's text logged at DEBUG) and the next candidate is tried; after MAX_SKIPS_PER_CATEGORY_PER_CYCLE skips (a guard failure counts as one) the category stops for that cycle, so it can never loop.
  * CODE adds the hashtags after the post (config/wtal.yaml `hashtags`), converts **bold** and ||spoiler|| to Telegram HTML, strips any hashtag or URL the model wrote anyway, and runs the facts guard.
  * The exact-words check and the copy guard are NOT used for a written category (the point is not to copy). The guard that stays is on FACTS: every number and year in the post must appear in the source text, and every
    capitalised name must too (apart from a short list of common words), a stock score like -1000 PTS in the header aside. One retry with the offending items named; a second failure drops the item like a SKIP.
  * The original source text and link are shown below the draft on the card (verbatim.original_block), as for raw cards.

`written_live` in the config is the master switch: until it is true a category configured `written` still takes the raw path, so the operator can judge sample cards before anything changes for real (`live_mode`).
"""

import html
import logging
import re

from pipeline import verbatim

logger = logging.getLogger(__name__)

TEMPERATURE = 0.9
MAX_TOKENS = 700
MAX_SKIPS_PER_CATEGORY_PER_CYCLE = 5
DRAFT_MAX_CHARS = 3500

SYSTEM_PROMPT = """You are the unhinged, darkly comedic writer for a viral Telegram entertainment channel. Your ONLY job is to turn raw facts, weird news, shower thoughts, and world records into savage, hilarious, jaw-dropping posts that make readers burst out laughing and immediately say "WTF" or "I'm going to hell for laughing."

CRITICAL FILTERING RULE (BORINGNESS GATE ONLY):
- Be ruthless about boring content. Reject 85% of inputs.
- If the raw text is just a plain battle date, a predictable accident, an academic definition with no payoff, or mildly interesting trivia, output ONLY the word: SKIP
- NEVER output SKIP because a joke or historical event is dark, morbid, offensive, or immoral. Only skip items that are DRY or BORING.
- Only proceed if the story has genuine dark irony, absurd human stupidity, chaotic karma, or a shocking twist (benchmark: Robert Liston's 300% mortality surgery, Franz Reichelt jumping off the Eiffel Tower in a parachute coat, or the 1788 Battle of Karánsebes).

WRITING & TONE RULES:
1. VOICE: Write like the funniest, most chaotic friend in a 2 AM group chat. Never sound like Wikipedia, a news anchor, or a textbook.
2. DARK & SAVAGE: Lean hard into dark irony, morbid absurdity, and brutal honesty.
3. NO MORALIZING OR SANITIZING: Never lecture, preach, apologize, or add disclaimers, warnings, sympathy statements, or "lessons learned." Never water down a dark joke or try to make the ending wholesome. Keep the punchline ruthless, bleak, and unapologetically funny (only avoid graphic real-world gore or hate speech/slurs that would get a Telegram channel banned on iOS).
4. ZERO JUNK: Strip all safety warnings, legal disclaimers, citations, URLs, and hashtags. Never output hashtags.
5. STRUCTURE (Max 4 short paragraphs):
   - Line 1: Bold episode header (e.g., **[SPOT IN HEAVEN: -1000 PTS]**, **On today's episode of: We should have never invented Wi-Fi**, **On today's episode of: History was drunk**, **On today's episode of: Somebody actually trained for this**).
   - Line 2: A sharp, curiosity-gap hook.
   - Line 3: The chaotic setup in 2–3 short, vivid sentences.
   - Line 4: The darkest, funniest, or most ironic punchline wrapped in Telegram spoiler tags like ||this|| with 1–2 fitting emojis."""


# ------------------------------------------------------------------------------------------------ mode
def configured_mode(category: str) -> str:
    m = ((verbatim.load_config().get("categories") or {}).get(category) or {}).get("mode", "raw")
    return m if m in ("raw", "written") else "raw"


def live_mode(category: str) -> str:
    """The mode actually used: 'written' only when the category is configured written AND the master switch `written_live` is on."""
    cfg = verbatim.load_config()
    if configured_mode(category) == "written" and cfg.get("written_live", False):
        return "written"
    return "raw"


# ------------------------------------------------------------------------------------------------ the model's output -> a post
def is_skip(output: str) -> bool:
    o = output or ""
    if re.sub(r"[\s*_`'\".!:|-]", "", o).upper() == "SKIP":
        return True
    return bool(re.match(r"^[\s*_`'\"]*skip[\s*_`'\"]*(?:[.:!|–—-]|$)", o, re.I | re.M)) and len(o) < 80      # "SKIP - dry trivia", but not a post that begins with the word


def clean_output(output: str) -> str:
    t = (output or "").strip()
    t = re.sub(r"^```[a-z]*\s*|\s*```$", "", t)                 # a code fence around the whole answer
    t = re.sub(r"https?://\S+", "", t)                          # rule 4: no URLs
    t = re.sub(r"(?<!\w)#\w+", "", t)                           # rule 4: no hashtags (code adds them)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


_TAG_TOKEN = re.compile(r"</?(b)>")


def _balanced(h: str) -> bool:
    stack = []
    for m in _TAG_TOKEN.finditer(h):
        if m.group(0).startswith("</"):
            if not stack or stack.pop() != m.group(1):
                return False
        else:
            stack.append(m.group(1))
    return not stack


def to_html(text: str) -> str:
    """The model's **bold** and ||spoiler|| -> Telegram HTML (parse mode HTML); everything else is escaped. Mis-nested or stray markers are removed rather than sent broken."""
    t = html.escape(text, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"\|\|(.+?)\|\|", r"\1", t, flags=re.S)                  # no spoilers anywhere (operator 2026-11-21): ||x|| is plain text
    t = t.replace("**", "").replace("||", "")
    if not _balanced(t):
        t = _TAG_TOKEN.sub("", t)
    return t


# ------------------------------------------------------------------------------------------------ the facts guard (names, numbers, years)
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_NAME = re.compile(r"\b[A-Z][a-z]{2,}(?:-[A-Z][a-z]+)?\b")
_COMMON_CAPS = {w.lower() for w in (
    "Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June July August September October November December "
    "God Hell Heaven Satan Devil Earth Internet Telegram Google Wikipedia Darwin Christmas Easter Santa Wikipedia Guinness Olympics Olympic Nobel Oscar Oscars Netflix Twitter Facebook Instagram "
    "The And But For Not You Your This That When What Who Why How Yes Nope Well Oh Meanwhile Spoiler Breaking Newsflash Congratulations Congrats Rest Peace Plot Twist Moral Lesson").split()}


def _nums(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text or "")}


_PTS = re.compile(r"[-+]?\d[\d,]*\s*PTS", re.I)


def _guard_text(text: str) -> str:
    """The text the guard reads: the whole post, except a stock score in the header ("-1000 PTS" is a joke, not a fact). The header is NOT exempt otherwise: an invented number or name there ("AISLE 7") is caught too."""
    return _PTS.sub(" ", (text or "").replace("**", " ").replace("||", " "))


def guard_issues(post_text: str, source_text: str) -> list[str]:
    """Invented numbers, years and names: a list of plain-English problems (empty = clean). The check runs on the model's own text, header included (a stock '-1000 PTS' score aside)."""
    body = _guard_text(post_text)
    issues = []
    src_nums = _nums(source_text)
    for n in sorted(_nums(body) - src_nums):
        issues.append(f'the number or year "{n}" is not in the source')
    src_low = (source_text or "").lower()
    seen = set()
    for m in _NAME.finditer(body):
        tok = m.group(0)
        low = tok.lower()
        prev = body[:m.start()].rstrip()
        if not prev or prev[-1] in '.!?\n"“‘(|:' or low in _COMMON_CAPS or low in seen:
            continue
        stem = low[:max(4, len(low) - 2)]
        if low in src_low or stem in src_low:
            continue
        seen.add(low)
        issues.append(f'the name "{tok}" is not in the source')
    return issues


# ------------------------------------------------------------------------------------------------ writing one item
def source_text_of(payload: dict) -> str:
    return "\n".join(x for x in ((payload.get("dateline") or "").strip(), (payload.get("title") or "").strip(), (payload.get("body") or "").strip()) if x)


def _user_message(category: str, payload: dict, feedback: list[str] | None) -> str:
    label = verbatim.category_settings(category).get("label") or category
    msg = f"CATEGORY: {label}\nRAW TEXT:\n{source_text_of(payload)}"
    if feedback:
        msg += ("\n\nYour last draft invented details that are not in the raw text: " + "; ".join(feedback[:6]) +
                ". Rewrite it using ONLY the names, numbers and years in the raw text (or SKIP).")
    return msg


def _generate(conn, category: str, user_message: str) -> str:
    from pipeline import llm, llm_usage
    with llm_usage.tagged("wtal_written"):           # a SKIP-and-retry loop makes several calls per cycle by design (capped per category); still metered for spend
        return llm.generate_write(conn, category, user_message, system=SYSTEM_PROMPT, temperature=TEMPERATURE, max_tokens=MAX_TOKENS)


def write(conn, category: str, raw_item: dict, *, generate=None) -> dict:
    """{'status': 'ok' | 'skip' | 'guard' | 'error', 'text': Telegram HTML incl. hashtags (ok only), 'tags', 'original_html', 'issues', 'raw_output'}. `generate` is injectable for tests."""
    payload = raw_item.get("payload") or {}
    gen = generate or (lambda msg: _generate(conn, category, msg))
    original_html = verbatim.original_block(payload)
    base = {"status": "error", "text": "", "tags": verbatim.hashtags_for(category), "original_html": original_html, "issues": [], "raw_output": ""}
    feedback = None
    for attempt in range(2):
        try:
            out = gen(_user_message(category, payload, feedback))
        except Exception:
            logger.exception("written: the model failed for %s raw_item_id=%s", category, raw_item.get("raw_item_id"))
            return base
        base["raw_output"] = out
        if is_skip(out):
            return {**base, "status": "skip"}
        cleaned = clean_output(out)
        issues = guard_issues(cleaned, source_text_of(payload))
        if issues:
            base["issues"] = issues
            feedback = issues
            continue
        post_html = to_html(cleaned)
        tags = " ".join(base["tags"])
        text = post_html + (("\n\n" + html.escape(tags, quote=False)) if tags else "")
        if len(text) > DRAFT_MAX_CHARS or not cleaned:
            base["issues"] = ["the post is empty or too long for a card"]
            return {**base, "status": "guard"}
        return {**base, "status": "ok", "text": text, "issues": []}
    return {**base, "status": "guard"}


# ------------------------------------------------------------------------------------------------ trying candidates until one is written, within the skip budget
def hold_item(conn, raw_item_id: int, reason: str) -> None:
    """A dropped item is HELD (never deleted) with the reason on its payload, so it is not offered again and can be released by hand."""
    import json
    from pipeline.db import dict_cursor
    with dict_cursor(conn) as cur:
        cur.execute("UPDATE raw_items SET held = TRUE, payload = payload || %s::jsonb WHERE id = %s", (json.dumps({"writer_dropped": reason}), raw_item_id))
    conn.commit()


def draw(conn, category: str, rows: list[dict], capacity: int, *, writer=write, hold=hold_item, max_skips: int = MAX_SKIPS_PER_CATEGORY_PER_CYCLE) -> tuple[list[tuple[dict, dict]], dict]:
    """Walk the candidate `rows` in order and return ([(row, written)], stats) for up to `capacity` items the model wrote. A SKIP (or a guard failure) drops the item and tries the next, but after `max_skips`
    drops the category stops for this cycle. A model error stops the category too (no hammering a failing API)."""
    accepted: list[tuple[dict, dict]] = []
    stats = {"tried": 0, "skips": 0, "guard_failures": 0, "errors": 0, "written": 0}
    for row in rows:
        if len(accepted) >= capacity or stats["skips"] + stats["guard_failures"] >= max_skips:
            break
        res = writer(conn, category, {"raw_item_id": row["raw_item_id"], "payload": row["payload"]})
        stats["tried"] += 1
        status = res["status"]
        if status == "ok":
            accepted.append((row, res))
            stats["written"] += 1
        elif status == "skip":
            stats["skips"] += 1
            logger.debug("written: SKIP category=%s raw_item_id=%s text=%r", category, row["raw_item_id"], source_text_of(row["payload"])[:600])
            hold(conn, row["raw_item_id"], "SKIP: boring")
        elif status == "guard":
            stats["guard_failures"] += 1
            logger.debug("written: GUARD category=%s raw_item_id=%s issues=%s text=%r", category, row["raw_item_id"], res.get("issues"), source_text_of(row["payload"])[:600])
            hold(conn, row["raw_item_id"], "guard: " + "; ".join(res.get("issues") or [])[:200])
        else:
            stats["errors"] += 1
            break
    return accepted, stats
