# Backlog

Deliberately-deferred items — noticed and worth tracking, not yet implemented.
Now that this repo is pushed (github.com/natanimxo/crypto-content-bot), these
could move to GitHub Issues instead; until that switch is made, this file is
the one place to check.

## Scoring

- **DeFi yields: APY spikes and near-zero yields -- FIXED 2026-09-21
  (`pipeline/score.py::_defi_components`); one residual plateau remains.**
  Open since 2026-09-09; operator flagged it as producing visibly bad output.
  Real diagnosis, which corrected the original theory: the 462%/367%/293%
  pools that all scored exactly 79.5 were NOT emissions pools -- every one was
  fee APY (`apy_base == apy`, no rewards) on ~$100k-TVL Uniswap pools, i.e. a
  thin-liquidity fee burst annualized. Impact and novelty both saturated (a 7d
  change ~= the whole APY) and credibility gave a free 60 for being
  fee-driven. Separate bug: a 0.75% APY pool scored 51.0 (threshold 50) on
  credibility 100 + novelty 50 (the no-history default) + actionability 85
  with impact 2.2; a 0.73% pool scored 55.3 because a -23-point 7d drop read as
  novelty 100.
  Fix: (1) past 100% APY, impact and novelty decay by sqrt(100/apy), so 293%
  and 462% differ and rank below healthy pools while TVL still lifts
  credibility; (2) novelty and actionability scale by clamp(apy/8%), so a pool
  with no meaningful yield cannot clear on non-yield components. Validated
  against every decision the operator made: all 17 non-expired defi decisions
  still clear threshold (86-240% APY approved items drop 89.5->70.5 and
  87.9->66.6, still clearing); 462% -> 47.5, 0.75% -> 28.1, 4996% -> 28.0.
  Not fixed by this, and worth knowing: 293% (54.6), 367% (50.9) and 352%
  (51.5) still clear threshold narrowly -- they now rank at the bottom of a
  digest, they aren't excluded. The operator approved the 367% pool, so a hard
  exclusion wasn't justified by the data. Unnotified rows were rescored in
  place (stored GoPlus credibility kept, no API calls, 6,319 changed);
  already-notified rows keep the score they were sent with so calibration data
  stays intact.
  **Residual, not addressed:** 31-90% APY pools with a >=12.5-pt 7d swing
  still saturate impact and novelty together, so ~50 unnotified pools tie at
  79.6-80.0. That is the same plateau in a different band, and the operator's
  decisions there don't separate them (approved 33%, 44%, 74%; rejected one
  44%), so there's nothing to calibrate against. The relative-to-rolling-
  percentile design from the original note is still the right next step if
  this matters. Also unchanged: novelty is direction-blind (a yield collapse
  earns the same novelty as a spike, now merely damped at very low yield).

- **web3_jobs scores are near-constant; category currently has no real
  prioritization.** Surfaced 2026-09-11 while investigating why
  `alpha_edge_crypto` had never received a candidate: fixed a real logic bug
  in the actionability component (commit `0ac61f7` — the location-
  restriction heuristic was treating any populated `location` field as
  restrictive, when only 6% of location-labeled listings actually are).
  That fix was correct and stays.
  But applying it exposed a second, separate problem: with the bug gone,
  **13/13 of the current live candidates clear `review_threshold=50`**, and
  12 of those 13 land on exactly two values (55.5 or 51.0) — a threshold
  that passes 100% of candidates isn't filtering anything, and two-value
  bucketing means the score isn't discriminating between listings, it's
  just sorting them into two buckets. Root cause, component by component:
  `impact` is floored at 30 for ~98% of listings (undisclosed salary —
  genuinely representative of the feed, not a bug), `novelty` is 0 for
  most (median listing age ~84 days — also genuinely representative), and
  `credibility` only ever lands on 55 or 70 right now (the +30 logo bonus
  is unreachable — 0/55 live listings currently have a `company_logo`
  field populated at all). That leaves `actionability` (now correctly 90
  for almost everyone post-fix) and small credibility variation as
  basically the only things still varying — nowhere near enough signal
  across 4 weighted components for a real 0-100 spread.
  **Deliberately not touched further** — operator direction 2026-09-11:
  don't lower `review_threshold` to route around this (would hide the
  problem, not fix it) and don't touch `impact`/`novelty`/`credibility`
  either (they correctly reflect genuine, current properties of this feed,
  not scoring flaws). `config/category_config.yaml`'s own top-of-file note
  says weights are starting values meant to be "recalibrate[d] later
  against real accept/reject decisions" — there aren't enough of those yet
  for `web3_jobs` to do that honestly. Revisit once there's a real body of
  operator accept/reject history to calibrate against, per that same note.

## Whale movements (Phase 2, built 2026-09-10)

**Resolved same-day, after the first live collect run surfaced real problems:**

- ~~Backfill was native-ETH only~~ — **fixed.** Live evidence (a watched
  Binance wallet whose native-ETH activity was 100% zero-value dust, real
  activity 100% in tokens) confirmed this wasn't a minor gap but made memory
  non-functional for token-heavy wallets. `_backfill_whale_history` now scans
  both native-ETH and ERC-20 `tokentx`, merged into true chronological order,
  using DefiLlama's historical per-contract pricing for tokens.
- ~~"Not on our 8-address watchlist" was treated as "external/retail
  wallet"~~ — **fixed structurally.** Live-confirmed real bug: `0xa9d1e08c...`
  is Etherscan-labeled "Coinbase 10" but wasn't on the watchlist, so 7 posts
  described transfers to it as going to "an external wallet" when it's almost
  certainly Coinbase moving funds between its own wallets. Now two separate
  sets: `WATCHLIST` (actively collected from, small) vs.
  `KNOWN_EXCHANGE_ADDRESSES` (classification-only, ~17 addresses across 10
  exchanges, WATCHLIST is a subset) — plus a self-maintaining fallback
  (`INSTITUTIONAL_SENT_TX_THRESHOLD`) that deprioritizes unlabeled-but-
  clearly-not-retail counterparties by transaction volume, since hand-
  verifying "hundreds" of addresses isn't practical. See the block comment
  above `WATCHLIST` in `collectors/whale_movements.py` for the full design
  and maintenance approach.
- **First live collect run also surfaced a data-freshness bug** (separate
  from the two above, already fixed same day): no recency filter meant a
  transaction from 2023-06-21 got collected as if it had just happened. Fixed
  with a 24h window (`WHALE_MAX_AGE_HOURS`).

**Resolved via the first clean cycle (2026-09-10, post-fix):**

