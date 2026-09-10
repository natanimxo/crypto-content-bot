# Handoff — 2026-09-11

Written for a fresh Claude Code session picking this project up cold. Read
this before touching anything — it's the fast path to current state, without
re-deriving what already got figured out. [intelligence-bot-spec-v2.md](intelligence-bot-spec-v2.md)
is the full design reference (checked in as of today); this doc is state +
history + what's actually still open, not a design doc.

## Current state per category

| Category | Channel(s) | Status |
|---|---|---|
| `defi_yields` | Crypto Notebook | **Built, tested, live.** Phase 1 MVP — full notify→approve→write→deliver loop validated end-to-end first, before any other category was started. |
| `whale_movements` | Crypto Wall Street | **Built, tested, live.** Deep verification pass (first category with genuinely messy real-world data — dormant wallets, mislabeled exchanges, stale transactions). Three real bugs found and fixed same-day (see below). $2M collect floor and the 10k institutional heuristic are both first-pass tuned, not yet revisited against more cycles. |
| `web3_jobs` | Alpha Edge Crypto / CoinCraft (alternation) | **Built, tested, live.** Faster verification pace by explicit operator direction (structured data, no price verification, no editorial judgment needed). Volume is thin (13/56 raw listings relevant per cycle; only 2 have ever cleared `review_threshold`) — see "Source volume" below. |
| `news`, `tool_launches`, `grants`, `startup_jobs`, `airdrops` | Crypto Wall Street / Hustle to Million | **Not built.** Listed in `channel_config.yaml` for record-keeping only; `bot/notify.py` skips them via `category_config_exists()`. `airdrops` specifically paused by operator direction (no reliable free source for wallet-level whale tracking). |

**Production deployment: not yet live.** GitHub Actions Secrets have never
been configured — see "GitHub Actions secrets" below. Everything above has
been validated by running the pipeline manually/locally against the real
Supabase DB and real Telegram bot, not via the actual cron.

## Every bug found this session, with root cause

