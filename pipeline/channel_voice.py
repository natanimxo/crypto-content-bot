"""Channel-level voice (2026-11-14, operator direction).

Subscribers reported that every channel reads identically. The cause was
structural: voice lived in category_config.prompt_notes, so the same news story
was generated from the same prompt whether it landed on Crypto Wall Street or
Alpha Edge. Now the CHANNEL owns voice -- who is talking, the shape of the post,
its length, the person, whether hashtags and our own-data context lines appear --
and the category supplies only facts, a voice-neutral content brief and the
guardrails (pipeline/post_facts.py).

A voice profile is a dict (config/channel_voices.yaml -> channel_config.
voice_profile JSONB):

    name, summary, tone, title_style     prompt text
    title_check                          has_number | question_or_concept | null
    paragraphs: [role, ...]              the post's body structure, in order
    length: {min, max}                   characters of the whole post EXCEPT the
                                         Source line and hashtags (so history /
                                         context blockquotes and risk flags count)
    person                               second | third | null
    hashtags                             bool -- add the category's hashtags
    keep_context_lines                   bool -- keep the history / "Also in our
                                         own tracking" blockquotes (dropped on a
                                         terse wire-service channel)
    banned_words                         words the post must not contain

Everything checkable is checked in code on the generated output (find_voice_
issues), not just requested in the prompt -- same "prompts get ignored, a
code-level guard doesn't" discipline as the overclaim/neutrality/speculation
guards. Two things are NOT exactly checkable and are honest about it:
"no adjectives" is a prompt rule plus a banned-word list of the evaluative
words that actually show up (a real part-of-speech check would need an NLP
dependency), and the paragraph ROLES (setup -> mechanic -> how to spot it) are
prompt-enforced -- code can verify there are three paragraphs, not that the
third one is useful.

GUARDRAILS BEAT VOICE, always. The composed prompt says so, and says what to do
on a conflict (write around the voice instruction); the guardrails live next to
their structural guards in pipeline/write_post.py and no voice profile can
switch one off.
"""

import html
import os
import re
from dataclasses import dataclass, field

import yaml

from pipeline.db import dict_cursor

VOICES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "channel_voices.yaml")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SECOND_PERSON_RE = re.compile(r"\b(you|your|yours|yourself|you're|you'll|you've|you'd)\b", re.IGNORECASE)
_FIRST_OR_SECOND_RE = re.compile(r"\b(you|your|yours|yourself|we|our|ours|us|let's)\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d")
_QUESTION_OPENERS = {
    "why", "how", "what", "when", "where", "who", "which", "is", "are", "can", "does",
    "do", "should", "inside", "anatomy", "understanding",
}


class VoiceProfileError(ValueError):
    pass


@dataclass(frozen=True)
class VoiceProfile:
    channel: str
    name: str
    summary: str
    tone: str
    title_style: str
    paragraphs: list[str]
    length_min: int
    length_max: int
    person: str | None = None
    hashtags: bool = True
    keep_context_lines: bool = True
    title_check: str | None = None
    banned_words: list[str] = field(default_factory=list)


def parse_profile(channel: str, d: dict) -> VoiceProfile:
    """Validate loudly -- a half-specified profile must fail at load, not
    silently generate off-brand posts."""
    try:
        paragraphs = list(d["paragraphs"])
        length = d["length"]
        profile = VoiceProfile(
            channel=channel, name=d["name"], summary=d["summary"], tone=d["tone"],
            title_style=d["title_style"], paragraphs=paragraphs,
            length_min=int(length["min"]), length_max=int(length["max"]),
            person=d.get("person"), hashtags=bool(d.get("hashtags", True)),
            keep_context_lines=bool(d.get("keep_context_lines", True)),
            title_check=d.get("title_check"), banned_words=list(d.get("banned_words") or []),
        )
    except (KeyError, TypeError) as exc:
        raise VoiceProfileError(f"voice profile for '{channel}' is missing/invalid: {exc!r}") from exc
    if not paragraphs or not all(isinstance(x, str) and x.strip() for x in paragraphs):
        raise VoiceProfileError(f"voice profile for '{channel}': paragraphs must be a non-empty list of role strings")
    if profile.length_min <= 0 or profile.length_max < profile.length_min:
        raise VoiceProfileError(f"voice profile for '{channel}': bad length range")
    if profile.person not in (None, "second", "third"):
        raise VoiceProfileError(f"voice profile for '{channel}': person must be second|third|null")
    if profile.title_check not in (None, "has_number", "question_or_concept"):
        raise VoiceProfileError(f"voice profile for '{channel}': unknown title_check {profile.title_check!r}")
    return profile