- ~~Is the category fundamentally too dominated by internal exchange
  plumbing?~~ — **validated no, works as designed.** First clean cycle (8
  candidates): 3 confirmed-exchange, 1 likely-institutional, 4 genuine-
  external. Raw collection is ~50% plumbing/institutional, but **none of the
  3 confirmed-exchange items cleared review_threshold** (scored 36.6,
  correctly suppressed by the actionability penalty) — what actually reaches
  the operator was 2 genuine-external + 1 likely-institutional. The scoring
  layer, not the collection floor, is what does the real separation, and it's
  working. No category rethink needed — operator direction 2026-09-10.
- **ERC-20 backfill: "unproven but not failing."** Deliberately stopped
  chasing a live success case (operator direction 2026-09-10) — three
  separate candidates across two cycles all rendered no history, and each
  negative was individually verified correct against live Etherscan data
  (including widening one scan from 20 to 100 transactions — still nothing
  qualified). The logic itself is confirmed sound: real historical prices
  computed correctly for 14 genuine token candidates in one of those checks,
  none of which happened to clear $2M. It's a one-time per-wallet bootstrap,
  not a recurring code path — most flagged moves use the cheaper, already-
  proven-working own-history path instead (`_compute_whale_own_history_line`).
  A live success case will show up naturally as new first-sighting wallets
  come through; not worth further dedicated testing.

**Still open:**

- **Exchange-to-exchange transfers can generate two `raw_items`** — one from
  each watched wallet's own `txlist`/`tokentx` call, since the collector
  fetches per-address rather than per-transaction. `external_id` is scoped
  per (tx_hash, watched_address, direction) specifically to avoid a UNIQUE
  collision here, which means both sides get stored and potentially both get
  notified/scored independently for what's really one real-world event. Not
  deduped across the two — a candidate for cross-checking `tx_hash` at
  collection time if this proves to matter in practice (exchange<->exchange
  is already scored lower on actionability, so the practical impact of a
  double-surface may be small).
- **$2M collect threshold not yet tuned.** First clean cycle ran 2026-09-10
  post-fix: 8 collected, 3 cleared review_threshold. One data point — still
  want a few more cycles before actually adjusting the $2M floor either way.
- **KNOWN_EXCHANGE_ADDRESSES is ~17 addresses across 10 exchanges** —
  hand-verified the same way as WATCHLIST, not exhaustive (genuinely "hundreds"
  exist). The `INSTITUTIONAL_SENT_TX_THRESHOLD` heuristic is the deliberate
  safety net for what this list doesn't cover by name.

- **Score looks flat against operator judgment for `whale_movements` --
  NOT actionable yet, n is too thin. Recheck at 15-20 decisions.** First
  real calibration pull (2026-09-20, after `scripts/calibration_report.py`
  was rewritten to count ignored cards as negatives): 5 accepted cards
  averaged score 59.8; the ones left ignored averaged 59.0 (pending, all
  ages) / 60.3 (ignored >=3 days) / 59.5 (>=1 day). AUC (the chance an
  accepted card out-scored an ignored one; 0.5 = the score says nothing
  about the operator's choice) is 0.50 at a 3-day ignore cutoff and 0.54 at
  1 day -- stable across cutoffs, so it isn't an artifact of where the line
  is drawn. By score band the acted-on rate is 1/5, 2/6, 2/6: flat. The
  report auto-flags it ("NOT tracking") but tags the verdict THIN, which is
  the honest reading -- 5 accepted vs 12-14 ignored cannot separate "the
  score is uninformative" from noise, and no scoring change should be made
  off it. Contrast: `tool_launches` (AUC 0.87) and `news` (0.76) show the
  score clearly tracking the operator's picks, so this is a `whale_movements`
  property, not a general artifact of the method.

  One lead to look at when there's more data, not a conclusion: accepted
  whale cards average novelty 48 vs 25-29 for ignored ones, while ignored
  cards average HIGHER impact (66 vs 45) -- i.e. the operator may be picking
  on "unusual for this wallet" rather than on transfer size, which is the
  opposite of `impact` carrying the most weight. Worth checking whether
  that holds at 15-20 decisions before touching the 0.30/0.30/0.20/0.20
  weights. Until then: leave the scorer alone.

- **`scripts/calibration_report.py` rewritten 2026-09-20 to use ignored
  cards as a real negative signal.** The operator approves or ignores and
  almost never taps Reject (first pull: 50 decisions, seven of eight
  categories with zero rejections, 81 of 105 undecided cards >2 days old),
  so the old report -- which treated 'pending' as "no data yet" -- was
  discarding the most useful data being generated and could not compute an
  accept rate that meant anything. Now: pending past a cutoff (default 3
  days, `--days N`) counts as ignored; too-recent and `expired` cards are
  excluded and shown separately (expired stays out deliberately: rejecting
  the 46 Crypto Notebook orphans would have poisoned these stats). Per
  category it prints the implicit accept rate, acted-on rate by score
  tercile, top-vs-lower-half-of-digest rate, and an AUC with an explicit
  verdict (thresholds: >=0.65 tracks, <=0.55 flagged, <5 per side no
  verdict, <20 total tagged THIN). The cutoff is sanity-checked in the
  output itself: observed time-to-decision is median 0.3d / p75 0.5d / p90
  1.1d, so 3 days mislabels almost nothing as ignored.

  **Built-in caveat, printed with every report because it's real:** digests
  list cards highest-score first, so "acts on high scores" and "reads from
  the top and runs out of time" are the same observation, and an ignored
  card may simply never have been reached. Only picks from the LOWER half
  of a digest are evidence of content-driven choice (currently 2 of 8 news,
  2 of 6 tool_launches, 1 of 7 macro_news accepted cards). Only a
  deliberately shuffled digest order could fully separate score from
  position; not done. Operator is switching from ignoring to tapping Reject
  on cards they'd never post (2026-09-20), which is the cleaner fix -- each
  Reject is a real negative independent of reading order.

- **Dedup gaps, 2026-09-21: fixed the re-send; paraphrase duplicates remain.**
  Diagnosis of "titles_match and cross-cycle passes aren't catching these":
  the BBC pairs (identical headline re-sent 1-3 days apart) DO match each
  other under `is_duplicate_story` -- the cross-cycle pass just only ever
  compared still-eligible, unnotified candidates, so once copy A was notified
  copy B was compared against nothing (topic_key cooldown is only 12-24h).
  Fixed in `get_new_candidates`: candidates are also compared against items
  notified in the last 7 days (`NOTIFIED_DEDUP_LOOKBACK_DAYS`), EXACT title
  only. Audited against every real sent pair in that window first: all 5
  identical-title hits were true re-sends (incl. all three incident
  headlines); all 5 fingerprint-only hits were different events, so
  fingerprint is deliberately not used there.
  **Not fixed: "Circle Launches Arc Mainnet" (thedefiant) vs "Circle debuts Arc
  blockchain..." (coindesk).** They share only a weak ticker overlap (0.3 <
  0.5), and extraction produced junk (false ticker `UNI`, phrases like "Circle.
  More"). A proper-noun-overlap rule was built and measured: the loosest
  setting that catches this pair flags 1,402 pairs across 10 days of real
  news, merging distinct stories (separate Clarity Act developments,
  "Standard Chartered on SKY" vs "on ARB"); every stricter setting misses
  Circle. Shared names identify the topic, not the event, so it was removed.
  Options if this matters: an LLM same-event check on only the borderline pairs
  (>=2 shared proper nouns) -- costs a call per pair; or cap one item per
  primary subject per digest. Entity extraction junk is a separate cleanup.
  **Decision 2026-09-21 (operator): per-digest subject cap, no LLM check** --
  a non-deterministic call inside a filter can silently suppress real news and
  can't be debugged like the scoring. BUILT: `SUBJECT_CAP_CATEGORIES` (news and
  macro_news at first; NARROWED TO macro_news ONLY the same day, see below) in `select_candidates.py`, keyed on `entities.lead_subject`
  (first capitalized non-filler headline word), best effective score wins.
  Two findings while building: (1) `topic_key` could NOT be the key -- the
  Circle pair's were `UNI` vs `USDC`, some rows carry GUID/URL keys, and a
  topic_key cap would have removed 25 of 60 sent news items; (2) capped items
  are DEFERRED, not dropped (they stay unnotified and eligible next cycle, age
  bonus applies), so it spaces a subject out rather than deleting it -- which
  also means a duplicate Circle story can still arrive in a LATER digest; this
  limits repeats per digest, it does not detect that two stories are the same.
  Cost, audited on 117 sent items: 9 would have been spaced out, some genuine
  (three separate Bitcoin stories in one digest, three SEC stories). Watch
  item: a perpetually busy subject (Bitcoin) could keep deferring its second
  story; the age bonus is the only counterweight.
  **Revised same day -- cap removed from `news`, kept on `macro_news`.**
  Operator raised the Bitcoin starvation risk and asked whether news should
  have it at all. Replayed 10 days of real news arrivals (303 above-threshold,
  6 cycles/day, 10/day cap): Bitcoin was 5% of arrivals (14 of 303), NOT
  dominant -- the top lead word is generic "crypto" (20), itself a flaw in
  this key (it would group unrelated "Crypto.com..." / "Crypto market..."
  stories). Bitcoin backlog peaked at 7 (vs 5 uncapped) and did not grow
  unboundedly, but cap=1 raised Bitcoin's median wait 8.9h -> 27.0h and cost
  it 3 of 11 sends. cap=2 replayed IDENTICAL to no cap and would not have
  caught the two-item Circle pair, so it is not an option. The bottleneck is
  the daily cap, not the subject cap (213 of 303 arrivals unsent either way).
  Replay is approximate (ignores cooldown/holds). Kept on macro_news, where
  volume is low (16 eligible vs a 6/day cap) and deferral is cheap: real
  repeats there ("Interest rates held..." x2; three Fed rate-hike pieces) sit
  alongside three distinct AI-regulation op-eds, so precision is mediocre even
  there -- it spaces things out, it doesn't prove duplication. Per operator,
  a repeat is preferred to losing a distinct angle.

