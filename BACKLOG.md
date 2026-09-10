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

- **Unresolved: Telegram callback_query taps sometimes don't appear in the next
  `getUpdates` poll at all, or appear with significant delay, and — once they
  do appear — often fail `answerCallbackQuery` with "query is too old and
  response timeout expired" even on the very first answer attempt (not just
  after a slow write delays it).** Live-observed repeatedly 2026-09-09 across
  Approve, Reject, Edit, and (briefly, before that flow was removed) Publish
  taps — e.g. a tap visible in one manual `getUpdates` peek was simply absent
  from the very next call moments later with the same offset; on other taps,
  the state change succeeded but the acknowledgment still came back stale
  immediately. No root cause identified — ruled out: our offset math (verified
  correct via direct API calls), the bot token mismatch, the allow-list, and
  (per a later, cleaner comparison) our own manual diagnostic polling being the
  cause of the very first instances, though that doesn't explain the pattern
  recurring on fresh, never-peeked-at taps later in the same session. Current
  mitigations only manage the *symptoms*, not the cause:
    - `bot/approval_poller.py`'s two-phase run() (ack every tap in a batch
      before any tap's slow LLM write) prevents one tap's latency from starving
      another's acknowledgment.
    - `_safe_ack()` ensures a failed acknowledgment never blocks or drops the
      actual state change / deferred work — this is why the pattern is no
      longer *functionally* blocking, but the underlying Telegram-side
      flakiness is unexplained and could still cause a tap to be silently lost
      if it never arrives in any poll before GitHub Actions' cron naturally
      moves the offset forward. Worth real investigation once this runs on the
      live 5-minute cron instead of manual polling, where the pattern might
      look completely different (or vanish, or worsen) under real timing.
    - Since Publish/Cancel no longer exists (2026-09-10 — see below), this is
      now scoped to Approve/Reject/Edit only, but the mechanism is identical
      and there's no reason to assume it's specific to the removed flow.

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
