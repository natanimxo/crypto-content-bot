# Handoff — 2026-11-16

For a fresh Claude Code session picking this project up cold. This is STATE + the rules the operator has set + what is open. `BACKLOG.md` is the long decision log (read its
"READ FIRST -- traps" block, then the section for whatever you touch); `intelligence-bot-spec-v2.md` is the original design. The previous handoff (2026-09-11, stale: it says
"not yet live") is kept at `docs/HANDOFF-2026-09-11-archive.md` for the bug history of the first three categories.

## STATUS UPDATE (2026-11-20): Crypto Notebook (read this with the block below)

**Why Notebook looked silent (diagnosed from the DB, not guessed):** nothing was broken. Since the Nov 15 remap Notebook was sent 6 candidate cards (all `hacks_exploits`, ~1.1/day, each delivered with a Telegram message id); `gems_security`
contributed 0 (best score 48.7 against 50). None of the 11 Notebook approvals since Nov 5 was ever tapped (no approve, reject or Edit), so no draft was ever requested. Drafts do NOT fail on the 600-900 voice: a dry run of the real writer on all six
passed every guard (attempt 1 overshoots at 950-1,200 characters, one retry fixes it). `hacks_exploits` supply is ~6 incidents a week at the $500k floor: that is the ceiling.

**Built 2026-11-20 (operator request):**
1. **Technique of the week** (`collectors/technique_explainer.py`, category `technique_explainer`, Crypto Notebook): ONE explainer per ISO week on an attack type = DefiLlama's own `classification` (13 values, each with 10+ stored incidents), with
   three real incidents as examples (largest, most recent, largest with a different technique label) taken from the stored tracker rows. No new source, no model call in the collector. Type choice is deterministic (never covered / covered longest ago first, then the
   newest incident); the item's external_id is the ISO week. The writer (Notebook's explainer voice, 600-900) may cite ONLY the incidents, amounts, counts and years in the facts: `write_post._find_explainer_issues` refuses any other dollar figure
   (a rounding within 6% is allowed), incident count or year, and requires two examples to be named; the shared speculation guard applies. Scores are priors, sized so every eligible type clears the threshold (pinned by `scripts/check_technique.py`).
   A recovery figure larger than the loss (Euler V1: $240M back on $197M, a tracker quirk) is never printed. First item: the 12:00 UTC cycle of 2026-11-20 (type: Token & Share Accounting). Rotation covers all 13 types in 13 weeks.
2. **Finished drafts on Notebook cards** (`bot/notify.py` `DRAFT_FIRST_CHANNELS = {"crypto_notebook"}`): the post is written BEFORE the card, the card IS the draft (own message, buttons Approve / Edit / Skip, same callbacks as always), the text is stored in
   `approvals.draft_text` (column applied to production) and Approve sends exactly that text as the forwardable preview (no second model call). A candidate whose draft cannot be written (a guard keeps failing, model outage, over 3,900 characters) goes out as the
   ORDINARY card and is written on Approve as before: nothing is lost. To turn it off, empty the set. To add a channel, add it to the set.


Everything below is committed and pushed; HEAD passes `check_earning`, `check_sources`, `check_exchange_listings`, `check_voice_checks`. The operator's 2026-11-17 message REPLACES his earlier
"remove the recruitment penalty" and "full control" prompts: it is self-contained and is what is implemented. (A dangling empty bullet at the end of its item 1 was ignored.)

