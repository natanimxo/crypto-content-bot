-- Multi-Channel Intelligence & Content Bot — schema
-- Run once against the Supabase Postgres instance (see README for how).
-- Every table here is config-driven: adding a category or channel is a row insert,
-- never a schema change.

CREATE TABLE IF NOT EXISTS sources (
    id SERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,          -- 'api', 'rss', 'scrape'
    config JSONB NOT NULL,
    enabled BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS raw_items (
    id SERIAL PRIMARY KEY,
    source_id INT REFERENCES sources(id),
    category TEXT NOT NULL,
    external_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    collected_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source_id, external_id)
);

-- Category-level config: thresholds, weights, cooldowns, model choice — all tunable, no hardcoding
CREATE TABLE IF NOT EXISTS category_config (
    category TEXT PRIMARY KEY,
    score_weights JSONB NOT NULL,        -- impact/novelty/credibility/actionability weights
    review_threshold NUMERIC DEFAULT 50, -- filters what enters the digest AND what reaches an LLM at all
    cooldown_hours NUMERIC DEFAULT 6,    -- suppress same-topic repeats within this window
    soft_daily_cap INT,                   -- optional pacing cap, NULL = no cap
    triage_model TEXT DEFAULT 'deepseek-v4-flash',  -- or 'gemini-2.5-flash-lite', or 'template' for zero-LLM categories
    write_model TEXT DEFAULT 'deepseek-v4-flash',   -- or 'claude-sonnet-5' once benchmarked in for this category
    write_benchmark_status TEXT DEFAULT 'trial',    -- 'trial' | 'settled_deepseek' | 'settled_sonnet'
    label TEXT,                                    -- notification eyebrow / routing header, e.g. "🌾 DEFI YIELDS"
    voice TEXT,                                     -- e.g. 'educational', 'market_summary', 'opportunity_framed'
    prompt_notes TEXT,                               -- freeform voice/framing notes fed into the write prompt (Section 8)
    -- Subscriber-facing post template fields (2026-09-10 delivery/formatting
    -- overhaul). emoji is the single leading emoji on every post's title line
    -- (Section 8's one-emoji budget). display_label/hashtags are config, not
    -- hardcoded, so wording can change without a redeploy — display_label is
    -- metadata (may differ from the internal `category` key, which stays
    -- unchanged so scoring/dedup/history are unaffected) rather than rendered
    -- in the post body itself, since post titles must be item-specific, never
    -- a category label.
    emoji TEXT,
    display_label TEXT,
    hashtags TEXT[],
    -- Raw-collection noise floor in USD, DB-tunable (2026-09-10, Phase 2 whale
    -- movements) — unlike defi_yields' MIN_TVL_USD (a hardcoded module
    -- constant), this lives in config specifically so it can be tuned without
    -- a redeploy ("tune down if daily volume turns out too thin" — operator
    -- direction). NULL for categories that don't need a dollar-value floor.
    collect_min_usd NUMERIC
);

-- Channel-level config
CREATE TABLE IF NOT EXISTS channel_config (
    channel TEXT PRIMARY KEY,
    categories TEXT[] NOT NULL,
    region_profile TEXT DEFAULT 'default',
    soft_daily_cap INT,
    -- Kept for potential future reactivation of direct publishing, but unused
    -- as of 2026-09-10: the bot never posts to channels directly anymore (see
    -- pipeline/publish.py) — the operator forwards the labeled text themselves.
    chat_id TEXT,
    display_name TEXT   -- human-readable name for the label header, e.g. "Crypto Notebook"
);

-- Migration for a DB that already has the old column name from an earlier apply
-- of this schema (harmless no-op on a fresh database).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'channel_config' AND column_name = 'telegram_chat_id'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'channel_config' AND column_name = 'chat_id'
    ) THEN
        ALTER TABLE channel_config RENAME COLUMN telegram_chat_id TO chat_id;
    END IF;
END $$;

-- Migration for a DB from before display_name existed (CREATE TABLE IF NOT
-- EXISTS is a no-op on an existing table, so new columns need an explicit
-- ALTER — harmless no-op on a fresh database via IF NOT EXISTS on the column).
ALTER TABLE channel_config ADD COLUMN IF NOT EXISTS display_name TEXT;

-- Migration for a DB from before the post-template fields existed on
-- category_config (same reasoning as display_name above).
ALTER TABLE category_config ADD COLUMN IF NOT EXISTS emoji TEXT;
ALTER TABLE category_config ADD COLUMN IF NOT EXISTS display_label TEXT;
ALTER TABLE category_config ADD COLUMN IF NOT EXISTS hashtags TEXT[];

-- Migration for a DB from before the header/content message split existed.
ALTER TABLE post_previews ADD COLUMN IF NOT EXISTS content_message_id BIGINT;
ALTER TABLE post_previews ADD COLUMN IF NOT EXISTS content_b_message_id BIGINT;

-- Migration for a DB from before collect_min_usd existed.
ALTER TABLE category_config ADD COLUMN IF NOT EXISTS collect_min_usd NUMERIC;

