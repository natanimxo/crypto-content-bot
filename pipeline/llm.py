"""Provider-agnostic LLM layer (Section 4.4). Pipeline code never imports a
provider module directly or hardcodes a model name — it calls generate_triage /
generate_write, and the actual provider+model come from category_config at call
time. Adding a provider is a new module in llm_providers/ plus one line in
PROVIDERS; it never touches notify.py, write_post.py, or anything downstream.
"""

from pipeline import llm_usage
from pipeline.llm_providers import deepseek, gemini, template
from pipeline.score import load_category_config

PROVIDERS = {
    "deepseek-v4-flash": deepseek.generate,
    "gemini-2.5-flash-lite": gemini.generate,
}

# Same providers, returning (text, usage) so generate_write can meter spend
# (pipeline/llm_usage.py). PROVIDERS above stays the registry of what exists.
PROVIDERS_WITH_USAGE = {
    "deepseek-v4-flash": ("deepseek", deepseek.generate_with_usage),
    "gemini-2.5-flash-lite": ("gemini", gemini.generate_with_usage),
}


def generate_triage(conn, category: str, raw_item: dict) -> str:
    """The one-line digest summary (Section 8, step 1). `raw_item` needs at least
    a `payload` key; template-based categories read fields straight out of it."""
    cfg = load_category_config(conn, category)
    model = cfg["triage_model"]

    if model == "template":
        return template.render(category, raw_item)

    provider = PROVIDERS.get(model)
    if not provider:
        raise RuntimeError(f"Unknown triage_model '{model}' for category '{category}'")

    raise NotImplementedError(
        f"Category '{category}' has triage_model='{model}' but no triage prompt "
        f"builder yet — this path is for future unstructured-text categories "
        f"(news, forum posts). Add a prompt builder before wiring one up."
    )


def generate_write(conn, category: str, prompt: str, *, model_override: str | None = None) -> str:
    """Final post write (Section 8, step 2). Only ever called on operator-approved
    items. Pass model_override to force a specific model — used by write_post.py
    to force a specific model for a single call (tests, one-off comparisons)
    category_config.write_model currently points at."""
    cfg = load_category_config(conn, category)
    model = model_override or cfg["write_model"]

    provider = PROVIDERS.get(model)
    if not provider:
        raise RuntimeError(f"Unknown write_model '{model}' for category '{category}'")
    provider_name, with_usage = PROVIDERS_WITH_USAGE[model]
    try:
        text, usage = with_usage(prompt)
    except Exception:
        # A failed call still counts toward the daily call-count guard: a loop
        # of failing calls is exactly the runaway it exists to catch.
        llm_usage.record(conn, provider=provider_name, model=model, category=category, usage=None, ok=False)
        raise
    llm_usage.record(conn, provider=provider_name, model=usage.get("model") or model, category=category,
                     usage=usage, ok=True)
    return text
