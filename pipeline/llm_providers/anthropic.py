"""Claude Sonnet 5 provider — only invoked once a category's write_model is
flipped to it post-benchmark (Section 4.3), or during a trial's dual-generate.
Model id: claude-sonnet-5. Uses the raw Messages API via requests rather than the
anthropic SDK, to keep requirements.txt minimal for a system that expects to use
this provider rarely.
"""

import os

from pipeline.http import post_json

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL_ID = "claude-sonnet-5"


def generate(prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    body = {
        "model": MODEL_ID,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    resp = post_json(API_URL, json_body=body, headers=headers)
    return "".join(block["text"] for block in resp["content"] if block["type"] == "text").strip()
