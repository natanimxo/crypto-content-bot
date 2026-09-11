# Multi-Channel Intelligence & Content Bot — Technical Spec v2

Rewritten 2026-09-11 to match the actual implementation. The original spec
lived outside this repo and had drifted badly from what got built (see
BACKLOG.md's "Documentation" entry, now resolved by this rewrite) — most
notably it still described the bot auto-publishing straight to channels,
which was removed on 2026-09-10 in favor of a forward-based workflow. This
version is checked into the repo (`intelligence-bot-spec-v2.md`) and is now
the source of truth; section numbers below intentionally match the ones
already referenced throughout code comments (`# Section 7`, etc.) so those
comments stay meaningful.

---

## Section 0 — Build discipline

One category, fully working and verified against real live data, before
starting the next. No category gets added to `scripts/collect_cycle.py`'s
`COLLECTORS` dict or a channel's `category_config` list until its collector,
scorer, and prompt builder all exist end-to-end — a half-built category
should error loudly, never silently no-op.

Read-me-first rule: category-specific logic never leaks into the shared
pipeline modules (`store.py`, `select_candidates.py`, `publish.py`,
`telegram_api.py`). If a change to one of those seems to need an
`if category == ...`, that's a sign the category needs its own registered
function instead (a new `@register_scorer`, a new collector module, a new
entry in `pipeline/write_post.py`'s per-category prompt builders).

## Section 1 — Philosophy

Selective, memory-aware content, not a fixed-schedule feed. Two
differentiators this whole design serves:

1. **Memory the reader doesn't have.** Where honestly possible, a post
   references relevant history the subscriber wouldn't otherwise see (a
   pool's prior APY trend, a whale wallet's last comparable move). This must
   never become misinformation — omitting the history line is always safer
   than forcing a misleading one, so every category's history logic has an
   explicit "say nothing rather than guess" fallback (see Section 8).
2. **You get pinged when there's something to post, not on a timer.**
   `bot/notify.py` sends nothing when a cycle produces zero new candidates
   for a channel. No content quota, no filler.

## Section 2 — Top-level architecture

```
collect → score → notify → (operator approves via Telegram) → write → operator forwards manually
```

Zero persistent infrastructure — the entire compute layer is two GitHub
Actions workflows plus a Supabase Postgres database:

- **`collect.yml`** (every 4h): runs `scripts/collect_cycle.py`, which for
  every live category calls `collect()` → `score_new_items()`, then runs
  `bot/notify.py` once at the end across all channels.
- **`approval-poll.yml`** (every 5min, long-polling for ~4.5 of those
  minutes — see Section 9): runs `bot/approval_poller.py`, which drives the
  whole approve/edit/reject → write → "mark as sent" state machine.

No server process runs between invocations. All state (what's been
collected, scored, notified, approved, written, sent) lives in Postgres so
each cron firing is a fresh, stateless process picking up exactly where the
last one left off.

## Section 3 — Channels

Five target channels. Only the categories with a working collector are
actually seeded into `channel_config.categories` (see `config/channel_config.yaml`);
the rest are listed there for record-keeping but skipped by `bot/notify.py`
via `category_config_exists()` until built.

| Channel | Display name | Categories (live) | Region profile |
|---|---|---|---|
| `crypto_notebook` | Crypto Notebook | `defi_yields` | `us` |
| `crypto_wall_street` | Crypto Wall Street | `whale_movements`, `news` | `default` |
| `alpha_edge_crypto` | Alpha Edge Crypto | `web3_jobs` | `us` |
| `coincraft` | CoinCraft | `web3_jobs` | `default` |
| `hustle_to_million` | Hustle to Million | `tool_launches`, `startup_jobs`, `macro_news` (2026-09-12); `grants` deliberately out of scope, see below | `default` |

**Alternation.** `web3_jobs` is the first category shared across two
channels (`alpha_edge_crypto` and `coincraft`). `pipeline/channel_router.py`
assigns each *individual item* to exactly one of the two at collection time
(round-robin via the `channel_alternation` table), stored as
`payload['assigned_channel']` on the raw_item — never both. Deterministic
and testable: the same listing can never reach both channels, and which
channel a given item went to is recorded permanently on the row, not
recomputed later.

`airdrops` was deliberately paused (operator direction 2026-09-10) rather
than left in as a stub — no collector exists yet, so it's omitted entirely
from both channels' category lists rather than listed-but-skipped.

`grants` (Hustle to Million) is deliberately OUT OF SCOPE, same treatment,
decided 2026-09-12 after live-checking every realistic source first —
Grants.gov's API works but returns US federal research grants (NSF SBIR,
biomedical research centers), zero overlap with what a builder audience
means by "grants"; DevPost's API is bot-blocked; F6S/YC/Antler/Techstars
expose marketing pages, not feeds. Unlike every other category here,
there's no structured, frequently-updated source to poll in the first
place — accelerator/grant application windows are a handful of
predictable events a year per program, which is a calendar reminder, not
something a 4-hour collection cycle can meaningfully monitor. The
alternative (a hand-maintained watchlist of program pages, text-matched
for "applications open") was considered and rejected: it's the same
curated-list-rot problem hit four times already this build (`is_mintable`,
Syrup, osETH, Pendle — see BACKLOG.md), except this time the list itself
is unmaintainable — marketing pages get redesigned without notice, and
low-confidence text matching against them would fail silently. Revisit
only if a real structured source turns up later; not planned as a
someday-maybe the way Product Hunt was initially treated before also being
dropped outright.