### 1. Stale-transaction recency (`whale_movements`)
**Symptom:** a transaction from 2023-06-21 got collected as if it had just
happened. **Root cause:** fetching "the most recent 100 transactions" from
Etherscan has no implicit recency guarantee for a low-activity address — a
wallet with under 100 total (or under-100-since) transactions can have that
window reach back years. Confirmed live: two OKX withdrawals from
2023-06-21 surfaced today because the watched wallet's real recent activity
was almost entirely dust/spam-token noise, so its actual top-100-by-index
still reached back three years for the last "real" transfer. **Fix:**
`WHALE_MAX_AGE_HOURS = 24` — generous over the 4h collection cadence (a
single missed/delayed cycle still won't lose anything) while enforcing
genuine freshness. See `collectors/whale_movements.py`.

### 2. Counterparty classification (`whale_movements`)
**Symptom:** `0xa9d1e08c...`, Etherscan-labeled "Coinbase 10," wasn't on the
watchlist, so 7 posts described transfers to it as going to "an external
wallet" — when it's almost certainly Coinbase moving funds between its own
wallets. **Root cause:** "not on our watchlist" was being treated as
equivalent to "external/retail wallet," but the watchlist (`WATCHLIST`, the
small set we actively collect *from*) was never meant to be exhaustive for
*classification* purposes. **Fix:** split into two sets —
`WATCHLIST` (collected from) vs. `KNOWN_EXCHANGE_ADDRESSES` (classification-
only, ~17 addresses across 10 exchanges, WATCHLIST is a subset), plus a
self-maintaining fallback (`INSTITUTIONAL_SENT_TX_THRESHOLD = 10_000`) that
deprioritizes unlabeled-but-clearly-not-retail counterparties by transaction
volume, since hand-verifying "hundreds" of real exchange addresses isn't
practical. Calibration: confirmed exchange-linked counterparties observed
have `sent_tx_count` in the millions (2.7M, 18.2M); genuine external wallets
had 1–1,331. See the block comment above `WATCHLIST` in
`collectors/whale_movements.py`.

### 3. DefiLlama confidence field (`whale_movements`)
**Symptom:** none observed as a live failure — found by *adding* a check
that wasn't there before, after live-verifying two real prices (PROM, SPK)
against CoinGecko. **Root cause:** DefiLlama's `/prices/current/` and
`/prices/historical/` responses carry `confidence` (0-1, DefiLlama's own
reliability score) and `timestamp` (when that price point was actually
recorded) — neither was being checked. A long-tail/thin-liquidity token is
exactly the case where a cached/stale/low-confidence price could silently
turn a routine transfer into a false "$2M+ whale move." **Fix:**
`MIN_PRICE_CONFIDENCE = 0.8`, `MAX_PRICE_AGE_HOURS = 24` — below either
threshold, `_get_token_price_usd()` (live path,
`collectors/whale_movements.py`) and `_get_token_price_historical()`
(write-time backfill, `pipeline/write_post.py`) both return `None` rather
than a guessed price, and the item is skipped/omitted rather than published
with an untrustworthy dollar figure.

### 4. RemoteOK server-side encoding (`web3_jobs`)
**Symptom:** the Libertex listing's location rendered as
`ÙØ³ÙØ·, ÙØ³ÙØ· ÙØ³ÙØ· Ø¹ÙØ§Ù` — mojibake, actually Arabic "Muscat,
Oman." **Root cause:** RemoteOK's API has a server-side bug where non-ASCII
text comes back with each UTF-8 byte reinterpreted as its own Latin-1
codepoint before JSON-escaping. **Fix:** `_fix_mojibake()` in
`collectors/web3_jobs.py` — re-encode as Latin-1 to recover the original
bytes, decode as UTF-8. Applied unconditionally to every string field from
this API; safe on already-correct text too (pure ASCII round-trips
unchanged; genuinely-correct non-ASCII either round-trips unchanged or fails
the encode/decode step outright, in which case the original value is
returned rather than risking further corruption).

### 5. The relevance gate (`web3_jobs`)
**Symptom:** "Junior Illustrator at Twine" cleared scoring for a crypto
channel — a generic freelance listing with a crypto tag, not a Web3 role.
**Root cause:** live-confirmed ~70% of a real 56-listing collection were
generic corporate roles (Little Caesars Pizza, The Walt Disney Company,
Nestlé Health Science, DSW) swept in by RemoteOK's own tag system — some
listings carried 40-58 tags, "crypto" just one of them. Raising the score
threshold would have filtered by *score*, not *relevance* (a pizza-chain
listing can score fine on salary/logo/location while being totally
irrelevant). **Fix:** `_is_web3_relevant()`, a separate binary gate applied
*before* scoring — checks company identity against `KNOWN_WEB3_COMPANIES`
first, falls back to a job-title keyword check (`WEB3_TITLE_KEYWORDS`) for
companies not on the list. Same "known-entity list + self-maintaining
fallback" shape as bug #2's exchange classification.

### 6. Telegram callback-query expiry (approve tap failing across all 3 categories)
**Symptom:** an operator tap on Approve/Edit/Reject/Mark-as-sent sometimes
did nothing at all — no ack, no error, no downstream effect. Reported
independently on `defi_yields`, `whale_movements`, and `web3_jobs`; only
ever mitigated before (`_safe_ack()`), never root-caused. This was the
single most severe bug found this session — it made the entire pipeline
unusable regardless of collector quality, since nothing downstream of
"operator taps a button" could be trusted to run.

**Investigation** (per the operator's explicit checklist): confirmed the
polling offset persists correctly across runs (stable across 12+ runs, one
writer); confirmed no Telegram webhook is registered (would silently steal
updates from `getUpdates` if one were); confirmed the only other process
that could touch the same bot's updates — `approval-poll.yml`'s own
scheduled cron — had in fact *never once succeeded* (crash-looping on
missing `DATABASE_URL`, failing before it ever reaches the Telegram API —
this ruled it out as a competing consumer, but is itself bug/blocker #7
below).

**Root cause**, via a deliberate controlled test (tap → poll immediately vs.
tap → poll after exactly 10 minutes untouched, repeated twice): a tap
succeeds 2/2 when polled within seconds, and is **completely absent** from
`getUpdates` — not unanswerable, genuinely gone — after 10 minutes. Telegram
drops an un-fetched `callback_query` from its delivery queue well under the
documented 24h retention for ordinary updates, a property specific to
`callback_query`'s interactive nature. This is separate from (and in
addition to) the already-known `answerCallbackQuery`-expiry issue
`_safe_ack()` already handled — that one meant a tap could be seen but not
acknowledged; this one meant most taps were never seen at all on a 5-15
minute short-polling cron.

**Fix:** switched `bot/approval_poller.py` to long-polling
(`LONG_POLL_TIMEOUT_SECONDS = 270`, ~4.5 min, under the 6-minute job
timeout) instead of short-polling (`timeout=0`). Telegram delivers an
update the instant it occurs while a `getUpdates` call with `timeout>0` is
open — holding that connection open for most of the gap between 5-minute
cron firings shrinks the blind window from "up to 15 minutes" to roughly
10-20 seconds. No server added (keeps the zero-infrastructure design).
`approval-poll.yml` timeout raised to 6 minutes to match, plus a
`concurrency: {group: approval-poll, cancel-in-progress: false}` guard so
two long-polling runs never race against the same offset.

**Verified live:** a 2-minute long-poll window caught 3 real taps and
returned in 0.4 seconds. The real poller then processed all three
correctly — one against a still-live item (approved, write generated,
preview sent), two against already-deleted items from earlier
relevance/staleness cleanups (correctly no-opped as "already handled").

## Open issues, priority order

1. **GitHub Actions Secrets are not configured — production cannot run at
   all yet.** Confirmed via `gh run list` / `gh run view --log`:
   `approval-poll.yml` has been crash-looping every ~5 minutes on missing
   `DATABASE_URL` since the repo went public. Needed secrets (repo Settings
   → Secrets and variables → Actions → New repository secret):

   | Secret | Where to get it | Required for |
   |---|---|---|
   | `DATABASE_URL` | Supabase dashboard → Project Settings → Database → Connection string (URI, "Session" mode) | both workflows |
   | `TELEGRAM_BOT_TOKEN` | @BotFather → `/newbot` | both workflows |
   | `TELEGRAM_OPERATOR_CHAT_ID` | DM the bot, then `https://api.telegram.org/bot<token>/getUpdates` (or @userinfobot) — this is the operator's own private chat with the bot, NOT any channel's chat_id | both workflows |
   | `TELEGRAM_ALLOWED_USER_IDS` | @userinfobot for your own numeric id; comma-separate if more than one approver | `approval-poll.yml` |
   | `DEEPSEEK_API_KEY` | platform.deepseek.com → API keys | both workflows (default triage/write model) |
   | `ETHERSCAN_API_KEY` | etherscan.io/apis → sign up → create key (free tier: 3 calls/sec, 100k/day) | both workflows — `whale_movements` collection AND write-time history backfill on approve |
   | `GEMINI_API_KEY` | aistudio.google.com/apikey | optional — free-tier alt triage model, not currently used by any live category |
   | `ANTHROPIC_API_KEY` | console.anthropic.com | optional — only needed if a category's `write_benchmark_status` flips to `trial` |

   Once added: Actions tab → run `Collect` manually once (Run workflow
   button) to confirm the full path before trusting the cron.

2. **`web3_jobs` source volume is thin.** 13/56 raw RemoteOK listings pass
   the relevance gate per cycle, and only 2 have ever actually cleared
   `review_threshold` (both went to `coincraft`; `alpha_edge_crypto` has
   never had a qualifying candidate). Investigated 2026-09-11:
   - Broadening RemoteOK's tag query (`web3`, `blockchain`, `defi` alongside
     `crypto`) does **not** help — union of all four tags is 67 raw
     listings, but the exact same 13 pass the relevance gate. Ruled out.
   - Web3.career (`web3.career/api/v1`, via their "Bondex" platform) has a
     real, structurally cleaner JSON/RSS API — but requires a free account +
     API token (`?token=...` query param), which is outside what an
     unattended agent can set up (account creation is off-limits). **Needs
     the operator to sign up at `web3.career/web3-jobs-api` and hand over a
     token** before a second collector gets built. Not currently blocking —
     recommendation was to ship on RemoteOK alone and treat this as a later
     volume upgrade, not a prerequisite.

