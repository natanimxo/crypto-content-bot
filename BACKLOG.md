# Backlog

Deliberately-deferred items — noticed and worth tracking, not yet implemented.
Now that this repo is pushed (github.com/natanimxo/crypto-content-bot), these
could move to GitHub Issues instead; until that switch is made, this file is
the one place to check.

## Scoring

- **DeFi yields: penalize implausible APY spikes instead of rewarding them.**
  Live-observed 2026-09-09: pools with 200%+ APY and 100+ percentage-point
  `apy_pct_7d` jumps (e.g. `raydium-amm WSOL-USDC`, `pepeteam-swaves SWAVES`)
  scored 87-90/100 — as high as a healthy, stable yield. `impact` saturates at
  33%+ APY with no ceiling-awareness, and `novelty` rewards a big 7d swing
  regardless of direction or plausibility. In practice this pattern usually
  signals unsustainable token emissions, not a real opportunity.
  Candidate fix: a penalty when `apy` is far above the category's own rolling
  median/percentile (relative, not a fixed cutoff — "high" depends on market
  conditions), which needs a rolling stat over recent `raw_items`, not just the
  single payload being scored. See TODO comment at
  `pipeline/score.py::score_defi_yields`.
  **Deliberately not implemented yet** — validate the MVP notify → approve →
  write → labeled-delivery loop end-to-end first, before touching scoring weights.
  (Validated 2026-09-10 — loop confirmed working; still not implemented, now
  just genuinely next-up rather than blocked on validation.)
  **Observed in the wild a second time, 2026-09-12** — no longer just
  theorized from the 2026-09-09 sample: building the news category's
  cross-category connection feature (pipeline/write_post.py) surfaced
  `uniswap-v3 BRZ-USDT`, 43.5% APY, TVL $100,126 — $126 above the
  category's own $100k collection floor — as an independently-notable
  candidate (cleared `review_threshold=50` on its own). Exactly the shape
  this gap predicts: a thin, high-APY pool scoring as if it were a
  healthy, established one. Not fixed in that pass (out of scope — the
  news feature's own fix was requiring genuine relatedness, not correcting
  defi_yields' scoring), but this is now real evidence the gap actively
  produces bad output today, not just a plausible future risk.

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
