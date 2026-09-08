# Multi-Channel Intelligence & Content Bot

Selective, memory-aware crypto/business content pipeline for Telegram. Full design
in the technical spec (kept alongside this repo, not checked in here — ask the
project owner for it if you need the full rationale/section numbers referenced
in code comments throughout this repo).

**Current build phase (Section 16, Phase 1 — MVP):** one category, one channel,
full pipeline: DeFi yields (DefiLlama, free/keyless) → Crypto Notebook.
Everything is config-driven, so Phase 2+ (more categories/channels) is mostly
config + one new collector/scorer/prompt-builder per category, not new
architecture — see `pipeline/score.py`, `pipeline/write_post.py`, and
`pipeline/llm_providers/template.py` for the registry pattern each new category
plugs into.

## Architecture

```
collect (GitHub Actions, every 4h)
  -> store (Postgres/Supabase, UNIQUE constraint = idempotent)
  -> score (100% deterministic Python, zero LLM calls)
  -> notify (only if new candidates cleared threshold — Telegram message w/ inline buttons)
approve (operator taps a button; GitHub Actions polls every 5 min)
  -> write (LLM, only on approved items)
  -> publish confirm (operator taps Publish/Cancel)
  -> publish (Telegram Bot API sendMessage to the public channel)
```

No server, no VPS. GitHub Actions runners are the only compute, and the
repo **must stay public** for scheduled (`cron`) triggers to run reliably on
the free plan.

## One-time setup

### 1. Supabase (free Postgres)

1. Create a project at [supabase.com](https://supabase.com) (free tier).
2. Project Settings → Database → Connection string (URI, "Session" pooler mode)
   → this is your `DATABASE_URL`.
3. Apply the schema:
   ```bash
   pip install -r requirements.txt
   cp .env.example .env   # fill in DATABASE_URL at minimum
   python scripts/apply_schema.py
   ```

### 2. Telegram bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token
   (`TELEGRAM_BOT_TOKEN`).
2. DM your new bot once (so it's allowed to message you back), then get your own
   numeric user id from [@userinfobot](https://t.me/userinfobot)
   (`TELEGRAM_ALLOWED_USER_IDS`, comma-separated if more than one operator).
3. Your operator chat id (`TELEGRAM_OPERATOR_CHAT_ID`) is the same DM chat — hit
   `https://api.telegram.org/bot<token>/getUpdates` after DMing the bot and read
   `message.chat.id` off the response.
4. For each channel, add the bot as an admin with post permissions, post
   anything in it, then read its `chat.id` the same way via `getUpdates`
   (channel ids look like `-100xxxxxxxxxx`). These go directly into the
   `chat_id` field for that channel in `config/channel_config.yaml` — not env
   vars — and reach the DB via `scripts/seed_config.py`.

### 3. LLM keys

- `DEEPSEEK_API_KEY` — required (default triage + write model).
  [platform.deepseek.com](https://platform.deepseek.com)
- `GEMINI_API_KEY` — optional, only needed if you switch a category's
  `triage_model` to `gemini-2.5-flash-lite`. [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- `ANTHROPIC_API_KEY` — optional, only needed once a category's `write_model` is
  benchmarked to `claude-sonnet-5` (Section 4.3), or during a trial period.
  [console.anthropic.com](https://console.anthropic.com)

### 4. Seed config into the DB

```bash
python scripts/seed_config.py
```

This reads `config/category_config.yaml` and `config/channel_config.yaml` and
upserts them into `category_config` / `channel_config`. Re-run it any time you
want to reset DB-side tuning back to what's checked into git.

### 5. GitHub repo + Actions Secrets

1. Repo must be **public** (Section 4.1 — free scheduled Actions requirement).
2. Settings → Secrets and variables → Actions → New repository secret, one per:
   `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OPERATOR_CHAT_ID`,
   `TELEGRAM_ALLOWED_USER_IDS`, `DEEPSEEK_API_KEY`, and optionally
   `GEMINI_API_KEY` / `ANTHROPIC_API_KEY`.
3. Actions tab → enable workflows if prompted.
4. Run `Collect` once manually (Actions → Collect → Run workflow) to confirm the
   full collect → score → notify path works before waiting for the cron.

## Local development

```bash
python scripts/collect_cycle.py     # one collect+score+notify cycle
python -m bot.approval_poller       # one poll for operator taps
```

Both are safe to re-run — collection is idempotent (`UNIQUE(source_id,
external_id)`), and the poller tracks its own Telegram update offset in
`telegram_poll_state`.

## Repo structure

```
collectors/       one module per category, self-contained (fetch -> filter -> store)
pipeline/          shared, category-agnostic machinery
  score.py          deterministic scoring, one function per category (registry pattern)
  select_candidates.py   threshold + cooldown + soft-cap filtering
  llm.py            provider-agnostic dispatch (Section 4.4)
  llm_providers/    deepseek.py, gemini.py, anthropic.py, template.py (zero-LLM)
  write_post.py     final post writer, one prompt builder per category
  publish.py         Telegram sendMessage + posts bookkeeping
  telegram_api.py    thin Bot API wrapper shared by publish/notify/poller
  alerts.py          "N consecutive failures" operator alert
bot/
  notify.py          per-cycle operator digest (Section 9)
  approval_poller.py approve/edit/reject + publish confirm + benchmark A/B state machine
config/            category_config.yaml, channel_config.yaml — versioned baseline (DB is live copy)
db/schema.sql      full schema, safe to re-run (IF NOT EXISTS throughout)
scripts/           setup + orchestration entry points
.github/workflows/ collect.yml (4h), approval-poll.yml (5min)
```

## Extending to a new category (Phase 2+)

1. Write `collectors/<category>.py` (copy `defi_yields.py`'s shape: fetch → filter
   obvious noise → `insert_raw_item`).
2. Register a scorer in `pipeline/score.py` (`@register_scorer("<category>")`) —
   four 0-100 components (impact/novelty/credibility/actionability).
3. Register a triage renderer: a `template.py` function for structured data, or
   wire a real prompt through `pipeline/llm.py`'s `generate_triage` for
   unstructured prose (news, forum posts).
4. Register a prompt builder in `pipeline/write_post.py`
   (`@register_prompt_builder("<category>")`).
5. Add the category to `config/category_config.yaml` (weights, threshold,
   cooldown, cap, models, voice/prompt_notes) and to the relevant channel(s) in
   `config/channel_config.yaml`.
6. Add it to `COLLECTORS` in `scripts/collect_cycle.py`.
7. `python scripts/seed_config.py` to push the new config into the DB.

Never let category-specific logic leak into `store.py`, `select_candidates.py`,
`publish.py`, or `telegram_api.py` — if it seems to need to, that's a sign the
category needs its own registered function instead (per Section 0's read-me-first
rule).