3. **`whale_movements` tuning not yet revisited.** Both tuned values below
   are first-pass, calibrated against one clean cycle each — worth a few
   more cycles of real data before adjusting further in either direction.

4. **Exchange-to-exchange transfers can generate two `raw_items`** — one
   per watched wallet's own `txlist`/`tokentx` call, since the collector
   fetches per-address, not per-transaction. Not deduped across the two
   sides. Lower priority: exchange↔exchange is already scored lower on
   actionability, so the practical impact of a double-surface is likely
   small. Candidate fix if it proves to matter: cross-check `tx_hash` at
   collection time.

5. **`defi_yields` APY-spike scoring gap** (oldest open item, pre-dates this
   session): pools with 200%+ APY and 100+ point 7-day jumps score as high
   as a healthy stable yield — `impact` saturates at 33%+ APY with no
   ceiling-awareness, and `novelty` rewards a big swing regardless of
   direction/plausibility. In practice this pattern usually signals
   unsustainable token emissions, not a real opportunity. Candidate fix: a
   penalty when APY is far above the category's own rolling
   median/percentile (relative, not a fixed cutoff) — needs a rolling stat
   over recent `raw_items`. See the `TODO(scoring)` comment in
   `pipeline/score.py::score_defi_yields`. Deliberately still not
   implemented — was waiting on the MVP loop validation, which is long done
   now, so this is genuinely next-up whenever scoring work resumes.