`macro_news` (Hustle to Million, 2026-09-12) reuses `news`'s entire
infrastructure unchanged (entity fingerprinting, cross-outlet dedup, topic
caps) against a different feed list (BBC World/Business, NPR Economy, CNBC
Economy, Federal Reserve, Axios, Ars Technica, The Verge, Wired — see
`collectors/macro_news.py`'s module docstring for the full live-verification
reasoning per feed). Deliberately NOT "business news": macro developments
that plausibly affect a reader's economic situation (rates/inflation, trade
policy, geopolitical events with real economic spillover, AI regulation,
major regulatory action), gated by a deliberately high relevance bar — a
cycle with zero candidates is the expected outcome on a quiet day, live-
verified at ~10% of a real cycle's candidate feed items clearing it. Carries
a hard political-neutrality requirement, enforced both in the write prompt
and structurally (`pipeline/write_post.py`'s `_find_neutrality_violations`,
same checked-write/retry/`RuntimeError` mechanism as the gems_security
overclaim guard and the news copyright guard) — see BACKLOG.md's "Political
neutrality guard" entry for the real-draft test run and the explicit
floor-not-guarantee framing.

## Section 4 — LLM layer

### 4.1 — Hosting requirement

GitHub Actions' free tier only fires **scheduled** workflows reliably on
**public** repos — a private repo's cron silently stops firing after 60 days
of inactivity, and scheduled workflows on private repos consume billed
minutes. This repo must stay public for `collect.yml`/`approval-poll.yml`'s
`schedule:` triggers to work at all.

### 4.2 — Default providers

- **DeepSeek** (`deepseek-v4-flash`) is the default triage *and* write model
  for every live category — cheap enough that "cheap-LLM triage" from the
  original brief is satisfied without a free-tier dependency.
- **Gemini** (2.5 Flash-Lite) is wired up as a free-tier alternative triage
  model, available but not currently the default for any category.

### 4.3 — Benchmark trials

`category_config.write_benchmark_status` controls whether a category's final
post is written once (`settled_deepseek` — current state for all three live
categories) or as an A/B trial (`trial` — generates both a DeepSeek and a
Sonnet variant via `generate_post_variants()`, sends both to the operator
labeled Version A / Version B, and only counts as "written" once the
operator marks whichever one they actually sent). All three live categories
are deliberately `settled_deepseek`, not `trial`: the operator wants to
evaluate DeepSeek's writing quality on its own first, rather than spending
Anthropic credit under pressure to use it immediately. Flipping any category
to `trial` needs `ANTHROPIC_API_KEY` set.