### DONE (live on Railway after the push; the new sources' first cycle is the 08:30 UTC earning run)
1. **Recruitment / referral.** The recruitment-shaped trust penalty and the Raenest-style referral-promotion detection are REMOVED entirely. A stated per-referral earning ("$15 per friend", "up to $15 for each
   friend") is read as the offer's PAYOUT when the offer states no other, scored like any payout. Multi-level wording (downline, MLM) is a WARNING LABEL on the card and in the digest, with NO score effect.
   The referral availability bonus (<= 12 value points) stays. **The recruitment penalty was the assistant's recommendation; the operator overrode it (2026-11-16 once, 2026-11-17 for good). It must NOT be restored.**
   The Raenest bounty was un-expired and its approval put back to pending.
2. **Nothing disappears silently.** Floors, caps and the one-card-per-cycle limit are kept. The daily below-the-line digest (`bot/below_line.py`) now lists every soft-floor failure AND every hard-gated item whose
   reasons are security-flavoured (seed-phrase, connect-wallet, upfront-fee wording, a GoPlus-flagged contract) with a RED label and the gate's reason; each has Approve. The expected-payout floor applies to RAFFLES
   only (a guaranteed $2 deposit bonus passes); a competition keeps its own largest-prize floor. **Roundup posts are expanded**: each block with its own link becomes an offer that goes through the ordinary gate,
   scoring and dedup (`extract_roundup_offers`); a roundup with no linked block stays excluded. Kept: no discounts / free games (not-income gate), the source registry, the Telegram spam filter, dedup. A plain
   deposit is never a block (the fee gate matches fees, not deposits) and an offer that asks for a deposit, is a bank / exchange sign-up, or has a referral program gets a small "monetizable" value bonus (5 points,
   label "💸 monetizable").
3. **Writer.** Guards against invented facts and copied text are unchanged. When an earning draft still fails them after its retries, the poller sends a PLAIN FACTS card (`pipeline/fallback_card.py`, no model, no URL,
   no referral wording) as an ordinary preview; every preview now has an Edit button (reply with the final text, the preview is replaced in place).
4. **Sources** (each verified from Railway FIRST; only fresh ones built). See BACKLOG "Earn Stack sources, round 2".

### STILL TO DO / OPEN
- Read the first Railway cycles of the new earning sources (`collect_earning_opportunities` details.sources: `binance_campaigns`, `kucoin_campaigns`, `galxe`, the five new Telegram channels, OKX / Bybit announcements)
  and the first digest that carries red items. Galxe and the open Telegram channels are tier 3: almost all of their items fail the trust floor and land in the digest, which is intended; watch the digest size.
- Nov 23 09:00 local: the scheduled draft-attempts report (`earn-stack-draft-attempts-nov23`). Decision rule: if most cards need 4+ attempts, widen the 400-750 band.
- Discord (waits on the operator's server and bot), Bybit from Railway (403), AirdropAlert's real feed URL (its `rss-feed` page answers 200 from Railway but is HTML; it 429s from a dev machine).
- startup_jobs name link on startup_news: built, expected ~0-1 a month. `scripts/check_reproduction_masking.py` reports 2 PROBLEM cases that predate all of this.

## What this is

A pipeline that collects items, scores them, sends the operator a candidate card on Telegram, and on **Approve** writes the post (DeepSeek) and sends a forwardable preview.
**The bot never posts to a channel**: the operator copies / forwards the text himself (`pipeline/publish.py`). Nothing reaches a reader without his tap.

Stack: Python on Railway, Supabase Postgres, DeepSeek (`deepseek-v4-flash` writer; `deepseek-flash` + the `deepseek_read_url` tool for landing-page reads), Telegram bot polling.

## Railway services (project `fortunate-success`, production)

| Service | What | Schedule |
|---|---|---|
| `crypto-content-bot` | always-on poller (approve / edit / reject taps, previews) | always on; redeploys on every push (~60-90 s gap) |
| `collect-cycle` | `scripts/collect_cycle.py`: every collector -> score -> notify for ALL channels | cron `0 */4 * * *` |
| `collect-earning` | `scripts/collect_earning.py`: earning_opportunities only -> notify Earn Stack, then the below-the-line digest (once a day), source probes | cron `30 */2 * * *` |

IaC is `.railway/railway.ts`; `railway config apply` is authoritative. A cron service must EXIT. A service is not "running" until it has a `run_logs` row: `collect-earning` ran
twice doing nothing because its `DATABASE_URL` was the placeholder `db.xxxxxxxxxxxx.supabase.co`. `railway ssh` is not used (needs a registered key).
**Verify every source FROM RAILWAY** (a dev machine and Railway see different data: OKX served a regional feed, Bybit and Coinbase Learn answer 403 from Railway). Daily probes are in
`run_logs` jobs `source_probe` and `telegram_probe` (`pipeline/source_probe.py`); both re-run whenever their candidate set changes.

## Channels -> categories (config/channel_config.yaml; `chat_id`s live there)

| Key | Displayed as | Categories (daily caps) |
|---|---|---|
| `crypto_wall_street` | Crypto Wall Street | news (shared with Alpha Edge), whale_movements, exchange_listings (cap 4) |
| `alpha_edge_crypto` | Alpha Edge Crypto | news (shared), defi_yields |
| `crypto_notebook` | Crypto Notebook | gems_security, hacks_exploits |
| `hustle_to_million` | Hustle to Million (cap 6) | tool_launches **2**, startup_jobs **2**, macro_news **1**, startup_news **1** (a category's own cap IS its share) |
| `coincraft` | **Earn Stack** (renamed from CoinCraft 2026-11-16; the key and chat_id never changed) | earning_opportunities (cap 8; competitions at most 2/day, lowest priority) |

Voices: `config/channel_voices.yaml` (one profile per channel; the category supplies facts + guardrails, the channel supplies voice; guardrails beat voice). Config is YAML in git, upserted
into the DB by `scripts/seed_config.py` (`seed_categories`, `seed_channels`, `seed_voices`); the DB row is what runs.

## Sources by category (the Telegram / RSS sources added 2026-11-16 were verified from Railway on 2026-11-17)

- **news**: CoinDesk, The Block, Decrypt, Unchained, The Defiant, Protos (RSS) + **Wu Blockchain** (`@wublockchainenglish`, tier 3) + **Cointelegraph** (`@cointelegraph`, tier 2). Cointelegraph reverses the
  operator's 2026-09-11 exclusion (deliberate, 2026-11-16). Telegram posts go through the same dedup, a text-based opinion / sponsored / promo gate, and the copyright guard.
- **whale_movements**: Etherscan wallet watch + **Whale Alert** (`@whale_alert_io`): transfers only (never mints, burns, news posts, or a transfer with no exchange side), parsed deterministically,
  **deduped by tx hash both ways**. Adds BTC and other chains.
- **hacks_exploits**: DefiLlama `/hacks` + **ZachXBT** (`@investigations`, tier 3): only a post stating a loss with an amount >= $500k; the claim is attributed to him and never stated as confirmed;
  deduped against DefiLlama by protocol name.
- **exchange_listings**: Binance / OKX / Bybit / KuCoin announcement APIs (spot only; Bybit is blocked from Railway). **Binance Announcements** (`@binance_announcements`) = corroboration only: it sets a flag, it never creates an item.
- **earning_opportunities** (income only): Devpost, Doctor of Credit, Superteam Earn, Zindi, Immunefi audit competitions, four airdrop Telegram channels. See "Earn Stack rules".
- **startup_news** (Phase 6): Tech.eu, Sifted, TechCrunch Venture RSS. Funding rounds with a STATED amount and acquisitions only; amounts shown exactly as the outlet wrote them.
- **Rejected on purpose (do not re-propose without new facts)**: Cryptopolitan (half price-bot posts, ~60 views), ICO Drops (news, not offers), CryptoRank / CryptoQuant (analysis, no category), CoinMarketCap News / TON Community /
  CertiK (no live public channel found), @tech (1 funding amount in 123 posts), Galxe, exchange campaigns (KuCoin/Binance/Bitget), bug-bounty platform feeds (kept in BACKLOG "available if volume disappoints").

## Earn Stack rules (operator-set; the scoring is deliberate)

1. **Two scores, never blended**: `value` (what it is worth) and `trust` (checkable facts). Selection = trust >= 40 AND value >= 40 AND (for a RAFFLE expected payout >= $10; for a competition the largest prize >= $100; guaranteed payouts have no payout floor; UNKNOWN passes and scores 20-25 on the payout component). Order by value. Both numbers, plus referral / KYC / skill facts, are on every card.
2. **Award kind** (`pipeline/prizes.py`): `guaranteed`, `raffle` (pool / entrants is meaningful: the only kind with an expected amount), `competition` (prize structure + odds shown, no expected amount, e.g.
   "Top 3 share €25,000 (€12k / €8k / €5k) · ~142 entrants · 3 winners"). **Amounts are ALWAYS shown in the original currency, never converted.** Devpost's `prizes_counts.cash` is NOT a winner count (it counts prize categories).
3. **Hard gate** (item stored HELD, never a normal card, but SHOWN in the daily digest with a red label when its reasons are security-flavoured): seed phrase / key, connect-wallet-to-claim, upfront-fee wording,
   a GoPlus-flagged contract. Still excluded entirely: not income (free games, freebies, credits, discounts), spam, a roundup (its offers are extracted instead). The wallet rule is NARROW (claim-shaped only) for exactly
   `superteam` / `zindi` / `immunefi`; broad for every relay. **No forgery / KYC-evasion gate: the operator handles those by hand.**
4. **Referral**: counts in value (<= 12 points, added on top); availability is read from the offer TEXT only (a code on the source's own link is "unconfirmed"); referral parameters are still stripped from every stored link
   (the operator adds his own). A stated per-referral earning is the PAYOUT when no other is stated. Multi-level wording is a warning LABEL, no score effect. **No recruitment penalty and no referral-promotion detection: both
   were removed at the operator's order and must not be restored.**
5. **Token-denominated reward** (not a stablecoin): an unclear amount, never priced, value capped at 30 (below the floor).
6. **Selection**: non-competitions first; competitions only fill slots nothing else wants, at most 2 per rolling 24h. One card per 2-hour cycle.
7. **Daily "below the line" digest** (`bot/below_line.py`): every item that failed a SOFT floor plus every security-gated item (red), with scores + reason + an Approve button into the normal write flow. Outside the cap
   (`notifications.kind = 'below_line'`; every cap query counts only `kind = 'candidate'`). `python -m bot.below_line --dry-run --hours 72` previews it. The expected-payout floor is for RAFFLES only.
8. Cards: curated-find voice (a "why this one" clause drawn ONLY from the facts' "what stands out" line), band 400-750 characters, 9 retries. `earning_draft` rows in `run_logs` count attempts.
9. Any calibration data on this category from before 2026-11-16 is SUSPECT (a parser that could not read "50$" or Russian / Portuguese deposits, before the referral bonus and the competition change).

## Standing operator rules (all categories)

- Flag mistakes and surprises unprompted; confirm a plan before a big build; do not widen sources when you can tighten floors; near misses leave a floor alone; "unknown is never coded as clear".
- Never state an inference as a fact on a card. Never relabel or convert currency. Never reproduce a source's wording (copyright guard). No investment advice anywhere.
- A source is not verified until it has run from Railway. Dead sources flag themselves (stale-feed alerts); do not chase the ones already recorded as dead.
- Hustle shares of its cap of 6 are 2 / 2 / 1 / 1; the channel cap rarely has to choose.

## Open items / dates

- **2026-11-23 09:00 local**: a scheduled one-time task (`earn-stack-draft-attempts-nov23`) runs `python scripts/earning_report.py --drafts --days 7` and REPORTS mean attempts, the share needing 4+, and any source that keeps
  failing. Decision rule: if most cards need 4+ attempts, WIDEN the 400-750 band rather than keep retrying. It only recommends.
- **Discord** (Earn Stack): waits on the operator creating the server and bot.
- Bybit from Railway (403): honest "not tracked after <date>" wording, a non-US proxy, or drop it: undecided.
- Penalty-ignores-odds for giveaways (a design question), a competition size floor of $100 (the assistant's choice, one constant: `prizes.COMPETITION_MIN_TOP_PRIZE_USD`), the connect-wallet gate beyond the three hosted platforms: all in BACKLOG.
- startup_jobs name link on startup_news: built strictly, expected ~0-1 a month; verify it ever fires before investing more.
- `scripts/check_reproduction_masking.py` reports 2 PROBLEM cases and did so before the 2026-11-16 changes (it reads real DB items); not caused by them, not investigated.

## Traps (the expensive ones)

- **Row-Level Security is ON for every public table, with no policies (2026-11-20, Supabase security advisory).** `DATABASE_URL` connects as the `postgres` role (table owner, BYPASSRLS), so the bot is unaffected; the Supabase REST API (anon / authenticated keys) can read and write nothing. Never switch the bot to the anon or service-role keys, never add `FORCE ROW LEVEL SECURITY`, and keep the RLS `DO` block LAST in `db/schema.sql` (it loops over all public tables, so new tables are covered on the next `scripts/apply_schema.py`). The git history (124 commits, all refs, unreachable commits) was scanned: no Supabase key, project URL or real `DATABASE_URL` was ever committed.

- **Python in a bash heredoc halves backslashes** on this Windows setup: `\\n` becomes a real newline, `\\b` a backspace character. Write regex / string edits with the Edit or Write tool, or a script file; then `grep -c $'\x08' file`
  and `python -c "import ast; ast.parse(...)"`. This corrupted five files before it was caught.
- Local DNS can drop (scripts fail); `railway config plan` fails on an IaC CLI-version check (not applied); `SIGALRM` does not exist on Windows; DeepSeek's balance lags by minutes (see BACKLOG READ FIRST).
- Telegram `t.me/s/<handle>` shows only the newest ~20 posts; a handle that answers 200 may be a hijacked or inactive channel (CoinMarketCapNews): check the newest post date AND the title.

## How to check things

```
python scripts/check_earning.py       # earning scoring / gate / prizes / digest / selection (no DB, no network)
python scripts/check_sources.py       # Whale Alert / news Telegram / ZachXBT / Binance corroboration / startup_news / Hustle shares
python scripts/check_exchange_listings.py ; python scripts/check_voice_checks.py
python scripts/earning_report.py [--unproven | --drafts --days 7]
python -m bot.below_line --dry-run --hours 72
python -m pipeline.source_probe [probe ids]          # local; Railway runs the same list daily
```
Railway: `railway logs --service collect-earning --lines 100`; `railway variables --service <name> --kv` (never print secrets); the DB is the same Supabase for all services, so a local script that writes is a production write.

## Where the history is

`BACKLOG.md` sections, in order of build: Phase 0-4 (reliability, voices, channel remap, exchange_listings), Phase 5 (earning_opportunities, rounds 1-9, the source research, the platform sources, referral, competitions),
"Channels and Phase 6" (this session's last block).
