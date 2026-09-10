"""Deterministic post assembly (Section 8, redesigned 2026-09-10).

The LLM's job is narrowed to writing prose only — it returns structured JSON
(title / narrative / why_it_matters), never raw HTML. Every structural element
around that prose — the history blockquote, the risk flag, the source
attribution, the hashtags, emoji placement, blank-line spacing — is computed
here in plain Python from real data (payload, history, config), never
invented by the model.

This is a deliberate reliability choice: asking an LLM to freely emit HTML
that must hit a specific structure (single-emoji budget, character target,
disable_web_page_preview-safe markup) risks drift on every single generation.
Constraining the model to "write these three pieces of prose" and assembling
the rest ourselves guarantees format compliance regardless of model quirks —
the same philosophy as pipeline/score.py keeping scoring 100% deterministic
and pipeline/llm_providers/template.py keeping structured-data triage LLM-free.
"""

import html
import json


def parse_llm_json(raw: str) -> dict:
    """LLMs sometimes wrap JSON in a markdown code fence despite instructions
    not to — strip that before parsing. Raises json.JSONDecodeError (letting
    the caller decide how to handle a malformed response) rather than
    swallowing it, since a malformed post should never silently ship."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())


def assemble_post(*, emoji: str, title: str, narrative: str, why_it_matters: str,
                   history_line: str | None, risk_line: str | None,
                   source_name: str | None, hashtags: list[str]) -> str:
    """Builds the final HTML post text. Every input here is either
    deterministically computed (history_line, risk_line) or config (emoji,
    source_name, hashtags) or LLM prose (title, narrative, why_it_matters) —
    LLM output is HTML-escaped since only this function ever emits real HTML
    tags (<b>, <blockquote>), never the model.

    No hyperlinks anywhere (2026-09-10, operator direction) — source_name is
    plain-text attribution ("Source: DefiLlama"), not a clickable link. Even
    so, callers should still pass disable_web_page_preview=true on the actual
    Telegram send as a defensive measure — this function can't guarantee the
    LLM's free-form prose never happens to contain something URL-shaped.

    Target ~400-700 chars, one emoji in the title, ⚠️ only when risk_line is
    present — callers are responsible for keeping narrative/why_it_matters
    within budget via the prompt; this function doesn't truncate.
    """
    # quote=False: this text goes into HTML *content* (between tags), not an
    # attribute value, so a bare apostrophe/quote is fine and should render as
    # itself — html.escape's default (quote=True) would turn every apostrophe
    # into a literal "&#x27;" on the subscriber's screen, which is wrong here.
    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    lines = [f"{emoji} <b>{esc(title)}</b>", ""]
    lines.append(esc(narrative))
    lines.append("")
    lines.append(esc(why_it_matters))

    if history_line:
        lines.append("")
        lines.append(f"<blockquote>{esc(history_line)}</blockquote>")

    if risk_line:
        lines.append("")
        lines.append(f"⚠️ {esc(risk_line)}")

    if source_name:
        lines.append("")
        lines.append(f"Source: {esc(source_name)}")

    if hashtags:
        lines.append("")
        lines.append(" ".join(hashtags))

    return "\n".join(lines)