### 4.4 — Provider-agnostic dispatch

Pipeline code never imports a provider module directly or hardcodes a model
name. `pipeline/llm.py` exposes exactly two entry points —
`generate_triage(conn, category, raw_item)` and
`generate_write(conn, category, prompt, model_override=None)` — which
dispatch to `pipeline/llm_providers/{deepseek,gemini,anthropic,template}.py`
based on `category_config.triage_model` / `write_model`. Adding a new
provider means adding one module there with a matching call signature;
nothing else in the pipeline changes.

`template` is a special zero-LLM "provider" (`llm_providers/template.py`):
for categories whose source data is already structured numbers (all three
live categories use it for triage), the "one-line summary" is just
formatting those fields well in Python — no model call, no cost, no latency,
no hallucination risk for data that's already fully known.

## Section 5 — Data sources

| Category | Source | Auth | Notes |
|---|---|---|---|
| `defi_yields` | DefiLlama yields API | none (free, keyless) | Best free resource of any category — 200+ protocols, no rate-limit friction hit so far. |
| `whale_movements` | Etherscan API V2 | free API key (3 calls/sec, 100k/day) | V1 was deprecated mid-build; V2 requires an explicit `chainid` param — see `collectors/whale_movements.py`'s `ETHERSCAN_URL` comment. |
| `whale_movements` (pricing) | DefiLlama current + historical price APIs | none | Also used at write-time for history backfill — see Section 7/8. |
| `web3_jobs` | RemoteOK `?tags=crypto` | none (needs a real `User-Agent` header or requests get blocked) | Primary and, for now, only source — see the sourcing note below. |
| `web3_jobs` (evaluated, not wired up) | Web3.career / Bondex API (`web3.career/api/v1`) | **requires a free account + API token** (query-param auth, `?token=...`) | Investigated 2026-09-11: real JSON/RSS API, filterable by tag/country/remote, up to 100 results/request, 429 on rate-limit. Structurally cleaner than RemoteOK's tag system, but account creation is outside what this bot can do unattended — needs the operator to sign up and hand over a token before a second collector gets built. Terms require follow-linking `apply_url` with no modification/tracking params if wired up. |

**web3_jobs sourcing note (2026-09-11):** broadening RemoteOK's tag query
(`web3`, `blockchain`, `defi` alongside `crypto`) was tested and confirmed
*not* to help — the union of all four tags is 67 raw listings, but the exact
same 13 pass the relevance gate (Section 7) as the single `crypto` tag alone.
RemoteOK's actual crypto-job inventory is just thin right now; this isn't a
query-selection problem. 13/cycle across two channels is a real, if thin,
signal — not currently blocking on Web3.career.

## Section 6 — Config schema

Two YAML files are the versioned baseline, upserted into Postgres by
`scripts/seed_config.py`; the **DB row**, not the YAML file, is what the
pipeline reads at runtime, so an operator can retune thresholds/weights
directly in Supabase without a redeploy — re-running the seed script resets
back to what's checked in.

`config/category_config.yaml` (one entry per category):

```yaml
<category>:
  score_weights: {impact, novelty, credibility, actionability}  # must sum to 1.0
  review_threshold: <0-100>       # below this, never reaches an LLM at all
  cooldown_hours: <float>         # suppress re-surfacing the same topic_key within this window
  soft_daily_cap: <int|null>      # per-category daily notify cap for a channel
  collect_min_usd: <int>          # whale_movements only — collection-time $ floor
  triage_model: template | deepseek | gemini
  write_model: deepseek-v4-flash | claude-sonnet-5
  write_benchmark_status: settled_deepseek | trial
  label: "<emoji> CATEGORY NAME"  # digest eyebrow + post label header (Section 10)
  emoji: "<single emoji>"         # the ONE leading emoji on a post's title line
  display_label: "<Human Name>"   # metadata, not rendered in the post body
  hashtags: ["#Tag1", "#Tag2"]
  voice: <free text>              # register description, informs prompt_notes
  prompt_notes: <free text>       # shapes ONLY the LLM's prose (title/narrative/why_it_matters)
```