## Telegram / bot reliability

- ~~Unresolved: callback_query taps sometimes don't appear in getUpdates, or
  fail answerCallbackQuery even immediately~~ — **root-caused and fixed
  2026-09-11.** A deliberate controlled test (tap -> poll immediately vs. tap
  -> poll after exactly 10 minutes untouched) proved it decisively: a tap
  succeeds 2/2 when polled within seconds, and is completely absent from
  getUpdates — not just unanswerable, genuinely gone — after 10 minutes
  (confirmed twice). Root cause: Telegram drops an un-fetched callback_query
  from the delivery queue well under its documented 24h retention for
  ordinary updates, specific to callback_query's interactive nature. Separate
  from the already-known answerCallbackQuery-expiry issue `_safe_ack` already
  handled — this one meant the update never arrived at all, which no amount
  of ack-handling could fix. Made short-polling on a 5-15 minute cron
  fundamentally incompatible with catching most taps — a real design problem,
  not a quirk.

  Fixed by switching `bot/approval_poller.py` to long-polling
  (`LONG_POLL_TIMEOUT_SECONDS=270`) instead of short-polling (`timeout=0`) —
  Telegram delivers an update the instant it occurs while a long-poll
  connection is open, rather than waiting for the timeout. No server added
  (keeps Section 2's zero-infrastructure design): a GitHub Actions job long-
  polling for most of the gap between 5-minute cron firings, back-to-back,
  shrinks the blind window from "up to 15 minutes" to roughly 10-20 seconds.
  `approval-poll.yml`'s job timeout raised to 6 minutes to match, with a
  `concurrency` guard added (queue, don't overlap) since two runs long-
  polling simultaneously against the same offset would race.

  Verified live: a 2-minute long-poll window caught 3 real taps and returned
  in 0.4 seconds — nowhere near the 120s timeout, proving instant delivery
  while a connection is open. The real poller then processed all three
  correctly (one against a still-live item — approved, write generated,
  preview sent; two against already-deleted items — correctly no-opped as
  "already handled" rather than erroring).

  **Separate, still-open finding from the same investigation:** no GitHub
  Actions Secrets are configured on the repo at all — the "Approval Poll"
  cron has been crash-looping on a missing `DATABASE_URL` every ~5 minutes
  since the repo went public (confirmed via `gh run list` / `gh run view
  --log`), failing before it ever reaches the Telegram API call. Harmless to
  this investigation (ruled out as a second consumer of updates, since it
  never got that far), but means the production cron — long-polling fix or
  not — cannot actually run yet. Needs the operator's go-ahead before secrets
  are written into the repo's settings.

- **Collection moved off GitHub Actions to a Railway cron service,
  2026-09-15 -- the same cron-unreliability pattern that already moved the
  approval poller, this time measured on `collect.yml` itself.** Diagnosed
  a "6 hours, nothing received" report by pulling the actual gaps between
  the last 10 scheduled `collect.yml` runs: 2.9h, 3.6h, 5.4h, 6.1h, 8.2h,
  5.2h, 4.6h, 5.7h, 7.1h — averaging 5.4h against the intended 4h interval,
  never once on schedule. Operator direction: move collection to Railway
  too, same reasoning, since it's already proven reliable for the poller.

  Set up as a second Railway service ("collect-cycle") in the same project,
  connected to the same repo, running `scripts/collect_cycle.py` (already a
  clean one-shot script -- collect → score → notify, then exit -- no code
  changes needed) on Railway's native cron (`deploy.cronSchedule`,
  5-minute-minimum granularity, container starts fresh per run and must
  terminate cleanly, which this script already does).

  **How it's actually configured, worth being explicit about since it's not
  what a first read of the repo would suggest**: a `railway.collect.json`
  Config-as-Code file was written and committed, mirroring the existing
  `railway.json`'s pattern for the poller -- but Railway's API rejected
  binding a NEW service to it (`railwayConfigFile`), returning "Config as
  Code (railway.json / railway.toml) is deprecated... Use Infrastructure as
  Code (.railway/railway.ts) instead" as a hard error, even though the
  existing poller's `railway.json` keeps working under a 2026-12-01
  grandfather clause. Rather than doing the Infrastructure-as-Code
  migration under this ask's scope, the service's startCommand/
  cronSchedule/restartPolicyType/builder were set directly via
  `serviceInstanceUpdate` (Railway's own GraphQL API, the same one the CLI
  itself uses) -- these are genuinely durable, service-level settings, not
  a workaround; they don't reset on redeploy. `railway.collect.json` was
  therefore removed rather than left in the repo looking like the live
  config when it isn't bound to anything. If the poller's `railway.json`
  ever needs the same treatment before 2026-12-01, expect the same
  rejection and use the same direct-API path, or do the real
  `.railway/railway.ts` migration then.

  `.github/workflows/collect.yml`'s schedule trigger is commented out, not
  deleted -- `workflow_dispatch` still works for manual runs. Re-enabling
  the schedule would double-collect against the Railway job.

  **Separate, real incident from the same work, not swept under this
  entry**: checking whether the poller service already had
  `ETHERSCAN_API_KEY` set (needed for the new collect-cycle service but not
  the poller) via `railway variables --json` printed all six of that
  service's real secret values in cleartext into the session transcript —
  `DATABASE_URL` (with password), `DEEPSEEK_API_KEY`, `ETHERSCAN_API_KEY`,
  `TELEGRAM_BOT_TOKEN`, and both Telegram IDs. A masked/table-view command
  should have been used instead. Operator direction: rotate all three
  credential-shaped values (DB password via Supabase, DeepSeek key,
  Etherscan key) out of caution, same posture as the earlier Telegram token
  incident. The new collect-cycle service's own variables are Railway
  cross-service references (`${{crypto-content-bot.VAR}}`) by name, not
  copied values -- set without the actual secret values ever being touched
  a second time, and they'll pick up the rotated values automatically once
  the poller service's are updated.

- **soft_daily_cap moved from a UTC-calendar-day boundary to a rolling 24h
  window, and cap selection now uses an age-weighted effective score instead
  of raw score, 2026-09-16 -- both real, confirmed problems, not
  precautionary changes.** Operator (UTC+3) reported the daily cap's
  behavior directly: quota exhausted during their evening, sat fully spent
  overnight while they slept, then the WHOLE cap freed up the instant UTC
  rolled over, dumping everything that had backed up in one notification --
  live-verified 2026-09-15/16 (a 20:09 UTC run sent 0 news items despite 9
  clearing threshold; the very next run, 00:09 UTC, sent 27 across 3
  channels in one burst). Gate-then-flush, the opposite of pacing.
  Considered shifting the reset hour to the operator's own local midnight
  instead (their other proposed option) and rejected it: that only
  relocates the burst, since most people's sleep window straddles their own
  local midnight too -- it doesn't remove the gate-and-flush dynamic, just
  moves which clock hour it happens at.

  Fixed with a rolling window (`CAP_WINDOW_HOURS = 24`,
  pipeline/select_candidates.py) -- both the per-category cap
  (`_category_notified_recent_count`) and the channel-wide cap (below) now
  count "sent in the trailing 24h" instead of "sent since the UTC calendar
  date changed." Capacity drains and refills continuously; there's no
  moment where the whole thing resets, and no timezone to get right (or
  wrong) since there's no boundary at all. Confirmed against real data
  post-fix: `news` and `hustle_to_million` both showed 0 remaining headroom
  immediately after deploying, correctly reflecting that the OLD boundary
  system had already let more through in the trailing 24h than the cap
  allows (2x, in news' case, since it got a full quota under the old system
  on both sides of a single UTC midnight) -- expected, temporary, self-heals
  as those over-quota sends age past 24h. Not a bug.

  **Second, separate problem confirmed twice (not inferred) via the same
  incident**: traced specific `raw_item_id`s and proved over-cap candidates
  are genuinely deferred, not dropped -- but deferred candidates have no
  seniority under pure score-ranking, since every cycle re-ranks from
  scratch. In a high-volume category (`news`: ~20/21 items clearing
  threshold most cycles, cap far below that), a mid-scoring item can lose to
  fresher, higher-scoring arrivals indefinitely -- "never selected" and
  "dropped" look identical from the operator's side even though the DB
  state differs. Fixed with `_age_bonus`: a small bonus added to a
  candidate's EFFECTIVE ranking score only (never the stored/displayed
  `scores.score`) that grows the longer it's sat eligible and unnotified --
  +1 point per 6h waited, capped at +12 after 3 days. Deliberately capped
  well below a typical "barely clears threshold" vs. "genuinely excellent"
  gap (real news scores span roughly 50-95) so a stale mediocre candidate
  can gain enough ground to beat something only modestly better after
  waiting, but can never leapfrog something actually much better just by
  sitting around -- verified with synthetic data before trusting it: a
  72h-old score-52 candidate correctly beat fresh 58/60-scored candidates
  (effective 64 vs 58/60) but a 95-scored fresh candidate stayed
  untouchable regardless of how long anything else had waited.

  Real bug caught before shipping, not after: `scores.score` is Postgres
  `NUMERIC`, which psycopg2 returns as `Decimal` -- `Decimal + float`
  (the plain-float `_age_bonus` return) raises `TypeError` outright,
  confirmed by direct reproduction before it ever reached the sort call.
  Both call sites (`select_candidates.get_new_candidates` and
  `notify.notify_channel`) explicitly cast to `float(row["score"])` first.

  **Channel-level cap wired up in the same pass**: `channel_config.
  soft_daily_cap` had been documented since day one (pipeline/
  select_candidates.py's own long-standing comment) as "the separate
  combined cap, applied by the caller across all of a channel's
  categories" -- but `bot/notify.py` never actually read it. A channel
  whose every category individually stayed under its own cap could still
  have all of them fire the same cycle and bundle into one oversized
  notification, with nothing throttling the channel as a whole. Same
  silent-no-op shape as the Telegram-token log leak and the
  approval-with-no-write bug -- documented as enforced, never actually
  wired. Fixed rather than removed (real, useful throttle): `bot/notify.py`
  now sums each channel's total notified-candidate count over the same
  rolling 24h window and trims the combined candidate list down to
  whatever headroom remains, using the identical age-bonus effective-score
  tie-break as the per-category cap -- one consistent pacing mechanism at
  both levels, not two different ideas of "soft cap."

## Gems/security screening (Phase 2/3, built 2026-09-11)

- **Pre-liquidity discovery gap — accepted tradeoff, not fixed.** Discovery
  reuses defi_yields' own DefiLlama pool feed (operator-approved plan): a
  token only becomes a candidate once it has a real DEX pool with ≥$100k
  TVL. This misses tokens at the pre-liquidity stage — arguably when a scam
  token is most dangerous — since there's no free, keyless "new token
  firehose" available to catch that earlier. Not blocking; a separate,
  harder sourcing problem for later if it turns out to matter in practice.

**Resolved same-day, after the first live collect run surfaced real problems
(same pattern as whale_movements' first cycle):**

- ~~Chain-name case mismatch~~ — **fixed.** `collectors/gems_security.py`
  compared DefiLlama's `chain` field ("Ethereum") directly against
  `CHAIN_NAME_TO_GOPLUS_ID`'s lowercase keys without lowercasing first — every
  single pool read as an unmapped chain (`deferred_unmapped_chain=6756` of
  6801 qualifying pools on the first run). One-line fix.
- ~~GoPlus flagged well-known blue-chip assets~~ — **fixed.** First real run
  (post chain-mapping fix) produced 18 candidates; every single one was a
  top-tier established asset (WBTC, USDT, wstETH, BlackRock's BUIDL fund,
  etc.) — sorting qualifying pools TVL-descending means the highest-TVL
  pools, dominated by exactly these assets, consume the whole per-run
  screening budget first. Verified live that "this creator has deployed a
  honeypot before" on BUIDL was a heuristic artifact (BlackRock's fund has 5
  holders, 80% in one institutional wallet — the flag is GoPlus's
  same-deployer heuristic conflating shared institutional tokenization
  infrastructure with a repeat scammer). Fixed with a hand-maintained
  `KNOWN_MAJOR_TOKENS` exclusion list (same pattern as whale_movements'
  `KNOWN_EXCHANGE_ADDRESSES` / web3_jobs' `KNOWN_WEB3_COMPANIES`) — excluded
  before screening, not just before posting, so budget isn't wasted on them.
- ~~`is_mintable` / `honeypot_with_same_creator` dominated every flag~~ —
  **fixed.** Re-ran after the exclusion list: 24 candidates, but
  `is_mintable` fired on 17/24 (71%) and `honeypot_with_same_creator` on
  8/24 (33%), and in 5 cases were the ONLY flag on tokens that turned out to
  be well-known liquid-staking derivatives (rETH, tBTC, kBTC) and a second
  institutional fund (Securitize-tokenized CLO). Root cause: minting is the
  correct, designed behavior of a liquid-staking/wrapped-asset receipt token
  (it mints on every new stake/deposit) — GoPlus's heuristic can't tell
  "mints because that's the product" from "mints to rug holders," and
  same-creator conflates shared deployer/factory infrastructure with a
  repeat offender. Both fields were almost certainly built against a
  memecoin-style scam population, a poor match for what a DeFi yield-pool
  feed actually surfaces. Fixed by demoting both to corroborating-only
  (`pipeline/goplus.py`'s `NON_GATING_FIELDS`) — still reported if another
  field also fires, but can no longer be the sole reason something qualifies.

**Still open — a real, structural question, not a bug:**

- **Even after both fixes, TVL-descending prioritization keeps surfacing
  institutional/yield-infrastructure tokens over genuine small-cap "gems."**
  The post-fix candidate list (21 items) is still dominated by yield-bearing
  wrapper tokens (osETH-style receipt tokens, Maple's Syrup product across 9
  of 21 pools) tripping `hidden_owner`/`is_blacklisted` — plausibly
  legitimate, disclosed features of audited yield products, same shape of
  mismatch as the two already-fixed fields, just one level less obvious. The
  two clearest genuine "gem"-style findings (real concentration/transparency
  concerns on small, unrecognizable tokens — `BMD-USDC`, `WETH-GFC`) only
  reached screening near the bottom of the TVL-sorted budget. Candidate fix:
  stop sorting pools TVL-descending (which structurally privileges the most
  institutional, least "gem"-like assets every cycle) — a TVL *band*
  (excluding both sub-floor noise and the largest, most-established pools)
  would more directly target what "gems" actually means here. **Deliberately
  not implemented yet** — this changes what the category actually screens,
  not just fixes a bug, and needs operator direction rather than a unilateral
  call. 21 real candidates are sitting collected-but-unscored right now,
  pending that direction.

## Copyright guard (cross-category, 2026-09-12)

- **The "RSS-only collection means there's no full article to paraphrase"
  reasoning in `pipeline/write_post.py`'s copyright-guard comment was never
  an architectural guarantee, and macro_news proved it — followed up,
  fixed, and re-verified, not just corrected in prose.** Operator
  follow-up after the macro_news build: since `macro_news` reuses `news`'s
  RSS collection method but some of its feeds turned out to deliver
  full-body text, does that same finding apply to `news`'s own 6 crypto
  feeds, and does the documented "the architecture protects us" reasoning
  need correcting?

  Checked live: no, `news`'s 6 feeds are genuinely short — max real
  description length 373 chars across theblock/decrypt/blockworks/
  thedefiant/protos; CoinDesk's `summary` field is empty on every entry
  (headline-only feed by design, not a bug — confirmed by inspecting the
  raw entry, `content`/`summary_detail` are empty too). So the ORIGINAL
  claim happens to still hold for these specific 6 feeds.

  But the REASONING behind it was wrong regardless, and macro_news is the
  proof: its Axios feed delivers full article bodies (1000-3400+ chars/
  item, live-measured) through the exact same feedparser/RSS `summary`
  mechanism `news` uses. Same collection method, opposite outcome — "RSS
  collection" was never what was protecting anything; it was always a
  fact about what these 6 specific feeds happen to put in that field,
  external and changeable without any code here changing. Corrected the
  comment in `pipeline/write_post.py` to say so plainly, and to note the
  check runs for every category via `generate_post`'s generic
  `payload.get("description")` line — it was never actually gated to
  `news` specifically, just naturally a no-op for categories with no
  `description` field.

  Testing the reasoning against real data surfaced a second, deeper,
  actually-fixed bug, not left as a documentation note: the similarity
  check used `SequenceMatcher(None, draft, source).ratio()`, which is
  `2*matched / (len(draft)+len(source))` — a real 90-word verbatim lift
  from macro_news's 2565-char Axios source scored ratio=0.067 (nowhere
  near the 0.6 threshold), purely because the long source diluted the
  denominator, not because the copying was subtle. Replaced with
  `_content_overlap_fraction` — matched characters as a fraction of the
  DRAFT's own length, source length no longer dilutes anything. Re-tested
  before trusting: the same verbatim lift now scores 1.0 (caught); a
  legitimate own-words summary scores 0.0 (correctly not caught); two
  realistic partial-reproduction drafts (half verbatim/half original
  commentary; one ~20-word verbatim run inside an otherwise-original
  draft) scored 0.37-0.38, which is what the new threshold (0.3) sits just
  below. Regression-tested against 15 real `news` items post-fix — 14
  passed, 1 correctly failed the (unchanged) n-gram check on a genuine
  verbatim lift from a short teaser, unrelated to this fix. Re-verified
  all 13 real macro_news candidates (including the full-body Axios items)
  still generate cleanly.

  **Named, NOT fixed — a real, structural limit, same "floor not
  guarantee" honesty as the neutrality guard**: none of this catches a
  genuine semantic paraphrase (same meaning, different words, same
  structure) — tested directly, a synonym-substituted paraphrase of a real
  passage scored 0.17, well under threshold, both before and after the
  fix. Character-diff heuristics structurally cannot see that; only
  semantic/embedding comparison could, a materially different approach
  (and not a zero-LLM-call one) not attempted here.

## Content quality fixes from real-digest review (2026-09-16)

Four issues from one operator review of real output -- three content
quality, one structural (the fourth, web3_jobs source exhaustion, is
tracked separately below under Hustle to Million rather than here, since
it's a sourcing decision still pending operator direction, not a fix).

- **Cross-outlet/cross-cycle dedup failed on an identical headline --
  "AI regulation faces political deadlock as calls grow for Congress to
  act", byte-identical title AND description from bbc_world and
  bbc_business.** Two real, distinct root causes, not one:

  1. `fingerprint_overlap` (pipeline/entities.py) hard-gates on ticker-or-
     figure presence before ever checking phrase overlap -- and this story
     had neither (a policy/AI deadlock piece, not a market-moving one),
     so it scored 0.0 regardless of how much text actually matched.
     Structural: phrase-only corroboration was never possible for content
     lacking both signals, common for macro/policy stories, rare for
     crypto ones (why this sat latent through news.py's build without
     surfacing).
  2. The two copies that actually landed in the same digest were
     collected 7h41m apart, in separate `collect()` runs -- same-cycle
     dedup (collectors/news.py, collectors/macro_news.py) only ever
     compares candidates gathered within ONE run; it structurally cannot
     catch a story a feed re-serves across separate collection cycles
     before either copy is notified.

  Fixed both. `pipeline/entities.py` gained `titles_match` (an exact,
  case/whitespace-normalized title comparison -- titles are deliberately
  excluded from `extract_entities`' phrase extraction, since these feeds
  Title-Case every headline word, so an identical headline previously
  carried zero weight in the fingerprint score) and `is_duplicate_story`
  (title match OR fingerprint overlap, either sufficient), now used
  everywhere same-story dedup happens instead of three copies that could
  drift. Collection-time dedup in both collectors now calls
  `is_duplicate_story` instead of `fingerprint_overlap` directly.
  Selection-time (`pipeline/select_candidates.get_new_candidates`) gained
  a NEW cross-cycle dedup pass using the identical test, applied across
  every currently-eligible not-yet-notified candidate for a category
  regardless of which cycle collected it -- deliberately NOT a topic_key
  collapse (too coarse, "same primary subject" not "same specific event",
  would risk merging genuinely different same-subject stories). Verified
  against the real incident: the 4 actual duplicate raw_items in the live
  DB collapse to 1 through the real code path, not a synthetic test.

- **tool_launches had no gate for "is this actually a tool."** Real
  examples: "The bottom 50% of U.S. households are short after essentials
  (BLS data)" (score 64.5) and "Loss. a tiny satire about AI progress"
  (score 60.0) both cleared HN's points/comments floor with nothing
  checking whether the linked thing was software. "Show HN: I made X"
  covers essays, data analyses, research papers, and jokes as readily as
  real products.

  Checked both flagged examples directly before writing any rule, not
  assumed from the titles alone: loaded the actual linked pages. "Loss"
  genuinely is a satirical fake-corporate-AI landing page (verified --
  mock "rituals"/"omens" copy, no real functionality). The household-
  finance one is NOT a false positive -- it's a genuine, real interactive
  dashboard (adjustable income-concept/projection toggles, three real
  charts) whose HN title just states its finding instead of describing
  itself as a tool. Built the gate around what that comparison actually
  showed rather than both examples: scanned all 185 stored HN candidates
  first (only 3 total hits for any candidate pattern -- a narrow,
  high-precision gate is the right scope here, not a broad classifier off
  2 examples). Two signals, both mechanically or self-evidently reliable:
  HN's own auto-appended `[pdf]/[video]/[audio]` suffix (a bare document/
  media file is never itself a runnable tool), and explicit self-labeling
  as opinion/satire/essay in the title (a poster who titles their post "a
  tiny satire about X" is already telling you what it is). Re-verified
  post-fix against the same 185 real rows: catches exactly the 2 genuine
  non-tools, correctly preserves the household-finance dashboard, zero
  other false positives or false negatives in the full set.

- **macro_news's taxonomy gate cleared on incidental keywords, not
  subject matter -- same class of bug as the earlier Axios full-body-text
  finding.** "Scoop: Top House Democrat launches investigation into
  Donald Trump Jr.'s wedding" scored 74.2, zero economic implication for
  a reader, in the one category where that kind of false positive matters
  most (a hard political-neutrality requirement). Root cause: the bare
  `investigations?`/`regulations?` pattern added 2026-09-15 specifically
  to catch a real, on-brief story (Wired's Polymarket-trading-
  investigation piece) was never actually anchored to that story's real
  distinguishing signal -- "investigation" co-occurring with a market/
  financial subject in the same title ("...Investigations of Polymarket
  Trades") -- so it matched ANY investigation into ANYONE, regardless of
  subject. Fixed by requiring that co-occurrence, same compound-condition
  shape as the existing conflict+economic-spillover bucket. Re-verified
  against every currently-stored macro_news title, not just the one
  reported case: 7 titles flip out of the regulatory bucket. One
  deliberate, disclosed tradeoff -- an Axios "OpenAI faces Senate
  investigation into Hugging Face breach" story also loses its only
  matching bucket, accepted since it was itself borderline against the
  brief (general tech-company legal scrutiny, not clearly "AI regulation
  or industry-shifting capability") and precision over recall is this
  category's whole posture. The Polymarket case itself was re-confirmed
  to still match after tightening.

## Hustle to Million (Phase 3, 2026-09-12)

- **`grants` deliberately out of scope, not just unbuilt.** Live-checked
  every realistic source before concluding anything (same discipline as
  everything else in this file): Grants.gov's API works technically but
  returns US federal research grants (NSF SBIR, biomedical research
  centers) — zero overlap with what a builder/indie-hacker audience means
  by "grants". DevPost's hackathon API is bot-blocked (403). F6S, YC,
  Antler, and Techstars expose marketing pages, not feeds or APIs.

  Unlike every other category built so far, there's no structured,
  frequently-updated source to poll in the first place — accelerator/grant
  application windows are a handful of predictable events per program per
  year, which is a calendar reminder, not something a 4-hour collection
  cycle can meaningfully monitor.

  The obvious alternative — a hand-maintained watchlist of known program
  pages, text-matched for "applications open" — was considered and
  rejected (operator direction): it's the same curated-list-rot problem
  already hit four times this build (`is_mintable`, Syrup, osETH, Pendle —
  see the Gems/security screening section above), except this time the
  list itself is unmaintainable long-term — marketing pages get redesigned
  without notice, and low-confidence text matching against unstructured
  prose would fail silently rather than loudly. Same treatment as Product
  Hunt (tool_launches) and `airdrops` (Section 3 of the spec) — dropped
  outright rather than parked as a someday-maybe or stretched into looking
  like a real collector. Revisit only if a genuinely structured source
  turns up later.

- **Political neutrality guard (`macro_news`) is a floor, not a
  guarantee — write this down plainly, operator direction 2026-09-12.**
  `pipeline/write_post.py`'s `_find_neutrality_violations` (wired into the
  same checked-write/retry/`RuntimeError` mechanism as the gems_security
  overclaim guard and the news copyright guard) catches three real,
  code-detectable failure modes: loaded characterization of a political
  actor ("authoritarian", "corrupt regime"), unattributed motive-assertion
  ("is trying to distract from"), and unattributed prescriptive advocacy
  ("the Fed should raise rates"). All three are regex/keyword checks on
  LOADED VOCABULARY.

  What they structurally cannot see, and were never going to: bias in
  macro coverage lives mostly in SELECTION and FRAMING, not vocabulary —
  whose casualties get numbers and whose don't, whose action reads as "a
  response" versus "an escalation," which side's stated reasons get
  repeated in the piece and which side's don't. None of that shows up as
  a banned word a regex can catch. Passing all three checks is not a
  claim that a given post is neutral or safe to publish unreviewed — it's
  a claim that it avoids the specific, narrow vocabulary-level failure
  this code knows how to look for. The operator's own approval step is
  what actually covers selection/framing bias; this guard is real
  defense-in-depth underneath that, not a replacement for it.

  Before being trusted, the prescriptive-advocacy sub-check specifically
  was tested against real generated drafts (operator direction: "show me
  the check running against a handful of real generated drafts, not just
  synthetic test strings," since a false positive here means a legitimate
  post never generates rather than degrading). Two passes: (1) all 13
  real held candidates from macro_news's first live cycle (2026-09-12)
  were written through the full pipeline end to end — zero triggered any
  of the three checks, meaning the model already avoids advocacy-flavored
  language on its own with the prompt instructions in place, so this
  batch didn't actually exercise the risky code path; (2) the operator's
  own named pair ("the Fed should raise rates" vs. "analysts expect the
  Fed will need to raise rates") plus seven adjacent hand-built cases were
  run directly against `_find_neutrality_violations` — all nine behaved
  as intended, including the attributed-via-"said"-at-sentence-end and
  attributed-via-"according to" variants. One deliberately adversarial
  case was included and left unfixed as a known, named limit (see that
  function's docstring): the attribution exemption checks for cue
  PRESENCE, not real parsing, so a sentence gaming the exemption with a
  fake attribution phrase would slip through. Narrow enough that no real
  draft has hit it, not fixed pre-emptively.

- **`web3_jobs`/`startup_jobs` duplicate a real chunk of RemoteOK-specific
  logic.** Both collectors hit the same API and hit the same real
  data-quality bugs (server-side mojibake, duplicated location strings,
  the location-restriction detector) — `_fix_mojibake`, `_dedupe_location`,
  `LOCATION_RESTRICTION_PATTERNS`/`_is_location_restricted`, and
  `score.py`'s salary/novelty/credibility/actionability formula are all
  copy-pasted between the two rather than shared. Deliberate, not an
  oversight — building `startup_jobs` mid-session was a chance to refactor
  `web3_jobs.py` to extract a shared `pipeline/remoteok.py`, but that
  means touching a working, already-relied-upon category for a DRY
  concern alone, real regression risk for zero behavior change. Worth
  doing eventually (a fix to the mojibake/location logic would otherwise
  need to land in two places and could silently drift), just not folded
  into this weekend's push. Low priority — both copies are small, stable,
  and already independently live-verified.

- **`web3_jobs` source genuinely exhausted, confirmed not inferred --
  CoinCraft and Alpha Edge Crypto have received nothing since Sept 10.**
  Both channels feed only from `web3_jobs`; it hasn't yielded a new
  candidate in 6 days despite fetching 55 listings every cycle. Checked
  directly whether this is the relevance gate being too strict or the
  source being dry: fetched RemoteOK's live crypto-tagged feed and
  compared external_ids against what's already stored -- byte-for-byte
  identical set of 13, every single cycle. Zero new postings on this feed
  in 6 real days; the gate is correctly passing the same 13 real listings
  every time, not rejecting anything new. Two of five channels have been
  structurally dead since before this build session started, not because
  of anything built this week.

  Real options researched, not guessed:
  - **Web3.career API** — free tier, real account signup required (token
    auth). Live-checked the actual ToS, not just the marketing page: it
    *requires* displaying `apply_url` as a live, followed hyperlink back
    to web3.career (`rel="follow"` or no rel attribute — `rel="nofollow"`
    or omitting the link risks API suspension) and prohibits modifying
    that URL. This directly conflicts with this system's existing "no
    hyperlinks anywhere" design decision (operator direction, 2026-09-10,
    applied to every category since). Adopting this source means either
    breaking their ToS (real suspension risk) or carving out a link-
    exception specifically for this one category/source — a real,
    load-bearing design question, not a minor detail.
  - **Reconsider `airdrops`** — spec-mentioned, paused 2026-09-10 with no
    source research ever done (unlike `grants`, which has a full
    live-verified reasoning trail). Genuinely unresearched; would need
    the same live-source-verification pass `grants` got before it's a
    real option, not a decision that can be made today.
  - **Route an existing live category to these two channels** — cheapest,
    fastest (defi_yields/whale_movements/gems_security/news are all
    already flowing well and over their own channels' caps). Real cost:
    Alpha Edge Crypto and CoinCraft are spec'd as jobs channels
    specifically; routing general crypto content there changes what
    subscribers signed up for, not just what feeds it.

  Deliberately not implemented — pending operator direction on which
  option to pursue (or whether to reconsider what these two channels are
  for). Explicit operator instruction: fix sourcing properly, don't lower
  the relevance gate to manufacture volume from a genuinely dry source.

## Channel routing (2026-09-20)

- **Rebalance: `crypto_notebook` removed, `gems_security` and `defi_yields`
  reassigned (first mapping below was SWAPPED the same day -- see the next
  entry for the final one), `web3_jobs` stays shared via alternation.**
  Operator direction: Crypto Notebook is hand-written from now on, so nothing
  in this pipeline feeds it; one category each (not both shared) so each
  channel keeps a distinct identity -- they're priced separately for ads.
  `scripts/seed_config.py` was upsert-only, so deleting a channel from the
  YAML would have done nothing to the DB (row and routing stayed live -- the
  same silent-no-op shape as the channel-cap and token-log bugs); it now
  prunes DB channels absent from the YAML. No FKs point at `channel_config`
  and old notifications/approvals/previews store `channel` as plain text, so
  pruning orphans nothing (old cards fall back to the raw slug in labels).

  Checked, not assumed: (1) alternation is unaffected -- it only ever touches
  `web3_jobs`, is driven by a hardcoded list in `pipeline/channel_router.py`
  (not derived from `channel_config`), and that list still matches the DB
  exactly; a channel going from one category to two, or two to one, never
  enters that code path. (2) `defi_yields`' prompt_notes hardcoded "This
  channel is region_profile 'us'" -- false after the move (CoinCraft is
  'default'), and `build_defi_yields_prompt`'s per-region US note stops
  firing too. Reworded to keep the conservative never-recommend-deposits
  rule unconditionally (stricter than before) without the false claim.
  (3) Soft caps: category caps untouched (defi 6, gems 4); channel cap 12 on
  both now exceeds each channel's live category sum, so it's non-binding
  until web3_jobs revives (Alpha: 4+8=12; CoinCraft: 6+8=14).

  **Volume asymmetry, the real consequence of the mapping:** `defi_yields`
  clears threshold ~7/day (226 unheld in 4 days, 29 >=50) -- CoinCraft gets
  6 cards on the very next cycle. `gems_security` is rare by design: 27 rows
  in 6 days, and since the scoring fix none of the unheld ones cleared 50
  (latest 21-48). Alpha Edge will receive ~0-1/day, effectively still quiet;
  6 held gems rows scoring 52-61 exist as backlog to release manually.
  Leftover: 46 pending approvals and 8 pending previews still reference
  `crypto_notebook` -- not touched, still tappable.

- **Mapping swapped same day: `defi_yields` -> Alpha Edge Crypto,
  `gems_security` -> CoinCraft (final).** The volume asymmetry flagged in
  the entry above was the deciding fact: Alpha Edge is the highest-priced
  crypto channel (~13k subs, mostly US) and needs steady volume for
  advertisers; putting the rare-by-design feed (`gems_security`, ~0-1/day)
  there was backwards. `defi_yields` clears threshold ~7/day against a
  category cap of 6 (trims ~1/day -- raise it if the channel should run
  closer to its 12 cap); `gems_security` on CoinCraft is deliberately a
  low-volume feed on the channel that doesn't depend on steady volume.
  `defi_yields`' `region_profile` note is back on a 'us' channel, so
  `build_defi_yields_prompt`'s per-region US note fires again alongside the
  now-unconditional conservative rule in `prompt_notes`.

  **The 46 pending approvals and 8 pending previews left on the old
  `crypto_notebook` were cleared, deliberately NOT as 'rejected':**
  `approvals.decision` is what `scripts/calibration_report.py` reads to
  calibrate scoring, and 46 fake rejections of never-judged items would have
  skewed exactly the reject-rate it exists to measure. Set to a new
  `'expired'` decision instead (excluded from calibration's DECIDED set; the
  poller only acts on 'pending'/'approved', so nothing else is affected;
  schema comment updated). Previews -> the existing `'cancelled'`. Update
  counts matched exactly (46/8) inside one transaction that asserted them
  before committing. Their raw_items stay marked notified, so they won't
  resurface on another channel.

  **Found while rewriting spec Section 2 -- a live bug, not a doc issue:
  `approval-poll.yml` was still on a `*/15` schedule, six days after the
  poller moved to Railway.** Telegram allows one active `getUpdates`
  long-poll per bot, so every scheduled Actions run competed with the
  Railway poller for the same update queue: 20/20 recent scheduled runs
  failed with `409 Conflict`, and each run that opened a long-poll could
  knock the Railway poller into its 3-attempt retry and 15s backoff (a tap
  landing in that window at risk of being delayed or lost). The operator had
  asked for this to be disabled once the Railway poller was confirmed; that
  never happened -- one more config that looked done and wasn't. Schedule
  commented out (workflow_dispatch kept), same as `collect.yml`. Spec
  Sections 2, 4.1, 9, 12, 13 rewritten for the Railway reality; 4.1's
  "repo must stay public for cron" replaced with the measured
  Actions-scheduler gaps that justified the move.

## Documentation

- **Update the technical spec doc** to reflect two implemented decisions that
  diverge from its original text:
    1. **No direct channel publishing.** Section 11 ("Publishing") originally
       specified the bot calling Telegram's `sendMessage` straight to each
       channel's `chat_id`. As of 2026-09-10 (operator direction) this was
       removed entirely — the bot never posts to a channel itself. The
       operator receives the final labeled text and forwards/copies it
       manually; "Publish/Cancel" became "Mark as sent/Discard", which only
       logs to `posts` for history/dedup. Sections 9 and 11 both assume
       auto-publish and should be corrected or annotated.
    2. **Label header on every delivered post.** Not explicitly speced —
       Section 10 covers the *notification* label (the digest eyebrow line)
       but not a label on the final written post itself. Since the operator
       now manually forwards content, every final post leads with
       `<category label> → <channel display name>` (e.g.
       "🌾 DEFI YIELDS → Crypto Notebook") so it's unambiguous which channel
       it's for. Worth folding into Section 8 or a new subsection, since it's
       now a real, load-bearing part of the operator workflow, not a nice-to-have.
  The spec doc itself lives outside this repo (per the README's note to
  Claude Code) — this entry is the pointer so it isn't forgotten.