## Tuned config values and reasoning

- **`whale_movements.collect_min_usd = $2,000,000`** — not the originally
  proposed $500k. Exchange hot wallets move $500k as routine operations; at
  that floor the collector would surface noise, not signal. DB-tunable
  (`category_config.collect_min_usd`) specifically so it can come down later
  without a redeploy if real daily volume proves thin. One clean cycle so
  far: 8 collected, 3 cleared `review_threshold` — not enough data to
  re-tune yet either direction.
- **`INSTITUTIONAL_SENT_TX_THRESHOLD = 10,000`** (`collectors/whale_movements.py`)
  — calibrated against real observed data: confirmed exchange-linked
  counterparties had `sent_tx_count` in the millions (2.7M, 18.2M); genuine
  external wallets had 1–1,331. 10,000 sits comfortably below the former and
  above plausible high-activity retail use.
- **`web3_jobs` undisclosed salary scores `impact = 30`, not `0`** —
  live-verified only ~2% of RemoteOK's crypto-tagged listings disclose
  `salary_max`. Zero would disqualify almost every real listing on the
  feed; 30 reads as "unknown," not "bad," and doesn't punish the vast
  majority of legitimate postings for something most listings never
  disclose regardless of actual quality.
- **`MIN_PRICE_CONFIDENCE = 0.8`, `MAX_PRICE_AGE_HOURS = 24`**
  (`whale_movements`, both live-collection and write-time-backfill price
  lookups) — DefiLlama's own reliability score, verified against two real
  CoinGecko cross-checks (PROM, SPK, both correct at confidence 0.99).
  Below either threshold, a price is treated as untrustworthy and the item
  is skipped/omitted rather than published with a guessed dollar figure.
- **`WHALE_MAX_AGE_HOURS = 24`** — generous over the 4h collection cadence
  (a single missed/delayed cycle still loses nothing) while still enforcing
  genuine freshness against Etherscan's no-recency-guarantee "last 100 txs."

## Where to look next

- [intelligence-bot-spec-v2.md](intelligence-bot-spec-v2.md) — full design,
  section-numbered to match code comments.
- [BACKLOG.md](BACKLOG.md) — deliberately-deferred items with fuller
  narrative detail than this doc's summaries (git blame / commit history has
  the rest, if a fix's exact reasoning needs re-deriving).
- `gh run list` / `gh run view <id> --log` — check whether Actions Secrets
  have been added yet and whether the cron is actually succeeding.