`config/channel_config.yaml` (one entry per channel): `display_name`,
`categories` (list), `region_profile`, `soft_daily_cap` (channel-wide,
separate from each category's own per-category cap), `chat_id` (kept for a
possible future reactivation of direct publishing — not read by any live
code path since Section 11's redesign).

## Section 7 — Scoring

100% deterministic, zero LLM calls (`pipeline/score.py`). An item's score
never crosses category lines — each category registers its own scorer via
`@register_scorer("category_name")`, returning a 0-100 breakdown across
exactly four components (`impact`, `novelty`, `credibility`,
`actionability`); `score_new_items()` applies that category's
`score_weights` and stores both the total and the breakdown.

An item below `review_threshold` never reaches an LLM at all — the entire
triage/write cost is gated behind this deterministic pre-filter. Above
threshold, `pipeline/select_candidates.py` applies two more deterministic
gates before anything reaches the operator: **cooldown** (same `topic_key`
suppressed for `cooldown_hours`) and **soft daily cap** (per-category and
per-channel). Cross-source dedup happens earlier still, at the `raw_items`
level (`UNIQUE(source_id, external_id)` plus `store.is_likely_duplicate()`
text-similarity check) — scoring never sees true duplicates.

### Tuned values and reasoning

**`whale_movements.collect_min_usd = $2,000,000`** — not the originally
proposed $500k. Operator direction 2026-09-10: exchange hot wallets move
$500k as routine operations; at that floor the collector would surface
noise, not signal. Kept DB-tunable specifically so it can come down later if
real daily volume proves thin, without a code change or redeploy. Still
"first clean cycle" data as of this writing (8 collected, 3 cleared
`review_threshold` in the one cycle run so far) — deliberately not re-tuned
again until a few more cycles establish a real baseline.

**`INSTITUTIONAL_SENT_TX_THRESHOLD = 10,000`** (`collectors/whale_movements.py`)
— a self-maintaining fallback for counterparties not on the hand-curated
`KNOWN_EXCHANGE_ADDRESSES` list. Calibrated against real data: confirmed
exchange-linked counterparties observed so far have `sent_tx_count` in the
millions (2.7M, 18.2M seen live); genuine external-looking wallets had
1–1,331. 10,000 sits comfortably below the former and above plausible
high-activity retail use, so an unlabeled address above this count is scored
as "likely institutional" (actionability 55, between the 85 a genuine
external wallet gets and the 40 a confirmed exchange gets) rather than
treated as an ordinary retail whale.

**`web3_jobs` undisclosed salary → `impact = 30` (moderate, not zero)** —
live-verified only ~2% of RemoteOK's crypto-tagged listings disclose
`salary_max`. Treating silence as disqualifying (`impact = 0`) would filter
out essentially all real listings; 30 reads as "unknown, not bad" rather
than penalizing the vast majority of genuinely legitimate postings for not
sharing a number most listings never share regardless of role quality.

**Per-category weight shapes** (deliberately not copied between categories —
operator direction 2026-09-10 each time a new category was built):

- `defi_yields`: impact 0.35 / novelty 0.25 / credibility 0.25 /
  actionability 0.15 — size of the yield/TVL move is the dominant signal.
- `whale_movements`: impact 0.30 / novelty 0.30 / credibility 0.20 /
  actionability 0.20 — novelty raised to match impact ("is this unusual for
  this wallet" carries as much story as "how big"); credibility redefined
  entirely to score the **counterparty's** establishment (age + tx count),
  not the exchange side, since every watchlist address is curated and would
  otherwise be a non-discriminating near-constant.
- `web3_jobs`: impact 0.25 / novelty 0.15 / credibility 0.30 /
  actionability 0.30 — "big number = important" doesn't apply to a job
  listing, so credibility (scam-listing risk is real on web3 job boards) and
  actionability (can a global reader actually apply) are weighted highest,
  compensation and freshness lower.

**`web3_jobs` relevance gate** (`_is_web3_relevant()`,
`collectors/web3_jobs.py`) — a separate, binary, pre-scoring filter, not a
score component. Live-confirmed 2026-09-10: ~70% of a real 56-listing
RemoteOK collection were generic corporate roles (Little Caesars Pizza, The
Walt Disney Company, Nestlé Health Science, DSW) swept in by RemoteOK's own
tag system, where some listings carried 40-58 tags with "crypto" as just
one. A higher score threshold would have filtered by *score*, not
*relevance* — a pizza-chain regional-director listing can still score well
on salary/logo/location while being completely irrelevant to a crypto
audience. The gate checks company identity against a hand-maintained
`KNOWN_WEB3_COMPANIES` set first, falling back to a job-title keyword check
(`WEB3_TITLE_KEYWORDS`) for companies not on the list — same "known-entity
list + self-maintaining-ish fallback" shape as `whale_movements`'
`KNOWN_EXCHANGE_ADDRESSES` + `INSTITUTIONAL_SENT_TX_THRESHOLD`, except here
the fallback is a keyword check rather than a numeric heuristic (no
on-chain-style signal exists for a job listing).

## Section 8 — Post generation (triage + write + assembly)

Two separate LLM touch-points per item, at two different stages:

1. **Triage line** (`generate_triage()`) — a one-line digest summary shown
   in the operator's approve/reject notification. All three live categories
   use `template` (zero-LLM): the underlying data is already structured
   numbers, so "summarize it" is just formatting those fields, not synthesis.
2. **Final write** (`generate_write()`) — only ever called on an
   operator-**approved** item. The LLM is deliberately scoped narrowly: it
   returns JSON `{title, narrative, why_it_matters}` — three pieces of prose
   — and nothing else. It never emits HTML or decides structure.

**Deterministic assembly** (`pipeline/post_format.py::assemble_post()`,
2026-09-10 redesign) builds the actual post from that JSON plus
deterministically-computed pieces:

- `history_line` — computed per-category in Python from real data (never
  the LLM). `defi_yields` and `whale_movements` each have their own
  "memory the reader doesn't have" logic (a pool's own APY history;
  `_compute_whale_own_history_line()`'s scan of prior flagged moves for the
  same wallet, or `_backfill_whale_history()`'s first-sighting Etherscan
  backfill). The rule from Section 1 applies literally: if a validated prior
  value isn't available, this returns `None` and the whole blockquote is
  omitted — never guessed.
  - `web3_jobs` **has no history line at all**, on purpose — it always
    returns `None`. A job posting isn't a recurring signal the way a
    wallet's past moves or a pool's yield trend are; forcing "memory" onto a
    category where the honest answer is "there isn't any" would be padding,
    exactly the anti-pattern Section 1 exists to avoid.
- `risk_line` — also computed, category-specific (e.g. low
  Etherscan-history counterparty flagged as lower-confidence signal).
- `emoji`, `source_name`, `hashtags` — straight from `category_config`.

**Formatting rules**, all enforced in code, never left to prompt
instructions alone:

- Output is Telegram HTML (`parse_mode: "HTML"`) — `<b>` around the title,
  `<blockquote>` around the history line, a leading `⚠️` only when a risk
  line exists.
- Exactly one emoji total, on the title line — nothing else in the post
  body gets an emoji.
- LLM prose is HTML-escaped with `quote=False` (content context, not an
  attribute — escaping apostrophes/quotes would render literal `&#x27;` on
  the subscriber's screen, which is wrong here) before insertion; only
  `assemble_post()` ever emits real `<b>`/`<blockquote>` tags — the model
  never does.
- **No hyperlinks anywhere** (operator direction 2026-09-10) — source
  attribution is plain text (`Source: DefiLlama`), never a clickable link,
  and every actual content send additionally sets
  `disable_web_page_preview=True` as a defensive measure against the LLM's
  free-form prose happening to contain something URL-shaped.
- Target ~400–700 characters total; the prompt is responsible for keeping
  `narrative`/`why_it_matters` within budget — `assemble_post()` does not
  truncate.

## Section 9 — Operator notification & approval flow

**Digest** (`bot/notify.py`, runs once per `collect.yml` cycle): for each
channel, gathers every category's new candidates
(`select_candidates.get_new_candidates()`), and — only if there's at least
one — sends **one** Telegram message bundling all of them, each with its own
inline **Approve / Edit / Reject** row (`callback_data` keyed on
`raw_item_id`, so nothing needs a DB row until a tap actually happens). The
whole message is built and the DB write (one `notifications` row + one
`approvals` row per candidate) only happens *after* `send_message()`
succeeds — a failed send leaves candidates untouched for retry next cycle
rather than silently marking them "already notified."

**Polling** (`bot/approval_poller.py`, `approval-poll.yml`, every 5 minutes):

1. Long-polls `getUpdates` for ~4.5 of the 5-minute gap (`LONG_POLL_TIMEOUT_SECONDS
   = 270`) — see the callback-expiry fix below for why this is long-polling
   and not a quick check-once call.
2. Verifies the tapping user against `TELEGRAM_ALLOWED_USER_IDS` (Section 13)
   before doing anything.
3. **Two-phase processing**: acknowledges (`answerCallbackQuery`) every tap
   in the batch first — fast, no LLM calls — before doing any slow LLM
   write. This means one candidate's write latency can never starve another
   tap's acknowledgment in the same batch. Every ack goes through
   `_safe_ack()`, which never lets a failed/expired acknowledgment block the
   underlying business logic (the approval is still recorded even if the tap
   button itself can no longer show a green checkmark).
4. **Approve** → `_handle_approve()` marks the approval row `approved` in
   the fast phase, then (deferred) `_generate_and_preview()` calls
   `generate_write()`/`generate_post_variants()` and sends the result as a
   **preview** — see Section 11 for exactly what that looks like.
5. **Edit** → operator's next text message to the bot becomes the final
   post text directly — no LLM call at all, straight to the same
   preview/confirm flow as a normal write.
6. **Reject** → marked `rejected`, no write, no preview.
7. **Mark as sent / Discard** — see Section 11.

### The callback-expiry bug and its fix (root-caused and fixed 2026-09-11)

**Symptom**, reported independently across three categories: an operator tap
on Approve sometimes simply did nothing — no ack, no error, no downstream
effect. Previously only mitigated (via `_safe_ack()`), never actually
root-caused.

**Investigation** (following the operator's exact checklist): confirmed the
polling offset persists correctly across runs (single writer, stable value
across 12+ runs), confirmed no webhook is registered
(`getWebhookInfo` empty) and no second process could be stealing updates —
the only other consumer, `approval-poll.yml`'s own scheduled runs, had
in fact never once succeeded (crash-looping on a missing `DATABASE_URL`
secret, failing *before* it ever reaches the Telegram API — see Section 13).

**Root cause**, confirmed via a deliberate controlled test (tap → poll
immediately, vs. tap → poll after exactly 10 minutes untouched, repeated
twice): a tap's `callback_query` update succeeds 2/2 when polled within
seconds of the tap, and is **completely absent** from `getUpdates` — not
merely unanswerable, genuinely gone — after a 10-minute gap. Telegram drops
an un-fetched `callback_query` from its delivery queue well under its
documented 24h retention for ordinary updates; this is a property of
`callback_query`'s interactive nature, separate from (and in addition to)
the already-known `answerCallbackQuery`-expiry issue `_safe_ack()` already
handled. This makes short-polling (`timeout=0`, "check once, return
immediately") on a 5–15 minute cron **fundamentally incompatible** with
catching most taps — a real design flaw, not an occasional quirk.

**Fix**: switch `get_updates()` to long-polling
(`LONG_POLL_TIMEOUT_SECONDS = 270`, i.e. ~4.5 minutes, just under
`approval-poll.yml`'s 6-minute job timeout). Telegram delivers an update the
**instant** it occurs while a `getUpdates` call with `timeout > 0` is open —
it does not wait for the timeout to elapse. Holding a long-poll connection
open for most of the gap between cron firings, back-to-back, shrinks the
blind window from "up to 15 minutes" to roughly the 10–20 seconds between
one run ending and the next starting. No server was added — this keeps
Section 2's zero-infrastructure design; it's the same stateless 5-minute
cron, just holding its one HTTP call open longer per firing. Because two
overlapping long-polls against the same offset would race, `approval-poll.yml`
gained a `concurrency: {group: approval-poll, cancel-in-progress: false}`
block so overlapping scheduled runs queue instead of running in parallel.

**Verified live**: a 2-minute long-poll test window caught 3 real taps and
returned in 0.4 seconds — nowhere near the 120s timeout, proving instant
delivery while a connection is open. The real poller then processed all
three correctly: one against a still-live item (approved, write generated,
preview sent), two against already-deleted items from earlier
relevance/staleness cleanups (correctly no-opped as "already handled"
rather than erroring).

## Section 10 — Notification label

The digest's per-candidate line (Section 9) leads with `category_config.label`
(e.g. `🌾 DEFI YIELDS`) — this is the "which category is this" eyebrow shown
*before* an item is approved. See Section 11 for the separate label header
that appears on the actual delivered post *after* approval.

## Section 11 — Delivery (not publishing)

**No direct channel publishing.** The original spec had the bot calling
Telegram's `sendMessage` straight to each channel's `chat_id`. Removed
entirely on 2026-09-10 (operator direction) — the bot never posts to a
channel itself. Instead, once an item is approved and written
(`_generate_and_preview()`), the operator receives it as **two separate
Telegram messages**:

1. **Routing header** — operator-only: `<label> → <channel display name>`
   (e.g. "🌾 DEFI YIELDS → Crypto Notebook"), the score, and **Mark as
   sent / Discard** buttons (or, during a Section 4.3 benchmark trial,
   **Mark A as sent / Mark B as sent / Discard**). This message is never
   meant to be forwarded — it carries buttons and internal metadata.
2. **The actual post** — the fully-assembled HTML from `post_format.assemble_post()`,
   sent as its own clean message with no buttons, so the operator can
   forward or copy-paste it to the real channel completely unedited. (In a
   benchmark trial, this is two content messages, A and B, sent back to
   back — see Section 4.3.)

The label header (item 1 above) is a load-bearing part of the operator's
manual workflow, not cosmetic — with five channels and multiple categories,
"which of these do I forward, and where" needs to be unambiguous at a
glance since the operator, not the bot, does the actual posting.

**Mark as sent / Discard**: tapping either only ever writes to the `posts`
table (`pipeline/publish.py`) for history/dedup purposes — this is the
system's actual record of what was really sent where, since the bot has no
other way of knowing whether the operator actually forwarded a given
preview. "Publish/Cancel" from the original spec became "Mark as
sent/Discard" for exactly this reason: the bot no longer performs the
publish action, only records that the operator did.

## Section 12 — Reliability

- **Job logging.** Every collector/notify/poll run wraps its work in
  `pipeline/run_log.py`'s context manager, which writes to `run_logs`
  (status, structured `details`, start/finish timestamps) even on an
  uncaught exception.
- **Alerting.** `pipeline/alerts.py` checks recent `run_logs` after every
  run: 3 consecutive failures on any job triggers a separate, no-buttons
  Telegram alert message to the operator, distinct from the normal digest.
- **A dead source logs and gets skipped, never blocks the rest of a run** —
  `pipeline/http.py`'s shared fetch wrapper handles timeout/retry/backoff
  once, centrally, for every collector and LLM call.
- **Idempotent inserts.** `UNIQUE(source_id, external_id)` on `raw_items`
  makes `store.py`'s insert functions safe to call repeatedly — a partial
  failure and retry never double-inserts.
- **Batch inserts.** `score_new_items()` and `store.insert_raw_items_batch()`
  use `psycopg2.extras.execute_values` for one round trip per batch rather
  than one per row — with `defi_yields`' first run alone touching hundreds
  of rows, one-row-at-a-time inserts were the actual risk to
  `collect.yml`'s 10-minute job timeout, not the collection/scoring logic
  itself.

## Section 13 — Secrets & security

All credentials are GitHub Actions repository secrets, injected as env vars
into both workflows — never committed (`.env` is gitignored;
`.env.example` documents the shape with no real values). See
[HANDOFF.md](HANDOFF.md) for the current list and where to obtain each one.

Every incoming Telegram update is checked against `TELEGRAM_ALLOWED_USER_IDS`
(a comma-separated allow-list) before any approve/reject/edit/mark-sent
action is processed — the bot's chat is not itself a secret boundary
(Telegram bots are discoverable), so this is the actual access-control layer.

## Section 16 — Build phases

- **Phase 1 (MVP)** — one category end-to-end, fully validated:
  `defi_yields` → Crypto Notebook. Complete.
- **Phase 2** — additional categories, one at a time (Section 0's
  discipline), each verified against a real live cycle before the next
  starts:
  1. `whale_movements` → Crypto Wall Street. Complete, deep verification
     (first category with genuinely messy real-world data — see BACKLOG.md).
  2. `web3_jobs` → Alpha Edge Crypto / CoinCraft (shared via alternation).
     Complete, faster verification pace by explicit operator direction
     (structured data, no price verification, no editorial judgment
     required).
- **Phase 3** — in progress, 2026-09-11/12:
  3. `gems_security` → Crypto Notebook (shares the channel with defi_yields).
     Complete — see BACKLOG.md for the real, multi-round tuning history
     (TVL screening band, protocol-template dominance rule, the
     behavioral-vs-capability field split).
  4. `news` → Crypto Wall Street (shares the channel with whale_movements).
     Complete.
  5. `tool_launches` → Hustle to Million. Complete — first non-crypto
     category (builder/SaaS/AI audience).
  6. `startup_jobs` → Hustle to Million. Complete.
  7. `macro_news` → Hustle to Million. Complete — reuses `news`'s
     infrastructure against a non-crypto feed list, with a hard political-
     neutrality requirement enforced structurally (see Section 3).
  `grants` (Hustle to Million) deliberately out of scope, see Section 3.
  `airdrops` (paused, see Section 3).

`scripts/collect_cycle.py`'s `COLLECTORS` dict and each channel's
`category_config.categories` list are the single source of truth for what's
actually live — `bot/notify.py` skips any listed-but-unbuilt category via
`category_config_exists()` rather than crashing, specifically so future
categories can be pre-listed in `channel_config.yaml` for record-keeping
without touching the live pipeline.

## Section 17 — Daily cap guidance

Suggested `soft_daily_cap` ranges used when the live categories were tuned
(not a hard rule — categories/channels can deviate with reasoning, as
`defi_yields` did at 6-8):

- Crypto-focused channels (Crypto Notebook, Crypto Wall Street, Alpha Edge
  Crypto, CoinCraft): 4–15, tuned per channel/category based on realistic
  source volume (`defi_yields` 6-8, `whale_movements` 12, `web3_jobs` 8-12).
- Non-crypto channel (Hustle to Million): 6 channel-wide, with each of its
  three categories also carrying its own tighter per-category cap
  (`tool_launches` 8, `startup_jobs` 8, `macro_news` 4 — deliberately low,
  matching `gems_security`'s "rare and notable, not a quota" posture, since
  `macro_news`'s relevance gate already does most of the real filtering).
