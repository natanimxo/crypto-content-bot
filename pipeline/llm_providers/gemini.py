"""Gemini provider — free-tier alternative triage model (Section 4.2).

Model id read from GEMINI_MODEL_ID (default `gemini-2.5-flash-lite`) rather than
hardcoded, same reasoning as deepseek.py — verify current free-tier quota/model
naming at ai.google.dev before depending on the $0 assumption.
"""

import os

from pipeline.http import post_json

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def generate(prompt: str, *, max_tokens: int = 800) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    model = os.environ.get("GEMINI_MODEL_ID", "gemini-2.5-flash-lite")
    url = f"{BASE_URL}/{model}:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.4},
    }
    # Same reasoning as deepseek.py's timeout=60 (2026-09-14): a completion
    # call is a slower workload than http.py's 30s default was tuned for.
    resp = post_json(url, json_body=body, timeout=60)
    return resp["candidates"][0]["content"]["parts"][0]["text"].strip()