def load_profiles_from_yaml(path: str = VOICES_PATH) -> dict[str, VoiceProfile]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return {channel: parse_profile(channel, d) for channel, d in raw.items()}


def load_profile(conn, channel: str) -> VoiceProfile | None:
    """The channel's profile from channel_config.voice_profile, or None -- None
    means "use the legacy category voice" (CoinCraft today; any channel whose
    profile hasn't been seeded)."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT voice_profile FROM channel_config WHERE channel = %s", (channel,))
        row = cur.fetchone()
    raw = row["voice_profile"] if row else None
    return parse_profile(channel, raw) if raw else None


# ---------------------------------------------------------------- measuring

def plain_text(post_html: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub("", post_html))).strip()


def measure(post_html: str) -> int:
    """Characters a reader sees: tags stripped, whitespace collapsed to single
    spaces. Callers pass the post WITHOUT its Source line and hashtags."""
    return len(plain_text(post_html))


# ------------------------------------------------------------------ prompt

def build_prompt(profile: VoiceProfile, *, display_name: str, spec, region_profile: str,
                 raw_item: dict, overhead_chars: int) -> str:
    """Channel voice + category facts/brief/guardrails + the output contract."""
    n = len(profile.paragraphs)
    prose_min = max(0, profile.length_min - overhead_chars)
    prose_max = max(prose_min, profile.length_max - overhead_chars)
    # Aim at the middle of the range, slightly low: a first attempt that targets the upper limit
    # lands a few characters over it (observed 2026-11-14: 508 vs 500, 469 vs 450, 461 vs 450).
    prose_aim = prose_min + int((prose_max - prose_min) * 0.45)
    overrides = getattr(spec, "role_overrides", None) or {}
    roles = "\n".join(f"  {i}. {overrides.get(i, role)}" for i, role in enumerate(profile.paragraphs, 1))
    audience = f"\nThe story's own subject matter is aimed at: {spec.audience}." if spec.audience else ""

    rules = []
    if profile.person == "second":
        rules.append("Address the reader directly in the second person (\"you\", \"your\").")
    elif profile.person == "third":
        rules.append("Third person only: never use \"you\", \"your\", \"we\" or \"our\".")
    if profile.banned_words:
        rules.append("Never use any of these words: " + ", ".join(profile.banned_words) + ".")
    rules_block = ("\n" + "\n".join(f"- {r}" for r in rules)) if rules else ""

    facts = spec.facts(raw_item["payload"])
    if profile.title_check == "has_number":
        # Found by the first real run (2026-11-14): a hard "title must contain a figure" rule
        # made the writer fail three drafts in a row on a story with no number in it -- the only
        # way to comply was to invent one. The rule applies only when the facts have a figure.
        rules.append("The title must contain at least one of the story's figures." if _NUMBER_RE.search(facts)
                     else "This story has no figures: lead the title with the concrete fact instead, and "
                          "NEVER invent or estimate a number.")
        rules_block = "\n" + "\n".join(f"- {r}" for r in rules)

    guardrails = spec.guardrails
    if region_profile == "us" and spec.us_guardrail:
        guardrails += " " + spec.us_guardrail

    return f"""You are writing one Telegram post for the channel "{display_name}".{audience}

CHANNEL VOICE -- {profile.name}
{profile.summary}
Tone: {profile.tone}
Title: {profile.title_style}{rules_block}

