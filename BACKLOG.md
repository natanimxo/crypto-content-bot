# Backlog

Deliberately-deferred items — noticed and worth tracking, not yet implemented.
Now that this repo is pushed (github.com/natanimxo/crypto-content-bot), these
could move to GitHub Issues instead; until that switch is made, this file is
the one place to check.

## READ FIRST -- traps that look like bugs and are not

- **DeepSeek's account balance lags the calls by MINUTES and reports whole cents.** Compare `fetch_deepseek_balance_usd()`
  against `llm_usage` only after waiting several minutes and only over a spend of ~$0.05 or more. Checked 2026-11-15: 20
  seconds after a $0.09 burst the balance showed a $0.05 drop, which reads as "the estimator over-counts 2x"; minutes later it
  had settled at $0.09 and agreed with the estimate (rates also match DeepSeek's published pricing). There is no 2x
  estimator bug -- do not re-discover it. Full trail: "LLM cost estimator verified" near the end of this file.
- **The daily LLM spend alert counts TAGGED dev/test calls** (they are real money). A heavy build/test day trips it once;
  the message separates production from tagged calls and says when it is almost all test traffic. "N production calls"
  in it is NOT the number of calls that produced the dollar figure.
- **exchange_listings "no earlier listing" is a statement about a SHORT record** (KuCoin's API reaches back to 2026-03;
  the others 2024-09 to 2025-03). Posts must say "since <month year>" beside it; never "first anywhere". See Phase 4 follow-ups.

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

## Delivery pacing and freshness (2026-09-22)

- **Per-cycle pacing -- one cycle could spend a whole day's cap in one burst,
  then go silent.** Operator's diagnosis was exactly right and confirmed by
  replay of 10 days of real news arrivals (cap=10/day, 6 cycles/day, no age
  bonus): 30 of 60 cycles sent nothing, the other half sent up to 5 at once.
  Fixed: a per-cycle cap (`pipeline/select_candidates.py`, derived from
  `CYCLES_PER_DAY`) bounds a single
  cycle to `ceil(soft_daily_cap / CYCLES_PER_DAY)` of the REMAINING rolling-
  24h headroom -- the 24h count is still the real ceiling, this only spreads
  it across the day instead of letting one cycle claim it all. Same replay
  with the fix: 1 of 60 cycles empty, max 2/cycle, matching the "1-3 per
  cycle" the operator asked for. `CYCLES_PER_DAY` is derived from
  `CYCLE_HOURS = 4.0` (the real Railway cron cadence), not hardcoded per
  category.

- **Freshness for time-sensitive categories -- FIXED.** Real incident: "The
  Fed is expected to raise interest rates for the first time in 3 years" --
  covering a decision made 2026-09-16 -- was delivered 2026-09-22, six days
  later, carrying the full +12 age bonus (which exists to stop starvation, not
  to reward staleness). For `news`, `macro_news`, `whale_movements`: (1) the
  age bonus no longer applies at all (`NO_AGE_BONUS_CATEGORIES`) -- ranking is
  real score only; (2) a hard eligibility cutoff, keyed on the item's OWN
  timestamp (`published_at` for news/macro, the transaction's own block
  `timestamp` for whale -- NOT `collected_at`, which only says when our
  pipeline saw it and is exactly what let a 6-day-old story look "fresh"),
  drops a candidate once it's older than `FRESHNESS_MAX_AGE_HOURS` (48h news/
  macro, 24h whale -- matched to each collector's own existing MAX_AGE_HOURS/
  WHALE_MAX_AGE_HOURS collect-time cutoff, not new numbers). Confirmed against
  the real Fed rows (230305, 230308): both now excluded, 166.8h and 168.6h
  old at test time. Age bonus and starvation-fix reasoning is kept as-is for
  the evergreen categories (`startup_jobs`, `web3_jobs`, `tool_launches`,
  `defi_yields`) -- a stale job posting or yield pool is still just as good as
  a fresh one. `gems_security` wasn't named by the operator either way; left
  in the age-bonus group on the reasoning that a real honeypot/rug-risk
  finding stays just as true days later (unlike a news event, which is
  superseded) -- worth a direct confirm if that reasoning is wrong.

- **BBC "Watch:" video posts -- filtered, same shape as tool_launches' HN
  [video] filter.** Real example: "Watch: Why has the Federal Reserve raised
  interest rates?" -- a no-links text post that only names a video, not a
  story of its own. `collectors/macro_news.py::_is_excluded` now also
  rejects a leading `Watch:`/`Listen:`/`In pictures:`/`Video:` prefix.
  Prefix-only match, deliberately -- a story that mentions a video partway
  through its own headline is still real.

- **Macro subject cap letting through near-duplicates via filler-word leaks
  -- fixed.** Three separate interest-rate stories got three different
  `lead_subject` keys instead of colliding: "Watch: Why has the Federal
  Reserve..." extracted `watch` (a video-prefix, not a subject -- now also
  caught at collection by the fix above, but `lead_subject`'s own skip list
  is a second, independent layer); "What's happening to UK interest
  rates..." extracted `what's` verbatim (only bare `what` was in the skip
  list, so the possessive form slipped through as its own key). Fixed in
  `pipeline/entities.py::lead_subject`: strips a trailing `'s` before the
  stopword check (also improves general precision -- "Trump's Fed pick..."
  now collides with a plain "Trump ..." headline instead of splitting into
  two keys), and the skip list gained `watch`/`listen`/`video`/`photos`/
  `podcast`. **Not fixed, and out of scope of what was reported:** BBC's
  sentence-case style capitalizes the true first word of a headline
  regardless of whether it's a proper noun -- "Interest rates hold
  expected..." still extracts `interest`, a real subject-key collision this
  mechanism can't distinguish from a genuine "Interest [rate policy]" story
  without a stopword list that would have to guess every common headline-
  opener. Left as a known limitation of the "first capitalized word"
  heuristic, not a filler-word gap; worth a proper-noun-aware approach if it
  keeps showing up.

## Railway: Config-as-Code -> Infrastructure-as-Code (2026-11-14)

- **Done ahead of Railway's 2026-12-01 cutoff for `railway.json`.** Both
  services are now declared in `.railway/railway.ts` and `railway.json` is
  deleted. `railway config plan` reports "already up to date" (file == live),
  verified after applying.
- **Why not just `railway config migrate`:** its dry run silently DROPS
  `restartPolicyType: ALWAYS` (emits only `start` + `replicas`), and it knows
  nothing about `collect-cycle`, whose start command and cron were set through
  the API and have no file. And `railway config pull` (the live state) can't see
  `railway.json` either: live service-level settings for the poller were
  `startCommand: null` and `restartPolicyType: ON_FAILURE` -- its real start
  command and `ALWAYS` came ONLY from the file. Applied the pull as-is, or let
  the file lapse on Dec 1, and the poller would have had no start command (the
  same "No start command detected" failure as September) and would not restart
  after a clean exit. `pull` also emitted a file that doesn't compile (the GitHub
  source and the poller service share one identifier), fixed by hand; the
  poller's start/restart were added by hand. The authoritative result is what
  Railway reports now: poller `start='python scripts/poller_daemon.py'`
  `restart=ALWAYS`; collect-cycle `start='python scripts/collect_cycle.py'`
  `restart=NEVER` `cron='0 */4 * * *'`; `railwayConfigFile` null on both (nothing
  was bound to the file, so deleting it is safe).
- **Procedure that worked (keep for next time):** `config pull` (no
  `--include-variables`: secrets stay `preserve()`) -> fix the file -> `config
  plan` (expected exactly 1 change: the poller's two settings; 0 add / 0 destroy;
  collect-cycle no diff) -> `config plan --out` + `config apply --plan --yes` ->
  `plan` again -> confirm the poller's heartbeat. The apply redeployed the
  poller only (collect-cycle untouched); polling resumed after a ~74s gap, all
  polls successful, with the usual transient 409 while the old instance
  finished its long-poll.
- **Windows gotcha:** `railway config plan/apply` need the SDK installed
  (`npm install railway --no-save --no-package-lock` in the repo root;
  `node_modules/` is gitignored and there is deliberately NO `package.json` --
  Railpack would detect Node on a Python service). The SDK checks the CLI
  version by running `$_ --version`, which throws a misleading "requires Railway
  CLI 5.42.1 or newer" on Windows because the npm `railway` shim isn't
  executable by Node. Fix: run with `_` set to the native binary, e.g.
  `env _="C:/Users/hp/AppData/Roaming/npm/node_modules/@railway/cli/bin/railway.exe"
  railway config plan`. (The CLI itself is 5.56.0; it is not out of date.)
- **Not covered by this file:** variables beyond the six `preserve()`d ones
  (the optional `LLM_*` alert thresholds are unset and use code defaults).
  Anything added in the Railway dashboard that isn't in `railway.ts` shows up
  as drift in the next `config plan`.

## gems_security "broken, not quiet" + hacks_exploits inserted=0 (2026-11-14)

- **`gems_security`: both halves of the operator's diagnosis were right, but
  they were two different problems, and only one of them was why nothing
  came out.** Measured by replaying the collector against live data (local
  replay on the same DB/cache; I had no Railway log access -- the CLI session
  had expired).
  **(1) GoPlus responses were being thrown away (fixed).** Of the 28 calls the
  collector spent its budget on in one cycle, **28 were discarded**: 24 were
  `code=3, message="OK"` WITH data present (15-16 fields) on Base tokens, 4
  were `code=5000 system error` on ids like `coingecko:usd-coin`. Three
  defects in `pipeline/goplus.py`: only `code == 1` was accepted (GoPlus's docs
  define code 2 as "partial data... request again in ~15 seconds"; code 3 is
  undocumented but behaves the same -- now both are used when data is present,
  tagged `_goplus_code`, cached 1 day not 14, missing fields still read as
  UNKNOWN, never clean); non-address ids were sent to the API (can only ever
  return 5000 -- now short-circuited to "unchecked", no call); and a failure
  was never remembered, so one token was re-requested once per pool containing
  it, AND the collector charged the 60-call budget per pool occurrence. A
  single Base token consumed 24 of those 28 calls. Real collector run after the
  fix: 13 distinct calls (was 60), 0 pools deferred for budget (was ~100), 13/13
  responses usable. Only 16 distinct tokens (~0.5%) were ever affected -- the
  cache already covered the rest -- but they monopolised the budget, so the
  sweep never made progress and the failure was invisible.
  **(2) That fix alone changes NOTHING (measured, not assumed):** the 12
  never-cached tokens all came back with data and none is red-flagged; replaying
  the full decision before and after gives 175 flagged pools, 167 removed by
  the dominance filter, 8 survivors, all 8 already delivered -> **0 new**
  either way. `red_flagged: 9` in the run log is the count AFTER the dominance
  filter, which is why 175 flagged pools looked like 9.
  **(3) The actual cause of inserted=0: the dominance filter's `project:`
  key.** 155 of the 167 exclusions tripped ONLY that key (no repeated token, no
  Pendle-style template) and carried **149 distinct flagged tokens**;
  `uniswap-v2` lost 90 pools, `uniswap-v4` 27, `uniswap-v3` 16. The key was
  built for protocol product families (Pendle), but an open-listing DEX is a
  venue, not a product, and it's exactly where small-cap gems live (15 of the
  removed pools carried a simulated honeypot/cannot-sell flag). **Fixed
  (`PROJECT_REPEAT_RATIO = 0.5`, `collectors/gems_security.py`):** a project key
  only counts when its flagged pools REPEAT their tokens (distinct flagged
  addresses <= 0.5 x pools); address and template keys unchanged. Effect on
  live data: survivors 8 -> 163, **141 new** (90 uniswap-v2, 27 v4, 14 v3, ...;
  101 Ethereum / 28 Base; median TVL $181k; 13 with a simulated-behaviour
  flag). **This is a policy change, flagged as one:** the category stops being
  "rare by design" -- the earlier "~0.5/week" figure in this file was largely
  the filter, not the market. Delivery stays bounded by `soft_daily_cap` 4/day
  and per-cycle pacing (1/cycle), so the ~140-row backlog drains over ~5
  weeks, best-scored first; many flags are blunt contract facts
  (`creator_percent`, `is_open_source`), so expect noise and judge it on real
  cards. Revert: set `PROJECT_REPEAT_RATIO = 0.0`.
- **Unmapped chains (1,754 deferred pools), separately.** Not an error -- a
  coverage gap. GoPlus's `/supported_chains` now lists 46 chains; the Sept note
  that Monad wasn't supported is stale. Added, each verified live with a real
  pool token (code=1 + data): Monad 143, Plasma 9745, Robinhood Chain 4663,
  Mantle 5000, Sonic 146, Linea 59144, Berachain 80094 (~265 pools; unmapped
  1,754 -> 1,490). NOT added: Scroll/Unichain (supported, but the one token
  tried on each returned no data -- insufficient evidence); Hyperliquid L1
  (127 pools), Flare, Katana (unsupported by GoPlus). **Solana is the real gap
  (738 pools, $4.5B TVL):** it has a separate GoPlus endpoint with a different
  response schema -- a feature, not a mapping fix; not done.
