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
    label TEXT,                                    -- notification eyebrow, e.g. "🌾 DEFI YIELDS"
    voice TEXT,                                     -- e.g. 'educational', 'market_summary', 'opportunity_framed'
    prompt_notes TEXT                                -- freeform voice/framing notes fed into the write prompt (Section 8)
);

-- Channel-level config
CREATE TABLE IF NOT EXISTS channel_config (
    channel TEXT PRIMARY KEY,
    categories TEXT[] NOT NULL,
    region_profile TEXT DEFAULT 'default',
    soft_daily_cap INT,
    telegram_chat_id TEXT
);

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
    telegram_message_id BIGINT,       -- the preview message carrying Publish/Cancel buttons
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'published', 'cancelled'
    chosen_variant TEXT,              -- 'a' or 'b', set on publish
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    approval_id INT REFERENCES approvals(id),
    channel TEXT NOT NULL,
    category TEXT NOT NULL,
    final_text TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    telegram_message_id BIGINT
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