STRUCTURE -- the body is exactly {n} paragraph{'s' if n != 1 else ''}, in this order:
{roles}

LENGTH -- the finished post must be {profile.length_min}-{profile.length_max} characters, enforced by code. A fixed {overhead_chars} characters are added around your prose (the leading emoji and any context lines), so the title plus all paragraphs together must total between {prose_min} and {prose_max} characters. AIM FOR ABOUT {prose_aim}: drafts that aim for the limit overshoot it. Count your characters before answering.

{facts}

WHAT THE POST MUST COVER (substance only -- the voice above decides how it is said)
{spec.content_brief}

GUARDRAILS -- these override everything above. If any voice, structure or length instruction conflicts with a guardrail, follow the guardrail and write around the instruction (for example, a paragraph that asks what the reader should do becomes what it means or what to watch, if the guardrail forbids advice):
{guardrails}

Return ONLY a JSON object (no markdown fence, no commentary) with exactly this shape:
{{
  "title": "the post title, in this channel's title style. No emoji.",
  "paragraphs": [{', '.join(f'"paragraph {i}"' for i in range(1, n + 1))}]
}}
"paragraphs" must hold exactly {n} non-empty string{'s' if n != 1 else ''}. No emoji anywhere in your output.
"""


# ------------------------------------------------------------------ checks

def _prose(parsed: dict) -> str:
    return " ".join([str(parsed.get("title", ""))] + [str(x) for x in parsed.get("paragraphs", [])])


def find_voice_issues(profile: VoiceProfile, parsed: dict, post_chars: int | None,
                      facts_have_figures: bool = True) -> list[str]:
    """Deterministic voice checks on the generated output. Each issue is a
    plain-English sentence that is fed straight back to the model on a retry,
    so the wording is written to be acted on."""
    issues = []
    paragraphs = parsed.get("paragraphs")
    title = str(parsed.get("title") or "").strip()
    if not isinstance(paragraphs, list) or len(paragraphs) != len(profile.paragraphs) \
            or not all(isinstance(x, str) and x.strip() for x in paragraphs):
        issues.append(f"the body must be exactly {len(profile.paragraphs)} non-empty paragraph(s) in the "
                      f"\"paragraphs\" array (got {len(paragraphs) if isinstance(paragraphs, list) else 'none'})")
        return issues  # nothing else is meaningful on a malformed body
    if not title:
        issues.append("the title is empty")

    prose = _prose(parsed)
    if post_chars is not None and not (profile.length_min <= post_chars <= profile.length_max):
        mid = (profile.length_min + profile.length_max) // 2
        if post_chars > profile.length_max:
            fix = f"cut about {post_chars - mid} characters (aim for roughly {mid} in total)"
        else:
            fix = f"add about {mid - post_chars} characters (aim for roughly {mid} in total)"
        issues.append(f"the finished post is {post_chars} characters but must be {profile.length_min}-"
                      f"{profile.length_max} -- {fix}")

    if profile.title_check == "has_number" and facts_have_figures and not _NUMBER_RE.search(title):
        issues.append("the title must lead with data and contain at least one figure (a number, amount or percentage)")
    if profile.title_check == "question_or_concept":
        first = re.sub(r"[^\w']", "", title.split()[0].lower()) if title.split() else ""
        if not (title.endswith("?") or first in _QUESTION_OPENERS):
            issues.append("the title must be a question or name a concept to learn (e.g. start with Why / How / "
                          "What, or end in a question mark), not a news headline")

    if profile.person == "second" and not _SECOND_PERSON_RE.search(prose):
        issues.append("write in the second person -- address the reader as \"you\"/\"your\"")
    if profile.person == "third" and _FIRST_OR_SECOND_RE.search(prose):
        issues.append("write in the third person only -- remove \"you\", \"your\", \"we\", \"our\"")

    for word in profile.banned_words:
        if re.search(rf"\b{re.escape(word)}\b", prose, re.IGNORECASE):
            issues.append(f"remove the banned word \"{word}\"")
    return issues