-- Score per item, per its OWN category — never cross-category
CREATE TABLE IF NOT EXISTS scores (
    id SERIAL PRIMARY KEY,
    raw_item_id INT REFERENCES raw_items(id),
    category TEXT NOT NULL,
    score NUMERIC NOT NULL,
    score_breakdown JSONB,
    scored_at TIMESTAMPTZ DEFAULT now()
);

-- One notification batch, sent whenever a collection cycle finds new candidates
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    channel TEXT NOT NULL,
    sent_at TIMESTAMPTZ DEFAULT now(),
    candidate_raw_item_ids INT[] NOT NULL,
    telegram_message_id BIGINT
);

CREATE TABLE IF NOT EXISTS approvals (
    id SERIAL PRIMARY KEY,
    notification_id INT REFERENCES notifications(id),
    raw_item_id INT REFERENCES raw_items(id),
    decision TEXT NOT NULL,          -- 'pending', 'approved', 'edited', 'rejected'
    edited_text TEXT,
    -- Extension beyond spec Section 6: message id of the "reply with corrected text"
    -- prompt sent after an Edit tap, so approval_poller.py can match an incoming
    -- text message back to the right approval via Telegram's reply_to_message.
    prompt_message_id BIGINT,
    decided_at TIMESTAMPTZ DEFAULT now()
);

-- Extension beyond spec Section 6: holds the generated post text between "Approve"
-- and the final Publish/Cancel confirm (Section 9 step 4), including both variants
-- during a category's DeepSeek-vs-Sonnet benchmark trial (Section 4.3).
CREATE TABLE IF NOT EXISTS post_previews (
    id SERIAL PRIMARY KEY,
    approval_id INT REFERENCES approvals(id) UNIQUE,
    channel TEXT NOT NULL,
    category TEXT NOT NULL,
    variant_a_model TEXT NOT NULL,
    variant_a_text TEXT NOT NULL,
    variant_b_model TEXT,             -- NULL outside a benchmark trial (single-variant write)
    variant_b_text TEXT,
    -- Split into two Telegram messages (2026-09-10, delivery/formatting
    -- overhaul): telegram_message_id is the operator-only routing header
    -- (category/channel/score + Mark as sent/Discard buttons); content_*
    -- are the clean, button-free post(s) the operator forwards unedited.
    -- content_b_message_id is NULL outside a benchmark trial.
    telegram_message_id BIGINT,
    content_message_id BIGINT,
    content_b_message_id BIGINT,
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'published', 'cancelled'
    chosen_variant TEXT,              -- 'a' or 'b', set on mark-as-sent
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    approval_id INT REFERENCES approvals(id),
    channel TEXT NOT NULL,
    category TEXT NOT NULL,
    final_text TEXT NOT NULL,
    -- As of 2026-09-10: set when the operator taps "Mark as sent" (manual
    -- forward), not when the bot posts anything — there is no bot-side send.
    -- Column kept as published_at rather than renamed, since it's still "when
    -- this became a real post" from the reader's perspective; see
    -- pipeline/publish.py for the full rationale.
    published_at TIMESTAMPTZ,
    telegram_message_id BIGINT   -- always NULL now; kept for schema stability / future reactivation
);

CREATE TABLE IF NOT EXISTS run_logs (
    id SERIAL PRIMARY KEY,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL,
    details JSONB,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

-- Logged during each category's DeepSeek-vs-Sonnet trial period (Section 4.3)
CREATE TABLE IF NOT EXISTS write_benchmark (
    id SERIAL PRIMARY KEY,
    approval_id INT REFERENCES approvals(id),
    category TEXT NOT NULL,
    deepseek_text TEXT NOT NULL,
    sonnet_text TEXT NOT NULL,
    operator_chose TEXT NOT NULL,   -- 'deepseek' or 'sonnet'
    compared_at TIMESTAMPTZ DEFAULT now()
);

-- Tracks which of the two shared channels (Alpha Edge Crypto / CoinCraft) got the
-- last item for each alternating category, so assignment stays a simple round-robin.
CREATE TABLE IF NOT EXISTS channel_alternation (
    category TEXT PRIMARY KEY,
    last_channel TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Extension beyond spec Section 6: getUpdates offset survives between poller runs.
-- Each approval-poll.yml run is a fresh, stateless GitHub Actions job, so this one-
-- row table is the only place "which updates have I already processed" can live.
CREATE TABLE IF NOT EXISTS telegram_poll_state (
    id INT PRIMARY KEY DEFAULT 1,
    last_update_id BIGINT,
    CHECK (id = 1)
);

CREATE INDEX IF NOT EXISTS idx_raw_items_category ON raw_items(category);
CREATE INDEX IF NOT EXISTS idx_raw_items_collected_at ON raw_items(collected_at);
CREATE INDEX IF NOT EXISTS idx_scores_raw_item_id ON scores(raw_item_id);
CREATE INDEX IF NOT EXISTS idx_scores_category ON scores(category);
CREATE INDEX IF NOT EXISTS idx_approvals_raw_item_id ON approvals(raw_item_id);