- **Visibility:** `collect_gems_security` run_logs now record
  `details.goplus` (ok / partial / unavailable / non_address_skipped, with the
  code), and an ERROR is logged when >50% of >=10 real calls return nothing
  usable, so a degraded GoPlus reads as a number instead of "quiet".
- **`hacks_exploits`: inserted=0 is correct -- and not the freshness window.**
  The collector has no freshness filter (that's in `select_candidates`); it
  re-fetches all ~770 incidents >= $500k each cycle and inserts only new
  `name:date` ids. Checked against the live feed: **0 qualifying incidents
  missing from the DB**; newest is 2026-11-10, ingested 11-11; recent inserts
  are 3-8/week, so consecutive zero cycles are the normal quiet state. Added
  `already_stored` to the run details and log so it reads that way. Latent,
  harmless: 3 pre-2025 incidents share a `name:date` id with another (Gamma,
  OcelotDex, Merlin) so the second was dropped on insert; not changed
  (re-keying would re-insert the whole history).

## Silent write failures (2026-10-06)

- **Approve "not working" on `hacks_exploits` -- root cause was my launch
  config, NOT the speculation guard; the real defect was that a failed write
  was silent and retried forever.** Diagnosis, from the DB and Railway logs:
  3 hacks approvals (Bitget 451, Astroport 462, Payy 499) sat at `approved`
  with no preview; every poll cycle the recovery sweep retried them and logged
  `RuntimeError: ANTHROPIC_API_KEY is not set`. I had launched the category
  with `write_benchmark_status: trial` (2026-09-30) -- trial mode writes BOTH a
  DeepSeek and a Sonnet draft, and Railway has no Anthropic key (HANDOFF.md
  even says it's only needed for trial). So DeepSeek succeeded, Sonnet raised,
  nothing was stored, repeat. 19,072 failed retries over 6 days -- each one a
  paid DeepSeek call -- and the operator was told nothing. Every other category
  is `settled_deepseek`, which is why only this one broke. Fixed: set
  `settled_deepseek` (YAML + DB); the live poller then self-recovered all 3
  previews within ~1 minute. A missing-key trial misconfig is exactly what a
  launch check should have caught; I didn't check the env before choosing
  `trial`.

  **The guard was cleared with real drafts, not assumed:** 18 first-attempt
  DeepSeek drafts (6 real incidents x 3, incl. Limit Break) -- `check_no_
  speculation` fired 0 times, and none contained hedge words like "reportedly"
  or "appears to". Worth knowing: those two phrases are NOT in its pattern
  list, so it wouldn't catch them either; the list targets "allegedly",
  "apparently", "suspected of", "probably an inside job", and attacker
  motive/identity claims. The false-positive risk flagged on the neutrality
  guard remains theoretical here, not observed.

  **Loop fixed, and failures are now loud** (`bot/approval_poller.py`,
  `approvals.write_attempts/write_last_attempt_at/write_failed_at/
  write_last_error`): retries are spaced >=120s and capped at 3; a config
  error ("... is not set") can't fix itself, so it fails permanently on the
  FIRST attempt. On permanent failure the operator gets a Telegram message
  naming the item, category, attempt count and the redacted error, with
  **Retry write** and **Reject** buttons. `write_failed_at` is only set after
  that message actually sends, so a Telegram hiccup re-alerts instead of
  going quiet. The same `_attempt_write` handles both a fresh tap and the
  sweep so the two can't diverge. Tested on throwaway rows: alert fires
  exactly at attempt 3 (not before), the 120s gap holds, a failed approval
  leaves the sweep for good, Retry resets state, success path unchanged.

  **Not resolved: the Limit Break tap.** Approval 505 is still `pending` --
  no Approve was ever recorded for it, and the tap path itself works (the
  pending lookup returns it cleanly). Railway doesn't retain logs from the
  tap time, so whether it reached the poller can't be proven. It's possible
  the taps that "did nothing" were on the other three hacks cards. Re-tap it
  to confirm.

## LLM spend: reconciliation and cost guard (2026-10-06)

- **DeepSeek balance $1.97 -> $0.07 in a week: the retry loop accounts for
  all of it.** Nothing recorded token usage (the response's `usage` block was
  discarded), so this was reconstructed from measured per-call numbers rather
  than read from a log. Measured on the 3 stuck incidents' real prompts, 2
  calls each: ~535 prompt tokens (384 served from DeepSeek's prefix cache,
  ~150 miss), ~127 completion, 0 reasoning tokens. Model id on Railway is the
  default `deepseek-chat` (`DEEPSEEK_MODEL_ID` unset). At current published
  Flash rates ($0.003 / $0.15 per 1M cached / uncached input, $0.60 output
  off-peak; peak is 2x) that is ~$0.0001 per retry. Run-log timestamps give
  19,078 retries since the first hacks approval; all fell off-peak (Oct 1-7 is
  a Chinese public holiday, which DeepSeek bills off-peak, and the rest missed
  the 01-04/06-10 UTC weekday peak windows) -> **$1.91** (range $1.91-$2.98
  depending on cache-hit assumptions; the measured hit rate sits at the low
  end). **Legitimate writes in the same window: 37 successful writes across 8
  categories, ~$0.006.** All 9 categories use `triage_model: template`, so
  triage costs nothing; every LLM call goes through `llm.generate_write`.
  $1.91 + $0.006 vs the observed ~$1.90 -- nothing else is spending. Caveats
  stated plainly: the legit figure is estimated from rebuilt prompts (not
  logged usage), excludes any guard-retry calls (also unlogged), and ~24
  calls of mine today (~$0.003) came after the window. Even a 10x error on the
  legit side is ~$0.06.

- **Cost guard built** (`pipeline/llm_usage.py`, tables `llm_usage` and
  `llm_spend_alerts`): every LLM call is logged with tokens and an estimated
  cost, including FAILED calls. Telegram alerts, once per UTC day per kind:
  `spend` (default $0.10/day), `calls` (default 150/day -- price-independent,
  so it still catches a runaway if rates change or usage is missing), and
  `balance` (DeepSeek's own `/user/balance`, default floor $0.50; checked once
  per collect cycle). Defaults sit ~100x above a normal day and below the
  incident's rate (~$0.32/day, ~3,200 calls/day). Override with
  `LLM_DAILY_SPEND_ALERT_USD`, `LLM_DAILY_CALLS_ALERT`, `LLM_BALANCE_ALERT_USD`
  on the Railway service. The alert slot is claimed before sending and
  released if the send fails, so an alert is neither spammed nor lost; metering
  failures are logged and never block a real write. Verified end to end: a
  real call recorded 15 prompt / 5 completion tokens, and the real balance
  alert reached Telegram. Estimate caveats: peak pricing is modelled, Chinese
  holidays are not (slight over-estimate, the safe direction); the Anthropic
  rate is an unverified placeholder (provider unused).
  **Superseded in part, same day (see the next entry): the daily guards are
  now a backstop only.** A once-a-day alert would have fired roughly a day
  into the incident (~$0.32/day against a $0.10 threshold) and then gone quiet
  while it kept running.
  **Alert only, deliberately -- no circuit breaker.** Refusing LLM calls over
  a threshold could block a legitimate burst of approvals; the retry cap
  (see "Silent write failures") already bounds the specific loop that caused
  this. Revisit if an alert is ever ignored.

## Removed: Anthropic provider and the A/B "benchmark trial" mode (2026-10-07)

- **Operator direction: never using Anthropic, never bought credit, every
  category is `settled_deepseek` -- clean it out so the 2026-10-06 failure
  can't recur in that form.** Removed: `llm_providers/anthropic.py`;
  `category_config.write_benchmark_status` (column, YAML, `seed_config.py`);
  `generate_post_variants()` and the dual-generate/Version A-B preview path in
  `bot/approval_poller.py`; the `write_benchmark` table; `post_previews.
  variant_b_model/variant_b_text/content_b_message_id/chosen_variant`; the
  `ANTHROPIC_API_KEY` env/docs/workflow references; the Anthropic cost branch
  in `pipeline/llm_usage.py`. Kept: the provider abstraction
  (`llm.generate_write`/`generate_triage`, `PROVIDERS`, `llm_providers/` with
  deepseek, gemini, template) so a new provider is still one module plus one
  registry line; `model_override` on `generate_write`; the gemini provider
  (unused, but it's the second example of the abstraction -- say if you want
  it gone too).
- **Verified nothing depended on any of it before dropping (live DB):**
  `write_benchmark` had 0 rows; 0 of 111 `post_previews` ever had a variant B
  or a `content_b_message_id`; all 9 categories were `settled_deepseek`; no
  DB view, foreign key, trigger or constraint referenced the table or the
  columns; no script, calibration report or other code read them (grepped
  repo-wide -- the only readers were `approval_poller.py`, `write_post.py`,
  `llm.py` and `seed_config.py`, all updated). The trial mode was never used.
- **Deploy order mattered and was followed:** the new code is compatible with
  both the old and new schema (it simply stops reading/writing those
  columns), so it was pushed and confirmed live on Railway FIRST, and the
  `DROP`s (end of `db/schema.sql`, idempotent) ran only afterwards -- dropping
  first would have crashed the live poller on `cfg["write_benchmark_status"]`
  and its `post_previews` INSERT.
- **Deliberately left:** `post_previews.variant_a_model/variant_a_text` keep
  their names (renaming would touch the live poller and publish path for no
  functional gain; the schema comment explains the leftover "_a"), and the
  `mark_sent_a` callback name (already-sent preview buttons in Telegram carry
  it). A stale `mark_sent_b` tap now just answers "Unknown action." Tested.

## LLM anomaly alerts, near-real-time (2026-10-07)

- **Rate and repeated-prompt guards that re-alert while the problem
  persists** (`pipeline/llm_usage.py::_check_anomaly`, table
  `llm_anomaly_alerts`), evaluated on every LLM call. Operator's point: the
  retry loop burned ~$0.32 and ~3,200 calls a day, and a once-daily alert
  fires a day in then goes quiet. (1) **rate:** >= `LLM_RATE_ALERT_CALLS`
  calls in the last `LLM_RATE_WINDOW_MINUTES`. (2) **repeat:** the same
  prompt sent >= `LLM_REPEAT_ALERT_COUNT` times in `LLM_REPEAT_WINDOW_MINUTES`.
  (3) Both **re-alert every `LLM_REALERT_MINUTES` while still true**, per
  (kind, key) -- an atomic claim, restored if the Telegram send fails so an
  alert is never lost. (4) The daily spend/call-count and balance alerts are
  unchanged, as a backstop.
- **Why "repeat" keys on the prompt, not on failures.** The operator asked
  for "the same approval or item fails repeatedly". But every one of the real
  loop's ~19,000 calls SUCCEEDED at DeepSeek -- the failure came afterwards,
  in the approval poller, which `llm_usage` never sees -- so a failed-call
  counter would have caught nothing. What the loop DID have, observably, was an
  identical prompt re-sent every minute. A prompt fingerprint
  (`llm_usage.prompt_hash`, sha1[:12]) catches that whichever caller is
  looping, independent of volume. The approval-level cap/alert
  (`write_attempts`, "Silent write failures" above) remains the first line for
  that specific path; this is the independent second one.
- **Defaults, derived from real usage, not guessed.** All 111 successful
  writes ever (2026-09-08 to 10-05, ~4 weeks) -- the busiest 15-, 30- and
  60-minute windows each held at most **7** writes, and the busiest day 8.
  Defaults: rate **20 calls / 15 min** (~3x the worst burst ever seen),
  repeat **5 identical prompts / 30 min** (the poller's own capped retry sends
  a prompt at most 3x, plus room for one manual Retry tap, so legitimate
  behaviour never trips it), re-alert **60 min**. Env vars:
  `LLM_RATE_WINDOW_MINUTES`, `LLM_RATE_ALERT_CALLS`,
  `LLM_REPEAT_WINDOW_MINUTES`, `LLM_REPEAT_ALERT_COUNT`,
  `LLM_REALERT_MINUTES`.
- **Verified by replaying the actual incident**, not just boundary tests:
  3 stuck approvals re-running one call per minute each (the real shape),
  with an injectable clock. Also: 7 distinct writes in 15 min -> no alert; a
  capped retry (same prompt x3) -> no alert; 19 calls -> none, the 20th ->
  alert; 4 identical prompts -> none, the 5th -> alert; loop stops -> alerts
  stop; Telegram down -> not lost, delivered on retry. **Replay result: the repeat alert fired at
  minute 4 and the rate alert at minute 6, then both re-alerted at exactly +60
  and +120 minutes (12 alerts over 3 hours: 9 repeat across the 3 prompts, 3
  rate) and stopped when the calls stopped.** For comparison, the existing
  daily guards on the same loop: the call-count backstop (150/day) would have
  fired ~50 min in, the spend backstop ($0.10/day) ~7.5 h in -- and each only
  ONCE, then silence until the next UTC day, which is the gap the operator
  named. Bug found by the first test run and fixed before shipping:
  `make_interval(mins => <float>)` doesn't exist (needs an integer); the
  cooldown now uses seconds.
- **Known limitation, stated plainly:** these guards only run when an LLM
  call happens (that's where the data is), plus once per collect cycle for
  the daily/balance checks. A runaway that fails BEFORE reaching the LLM
  (e.g. crashing early in the poller) makes no calls and spends nothing, so
  it won't trip them -- that case is the approval-level failure alert's job.

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

## Channel routing (2026-09-30)

- **News shared crypto_wall_street <-> alpha_edge_crypto via alternation;
  new `hacks_exploits` category added to coincraft; `defi_yields` stays
  alpha_edge_crypto-only (operator direction: "highest-priced channel,
  steady volume is what I'm selling on it").** Real headroom check before
  building anything, live-queried 2026-09-30: news clears review_threshold
  ~35/day against a single channel's 10/day cap; single-channel sent was
  already averaging only 8/day (some days as low as 4), well under even that
  one cap -- volume was being generated and thrown away, not a thin source.
  Splitting defi_yields with coincraft was ALSO evaluated (replay of 21 real
  days: each side would land ~4.6-4.8/day vs today's single-channel 5.9,
  combined output nearly doubling) -- genuinely viable, but rejected per
  operator direction rather than on the numbers; `hacks_exploits` fills
  coincraft's gap instead, at zero cost to Alpha Edge.

  Cross-channel duplicate suppression needed NO new code, verified live
  rather than assumed: `_recently_notified_stories`/`_topics_in_cooldown`
  (pipeline/select_candidates.py) already query notifications with no
  `channel` filter at all, so once EITHER channel sends a story, the other's
  very next `get_new_candidates` call (same run() loop, same cycle) already
  sees it as notified and suppresses an exact-title repeat -- confirmed with
  a live synthetic test (two throwaway raw_items, identical title, opposite
  assigned_channel; the second channel correctly excluded the item once the
  first's notification was inserted; test rows deleted after). What this
  does NOT catch, same residual gap as within one channel: two outlets
  covering the same event under different headlines (weak fingerprint
  overlap only) -- operator explicitly accepted this cost ("both are crypto
  channels with overlapping subscribers, some people will see the same story
  twice... acceptable for the volume").

  **Real pre-existing bug found and fixed while wiring this, not
  introduced by it:** crypto_wall_street's channel-level soft_daily_cap (12)
  was ALREADY silently trimming news below its own 10/day category cap --
  live-queried: exactly 12 items/day on 9 of the last 14 days (news' 10 +
  whale_movements' realistic ~4-6/day -- its nominal 12 cap is never
  actually approached -- summed to 14-16, over the channel's own 12). Raised
  to 16 (news' cap 10 + whale's realistic ceiling, not its unused nominal
  one). alpha_edge_crypto's channel cap raised 12 -> 16 for the same reason,
  pre-emptively this time: defi_yields(6) + news(10) could reach 16, and
  leaving the channel cap at 12 would let it silently trim INTO defi_yields'
  volume some cycles depending on which category scored higher that cycle --
  exactly the "steady volume" guarantee the operator said not to touch.
  coincraft's channel cap (12) was left alone -- combined real demand there
  (gems_security ~0.5/week + hacks_exploits ~1.5-2.6/week + web3_jobs dry)
  stays well under it.

  **`hacks_exploits` source research, done live before proposing it, not
  assumed:** DefiLlama's `/raises` (funding rounds) API, evaluated first,
  returned HTTP 402 -- now paywalled, dead end. Snapshot's governance
  GraphQL API is free and live but nearly all noise without a hand-curated
  "DAOs that matter" watchlist (a `first: 5, orderBy: created` pull surfaced
  `legonft.eth` and `rocketstarfoundation.eth` alongside real DAOs) -- same
  curated-list-rot risk already rejected for `grants` (see that section),
  not built. `airdrops` re-checked live rather than left at "genuinely
  unresearched": still a dead end, confirmed today -- airdrops.io has no
  JSON API (WordPress site, scraping-only, same ToS/fragility problem as
  Product Hunt), CoinMarketCap's airdrop data is paid-tier only.
  DefiLlama's `/hacks` API survived the check: free, no auth, same
  already-trusted vendor as defi_yields, robots.txt open, one-shot fetch
  (no pagination), 1,290 historical incidents. Threshold chosen ($500k, over
  a $1M alternative): $500k -> ~0.72/day (90d window) / ~0.64/day (180d,
  matches review_threshold=50's real cadence below); $1M -> ~0.47/day --
  $500k keeps this from reading as barely-there combined with gems_security.

  **Scoring -- the operator's core ask ("a small exploit on a major protocol
  may matter more than a large one on something nobody uses") is a real,
  validated BLEND, not just documented as an intent:** `impact` =
  0.65*log_scale(amount) + 0.35*log_scale(protocol's current TVL, via
  DefiLlama's `/protocols`, joined by `defillamaId` -- confirmed live,
  522/538 hacks that carry an id resolve to a real tracked protocol).
  Real concrete pair from live data: a $1.34M hack on Raydium AMM ($1.3B TVL)
  scores 55.8 total and clears review_threshold=50 on prominence alone
  despite a modest dollar figure; a $320M hack on an untracked target
  (Liquid Network, no DefiLlama TVL data at all) scores 67.1 -- still ranks
  higher for the larger real damage, but nowhere near the ~240x gap raw
  dollar amount alone would produce, which is the tempering effect asked
  for. Untracked targets (538/1290 -- wallets, bridges, chains, gambling
  platforms DefiLlama doesn't track TVL for, not just obscure protocols;
  COLDCARD $116M and Liquid Network $320M are real examples) default
  prominence to a neutral 50, never coded as low or high -- a short
  "known major exchanges" list was considered and rejected: the untracked
  set is too varied for a name list to meaningfully help, same curated-list-
  rot shape already rejected four times.

  `review_threshold` kept at 50, the same convention every other category
  uses -- deliberately NOT tuned down to manufacture volume (operator's own
  2026-09-16 direction: "fix sourcing properly rather than lower a
  threshold"). Real combined cadence at 50: hacks_exploits ~1.5-2.6/week +
  gems_security's own real rate (~0.5/week, 1 incident in the last 14 days)
  = roughly 2-3/week for the whole channel's second slot -- thin, reported
  honestly as such rather than inflated.

  `soft_daily_cap: 4`, not an arbitrary "small" number -- sized to the worst
  real day found in 90 days of history (2026-09-24 had 4 qualifying
  incidents same day: Bitget, Duelbits, Limit Break, Payy Network).

  Certainty discipline (operator: "same as gems -- report what's confirmed,
  don't speculate on cause or blame"), enforced structurally, not just in
  the prompt: `pipeline/write_post.py`'s new `check_no_speculation` guard
  (same checked-write/retry/RuntimeError mechanism as gems_security's
  overclaim guard and macro_news's neutrality guard) bans hedge-words that
  smuggle in an unconfirmed claim ("allegedly", "apparently", "probably an
  inside job") and any claim about an attacker's identity or motive -- there
  is no such field anywhere in DefiLlama's data, so any such claim in a
  draft is invented, not reported. Classification/technique ARE allowed as
  fact, since those are DefiLlama's own categorization of what happened, not
  our speculation about why.

  Backlog bootstrap, real and worth knowing: the first `collectors.
  hacks_exploits.collect()` run inserted 744 historical rows (unheld, same
  "poller's healthy, don't hold by default" posture every category has used
  since 2026-09-15); 293 clear review_threshold, but the 14-day freshness
  cutoff (`FRESHNESS_MAX_AGE_HOURS['hacks_exploits']`) narrowed that to
  exactly 1 real candidate eligible to notify on day one (Bitget, $387M,
  score 90.3) -- confirms the freshness gate does its job against a bulk
  historical backfill, not just against a slow trickle. Similarly, adding
  `news` to `SHARED_CATEGORY_CHANNELS` doesn't retroactively split existing
  unassigned rows on its own -- `assign_channels_to_new_items` only assigns
  rows with `assigned_channel IS NULL`, which was every one of the 768
  existing news rows (alternation is new) -- so the very first post-change
  run assigns the ENTIRE historical backlog in one pass, same bootstrap
  mechanism web3_jobs went through originally, not a bug.

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

## Phase 0 close + Phase 1 parts 2-3: gems hold, volume cut, queue cleanup (2026-11-14)

- **Phase 0 verified** on the 12:00 UTC cycle: cron fired 12:01:43 on the IaC-managed
  service; gems_security `details.goplus = {"ok": 60}`, inserted 143 (predicted ~141).
  `PROJECT_REPEAT_RATIO = 0.5` stays (operator decision).
- **gems_security held pending the Crypto Notebook remap**: 159 unnotified rows held in the
  DB, and `collectors/gems_security.py` `HOLD_ALL_CANDIDATES = True` so new rows insert
  held. Held gems total 173 (30 pre-Nov-14 rows from Sep-Oct + 143 from Nov 14). One gems
  card reached CoinCraft at 16:05 UTC before the hold (2 gems cards pending there). Flip
  the flag back to False when the channel routing changes.
- **Volume cut, from calibration** (last 60d, per (notification, category) batch): news -- 71
  batches had an accept, exactly one accepted in 68, and it was the top-scored in 64;
  tool_launches 20/20 exactly one, top-scored 18; whale 8/8 (top 7), startup_jobs 5/5 (top 4)
  -- the last two are below the n>=10 bar and were NOT cut. defi_yields/macro_news/hacks are
  single-card batches, no pattern. Applied: news 10 -> 6/day, tool_launches 8 -> 6/day.
  News is a shared category (Wall Street / Alpha Edge) so its cap is now CATEGORY-WIDE:
  ceil(6/6) = 1 card per cycle across both channels, channels taking turns
  (`bot/notify.py::_order_channels_for_shared_caps`, `select_candidates.SHARED_CYCLE_WINDOW_HOURS`).
  This also applies to web3_jobs (the only other shared category; source is dry).
  Expect news to pause for a cycle or two while the trailing-24h count (8 at the time)
  drains below 6.
- **Queue cleanup** (one transaction, counts asserted): 1,078 stale pending approvals
  (news 831, whale 172, macro 59, hacks 16 -- past FRESHNESS_MAX_AGE_HOURS by the item's own
  timestamp) -> `decision='expired'`; orphaned macro_news approval 1414 (Q3 GDP, write failed
  3x on the verbatim-reproduction guard, 15 days old) -> `expired`; 7,730 held-never-released
  raw_items (defi_yields 7,264, tool_launches 177, news 149, startup_jobs 89, whale 29,
  macro 22) marked via new `raw_items.expired_at` -- NOT deleted, the rows are what stops the
  collectors re-inserting them. `get_new_candidates` and `hold_candidates.py release` skip
  expired rows. Gems excluded. Evergreen pending cards (defi_yields 244, tool_launches 161,
  startup_jobs 67, web3_jobs 12) have no freshness window and were left pending.

## Phase 1 part 1: channel-level voice (2026-11-14)

**What changed.** Voice moved from the category (`category_config.voice` / `prompt_notes`) to the
channel. `config/channel_voices.yaml` -> `channel_config.voice_profile` (JSONB, seeded by
`scripts/seed_config.py`); `pipeline/channel_voice.py` (profile, prompt, deterministic voice
checks); `pipeline/post_facts.py` (per-category facts + voice-neutral content brief + guardrails,
in CODE on purpose: each guardrail is paired with a structural check, none is an operator knob);
`write_post.generate_post` takes the channel-voice path when the channel has a profile and the
category a FactSpec, else the legacy path (unchanged -- the 8 legacy prompt builders now share
the `facts_*` helpers; 56 sampled prompts across 9 categories x 2 regions were byte-identical
before/after). Profiles: crypto_wall_street (market desk, 1 paragraph, 200-350, no hashtags, no
context lines), alpha_edge_crypto (2 paragraphs, 350-500), crypto_notebook (setup/mechanic/spot-it,
600-900; NO channel_config row until the remap -- the seed applies it when the row exists),
hustle_to_million (second person, 300-450). coincraft has NO profile on purpose: legacy voice
until Phase 5 (earning-opportunities category). Length = whole post minus Source line and hashtags.
Guardrails beat voice: macro_news on Hustle answers "what to do" with "what it means for you".

**Found while building it (kept):**
- "Title must contain a figure" as a hard rule made the writer fail 3 drafts on a story with no
  number -- the only way to comply was to invent one. Now applies only when the facts contain a figure.
- The reproduction guard was calibrated on 400-700 char posts; at 200-350 chars the names and
  figures any accurate post must repeat dominated the ratio (Wall Street succeeded on 6/14 recent
  news items). Retry feedback now names the exact runs to reword, and (operator-approved) the guard
  measures EXPRESSION: entities are masked from draft and source before BOTH the 8-word-run check
  and the 0.30 overlap ratio (thresholds unchanged). Wall Street now 13/14.
  `scripts/check_reproduction_masking.py` against 60 real excerpts + 2 entity-dense synthetic
  sources: verbatim 62/62, half-lift 62/62, Title-Cased source 62/62 still flagged; facts-only and
  own-words paraphrase drafts pass; two entity-rich lifts (sentence copied with names interleaved)
  still flagged. KNOWN LIMITS, reported not hidden: an entity-dense source (e.g. 7 entities in 19
  words) with every name swapped for an invented one, or an invented word inserted every 4th
  word, can evade the masked guard (1-2 of 62; the old guard missed 3/62 swapped and 0/62
  sprinkled, so sprinkled is 1 worse on the pathological synthetic source and swapped is 1-2
  better). A real model copying lazily does neither.
- The first side-by-side opened both posts with the same sentence. Alpha Edge's first paragraph
  is now "the point" (plain-language meaning first, figures second); Wall Street leads on figures.
- The whale brief let the model invert the convention (an exchange withdrawal "read as
  distribution"). The brief now states it: withdrawal = accumulation/reduced sell pressure,
  deposit = possible sell pressure, stablecoin = no directional read. (The legacy whale prompt has
  the same loose instruction; it stops being used on Wall Street once the profile is live.)
- LLM call-rate alert tripped by dev testing: calls from deliberate scripts are now tagged
  (`llm_usage.tag_session`, `llm_usage.source`) and excluded from the rate / repeated-prompt /
  daily-call-count alerts, with a 10x ceiling of their own. Raising the threshold would have
  missed the real incident (~33 calls per 15 min).
- Cost of a Wall Street write: 1-3 DeepSeek calls (retries carry the specific issue).

**Wiring + first live integration run (2026-11-14, same day):** column + seed applied, code pushed
before the profiles were seeded; every profiled channel/category pair then run through the real
`generate_post` (DB profile) -- see `scripts/` history. Findings, all fixed:
- 3/8 first-run writes missed the UPPER length bound by 8-19 chars after retries. The prompt now
  aims at ~45% of the way up the range ("drafts that aim for the limit overshoot it") and a retry
  says how many characters to cut; max retries 2 -> 3. Measured across 41 + 18 items: ~92-95% first-
  pass-with-retries, the remainder length misses; a failed write surfaces as the usual "write failed"
  card (Retry / Reject).
- macro_news on Hustle: the channel's second paragraph is "what to do about it", which invites the
  advice macro_news's guardrail forbids -- the first generation wrote "plan for higher costs on any
  credit you rely on". Guardrail wins, applied two ways: (1) `FactSpec.role_overrides` replaces that
  paragraph's role for macro_news with "what it means for you / what to watch -- a statement about the
  world, never an instruction"; (2) `_find_reader_advice` checks the output (advisory modal aimed at the
  reader, or a clause opening with an advisory imperative; "Watch/Track/Note" allowed). Floor, not a
  guarantee -- advice phrased as a statement is invisible to it, and it flags attributed advice too
  (a false positive that just costs a retry). The channel's voice does feel slightly constrained on
  macro_news as a result; that is the intended trade.

## Phase 2: reliability fixes (2026-11-14)

**1. Collector isolation.** `scripts/collect_cycle.py` ran `for category: collect_fn()` bare, so one
exception ended the cycle -- 2026-11-03, a single Etherscan timeout in whale_movements killed the
seven collectors after it and notify. Now every collect, score and notify step runs under `guarded()`:
failure is logged with its traceback, recorded, and the cycle continues (notify still runs on what was
collected); a 25-minute SIGALRM ceiling per step turns a HANG into an isolated failure too (Linux/Railway
only -- not exercised on this Windows box). A `collect_cycle` run_logs row lists `failed_steps` (status
`partial_failure`), the process exits 1 at the very end, and -- found while doing this -- the
"3 consecutive failures" operator alert had only ever covered notify and the poller; it now runs for
every collector (and says "Railway logs", not "GitHub Actions"). Inside whale_movements each wallet is
isolated too (`address_errors`; the collector only fails if ALL wallets fail). Tested with a simulated
failing collector: the others, scoring, notify and the LLM checks all ran, exit 1.

**2. Whale impact weight.** Re-pulled the calibration, and first fixed a side effect of my own queue
cleanup: marking 1,078 stale cards `expired` had removed them as calibration negatives (whale showed 17
accepted / 0 negatives). New `approvals.expired_reason`; `stale_unactioned` rows count as IGNORED again
(`channel_retired`, `write_failed_stale`, `category_retired` stay excluded). With 17 accepted vs 172
never-actioned: impact AUC 0.36 (0.5 = none), novelty 0.70, actionability 0.64, credibility 0.50, total
0.72. Size is U-shaped against acceptance ($2-10M ~17%, $10-100M ~5%, $100M+ ~16%; median accepted $9.5M vs
$17.2M ignored) -- the middle is stablecoin mints and exchange-internal / institutional shuffles, so no
monotone rescale of impact fixes it. Weights 0.30/0.30/0.20/0.20 -> impact 0.15 / novelty 0.35 /
credibility 0.20 / actionability 0.30: total AUC 0.72 -> 0.75, all 17 accepted still clear 50, items
clearing 50 196 -> 188 of 905 (60d) -- reorders and trims ~19% of ignored-type cards, does not cut volume.
0.10 rejected (25% fewer clearing, 2 of 17 accepted lost). n=17: bootstrap intervals overlap; this stops
weighting a component with no demonstrated signal, it does not claim optimality. Re-check at ~40 accepted.

**3. Blockworks.** Not a moved URL: blockworks.co redirects to blockworks.com/feed (200, parses, 50
entries) but the newest is 311 days old -- frozen, and invisible to the collector because an all-stale feed
looks like a quiet one. Live-checked 11 candidates 2026-11-14. Replaced with **Unchained** (9 of 10 items in
48h were stories none of the other five feeds had, all pass the opinion gate, robots.txt allows it).
DL News is also frozen (17 days), CryptoSlate 403, Crypto Briefing is stocks-heavy, BeInCrypto SEO.
**Cointelegraph was the stronger candidate** (18 of 21 items new, 3 dup) but was left out by operator
direction 2026-09-11 ("revisit once the gate has a track record") -- NOT reversed; it is a one-line add
to `FEEDS` if wanted. Also: summaries from Unchained AND Protos (already stored that way) carried raw HTML
(<img> tags) into prompts, the reproduction guard and entity extraction -- `_clean_description` strips
markup and WordPress "appeared first on" boilerplate (no-op for the four feeds without markup). New
`STALE_FEED_DAYS = 7`: a feed whose newest item is that old is logged, recorded in `details.stale_feeds`
and the operator gets a Telegram alert (weekly per feed); tested against the real Blockworks feed.

**4. Whale watchlist.** Only 4 of 8 wallets were live (both OKX silent 219 days, Binance 0x631f... 1,986
days, Coinbase 0x7166... 80 days); the live four were Binance Cold Wallet 20, Coinbase 44, Kraken Hot
Wallets 2 and 3. Every replacement label was read from the address's own Etherscan page title (WebFetch;
search snippets were not reliable -- two labels conflicted) and must be live on-chain. Now 10 wallets: + Binance 14,
Binance 15, Coinbase: Miscellaneous, OKX 7, OKX 3, Bitfinex: Hot Wallet (promoted from classification-only).
The four dead ones moved to KNOWN_EXCHANGE_ADDRESSES (still useful for classifying counterparties).
Deliberately NOT added: Binance Cold Wallet 28 -- the busiest candidate (23 moves >= $2M in 7 days), but 22
of 23 were against ~194,000-tx unlabeled counterparties, i.e. Binance shuffling its own infrastructure, the
class the operator accepts 4 of 86 times (volume, not signal). Honest expectation: the six additions had ~0
moves >= $2M in the last 7 days each (Bitfinex: 3); this is resilience, not a volume jump.
**Found on the way (bigger than the refresh):** "most recent 100 transactions" is a count, not a time span.
Coinbase 44's last 100 native txs spanned 0.2 HOURS and Kraken Hot Wallet 2's 0.3, against a 4h cycle, so
most large native-ETH moves between cycles were never looked at (24h check: 2 moves >= $2M, old page saw
1; Kraken 1, old page 0). Fetch is now time-windowed: everything since a block ~12h ago (one
getblocknobytime per cycle), up to Etherscan's 10,000-row page, truncation logged loudly. Dry run on the new
watchlist: 12 notable moves in the 12h window (9 of them Bitfinex USDT). Expect whale candidate volume and
notified volume to RISE somewhat; caps (12/day category, 2/cycle) are unchanged. (The dormancy detector
that was "not built" here was built 2026-11-15 -- see Phase 2 follow-ups below.)
Binance is the exchange with the lowest accept rate (5/83); Cold Wallet 20 likely behaves like Cold Wallet 28.
Left in place -- revisit with a week of data.

**5. web3_jobs retired.** Removed from collect_cycle, channel_config.yaml (both channels), the DB
`channel_config.categories`, and `channel_router.SHARED_CATEGORY_CHANNELS`; 12 pending cards expired
(`category_retired`, excluded from calibration). Collector, scorer, prompt builder, FactSpec and
category_config row stay in the repo.

**6. Whale read wording.** The legacy whale prompt and `category_config.whale_movements.prompt_notes` said
only "accumulation vs. distribution"; now both state the convention (`post_facts.WHALE_READ_CONVENTION`,
shared with the channel-voice brief): exchange withdrawal = accumulation / less sell pressure, deposit =
possible sell pressure, stablecoin = no directional read. Real generations through the legacy path on all
four cases (ETH in/out, stablecoin in/out) read correctly. The other 50 sampled legacy prompts are
byte-identical; only the 6 whale ones changed.

### Phase 2 follow-ups (2026-11-15)

**Wallet dormancy detector** (`collectors/whale_movements.py`: `find_dormant_wallets`, `alert_dormant_wallets`,
`DORMANT_WALLET_DAYS = 7`). Same shape as the news stale-feed check: a watchlist wallet with no transaction of
any kind (native or token, any size) for 7 days is dead, not quiet -- recorded in the run's
`details.dormant_wallets` and the operator gets one Telegram alert per wallet per week
(`llm_anomaly_alerts` kind `wallet_dormant`). Runs every cycle but costs ~0 API calls: a wallet that returned
any tx in the 12h fetch window proves it is alive for free; only a wallet with an empty window gets a
last-activity lookup (newest native + newest token row). It cannot fail the collection (own try/except), and
a failed lookup skips the wallet rather than flagging it.
*Threshold evidence (live API, 2026-11-15):* every live watchlist wallet's last activity was <= 1.8 days
(Binance 15's native-only gap was 8.8 days but it had token transfers 0.3 days ago -- hence "any kind");
the four retired wallets' latest activity was 16.7 / 63 / 63 / 220 days. 7 days separates them with margin on
both sides: detector run against the 10 live + 4 retired wallets flagged exactly the 4 retired, none of the
live. Also tested: lookup error -> skipped, never-active -> flagged as "no activity on record", alert sent
once and suppressed on the immediate repeat, and a wiring run of `collect()` with a live + a dormant wallet
(details recorded, one alert, no inserts; test rows removed).
*Known limits:* dust/spam token transfers into a dead wallet count as activity and would mask it (the
retired Coinbase wallet's last token transfer was 16.7 days ago, native 80.9 -- a spammier wallet could stay
"alive" forever); activity != yield (a live wallet that never moves $2M is not flagged -- the accept-rate
calibration is the check for that).

**News: why 5 items collected but zero cards (00:00 UTC cycle).** Not a bug -- the rolling 24h cap was full.
`soft_daily_cap` counts ITEMS notified in the trailing 24h across both channels (news is shared). On
2026-11-14 the pre-fix per-channel cap let news go out at 08:06 (4 items), 09:31 (4) and 20:03 (4) = 12-14
items in the window at the 00:00 and 04:00 cycles (limit 6), so remaining headroom was 0 and nothing was
eligible; at 08:00 the window had drained to 12 -> still over. The 08:06 and 09:31 sends aged out on
2026-11-15, leaving the 20:03 pair (4 items < 6) -- headroom 2, per-cycle cap 1, with the best candidate
(score 77.5) waiting. The 20:03 pair ages out at 20:03 on 11-15. This is a one-off transition from the old
cap to the new one, not steady state. Verification of the first card under the new cap: next cycle.

## Phase 3: channel remap (2026-11-15)

Target (operator direction): Crypto Wall Street = news + whale_movements; Alpha Edge = news (split) +
defi_yields (whole); **Crypto Notebook = gems_security + hacks_exploits**; Hustle = tool_launches +
startup_jobs + macro_news; **CoinCraft = no categories** until the Phase 5 earning-opportunities category.
exchange_listings is Phase 4. Applied to the live DB with `seed_channels` / `seed_voices` (category_config
untouched) and mirrored in `config/channel_config.yaml`.

**What changed.** `crypto_notebook` row created (display "Crypto Notebook", cap 8 = the two category caps,
chat_id restored from the pre-2026-09-20 row -- unused, as for every channel) with its voice profile applied;
gems_security and hacks_exploits moved off CoinCraft; CoinCraft's row kept with `categories: []`. Zero
categories is safe: `bot/notify.py` loops over no categories, finds no candidates and returns (tested:
`notify_channel(conn, 'coincraft', [], 12)` -> None, and channel ordering with an empty channel). The two
pending gems cards on CoinCraft (approvals 1865, 1866) were expired as `channel_retired` (excluded from
calibration, same as the other retired-channel expiries). The 143 held gems rows were released AFTER routing
pointed at Notebook, and `collectors/gems_security.HOLD_ALL_CANDIDATES` flipped back to False.

**Found while testing, and fixed.**
1. *hacks_exploits had no FactSpec*, so on Notebook it would have silently kept the legacy category voice
   (the channel-voice path only runs for categories in `post_facts.FACT_SPECS`). Added one
   (`facts_hacks_exploits`, shared with the legacy prompt, which is byte-identical -- 56/56 prompt snapshot;
   speculation guard already ran on the voiced path; paragraph 3 role overridden because "how to spot it on
   any pool or token" does not fit an incident).
2. *Notebook's voice failed 18 of 21 test writes* (gems 14/15 failing, hacks 4/6). Three causes, fixed in
   order: (a) the retry told the model to "cut 725 characters" about a draft it could not see -- retries now
   include the rejected draft, and the cut request is 15% above the arithmetic and broken down per
   paragraph (the model under-corrects every round otherwise); (b) the code-built risk line of a gems post is
   91-417 characters (median 290) -- inside a 600-900 range that leaves the three-paragraph explainer 183-510
   characters; new profile field `length_excludes_risk_line`, true for Notebook (**a deviation from ruling #1
   "count the whole post except Source and hashtags" -- CONFIRMED by the operator 2026-11-15, see Phase 3 follow-ups**);
   (c) retries 3 -> 5, because the longest profile still ended 1-50 characters over on 3 of 15 gems items
   after (a) and (b). Result over the same 21 items: 3 failed -> 0 on the final run (gems 3-attempt
   successes; hacks 6/6 in <=2). Other channels' regression sample: 17/18 at <=2 attempts; the one failure was
   bug 3 below.
3. *A Phase 1 bug that was live on Wall Street:* the third-person check matched "US" (the country)
   case-insensitively as the pronoun "us", so any Wall Street news story mentioning the US failed all
   retries (6/6 drafts rejected on "Bitcoin Hits Four-Month Low as ETF Outflows Persist"). Now "us" matches
   lower case only; the feedback names the offending word.

**Premise check -- gems volume.** All 143 held gems rows scored 15.8-49.9, below the review threshold (50):
releasing them produced **zero** candidates. Only 15 gems rows have EVER scored >= 50 (9 notified), and the
first post-fix collection (2026-11-14 12:08) produced 2 qualifying of ~145. Expect Notebook to run at
roughly 1 card/day, mostly hacks_exploits (29 cards in ~6 weeks), not "real volume" from gems.

**Left for the operator.** Six hacks_exploits cards still pending in CoinCraft's Telegram queue (Kava Lend,
Initia, Mercado Bitcoin, TENDRIL, ACryptoS, Arcus Perps): approving one would write it in CoinCraft's legacy
voice under a "CoinCraft" label. Not expired or re-homed -- only the gems cards were in scope.

**To verify on the first cycle after deploy:** notify visits CoinCraft with no categories without error; the
first hacks/gems card arrives under Crypto Notebook; approving it writes in the explainer voice (3
paragraphs, 600-900 excluding the evidence line, hashtags).

### Phase 3 follow-ups (2026-11-15, operator rulings)

**Length-basis deviation, ratified and recorded.** Crypto Notebook's 600-900 does NOT count the code-built
evidence/risk line (`length_excludes_risk_line: true`). Operator reasoning: the original ruling ("count the whole
post except Source and hashtags") was about the history and cross-category lines, which are optional garnish; the
gems evidence line is 91-417 characters of *mandatory structured output*, and counting it leaves the explainer as
little as 183 characters, which defeats the educational voice. Every other channel still counts the risk line.

**Six pending hacks_exploits cards re-homed to Crypto Notebook** (notifications 577, 614, 661, 675, 678, 738 --
each held exactly one pending hacks item), instead of expired: Kava Lend is from today, and they now get Notebook's
voice and label. The Telegram cards already sent still read "for coincraft" in their text. Verified through the
real approval path (`generate_post(..., 'crypto_notebook', Kava)` -> explainer voice, evidence line shown).

**Defi_yields stays whole on Alpha Edge** (no split) until a week of real Notebook volume has been seen.

**Voice-check audit (after the "US" bug).** `scripts/check_voice_checks.py` is a no-DB regression table of awkward
cases; it found five more silent misfires, all fixed:
1. *banned words* matched proper nouns and technical terms: "key" banned every crypto story about a private key or
   key compromise, "big" banned Big Tech and the One Big Beautiful Bill, "major" banned Major League Baseball. Now a
   Capitalized use that is not at the start of a sentence (or is followed by a Capitalized word) is a name, and "key"
   beside a cryptography noun (private/public/API key, key compromise, ...) is the noun. "a major outflow", "huge",
   "key level" are still rejected.
2. *Notebook concept titles* "Spotting a hidden owner...", "Reading a GoPlus flag", "The anatomy of a mint exploit",
   "Honeypots, explained" were rejected as headlines (cost: a retry each). Added gerund/concept openers and an
   "explained / anatomy of / mechanics of" phrase test; "Kava Lend loses $3.7M..." is still rejected.
3. *Reader-advice guard was dead for the first sentence of paragraph 1*: the text was joined title + paragraph with
   no sentence break, so "Plan for higher costs..." never sat at the START of a sentence. Each field is now checked
   separately (found only because the test cases did not pass -- not visible in any real generation).
4. ...and once it works, it would have rejected *nouns*: "Increase in oil prices...", "Review of the tariff
   schedule...", "Spend on data centers...", "Build-out of the grid", "Sell-off in bonds". Nominal uses are now
   exempt; "plan/prepare/brace/budget/hedge" are unconditional.
5. The second-person check (Hustle) held up: it needs one "you/your/you're" in any case; no misfire found.
Empirical side: 60 real generations across every channel/category (WS news 10, AE news 6, whale 6, defi 6, tools 8,
jobs 6, macro 6, hacks 6, gems 6): 0 failures, and the only non-length rejections were the verbatim-reproduction
guard on Wall Street news (13 hits, all recovered by retry). Not touched (operator-approved lists, floors by
design): the macro neutrality patterns ("crackdown on" is flagged as an unattributed motive phrase though it is a
common neutral noun phrase; "reckless", "propaganda" likewise) -- worth a look if macro posts start failing.

## Phase 4: exchange_listings (2026-11-15)

Operator-approved scope: new SPOT token listings on Binance, OKX, Bybit and KuCoin, to Crypto Wall Street only
(market-desk voice). Perpetuals, futures, margin, tokenized stocks, equity perps, collateral, new trading PAIRS for
an already-listed token, delistings and migrations are excluded by a deliberately strict title filter -- if it is
too strict it shows as zero volume and gets loosened. No price, no market data (a stale number reads as a call and
nothing here makes one safe). Cap 4/day is a sanity ceiling. **Upbit and Coinbase are deliberately NOT included yet:**
Coinbase's blog returns 403 to scripts; Upbit's API failed certificate verification from the dev machine (not
bypassed). Retry Upbit FROM RAILWAY once the core four have a track record -- the cert failure may be local, and
Korean listings move prices harder than most.

**The prior-listing fact is the point of the post** (operator: "the one piece a reader can't get from the
announcement itself... make sure it's prominent, not a footnote"). Every stored listing carries `prior_listings`
-- earlier announcements of the same token on the other tracked exchanges, from our own record, computed at
collection time against the database plus the batch, strictly by announcement time. It is in the title or first two
sentences of every post (a code check, not a request), and the guard rejects a post that says "no earlier listing"
when the record shows one (or "already listed" when it shows none), names fewer exchanges than the record, or buries
it. Identity: normalised token NAME, or SYMBOL when >= 3 characters (Binance's "AIGENSYN" is OKX's "AI"; a 2-letter
symbol alone is not safe). Same exchange + same token within 30 days is a re-announcement (Bybit published "Bybit to
List KOR Protocol" twice in two days), skipped, not a second listing.

**What was built:** `collectors/exchange_listings.py` (four fetchers, strict parser, identity + prior-listing
computation, `--backfill`, per-exchange isolation, stale-source alert at 7 days, Binance article body -> "open
trading ... at <UTC>" for the trading start, KuCoin description likewise; a start time is never guessed), scorer in
`pipeline/score.py`, `post_facts.FACT_SPECS['exchange_listings']` (facts, brief, guardrails, role override,
`title_figures=False` -- the facts hold dates, not figures, so the market desk's "title must contain a figure" is not
applied), a `_find_listing_issues` output guard in `pipeline/write_post.py`, template triage, category_config row,
Crypto Wall Street routing, freshness 24h / no age bonus in `select_candidates`, `collect_cycle` entry, and
`scripts/check_exchange_listings.py` (no-DB regression table: parser, identity, prior listings, scorer, guard).

**Backfill (run once, 2026-11-15):** `python -m collectors.exchange_listings --backfill` -> 306 listings over up to 730 days
(Binance 52, OKX 90, Bybit 69, KuCoin 95). Record starts: Binance 2024-09, OKX 2024-11, Bybit 2025-03, KuCoin 2026-03
(KuCoin's API reaches back least). Rows older than the 24h freshness window can never become candidates; they exist
so a new listing can say what came before it.

**Measured volume (last 90 days): 47 listings, ~3.6/week, not 1-3 -- 60% of them KuCoin first listings of small
tokens.** With the prior weights EVERY one of them clears the review threshold of 50 (the lowest, KuCoin with three
earlier listings and no stated start time, sits at 50), so the threshold does not filter at all yet; ordering and the
4/day cap do the work. All weights are priors (tiers Binance 100 / OKX 80 / Bybit 70 / KuCoin 55; novelty 100/70/45/25
by number of earlier listings, decayed to 60% across the 24h window) -- re-fit with `scripts/calibration_report.py`
after ~20 decisions. If KuCoin tail listings are noise, raise the threshold (a KuCoin first listing with a trading
time ahead scores ~84, so it would have to be well above 60 to matter) or drop KuCoin's tier.

**Found while testing (all fixed):** (a) Post drafts said "was already listed on Binance, Bybit and KuCoin ... so it
was not previously listed on the other tracked exchanges" -- the guardrail phrase invited it; now conditional, and a
code check rejects the contradiction either way. (b) Drafts invented "Trading opens when OKX says so" when no start
time exists -- rejected in code. (c) A short post stuck at 197 characters (min 200) through five retries asking for
"78 more": under-length feedback is now 15% over and names what to add (facts already given, no filler), same fix as
the over-length one. (d) The daily LLM spend alert (threshold $0.10) fired from tagged dev/test calls (572 calls,
$0.101 today, production share $0.0007) and said "across 6 calls ... something may be looping" -- it now reports
production and tagged calls separately and says so when almost all of it is test traffic.

### LLM cost estimator verified against DeepSeek's live balance (2026-11-15)

Prompted by the daily spend alert ("$0.100 across 6 calls"). **The calls were never large and the estimator is right:**
- *The alert's wording was the bug, not the arithmetic.* `$0.100` was the day's TOTAL spend (701 calls: 695 tagged
  `voice_compare` test calls = $0.1155, 6 untagged production calls = $0.0007); "6 calls" was only the untagged count.
  Average call $0.00017, largest $0.00035 (2,167 prompt tokens); nothing near $0.017. The 6 untagged calls are real:
  operator approvals at 04:56-04:57 and 09:36 UTC (tool_launches, defi_yields, 2x whale_movements). Nothing untagged
  needed retro-tagging. The alert now splits production from tagged calls and only says "something may be looping"
  when production spend is at least half the threshold.
- *Rates match DeepSeek's published pricing* (api-docs.deepseek.com/quick_start/pricing, read 2026-11-15, deepseek-flash):
  cache hit $0.003 / miss $0.15 / output $0.60 per 1M off-peak, double at peak (01-04 and 06-10 UTC, Mon-Fri, excluding
  Chinese public holidays -- the estimator has no holiday table, so on those days it over-counts, the safe direction).
  The model id we send, `deepseek-chat`, is a legacy alias not on that page; the balance test below shows it bills at
  the Flash rates.
- *Live balance check* (production provider code path, tagged `cost_calibration`, 184 calls, 608k prompt tokens, all
  cache misses): estimate $0.0981; balance $1.92 -> $1.83 = $0.09 (true range $0.08-$0.10 given cent rounding of
  both readings). Agreement within rounding, if anything the estimate is at the conservative end. **The balance lags
  the calls by minutes**: read 20 seconds after the first burst it showed only $0.05 of the $0.09 and looked like a 2x
  over-estimate; read minutes later it had settled. Compare balances only after waiting, and only over spends of
  ~$0.05 or more (it reports whole cents).
- *Consequence for the guard:* the $0.10 daily threshold is real dollars to within ~20%. Tagged dev/test calls still count
  toward it (real money), so a heavy build day (like 2026-11-15, 695 test calls) will trip it once; the message now says so.

### Phase 4 follow-ups (2026-11-15, operator rulings)

**No blind retune of exchange_listings.** 47 listings in 90 days, all 47 clearing the threshold, ~60% KuCoin first
listings of small tokens: the score does not filter (the same near-constant-score problem web3_jobs had). Operator: do
not adjust tiers or threshold from the description; send real cards and judge from actual output. If after ~20 resolved
cards the KuCoin ones are consistently rejected, that is the calibration data to drop KuCoin's tier
(`pipeline/score.py` `EXCHANGE_LISTINGS_TIER`). `scripts/calibration_report.py exchange_listings` now prints accept rate
by exchange and by position among the four, and says how many resolved cards it has against the ~20 rule.

**The digest line carries the signal** (`pipeline/llm_providers/template.py`): `🟢 Binance · 1st of 4 tracked (none of
the other 3 list it in our record since Mar 2026) · PREDICT` versus `⚪ KuCoin · 4th of 4 tracked (already on Binance,
OKX, Bybit) · MegaETH (MEGA)`. Dot: green 1st, yellow 2nd, white 3rd/4th; the window is shown whenever it is a "1st".
The same exchange's own earlier announcement does not count as "another exchange already lists it".

**Thin-record wording, enforced in code.** The record is short and uneven (Binance from 2024-09, OKX 2024-11, Bybit 2025-03,
KuCoin 2026-03), so "no earlier listing appears among the other tracked exchanges" is only true for the window the OTHER
exchanges' records all cover (the LATEST of their start dates: a Bybit or Binance listing is judged against KuCoin's
2026-03; a KuCoin listing against Bybit's 2025-03). `post_facts.listing_record_window` computes it; the facts tell the
writer the window is part of the claim; `_find_listing_issues` rejects a "no earlier listing" post that does not say
`since <that month year>` (a wrong window is rejected too), rejects "first" unless the same sentence has both "tracked"
and "since", and rejects "newly listed", "new token", "only tracked", "sole" as well as the earlier "debut / first-ever /
brand new". Found while testing: a fixture that quoted "Mar 2026" for a KuCoin listing was correctly rejected (the right
window there is Mar 2025) -- the check is doing its job.

### exchange_listings: the first Railway cycle saw different data than the dev machine (2026-11-15 16:00 UTC)

**Lesson, twice now (Upbit's cert failure, and this): a source that works from the dev machine is NOT verified until it
has run from Railway.** The first production cycle: Binance 100 announcements and KuCoin 18 (fine); **Bybit 403** on
`api.bybit.com` (cloud/US addresses are refused); **OKX answered by IP region** -- from Railway's US address `www.okx.com`
served the OKX-US feed (`/en-us/help/...` URLs, a different set of listings, newest item 23 days old). Effects: the
stale-source alert fired for OKX at 16:06 (a true statement about the feed Railway was given, not about OKX), and 20
OKX-US rows were inserted under different external ids (duplicates of rows the backfill already held, plus US-only
listings that are not "OKX" for this record). None were notified (all >24h old). The 20 rows and their scores were
deleted (none had approvals or notifications).
Fixes: OKX is fetched from `my.okx.com` (the EEA endpoint, global feed) first, then `www.okx.com`; Bybit tries
`api.bybit.com`, `api.bytick.com`, `api.bybit.nl` in order; an OKX announcement gets ONE id whichever regional host served
it (`/help/<slug>`); a new row is skipped as a duplicate when the same exchange already has the same token stored within
30 days under any id; each run records `newest_announcement` per exchange so the next cycle shows what Railway actually
received. **Not yet verified from Railway:** whether the Bybit alternates are reachable and whether `my.okx.com` returns the
global feed there -- check `collect_exchange_listings.details.{exchange_errors,newest_announcement}` on the next cycle.
If Bybit stays blocked, the record is Binance/OKX/KuCoin only and the "four tracked exchanges" wording is wrong.

## Phase 5: earning_opportunities -> CoinCraft (2026-11-15)

CoinCraft is no longer a crypto channel. The category is anything a reader can take part in to earn money or get something of
value -- giveaways, free games, signup and account bonuses, free credits, hackathon prizes, bounties, airdrops -- for a GLOBAL
audience (internationally accessible beats single-country, however large). Cadence 2 hours, not 4.

**Built (Telegram + RSS/API first; Discord later, once the operator has set up the hub server and bot):**
`pipeline/earning.py` (the gate, extraction, both scores -- pure functions), `collectors/earning_opportunities.py`,
`pipeline/enrich_landing.py` (DeepSeek `deepseek_read_url` on the specific landing page, at write time, never as search),
the CoinCraft `how_to` voice, an output guard, the digest line, `scripts/collect_earning.py` (the 2-hour entry point), and
`scripts/check_earning.py` (no-DB regression table: gate, extraction, scores, guard, Telegram parsing, staleness).

**Sources, verified 2026-11-15 from the dev machine -- NOT yet from Railway** (the exchange_listings lesson: a source is not
verified until it has run there; the first Railway cycle's `collect_earning_opportunities.details.sources` is the check).
Working: Epic free-games API (official, tier 1), GamerPower API (tier 2, carries the steps), Devpost API (tier 2, now answers
keyless although an earlier note recorded 403), Doctor of Credit bank-bonus RSS (tier 2), OzBargain / MyDealz / Dealabs
(tier 3, single-country deals; ~0 pass the floors, by design), Telegram via t.me/s/: airdropfind, airdrops_io,
incrypted_airdrops, criptinho, EpicSteamGames, FreeGamesNews, r_FreeGamesOnSteam. Not built: GG.deals (API key), GiveawayBase
(403), Betalist / Startup Perks Directory (404), MaalFreeKaa (DNS fails), Product Hunt GraphQL (token), AirdropAlert (an
article blog, not opportunities). The three exchange channels (OKXAnnouncements, Bybit_Announcements,
CoinMarketCapAnnouncements) are CORROBORATION ONLY: they never produce candidates, so an event cannot reach two channels by two
paths. The six dead/absent handles are not configured (WWGiveaways 92d silent, freestuff_hub last post 2021, FREE_UDEMY_COURSES1
2024, bugbountyplzen 2022, officialairdropalert 2024, cryptosale_tg and 2top_airdrop do not exist). t.me/s/ exposes views and
reaction counts per message, so the reaction-count spam filter needs no MTProto (a post under 0.2x the channel's median views is
dropped, as are posts marked as ads).

**Staleness, as asked:** every configured source is checked each run -- a Telegram channel silent for 30 days, GamerPower/OzBargain/
MyDealz/Dealabs for 7, Doctor of Credit/Devpost for 14 -- recorded in the run log and alerted once a week per source
(`earning_source_stale`). Demonstrated on real dormant channels: WWGiveaways (92 days) and freestuff_hub (2,059 days) flag,
airdropfind (today) does not.

**Rulings (operator, 2026-11-15):** trust floor 40 AND value floor 40, both must pass; order by VALUE descending among survivors
(trust is a gate, not a ranking dimension); both numbers on every card; the two scores are never blended (`scores.score` IS the
value, trust is a floor in `select_candidates.COMPONENT_FLOORS`). Trust is deterministic facts only: source tier, independent
corroboration, terms stated, domain age (RDAP), Safe Browsing, URLhaus, GoPlus. **Dropped on purpose, possible later addition:**
(1) "does the official domain confirm it" (a `site:` search) and (2) "scam reports found" -- both need a search API, which the
operator does not want as a paid dependency and does not want to reintroduce search-as-discovery through the back door. The
operator does the "is this real" judgment by hand. Referral programs were never an input (SUPERSEDED 2026-11-16: operator override, see "Phase 5 follow-ups, round 5" -- they now count in value, capped).
Safe Browsing and URLhaus are wired but report "unchecked" until keys are supplied (GOOGLE_SAFE_BROWSING_API_KEY, a URLhaus
auth key) -- unchecked is never coded as clean. CoinCraft now carries a Phase-5 voice profile (`config/channel_voices.yaml`).

**Real-candidate run (362 fetched from 14 sources, dev machine):** 338 stored + 2 held by the gate (both "connect-wallet-to-claim") ->
178 pass both floors. By type (stored -> eligible): free_game 127 -> 99, hackathon 48 -> 41, giveaway 33 -> 21, bank_bonus 16 -> 12,
**airdrop 50 -> 4**, freebie 60 -> 0, bounty 3 -> 0. **The trust floor of 40 removes nearly every Telegram airdrop**: tier-3
channels average trust 25-33, and a post only reaches 40 with an old landing domain plus stated terms plus corroboration. 58
airdrop/giveaway/credits items had value >= 40 and trust < 40 -- the "worth it but unproven" quadrant, listed by
`python scripts/earning_report.py --unproven`. The cross-link fired where designed: "Another free $1000 credit for your AI Agent"
(Telegram, a 4-day-old domain) scores value 80 / trust 0. 63 of 338 landing domains had no RDAP age. Domain age, corroboration and
terms are the only levers an airdrop has, so if airdrops matter to CoinCraft the operator will want to see those 58 and decide the
floor or the corroboration rule -- it was not retuned.
**Dedup:** the same offer arrives from several sources (Epic's official API, GamerPower, EpicSteamGames). `topic_key` is the landing
page, or a significant-words title key when the link is an aggregator's own redirect, with a 72h cooldown, so it is one candidate.

**Write path:** the post is a how-to; its steps come ONLY from the offer itself (GamerPower's instructions, Epic's fixed steps, or
a DeepSeek read of the landing page) and are checked in code: no numbered steps when none are available, every step grounded in
the facts, no amounts the facts do not contain, the deadline stated, no URL / referral / promo code, no "the facts" leaking, one step
per line. Real generations: Epic, GamerPower, Doctor of Credit and Devpost items all pass (1-2 attempts). **The URL reader worked on
1 of 3 landing pages tried** (Doctor of Credit); Epic's store and Devpost were not fetched (the service declined or the page is
script-rendered), so those posts say the steps are on the offer's page rather than inventing them. Cost of a read: ~3,200 prompt
tokens (~$0.0005); pricing for the tool itself is not in the docs page I could read.

**Cadence / infrastructure -- NEEDS THE OPERATOR:** a separate 2-hour service must be created in Railway (`collect-earning`, start
`python scripts/collect_earning.py`, cron `0 */2 * * *`, restart NEVER, the same variables as the poller via references). Creating a
service and its variables is an account/secrets action, so it was not done. Until it exists the category rides the 4-hour cycle
(`scripts/collect_cycle.py`); both are idempotent. `.railway/railway.ts` needs the matching `service("collect-earning", ...)` entry
when the service is created. The 2-hour job notifies CoinCraft ONLY (`notify.run(only_channels=["coincraft"], job_name="notify_earning")`),
because the full notify at 2 hours would double every other channel's per-cycle pacing.

### Phase 5 follow-ups (2026-11-15, operator rulings and the airdrop trust analysis)

**Operator rulings.** Daily cap 8 stays as a prior, revisit after real cards. Until the 2-hour service exists the 4-hour cycle is
fine. **Dead sources are settled -- do not chase:** GG.deals, GiveawayBase, Betalist, Startup Perks Directory, MaalFreeKaa,
Product Hunt, AirdropAlert (7 of the research list, alongside the 6 dead Telegram handles). The surviving set is what we work with.

**Correction to an earlier note:** the Phase 5 entry said Safe Browsing and URLhaus were "wired". They were not -- the field existed
and always read "unchecked". They are now (`collectors/earning_opportunities.reputation_checks`; keys `GOOGLE_SAFE_BROWSING_API_KEY`,
`URLHAUS_AUTH_KEY`), covered by a MOCKED test only because no keys exist yet; any failure or unexpected response is "unchecked",
never "clean", and a "flagged" landing URL is gated out. Licensing the operator should decide before adding keys: Safe Browsing v4 is
"for non-commercial use only (not for sale or revenue generating purposes)" and Google points commercial users to Web Risk (paid);
URLhaus' free community API is fair-use and "commercial or for-profit use may require the paid enhanced abuse.ch API". The URLhaus
lookup request shape (host endpoint, `Auth-Key` header) was not documented on the page I could read -- verify on the first keyed run.

**Why 52 of 58 airdrop-channel items fail the trust floor (the question: junk correctly rejected, or a whole source type penalised?).**
Baseline arithmetic first: there is no "unvetted baseline of 35" -- an uncorroborated tier-3 item is 14 (tier) + 4 (reputation,
unchecked) = 18 before domain age (0/4/10/15, unknown 6) and stated terms (5 each, max 15); the ceiling with no keys and no second
source is 14 + 15 + 15 + 4 = 48. Classified by the facts the system could see:
  A. 5 correctly rejected -- a domain 2-4 days old, or the payout-for-nothing cross-link (value 80 / trust 0).
  B. 11 STRUCTURAL -- every airdrops_io post lands on airdrops.io's own pages, so the domain facts describe the aggregator (whose
     age RDAP could not return, because .io has no RDAP at rdap.org), not the project.
  C. 28 STRUCTURAL -- incrypted_airdrops and criptinho post in Russian; the deadline/eligibility/payout detectors are English-only, so
     "terms stated" is 0 by construction (0 of 34 Russian items scored any terms). 34 of 58 airdrop-channel items are Russian.
  D. 6 genuinely unproven -- an established domain, nothing stated, no second source.   E. 2 other.
So ~75% of the rejections are the facts being BLIND for a source type, not a verdict on the offer. What-ifs, NOT applied (none of the newly
passing items has a young domain or a penalty): established aggregators (airdrops_io, incrypted) tier 14 -> 24 passes +14; tier 14 -> 22
for all four channels passes +26; neutral domain-age for aggregator-own landing + unknown (not zero) terms for Russian passes only +1;
both Safe Browsing and URLhaus clean passes +2. Separately, several incrypted posts are news roundups ("Отжимка по дропам ...") rather
than offers -- passing the floor does not make them an opportunity; that is the operator's manual call.

**Railway service for the 2-hour cadence (operator creates it):** New service -> GitHub repo natanimxo/crypto-content-bot, name
`collect-earning`; Start command `python scripts/collect_earning.py`; Cron `30 */2 * * *` (UTC; :30 so it never overlaps the :00 main cycle);
Restart policy Never; Region sfo, 1 replica; variables as references to the poller's (`${{crypto-content-bot.DATABASE_URL}}`,
`DEEPSEEK_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OPERATOR_CHAT_ID`, `TELEGRAM_ALLOWED_USER_IDS`); later, when keys exist,
`GOOGLE_SAFE_BROWSING_API_KEY` and `URLHAUS_AUTH_KEY`. Matching `.railway/railway.ts` entry once it exists:
`service("collect-earning", { source: repo, start: "python scripts/collect_earning.py", replicas: { "sfo": 1 }, deploy: { cronSchedule:
"30 */2 * * *", restartPolicyType: "NEVER" }, env: { DATABASE_URL: preserve(), DEEPSEEK_API_KEY: preserve(), TELEGRAM_BOT_TOKEN: preserve(),
TELEGRAM_OPERATOR_CHAT_ID: preserve(), TELEGRAM_ALLOWED_USER_IDS: preserve() } })` and add it to `resources`.

### Phase 5 follow-ups, round 2 (2026-11-15, operator rulings)

**Safe Browsing and URLhaus: SKIPPED -- blocked on LICENSING, not on effort.** Operator ruling: Safe Browsing v4 is non-commercial only
("not for sale or revenue generating purposes") and the channels sell ads -- a clear no; URLhaus' community tier has the same commercial
ambiguity and its lookup request format could not be confirmed. "Unchecked, never clean" stays the honest default. The code
(`reputation_checks`) is dormant without keys and has a mocked test; nothing calls either service. Revisit only if a
commercially-licensed source is chosen (Google Web Risk, abuse.ch's paid tier) -- that is a licensing/cost decision, the engineering is done.

**Fix the blind spots, do NOT raise a tier (operator ruling).** Raising airdrops_io / incrypted from tier 14 to 24 would have passed 14 more
items "without the system knowing anything more about them" and would mask the same blindness on every future item from those sources.
Two fixes instead:
1. *Multilingual term detection* (`pipeline/earning.py`): Russian and Portuguese patterns for a deadline ("до 21 ноября", "Дедлайн:
   30.11.2026", "até 21 de novembro", "termina dia 3 de dezembro"), who can take part, and what is paid (a currency or a token/point
   quantity next to a reward word, e.g. "Разделяем 1 000 000 токенов", "Ganhe 50 USDT de prêmio"); space-separated thousands ("$4 000")
   now parse; reward cues and funding-round words exist in all three languages. Deterministic; if recall on real items stays poor, a
   narrow cheap LLM extraction call on items that already cleared the gate is the sanctioned fallback (small volume, structured
   output, Python decides what the answer means).
2. *Resolve the real project domain on aggregator posts*: an airdrops.io post's landing page is airdrops.io's own page, whose "Website:" link
   is a redirect to the project (`/visit/<id>/` -> e.g. elysium.kinetiq.xyz). The collector follows it so RDAP and corroboration describe the
   PROJECT; if it cannot be resolved the domain is left UNKNOWN, never the aggregator's. Verified live on 4 pages (elysium, arc, duelbet,
   extended -> their real sites). Side effect: several channels mentioning the same project now share a landing URL, which is what
   independent corroboration needs.
Also fixed: a latent regex bug (a backspace character where `` was meant) in the recurring-fee capital pattern.

**Result of the two fixes (airdrop channels, real candidates, 58 items; before -> after):** pass both floors **5 -> 25**; fail with value >= 40
**52 -> 32**. The 32: 9 correctly rejected (a domain 2-31 days old, or the payout-for-nothing penalty), 5 genuinely unproven (nothing
stated, no second source), 13 with partial evidence still under 40 (e.g. an established domain with one stated term and no second source;
9 of them sit at 38-34 -- one fact short), and only **5 still blind** (4 Russian posts with no detectable term, 1 airdrops.io roundup
with no single project). So ~84% of the remaining failures are real reasons, ~16% blind spots -- the floor is doing its job. Russian
term recall: 27 of 34 items now score at least one term (was 0 of 34). airdrops.io: 12 of 13 resolve to a project domain, 11 with a
known age. Found and fixed while measuring: (1) the Russian reward cues made "Разделяем $70 000 ..." (a pool shared among traders) read as a
per-person payout and tripped the payout-for-nothing penalty (four items to trust 0) -- pools are now recorded as pools (`extract_reward`)
and exempt; (2) `.io` / `.me` have NO RDAP server (IANA bootstrap), so 15 items had unknown domain age -- a WHOIS (TCP 43) fallback was added
and verified locally (arc.io 2013, zkverify.io 2024, airdrops.io 2017); **port 43 may be blocked on Railway** -- then the age stays unknown, never
guessed; a cached "no date" for .io/.me no longer shadows it.
**Open, flagged for the operator, not changed:** (a) roundup / digest posts (10 of the 58; 6 pass) are scored as one offer and take the
FIRST linked project's domain facts -- a multi-project post is not a single opportunity; (b) "up to $15,000 welcome bonus" is read as a
nominal payout and trips the penalty for tier-3 sources (WEEX/OKX exchange sign-up bonuses score trust 0) -- defensible, but it is a
judgement call; (c) near misses: 9 items at trust 34-38.

### Phase 5 follow-ups, round 3 (2026-11-15, operator rulings: roundups, "up to", near misses, the payout audit)

**1. Roundups are non-offers -- excluded.** A digest of several projects was scored as ONE offer and took the first linked project's domain
facts (6 of the 25 airdrop passes): the same class of error as scoring airdrops.io's domain. `earning.is_roundup` (title / first line:
roundup, digest, recap, radar, "N confirmed/early airdrops|campaigns", "top N", Russian подборка / отжимка / дайджест / итоги / обзор,
Portuguese resumo semanal / compilação) now gates them: stored HELD with the reason "not an offer: a digest / roundup of several
projects" (13 on the last run), never a candidate. A single offer that merely says "weekly"/"daily" ("Weekly giveaway: win a $100 gift
card", "Daily check-in reward") is deliberately NOT matched (tested). A genuinely good offer inside a roundup is expected to arrive through the
project's own channel or a second source.

**2. "Up to $15,000" welcome bonuses -- DELIBERATE CHOICE, not an oversight.** A stated MAXIMUM is scored as the payout and can trip the
payout-for-nothing penalty for an unvetted source. The operator's reasoning: a tiered sign-up bonus where almost nobody gets the headline
figure is exactly what the readers should not be sent chasing; if a specific exchange bonus is genuinely good it should clear on stated terms
(real terms, an old domain, a second source). Fixed alongside: the matcher could not SEE "up to" (the optional group made the text before the
match empty), so a max was being recorded as a plain per-person amount; it is now recorded as kind `max` and covered by tests in English,
Russian ("до $15 000") and Portuguese ("até $300").

**3. Near misses (trust 34-38) -- the floor stays.** Nine items one fact short is the floor working. If similar items later turn out to be
consistently good, that is calibration data, not a reason to move the floor now.

**4. The payout audit (the cross-penalty is the most aggressive rule and fires on a value-derived signal, so a misread of value silently
destroys trust).** Findings and fixes:
- The penalty now fires ONLY on an amount the text itself claims for one reader (`stated`) or a stated maximum (`max`). A pool/total
  (`pool`), an amount tied to N winners (`unclear`), a structured retail value (`worth`, e.g. GamerPower) and an expected value (`ev`) are
  recorded as such and can neither inflate value nor cost trust. Pool / unclear amounts are stored as `pool_usd` / `unclear_usd`, and the post
  facts say "shared among participants" or "not clear whether per person or a total".
- Phrasings now classified, in three languages (33 cases in `scripts/check_earning.py`): "prize pool", "total rewards", "in prizes / rewards / tokens",
  "shared among", "distributed", "up for grabs", "giving away ... among", "to be shared"; призовой фонд, общий пул, разыгрываем, на сумму,
  в наградах, между участниками, поровну; prêmio total, pool de, premiação, sorteio, dividido entre, em prêmios; "$X for N winners" /
  "для N победителей" / "N vencedores" -> unclear; an explicit per-person cue NEXT TO the amount ("each", "per winner", "каждому по",
  "cada", "por pessoa") wins over a pool word elsewhere in the sentence. A mixed phrasing ("$10 per winner from a $1,000 pool") records the larger
  (pool) figure, which is penalty-safe.
- Bugs found by the audit: Portuguese "$10.000" (dot thousands) was read as $10 -- now ten thousand ("1.50" stays a dollar fifty); Russian/Portuguese
  pool and per-person wordings were not detected at all (the amount was simply missed); Brazilian "R$" amounts were not parsed.
- Another misread, found on the last real run: a $5,500 solar-battery deal on OzBargain ("$3,500 worth" of extras) scored trust 0 because the
  "worth" figure was read as a payout for a "free" offer. The penalty now requires that "for nothing" is DEMONSTRATED: if the text carries
  another figure of at least half the nominal that was not read as a reward (a price, a deposit, a fee), capital is unknown rather than zero
  and the penalty is skipped.
- Result on real candidates: the penalty fired on 3 of 323 live items before that last fix (the AI-agent $1,000 credit from a 4-day-old
  domain -- correct; the WEEX "up to $15,000" -- the deliberate choice above; the solar deal -- the misread just fixed), 2 after.
- **Observation, not changed:** the penalty ignores ODDS. A chance-based giveaway with a $1,000 prize from an unvetted source is treated like
  a guaranteed $1,000. Tier-3 items cannot reach the floor without stated facts anyway, so the practical effect is small, but it is a design
  question: should `giveaway` / raffle types be exempt from a rule built for guaranteed payouts?

**Airdrop channels after all fixes (real candidates):** 46 live items (13 roundups held out), 19 pass both floors, 26 have value >= 40 and
trust < 40: 7 correctly rejected, 5 genuinely unproven, 10 with partial evidence still under 40, 4 still blind (Russian posts with no
detectable term). Russian term recall 22 of 29.


### Phase 5 follow-ups, round 4 (2026-11-16, operator ruling: income only)

**Scope rule.** earning_opportunities carries INCOME only: money (cash, stablecoin, crypto) that ends up in the reader's pocket. A discount is
a cheaper purchase and a free game is a free purchase; neither is income. Removed ENTIRELY (collectors deleted, not filtered): Epic API,
GamerPower, EpicSteamGames, FreeGamesNews, r_FreeGamesOnSteam, OzBargain, Dealabs, MyDealz (Slickdeals was never built). 226 old rows from those
sources were expired (kept for the record, never candidates again). What is left: Devpost (hackathons), Doctor of Credit's bank-account-bonus
feed (cash sign-up bonuses), four airdrop Telegram channels (airdropfind, airdrops_io, incrypted_airdrops, criptinho). A second safety net:
`earning.NON_INCOME_TYPES` (free_game, freebie, credits) is held with the reason "not income: saves money, does not pay it", so a future
source cannot smuggle a freebie in. railway.ts now declares the `collect-earning` service (cron `30 */2 * * *`); `railway config apply` is
authoritative if the operator picked a preset.

**Volume left per day (estimate before dedupe and the cap):** about 10 eligible items/day across the five remaining sources; most of
that is Devpost (largely stock listings that repeat) and the airdrop channels (noisy estimate). Actual sends are capped at 8/day for
CoinCraft and 1 per 2-hour cycle by the pacing rule, so the cap is not the binding constraint any more -- supply is.

**Research cycle: Railway-side probe.** `pipeline/source_probe.py` holds a probe list of candidate income sources; `scripts/collect_earning.py`
runs it at most once per 20h, AFTER notify, and stores the compact result in run_logs `source_probe`. A source is not verified until it has run
from Railway (OKX served a different feed from there, Bybit returned 403).

### Phase 5 follow-ups, round 5 (2026-11-16, operator override: referral now counts in value)

**The original rule, and whose it was.** Phase 5 shipped with "whether a referral program exists is NEVER an input to any score": referral
parameters stripped from links, no function reading a referral field, and a test (`a referral program changes neither score`) asserting
the scorer's output was identical with the flag toggled. That rule was **the assistant's recommendation, not the operator's decision** -- the
operator did not agree to it. On 2026-11-16 the operator **overrode it deliberately.** Do not "restore" it.

**What changed.**
1. A referral program the reader can earn from adds to VALUE, capped at `REFERRAL_VALUE_MAX = 12` points of 100 (the operator asked for about
   10-15%). It is ADDED ON TOP of the unchanged payout/capital/effort/prereq/geo score (`value = min(100, value_base + referral_bonus)`), so an
   offer with no referral program scores exactly what it did before and the floors did not move. A program with no stated figure is worth 50
   of 100 on the component (6 points); a stated per-referral amount scales on the same log scale as payouts, never below 50. A mediocre offer
   with a referral program does not outrank a strong one without (tested). The breakdown stores `value_base`, `referral_bonus` and `referral`.
2. Availability is a plain fact on the CARD beside both scores: `value 72 · trust 57 · referral yes` (or `no`, or `yes ⚠ recruitment-shaped`).
   It is not part of either number.
3. The assertion test was removed and replaced by tests of the new behaviour (bonus, cap, no-bonus, recruitment shape, extraction in en/ru/pt).

**Unchanged:** referral parameters are still stripped from every stored link (`normalize_url`), and the post guard still rejects referral /
invite / promo-code language. The operator adds his own referral links at forward time; no one else's code goes out under his name.

**The recruitment-shape warning (the one case where a referral is a warning, not a feature).** Referral-heavy offers correlate with MLM and
pyramid schemes. If the stated per-referral earnings EXCEED the base payout (the larger of payout / pool / unclear amount; with no base
payout at all, a stated referral amount is itself the whole offer), the referral adds NO value and costs TRUST
(`referral_recruitment_penalty`, 30 points -- the same size as the lighter payout-for-nothing penalty). It has no tier-1 exemption. The card
says "recruitment-shaped". Not covered (deliberately, not asked): multi-level wording ("levels", "downline"), and a referral with only a
percentage and no dollar figure -- both count as an ordinary program.

**How availability is decided (and a deliberate narrowing).** From the offer's TEXT, clause by clause (`earning.split_referral` /
`extract_referral`): "referral", "refer a friend", "invite friends and earn", "each/per friend", "affiliate program", Russian реферал /
"за каждого друга" / "за приглашённого", Portuguese indicação / "indique" / "cada amigo". Not a program: the poster's own code ("use my
referral code"), negations ("no referral needed"). A referral code on the SOURCE'S OWN LINK (`referral_link`) is only an inference (the poster
has a code, so a program probably exists); a fact on a card must not be an inference, so it is NOT counted and the card says "referral
unconfirmed (source link carried a code)". Counting it would have added the same +6 to 18 more airdrop posts. If the operator wants it counted, it is a one-line change in
`earning.referral_info`.
The referral clause is also removed from the text the PAYOUT is read from, otherwise "$25 for each friend you invite" would be read as the
offer's own payout and "referral exceeds base payout" could never be told.

**Two blind spots found on the way and fixed:** amounts with the sign AFTER the number ("50$", "15$", Russian / Portuguese style) were not read
at all (`_AMOUNT_RE`); "ЗАБИРАЕМ" (we take) was not a reward cue. Both affect payout reading on every source.

**Effect on the 116 live scored rows (re-scored in place):** 11 describe a referral program in their text (18 more only carried a code on the
link); 10 got a bonus (the 11th is the one below); NO item crossed the value floor because of the bonus (selection unchanged except as
follows). **One** item is recruitment-shaped: "ЗАБИРАЕМ ДО 15$ ЗА КАЖДОГО ДРУГА ОТ FOLK AI" (up to $15 per friend and nothing else) -- trust 43 -> 13, so it
no longer clears the floor; passing items 77 -> 76. That is the rule working on the shape it was written for.

### Phase 5 follow-ups, round 6 (2026-11-16, operator rulings on the referral change)

**Both choices confirmed.** (1) Availability comes from the offer's TEXT only; a referral code on the source's own link says the SOURCE has a
program, not that the offer does, and stating an inference as fact on a card is the discipline held everywhere else. The 18 link-code-only items
stay "unconfirmed". (2) The recruitment penalty (30 trust points) has NO official-source exemption: an official source running an offer whose
referral earnings exceed the base payout is still recruitment-shaped and must be flagged -- an exemption would be the system deciding a brand
name makes a structure acceptable.

**Multi-level wording is recruitment-shaped ON ITS OWN**, independent of the amount comparison (it is the clearest textual signal of a pyramid
structure). `earning.has_multilevel`: unambiguous terms anywhere in the text (downline, upline, multi-level, MLM, network marketing, unilevel,
binary plan, многоуровневая, даунлайн, multinível); the bare word "levels" only inside a referral clause (it is too common otherwise: "complete 3
levels", "level up" are tested as NOT matching), plus counted levels/tiers of referrals ("3 levels", "level 2", "2 уровня") when the text has a
referral program. It implies availability, gives no value bonus and costs the 30 trust points. A percentage-only referral with no figure stays an
ordinary program. False positives found and fixed on a real row: French "indique ... tiers" ("indicates ... third parties") matched Portuguese
"indique" and the bare word "tiers"; Portuguese "indique" now needs its object (um/uma/amigos/e ganhe) and "tiers" is no longer a level word.

**The "50$" fix and everything it touched.** The sign-AFTER-the-number form ("50$", "15$") was invisible to `_AMOUNT_RE`, which feeds payout
reading, the pool/unclear/max classification, the other-amount guard and the referral amount. The same family: CAPITAL extraction only read
"$500" after a deposit word, and had no Russian or Portuguese deposit word at all (депозит, внести, пополнить, depósito, ...), so a Russian
post asking for a deposit was scored capital 0 -- i.e. value OVERSTATED, the opposite direction to the understated payouts. Both fixed
(`_CAPITAL_TRAIL_RE`). All 342 scored rows (116 live, the rest expired) were re-scored in place from the stored title + description, FILL-ONLY
(the stored description is cut at 1200 characters, so a value is only filled in or raised, never dropped).
Measured effect: 2 payouts filled in (1 live), 2 capital figures raised (both live), 3 value scores and 1 trust score changed, and ONE live item
fell below a floor (the PumpFun "50$" post: capital 0 -> $60 once the deposit was read, value 52 -> 39). Passing items 76 -> 75. The effect was
small because most posts write "$50", but it is not zero and it ran in both directions.
**ANY CALIBRATION DATA ON THIS CATEGORY FROM BEFORE 2026-11-16 IS SUSPECT:** the pass/fail counts (19 of 46 airdrop items, 77 passing, the 9
near misses at trust 34-38, the 7/5/10/4 breakdown), the volume estimate and the payout-penalty counts were measured with a parser that
could not read "50$" and could not read Russian / Portuguese deposits, and (for value) before the referral bonus existed. Re-measure
before using them to tune a floor or a weight.

**Found while checking the PumpFun posts -- NOT FIXED, needs a ruling.** The criptinho PumpFun posts tell readers to use a European VPN and
"preferably fake GPS", and one says the bank statement can be "real or done through Photoshop (better the real one)" to pass KYC. That is
instructions to defeat identity verification with forged documents. Neither the gate nor the trust score looks for it (trust 43, so it can
clear the floor). Candidate gate terms: forged / photoshopped documents (фотошоп, "photoshop" near statement / KYC / document), fake GPS /
spoofed location, "VPN" paired with a residency claim. Proposed as a deterministic gate reason ("instructs how to defeat identity
verification"); not added unasked.

### Phase 5: new income sources -- research, verified from Railway (2026-11-16)

**Method.** 52 candidate endpoints in `pipeline/source_probe.py`, each fetched with no credentials FROM RAILWAY (`collect-earning`, 2026-11-16
06:31 UTC, stored in run_logs `source_probe`) and from the dev machine, then compared. The collect-earning service had silently done nothing for
its first two cron slots because its `DATABASE_URL` was the placeholder host `db.xxxxxxxxxxxx.supabase.co` (fixed by the operator at ~06:00 UTC):
a service is not "running" until it has a run_logs row. Railway vs local DIFFERED for: OKX (12 announcement types locally, 4 from Railway --
the regional feed again), Coinbase Learn (200 local, 403 Railway), Bybit (200 local, 403 Railway, known). Immunefi's public JSON was added
after the first run and is verified at the next cycle (08:30 UTC).

**Ranked by trustworthiness (platform that holds the money > aggregator > open campaign tool), then fit. Volume is a single snapshot, not measured turnover.**
1. **Superteam Earn** `earn.superteam.fun/api/listings` -- keyless JSON, Railway 200. Real money (USDC / USDG; some listings pay a project's own token: only
   USDC/USDG/USDT may count as USD -- one listing is "689,655 BAOBAO"). Fields: rewardAmount, token, deadline, sponsor, status. 11 open, ~$25k in stablecoin.
   Writing, design, video, dev tasks: fits CoinCraft's non-technical audience. Gaps: the `type` filter is ignored (project/grant return the same 11); the list
   exposes no region, and some listings are country chapters (Superteam Ukraine) -- geo needs the per-listing endpoint (unverified).
2. **Zindi** `api.zindi.africa/v1/competitions` -- keyless JSON, Railway 200 (10, newest end 2027-02). `reward` "$40 000 USD", open flag, dates, organisation.
   Cash prizes; global participation (Africa-founded, some competitions region-limited). Requires data-science skill.
3. **Immunefi** `immunefi.com/public-api/bounties.json` -- keyless JSON, 218 programs (6 MB), maxBounty, rewardsToken (USDC), launchDate, endDate; audit
   competitions have a fixed window and a prize (Plether, $80k, ends today). Local 200; Railway pending. 113 of 218 require KYC. Security-researcher audience.
4. **Bug bounty program feeds** -- YesWeHack `api.yeswehack.com/programs` (bounty_reward_min/max), Intigriti `app.intigriti.com/api/core/public/programs`
   (minBounty/maxBounty, createdAt), Bugcrowd `engagements.json` (rewardSummary), bounty-targets-data on GitHub (HackerOne / Bugcrowd / YesWeHack / Intigriti
   scopes). All Railway 200, keyless, global. Pay real money, but they are ONGOING programs, not time-boxed offers; skill-gated; many VDPs pay nothing
   (filter maxBounty > 0). Use only as a "new program launched" signal.
5. **KuCoin activities** `api.kucoin.com/api/v3/announcements?annType=activities` -- keyless, Railway 200 (7). Listing campaigns with token giveaways ("Matrix (MTX)
   70,000 MTX Giveaway"), trading competitions ("Win your share of 550,000 USDT"). Official, but paid in tokens, needs KYC and trading, and it overlaps
   exchange_listings (the same event on two channels). **Binance CMS** catalog 93 ("Latest Activities"): Railway 200; trading competitions and quiz-to-earn
   posts; HTML-in-JSON, needs a parser. **Bitget** announcements: Railway 200, but today's items are notices, not campaigns. **OKX**: the feed differs from
   Railway (see above) -- do not trust. **Bybit / Coinbase Learn: 403 from Railway.**
6. **Devfolio** `api.devfolio.co/api/hackathons` -- Railway 200 (20, to 2027-03), NO prize fields, many in-person in India: weak. **HackerEarth** events: Railway 200 but
   corporate events / webinars, not prize hackathons: weak.
7. **Galxe** GraphQL (POST) -- Railway 200; campaigns anyone can create ("like, comment and RT", "Win $50 USDT"): tier 3 by construction. Possible only as
   a low-trust source behind the existing gate.

**Exist, but NOT usable:** Sherlock (JSON works, but the feed holds only finished contests, newest 2026-06); Topcoder (v6 returns drafts/tests, 0 active,
and the 404 path in the probe is wrong -- not worth chasing); Algora (200, empty); Gitcoin bounties (404, discontinued); DoraHacks (404, no known API);
ETHGlobal / MLH / Code4rena / Cantina / CodeHawks / HackenProof / Immunefi site (HTML only, no feed); Kaggle (401, auth); AIcrowd (401); DrivenData and
Prolific and IssueHunt (login wall); Numerai (not an API); Layer3 (403); DefiLlama airdrops (404); airdrops.io RSS (works, newest item 2026-10-02, and
the items are guides, not offers).
**Paid research / user testing / AI-data work (Prolific, UserTesting, Respondent, Outlier, DataAnnotation): exist and pay real money, but there is no
public listing, only marketing pages and a login.** Nothing to ingest and nothing to verify; at most a static, hand-curated "evergreen" list.
**Learn & Earn (Coinbase, Binance): no API; Coinbase is 403 from Railway.**
