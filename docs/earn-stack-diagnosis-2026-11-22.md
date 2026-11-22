# Earn Stack: why every card came out empty (diagnosis of the last 10 cards, 2026-11-22)

Each card: (1) the full original source post, (2) what the page reader fetched from the offer's link and whether it failed, (3) exactly the facts the writer received (captured from the OLD facts builder as it was on the morning of 2026-11-22, run on each stored payload; the stored `enriched` is what the reader returned at write time), (4) what was dropped and why.

Note: the stored source post (`payload.description`) was capped at 1,200 characters by the collector, so a long post (the TransferGo guide) is cut mid-word below; the cap is now 4,000.

## Preview 292 (raw item 3180922, tg:criptinho, writer: deepseek-v4-flash)

**Title as collected:** 💵 Крутая акция от приложения TransferGo, дают 20€ вам за первый перевод и до 25€ за каждого реферала (довольно
**Post link:** https://t.me/criptinho/20399  **Landing link:** https://trgo.co/uk

### 1. The original source post (full, as stored)

```text
💵 Крутая акция от приложения TransferGo, дают 20€ вам за первый перевод и до 25€ за каждого реферала (довольно таки жирная награда учитывая что платформа работает не первый год и имеет много пользователей).

 ➡️ Приступим:

 🟢 Переходите по ссылке и принимаете приглашение: ссылка 
 🟢 Проходите регистрацию.
 🟢 Нажимаете на свой профиль, переходите в настройки и проходите быструю верификацию личности в разделе "документы" (занимает 1 минуту времени).
 🟢 Делаете перевод от 50€ какому-то знакомому в другою страну (вам дадут 20€).
 🟢 Копируете реф ссылку и приглашаете друзей (за каждого дадут 15€ сразу и 10€ когда сумма переводов будет больше 150€).

 ⚠️ Главное что нужно знать: 

 🟠 Человек который вас пригласил не может переводить деньги вам а вы не можете переводить ему, для получения бонуса вы должны совершить перевод третьему лицу, к примеру родственнику или знакомому.
 🟠 Вы должны быть новым пользователем и у вас не должно быть ранее созданного аккаунта.
 🟠 Для получения бонуса перевод должен быть обязательно в другую страну с конвертацией валюты, то есть вы не можете отправить $ с Польши в Украину, вам нужно отправлять к примеру злоты с Польши на гривны в Украину, так же и с друг
```

### 2. What the page reader fetched

Reader FAILED: `trgo.co/uk` is a referral short link that lands on TransferGo's Ukrainian home page; the URL reader returned nothing and nothing else was tried (stored as `{}`).

Stored `enriched` at write time (`{}` = the reader failed):

```json
{}
```

### 3. The facts the writer received

```text
Facts about this opportunity (collected automatically; the reader's own checks still apply):
- What: 💵 Крутая акция от приложения TransferGo, дают 20€ вам за первый перевод и до 25€ за каждого реферала (довольно
- Kind: airdrop
- Posted by: Criptinho (Telegram)
- Reward: stated as €20
- What it costs or requires: nothing is stated as required up front
- Deadline: not stated
- Who can take part: countries not stated
- Other requirements: none stated
- What stands out (give ONE or TWO of these as the reason this one is worth a look; use only these, never invent a reason): nothing is stated as required up front
- Participation steps (none available):
  NOT AVAILABLE -- the offer's own page could not be read. Write NO steps; tell the reader the steps are on the offer's page.
```

### 4. What was dropped, and why

EVERYTHING in the post: the 5 numbered steps (register, ID check in the documents section, a transfer of at least 50 EUR to someone abroad, ...), the 'new users only' rule, the 'transfer to a third party, to another country, with currency conversion' rules, the 15 EUR + 10 EUR referral terms. The facts builder never reads the post's text. It also turned the default `capital_usd = 0.0` into "nothing is stated as required up front" while the post requires a 50 EUR transfer and an ID check.

Guards: No guard dropped a fact. The brief told the writer to 'say plainly that it is not stated' and the voice to say 'the steps are on the offer's page', so it wrote filler; the guards would also have rejected any step or amount not in the facts, so even a writer that had read the post could not have used it.

### The card that went out

```text
🎁 <b>Get €20 from TransferGo for your first transfer</b>

TransferGo gives €20 for your first transfer. Who can take part and the deadline are not stated. Nothing is stated as required up front, which is why this one is worth a look.

The steps are on the offer's page. The page does not list them here, so check it for how to take part.

Nothing is stated as required up front. Countries, deadline and other limits are not stated. Check the offer's page before you start.

Source: Criptinho (Telegram)

#EarnOnline #Rewards
```

## Preview 265 (raw item 3093018, tg:airdrops_io, writer: deepseek-v4-flash)

**Title as collected:** 🪂 100M $MASK Airdrop & huge ref rewards 
**Post link:** https://t.me/airdrops_io/8058  **Landing link:** https://app.nullmask.io

### 1. The original source post (full, as stored)

```text
🪂 100M $MASK Airdrop & huge ref rewards 

 We covered the 100M $MASK airdrop, but missed one detail... 

 NullMask gives you 20% of the deposit fees your referrals pay, plus 10% of the points they earn. They keep all of their own points. 

 🎯 Ref Setup 
 1️⃣ Grab your link from the Nullmask Referrals page. 
 2️⃣ Share it before your friends deposit to earn 20% of their deposit fees + 10% of their points.
 3️⃣ Link your Solana wallet on the Points page for the $MASK payout. 

 💸 S eason 1 runs until Jan 17. If your friends are already farming, send them your link before they deposit. 

 🔗 Full guide
```

### 2. What the page reader fetched

Reader FAILED: `app.nullmask.io` is a JS shell (plain fetch: 23 characters, 'Loading'); the URL reader returned nothing.

Stored `enriched` at write time (`{}` = the reader failed):

```json
{}
```

### 3. The facts the writer received

```text
Facts about this opportunity (collected automatically; the reader's own checks still apply):
- What: 🪂 100M $MASK Airdrop & huge ref rewards 
- Kind: airdrop
- Posted by: AIRDROPS.IO (Telegram)
- Reward: not stated
- What it costs or requires: nothing is stated as required up front
- Deadline: Jan 17
- Who can take part: countries not stated
- Other requirements: none stated
- What stands out (give ONE or TWO of these as the reason this one is worth a look; use only these, never invent a reason): 56 days left to take part; nothing is stated as required up front
- Participation steps (none available):
  NOT AVAILABLE -- the offer's own page could not be read. Write NO steps; tell the reader the steps are on the offer's page.
```

### 4. What was dropped, and why

The post's own 3-step setup (grab the link from the Referrals page, share it before friends deposit, link a Solana wallet on the Points page for the $MASK payout) and the 20% / 10% reward. Facts: 'Reward: not stated', 'steps: NOT AVAILABLE'.

Guards: The facts builder also removes any step that mentions a referral (by design: the operator adds referral links himself), so even a successful read would have lost steps 1-2; the non-referral step (wallet link) and the reward were lost only because the post was never read.

### The card that went out

```text
🎁 <b>Join a 100M $MASK token airdrop, open until Jan 17</b>

You can take part in a 100M $MASK token airdrop. The reward amount is not stated. Countries and requirements are not stated. The deadline is Jan 17, with 57 days left. Nothing is stated as required up front.

The steps are on the offer's page. Open the link to see how to take part.

Nothing is stated as required up front, but check the offer's page for any costs, ID checks, or limits. The reward amount, eligible countries, and other requirements are not stated.

Source: AIRDROPS.IO (Telegram)

#EarnOnline #Rewards
```

## Preview 263 (raw item 3071251, tg:OKXAnnouncements, writer: deepseek-v4-flash)

**Title as collected:** Bought GTA 6 with your OKX Card? 
**Post link:** https://t.me/OKXAnnouncements/7430  **Landing link:** https://web3.okx.com/ul/3mkWxk?channelid=ACE505900

### 1. The original source post (full, as stored)

```text
Bought GTA 6 with your OKX Card? 

Trade tokenized market spotlight stocks on our DEX for a chance to get a share of 180K USDG in rewards.

Eligible @xStocksFi tokenized stocks: NVDAx, TSLAx, AAPLx, AMZNx, TTWOx, QQQx, SPYx

 👉 Join now 

Ends Nov 27, 11am UTC.


Our Socials:

 ⚜️ Sign up | OKX Announcement | OKX Campaign | TG | DC | OKX Orbit | Join Affiliate | CN TG | CN DC 

 ‼️ Please check the authenticity of all OKX links or channels at OKX channel verification
```

### 2. What the page reader fetched

Reader FAILED: `web3.okx.com/ul/3mkWxk` is an OKX deeplink (plain fetch resolves it to the campaign page, 8,377 characters of terms); the URL reader returned nothing.

Stored `enriched` at write time (`{}` = the reader failed):

```json
{}
```

### 3. The facts the writer received

```text
Facts about this opportunity (collected automatically; the reader's own checks still apply):
- What: Bought GTA 6 with your OKX Card? 
- Kind: giveaway
- Posted by: OKX Announcements (Telegram)
- Reward: not stated
- What it costs or requires: nothing is stated as required up front
- Deadline: Nov 27
- Who can take part: countries not stated
- Other requirements: none stated
- What stands out (give ONE or TWO of these as the reason this one is worth a look; use only these, never invent a reason): nothing is stated as required up front
- Participation steps (none available):
  NOT AVAILABLE -- the offer's own page could not be read. Write NO steps; tell the reader the steps are on the offer's page.
```

### 4. What was dropped, and why

The post: the reward pool (180K USDG), the eligible tokens (NVDAx, TSLAx, AAPLx, AMZNx, TTWOx, QQQx, SPYx), 'Ends Nov 27, 11am UTC'. Facts: 'Reward: not stated'. Only the deadline survived, from a structured field.

Guards: No guard dropped a fact; 'Countries and requirements are not stated' came from the brief.

### The card that went out

```text
🎁 <b>Win a GTA 6 giveaway with your OKX Card, deadline Nov 27</b>

OKX is running a giveaway for people who bought GTA 6 with their OKX Card. The reward is not stated. Countries and requirements are not stated. The deadline is Nov 27. Nothing is stated as required up front.

The steps are on the offer's page. Open the link to see what to do.

What it costs or requires is not stated. Limits are not stated. Check the offer's page for details before you take part.

Source: OKX Announcements (Telegram)

#EarnOnline #Rewards
```

## Preview 253 (raw item 2998809, kucoin_campaigns, writer: deepseek-v4-flash)

**Title as collected:** infiniFi (INFI) Listing Campaign — 46,000 INFI Giveaway
**Post link:** https://www.kucoin.com/announcement/en-infinifi-infi-listing-campaign-46000-infi-giveaway?lang=en_US  **Landing link:** https://kucoin.com/announcement/en-infinifi-infi-listing-campaign-46000-infi-giveaway?lang=en_US

### 1. The original source post (full, as stored)

```text
infiniFi (INFI) Listing Campaign — 46,000 INFI Giveaway. infiniFi (INFI) Listing Campaign — 46,000 INFI Giveaway
```

### 2. What the page reader fetched

Reader OK (5 steps, reward, deadlines, eligibility).

Stored `enriched` at write time (`{}` = the reader failed):

```json
{"cost": null, "from": "landing_page", "steps": ["Complete KYC verification to participate in the event.", "Click \"Join Now\" to participate in Activity 1.", "Trade at least $250 spot trading volume of INFI during the campaign period for the Lucky Draw.", "Achieve a minimum spot trading volume of $600 in INFI/USDT during the campaign period for the Trading Carnival.", "Complete the learning content, quiz, and designated tasks related to infiniFi (INFI) for Activity 2."], "reward": "46,000 INFI prize pool", "deadline": "Activity 1: 15:30 on November 24, 2026 (UTC); Activity 2: 08:00 on December 3, 2", "eligibility": "Complete KYC verification to participate in the event. Market maker accounts and institutional accounts are not eligible to participate in this campaign. Sub-accounts are not eligible."}
```

### 3. The facts the writer received

```text
Facts about this opportunity (collected automatically; the reader's own checks still apply):
- What: infiniFi (INFI) Listing Campaign — 46,000 INFI Giveaway
- Kind: giveaway
- Posted by: KuCoin campaigns
- Reward: 46,000 INFI prize pool
- What it costs or requires: nothing is stated as required up front
- Deadline: Activity 1: 15:30 on November 24, 2026 (UTC); Activity 2: 08:00 on December 3, 2
- Who can take part: Complete KYC verification to participate in the event. Market maker accounts and institutional accounts are not eligible to participate in this campaign (more on the page)
- Other requirements: none stated
- What stands out (give ONE or TWO of these as the reason this one is worth a look; use only these, never invent a reason): nothing is stated as required up front
- Participation steps (from the offer itself):
  1. Complete KYC verification to participate in the event.
  2. Click "Join Now" to participate in Activity 1.
  3. Trade at least $250 spot trading volume of INFI during the campaign period for
  (the offer has further steps -- say the rest are on the offer's page; never write them yourself)
```

### 4. What was dropped, and why

Step 3 cut at 80 characters ('Trade at least $250 spot trading volume of INFI during the campaign period for'), steps 4-5 dropped (MAX_FACT_STEPS = 3), the second deadline cut mid-date ('December 3, 2'), the $600 volume of the Carnival dropped, eligibility cut at 170 characters. 'What it costs: nothing is stated as required up front' (default capital 0) although the page needs KYC and $250 of volume.

Guards: No guard dropped a fact; the facts builder's own truncation did.

### The card that went out

```text
🎁 <b>Enter a 46,000 INFI prize pool on KuCoin, KYC required, until Nov 24</b>

KuCoin runs an infiniFi (INFI) listing campaign with a 46,000 INFI prize pool. KYC verification is required. Market maker and institutional accounts cannot join. Activity 1 ends 15:30 UTC on November 24, 2026. Nothing is stated as required up front.

1. Complete KYC verification.
2. Click "Join Now" for Activity 1.
3. Trade at least $250 spot volume of INFI. The rest of the steps are on the offer's page.

You need KYC, which means identity checks, and $250 in INFI spot volume. Activity 2 ends 08:00 on December 3, 2026. Other limits and prize details are not stated here.

Source: KuCoin campaigns

#EarnOnline #Rewards
```

## Preview 248 (raw item 2991572, tg:Bybit_Announcements, writer: deepseek-v4-flash)

**Title as collected:** 🔴 We’re live! 
**Post link:** https://t.me/Bybit_Announcements/10123  **Landing link:** https://i.bybit.com/10swab23

### 1. The original source post (full, as stored)

```text
🔴 We’re live! 

Join Rockman Zhang and Eric Li for WSOT 2026 × Bybit AI: Trade the future. See Bybit AI in action with live demos and audience Q&A.

 🎁 2,500 USDT in red packets and chances to win an iPhone 18 for eligible viewers.
```

### 2. What the page reader fetched

Reader OK (6 steps, reward, cost '$100 deposit within 7 days', eligibility).

Stored `enriched` at write time (`{}` = the reader failed):

```json
{"cost": "New users must deposit at least $100 within 7 days after receiving the red packets", "from": "landing_page", "steps": ["Register now to grab your 20 USDT coupon", "Register and claim 50 USDT welcome gifts", "Tune in live to grab 2,500 USDT in red packet rewards + an iPhone 18", "New users must sign up to Bybit within 3 days", "New users must deposit at least $100 within 7 days after receiving the red packets", "All users must register for WSOT to be eligible for the online and physical rewards"], "reward": "20 USDT coupon; 50 USDT welcome gifts; 2,500 USDT in red packet rewards + an iPhone 18", "deadline": null, "eligibility": "Only Bybit users with a valid UID; must provide a valid email address; open globally except residents of Canada (Ontario and Quebec), Singapore, the United States, the Chinese Mainland, North Korea, C"}
```

### 3. The facts the writer received

```text
Facts about this opportunity (collected automatically; the reader's own checks still apply):
- What: 🔴 We’re live! 
- Kind: giveaway
- Posted by: Bybit Announcements (Telegram)
- Reward: 20 USDT coupon; 50 USDT welcome gifts; 2,500 USDT in red packet rewards + an iPhone 18
- What it costs or requires: New users must deposit at least $100 within 7 days after receiving the red packets
- Deadline: not stated
- Who can take part: Only Bybit users with a valid UID; must provide a valid email address; open globally except residents of Canada (Ontario and Quebec), Singapore, the United States (more on the page)
- Other requirements: none stated
- What stands out (give ONE or TWO of these as the reason this one is worth a look; use only these, never invent a reason): nothing is stated as required up front
- Participation steps (from the offer itself):
  1. Register now to grab your 20 USDT coupon
  2. Register and claim 50 USDT welcome gifts
  3. Tune in live to grab 2,500 USDT in red packet rewards + an iPhone 18
  (the offer has further steps -- say the rest are on the offer's page; never write them yourself)
```

### 4. What was dropped, and why

Steps 4-6 (sign up within 3 days, deposit at least $100 within 7 days, register for WSOT) dropped by MAX_FACT_STEPS = 3, replaced by 'the rest are on the offer's page'; eligibility cut at 170 characters (the exclusion list); 'Deadline: not stated'.

Guards: No guard dropped a fact.

### The card that went out

```text
🎁 <b>Get a 20 USDT coupon and a shot at 2,500 USDT on Bybit</b>

Bybit is running a giveaway. You can get a 20 USDT coupon, 50 USDT in welcome gifts, and a chance at 2,500 USDT in red packets plus an iPhone 18. It is open globally except Ontario and Quebec in Canada, Singapore, and the US. No deadline is stated. Nothing is stated as required up front.

1. Register to grab your 20 USDT coupon.
2. Register and claim 50 USDT welcome gifts.
3. Tune in live for red packets and an iPhone 18.
The rest of the steps are on the offer's page.

New users must deposit at least $100 within 7 days after receiving the red packets. You need a valid Bybit UID and email address. The deadline is not stated.

Source: Bybit Announcements (Telegram)

#EarnOnline #Rewards
```

## Preview 246 (raw item 2984150, tg:OKXAnnouncements, writer: deepseek-v4-flash)

**Title as collected:** Stand a chance to share 250K USDG worth of rewards when you provide liquidity to eligible pools across tokeniz
**Post link:** https://t.me/OKXAnnouncements/7398  **Landing link:** https://web3.okx.com/ul/i53NnS?channelid=ACE505900

### 1. The original source post (full, as stored)

```text
Stand a chance to share 250K USDG worth of rewards when you provide liquidity to eligible pools across tokenized equities, precious metals, major crypto assets & stablecoins.

 👉 Join now 

Ends Dec 1, 7am UTC.


Our Socials:

 ⚜️ Sign up | OKX Announcement | OKX Campaign | TG | DC | OKX Orbit | Join Affiliate | CN TG | CN DC 

 ‼️ Please check the authenticity of all OKX links or channels at OKX channel verification
```

### 2. What the page reader fetched

Reader FAILED: `web3.okx.com/ul/i53NnS` deeplink.

Stored `enriched` at write time (`{}` = the reader failed):

```json
{}
```

### 3. The facts the writer received

```text
Facts about this opportunity (collected automatically; the reader's own checks still apply):
- What: Stand a chance to share 250K USDG worth of rewards when you provide liquidity to eligible pools across tokeniz
- Kind: giveaway
- Posted by: OKX Announcements (Telegram)
- Reward: not stated
- What it costs or requires: nothing is stated as required up front
- Deadline: Dec 1
- Who can take part: countries not stated
- Other requirements: none stated
- What stands out (give ONE or TWO of these as the reason this one is worth a look; use only these, never invent a reason): nothing is stated as required up front
- Participation steps (none available):
  NOT AVAILABLE -- the offer's own page could not be read. Write NO steps; tell the reader the steps are on the offer's page.
```

### 4. What was dropped, and why

The post: 'share 250K USDG ... provide liquidity to eligible pools across tokenized equities, precious metals, major crypto assets and stablecoins', 'Ends Dec 1, 7am UTC'. Facts: 'Reward: not stated', 'steps: NOT AVAILABLE'.

Guards: No guard dropped a fact.

### The card that went out

```text
🎁 <b>Share 250K USDG in rewards by providing liquidity to eligible pools, until Dec 1</b>

OKX is running a giveaway where you can share 250K USDG in rewards by providing liquidity to eligible pools. The deadline is Dec 1. Countries and requirements are not stated. Worth a look because nothing is stated as required up front.

The steps are on the offer's page. The link does not list them, so check the page for how to take part.

Nothing is stated as required up front, but check the offer page for any costs, limits or eligibility rules. The reward amount per person is not stated.

Source: OKX Announcements (Telegram)

#EarnOnline #Rewards
```

## Preview 245 (raw item 2984132, kucoin_campaigns, writer: deepseek-v4-flash)

**Title as collected:** KuCoin PROOF: Tis the Season — Win Your Share of 550,000 USDT
**Post link:** https://www.kucoin.com/announcement/PROOFTisTheSeasonFuturesContest?lang=en_US  **Landing link:** https://kucoin.com/announcement/PROOFTisTheSeasonFuturesContest?lang=en_US

### 1. The original source post (full, as stored)

```text
KuCoin PROOF: Tis the Season — Win Your Share of 550,000 USDT. KuCoin PROOF: Tis the Season — Win Your Share of 550,000 USDT
```

### 2. What the page reader fetched

Reader FAILED on this run (the same page read fine as a plain fetch: 4,530 characters: the PnL TraderPro Challenge, Daily Spin Wheel, New User Bonus for the first 1,000 users).

Stored `enriched` at write time (`{}` = the reader failed):

```json
{}
```

### 3. The facts the writer received

```text
Facts about this opportunity (collected automatically; the reader's own checks still apply):
- What: KuCoin PROOF: Tis the Season — Win Your Share of 550,000 USDT
- Kind: giveaway
- Posted by: KuCoin campaigns
- Reward: not stated
- What it costs or requires: nothing is stated as required up front
- Deadline: not stated
- Who can take part: countries not stated
- Other requirements: none stated
- What stands out (give ONE or TWO of these as the reason this one is worth a look; use only these, never invent a reason): nothing is stated as required up front
- Participation steps (none available):
  NOT AVAILABLE -- the offer's own page could not be read. Write NO steps; tell the reader the steps are on the offer's page.
```

### 4. What was dropped, and why

The description is only the title twice ('KuCoin PROOF: Tis the Season - Win Your Share of 550,000 USDT'), so nothing but the title reached the writer: 'Reward: not stated' (the 550,000 USDT pool was in the title), no deadline, no steps.

Guards: No guard dropped a fact.

### The card that went out

```text
🎁 <b>Enter KuCoin's Tis the Season giveaway for a share of 550,000 USDT</b>

KuCoin is running a giveaway called Tis the Season, with a prize pool of 550,000 USDT to be shared among winners. The reward per person, the deadline, and the countries that can take part are not stated. Nothing is stated as required up front, which is why this one is worth a look.

The steps to enter are on the offer's page. They are not listed here, so read the page before you do anything.

Nothing is stated as required up front, but check the page for any cost, ID checks, or limits. The deadline and eligible countries are not stated, so confirm them on the page. This is a giveaway, so treat it as a chance, not a payment.

Source: KuCoin campaigns

#EarnOnline #Rewards
```

## Preview 243 (raw item 2984131, kucoin_campaigns, writer: deepseek-v4-flash)

**Title as collected:** KuMining Black Friday: Mine More, Get More!
**Post link:** https://www.kucoin.com/announcement/en-kumining-black-friday-mine-more-get-more?lang=en_US  **Landing link:** https://kucoin.com/announcement/en-kumining-black-friday-mine-more-get-more?lang=en_US

### 1. The original source post (full, as stored)

```text
KuMining Black Friday: Mine More, Get More!. Unlock up to 72 USDT in rewards, earn exclusive bonuses, and get a chance to win an iPhone Duo, 1,000 USDT, Ledger Stax, and more!
```

### 2. What the page reader fetched

Reader OK but found ONE step ('Click Join Now') and the date range.

Stored `enriched` at write time (`{}` = the reader failed):

```json
{"cost": null, "from": "landing_page", "steps": ["Click \"Join Now\" to participate."], "reward": "Up to 72 USDT in Rewards", "deadline": "From 19:00 on November 16, 2026 to 19:00 on December 1, 2026 (UTC+8)", "eligibility": "This campaign is open to eligible KuCoin users."}
```

### 3. The facts the writer received

```text
Facts about this opportunity (collected automatically; the reader's own checks still apply):
- What: KuMining Black Friday: Mine More, Get More!
- Kind: giveaway
- Posted by: KuCoin campaigns
- Reward: Up to 72 USDT in Rewards
- What it costs or requires: nothing is stated as required up front
- Deadline: From 19:00 on November 16, 2026 to 19:00 on December 1, 2026 (UTC+8)
- Who can take part: This campaign is open to eligible KuCoin users.
- Other requirements: none stated
- What stands out (give ONE or TWO of these as the reason this one is worth a look; use only these, never invent a reason): nothing is stated as required up front
- Participation steps (from the offer itself):
  1. Click "Join Now" to participate.
```

### 4. What was dropped, and why

The description's prizes (an iPhone, 1,000 USDT, a Ledger Stax) were never passed (only the page's 'Up to 72 USDT'). 'Costs: nothing is stated as required up front' (the default).

Guards: No guard dropped a fact.

### The card that went out

```text
🎁 <b>Earn up to 72 USDT in KuMining Black Friday rewards, open to eligible KuCoin users until Dec 1</b>

KuMining's Black Friday campaign offers you up to 72 USDT in rewards. It is open to eligible KuCoin users, from 19:00 on November 16, 2026 to 19:00 on December 1, 2026 (UTC+8). Nothing is stated as required up front.

1. Click "Join Now" to participate.

Nothing is stated as required up front. Other requirements and limits are not stated. Check the offer page for details.

Source: KuCoin campaigns

#EarnOnline #Rewards
```

## Preview 240 (raw item 3027750, tg:OKXAnnouncements, writer: deepseek-v4-flash)

**Title as collected:** 🎉 Welcome to Growth & Apex S3 ! 
**Post link:** https://t.me/OKXAnnouncements/7422  **Landing link:** https://forms.gle/YwpAjWc6qoNYCL5c7

### 1. The original source post (full, as stored)

```text
🎉 Welcome to Growth & Apex S3 ! 

Join the season by contributing to the community in two tracks 👇 
Invite Rally
• Invite new users to join the server through unique Discord invite links
• A total reward pool of 150 USDT will be distributed based on invite performance

Path to Apex
Eligibility:
• Participate in #trend -polls voting daily to earn 5000 xp
• Use /checkin daily in #daily -check-in to earn 5000 xp
How to Unlock:
• Gain 30XP by chatting and staying active on the server
• Reach 500K XP to unlock the exclusive Apex ⚡ （gain extra 3 usdt in next event）

 🎁 Prize: 150 USDT
1st – 40 USDT
2nd – 30 USDT
3rd – 20 USDT
4th – 15 USDT
5th–7th – 10 USDT each
8th–10th – 5 USDT each

 📆 Event Period: 18/11/2026, 10:00 AM - 01/12/2026, 6:00 PM (GMT+8)
 👉 OKX Global Discord 
 👉 Join now 


Our Socials:

 ⚜️ Sign up | OKX Announcement | OKX Campaign | TG | DC | OKX Orbit | Join Affiliate | CN TG | CN DC 

 ‼️ Please check the authenticity of all OKX links or channels at OKX channel verification
```

### 2. What the page reader fetched

Reader OK: it followed the post's Google Form link (7 steps, the full prize ladder).

Stored `enriched` at write time (`{}` = the reader failed):

```json
{"cost": null, "from": "landing_page", "steps": ["Invite new members using your unique Discord invite link.", "Get your OKX Verified role.", "Vote daily in #trend-polls to earn 5000 XP.", "Use '/checkin' daily in #general-chat to earn 5000 XP.", "Chat and stay active to earn XP.", "Reach 500K XP to unlock the exclusive Apex role.", "Submit your responses with this Google Form with your OKX UID and Discord Username."], "reward": "150 USDT waiting for you to win! 1st – 40 USDT 2nd – 30 USDT 3rd – 20 USDT 4th – 15 USDT 5th–7th – 10 USDT each 8th–10th – 5 USDT each", "deadline": "1/12/2026 6:00 PM (GMT+8)", "eligibility": null}
```

### 3. The facts the writer received

```text
Facts about this opportunity (collected automatically; the reader's own checks still apply):
- What: 🎉 Welcome to Growth & Apex S3 ! 
- Kind: giveaway
- Posted by: OKX Announcements (Telegram)
- Reward: 150 USDT waiting for you to win! 1st – 40 USDT 2nd – 30 USDT 3rd – 20 USDT 4th – 15 USDT 5th–7th – 10 USDT each (more on the page)
- What it costs or requires: nothing is stated as required up front
- Deadline: 1/12/2026 6:00 PM (GMT+8)
- Who can take part: open worldwide
- Other requirements: none stated
- What stands out (give ONE or TWO of these as the reason this one is worth a look; use only these, never invent a reason): nothing is stated as required up front; open to readers anywhere
- Participation steps (from the offer itself):
  1. Get your OKX Verified role.
  2. Vote daily in #trend-polls to earn 5000 XP.
  3. Use '/checkin' daily in #general-chat to earn 5000 XP.
  (the offer has further steps -- say the rest are on the offer's page; never write them yourself)
```

### 4. What was dropped, and why

Steps 4-7 dropped (MAX_FACT_STEPS = 3): invite rally, 'chat to earn XP', 'reach 500K XP to unlock Apex', submit the Google Form with your OKX UID and Discord name. The reward ladder was cut at 120 characters ('... 5th-7th - 10 USDT each (more on the page)'), losing 8th-10th. The post's 'Event Period' and the 'Invite Rally' track never reached the writer.

Guards: No guard dropped a fact.

### The card that went out

```text
🎁 <b>Win up to 40 USDT in the OKX Growth &amp; Apex S3 giveaway, open worldwide, until Dec 1</b>

OKX is running a giveaway with 150 USDT in prizes: 40 USDT for 1st, 30 for 2nd, 20 for 3rd, 15 for 4th, and 10 each for 5th to 7th. It is open worldwide, and nothing is stated as required up front. Deadline: 1/12/2026 6:00 PM (GMT+8).

1. Get your OKX Verified role.
2. Vote daily in #trend-polls to earn 5000 XP.
3. Use '/checkin' daily in #general-chat to earn 5000 XP.
The rest of the steps are on the offer's page.

You need an OKX account and verification for the role. Daily voting and check-ins take time. Other requirements and limits are not stated. This is a giveaway, so treat it as a chance, not a payment.

Source: OKX Announcements (Telegram)

#EarnOnline #Rewards
```

## Preview 237 (raw item 2998808, binance_campaigns, writer: plain_facts_card)

**Title as collected:** Region Exclusive: Join the Affiliate Referral Festival and Refer for Rewards
**Post link:** https://www.binance.com/en/support/announcement/detail/fb40667e3c714d7f8aa2f1bac2b82aa9  **Landing link:** https://binance.com/en/support/announcement/detail/fb40667e3c714d7f8aa2f1bac2b82aa9

### 1. The original source post (full, as stored)

```text
Region Exclusive: Join the Affiliate Referral Festival and Refer for Rewards
```

### 2. What the page reader fetched

Reader OK (3 steps, a long reward text, India-only eligibility).

Stored `enriched` at write time (`{}` = the reader failed):

```json
{"cost": null, "from": "landing_page", "steps": ["Log in to your Binance account and click [Join Now].", "Invite new friends via your Referral Pro Link/ID to register on the Binance App.", "Generate qualifying Spot, Convert or Futures trading volume from your new referrals."], "reward": "Top 1 ranked referrer wins the new iPhone 18 Pro by default, but they can decide to exchange this reward for 1,500 USDT; 4,000 USDT in leaderboard rewards for t", "deadline": "2026-12-17 23:59 (UTC)", "eligibility": "Affiliates from India only; both Promotion A and Promotion B are open only to users in India; referred users must register through the participating Affiliate's Referral Pro Link/ID, top up at least 2"}
```

### 3. The facts the writer received

```text
Facts about this opportunity (collected automatically; the reader's own checks still apply):
- What: Region Exclusive: Join the Affiliate Referral Festival and Refer for Rewards
- Kind: giveaway
- Posted by: Binance campaigns
- Reward: Top 1 ranked referrer wins the new iPhone 18 Pro by default, but they can decide to exchange this reward for 1,500 (more on the page)
- What it costs or requires: nothing is stated as required up front
- Deadline: 2026-12-17 23:59 (UTC)
- Who can take part: both Promotion A and Promotion B are open only to users in India
- Other requirements: none stated
- What stands out (give ONE or TWO of these as the reason this one is worth a look; use only these, never invent a reason): nothing is stated as required up front
- Participation steps (from the offer itself):
  1. Log in to your Binance account and click [Join Now].
  (the offer has further steps -- say the rest are on the offer's page; never write them yourself)
```

### 4. What was dropped, and why

Steps 2-3 dropped by the referral filter ('Invite new friends via your Referral Pro Link/ID', 'volume from your new referrals'), so one step reached the writer; the reward cut at 120 characters ('... exchange this reward for 1,500'), eligibility cut to a fragment. The writer then FAILED its guards (nine retries) and the plain facts card went out: 'Reward not stated', 'Countries not stated', 'Nothing is stated as required up front' (it ignored the page's reward and India-only rule, which were in `enriched`).

Guards: The guards rejected every draft; the fallback card then printed 'not stated' for facts that WERE stored.

### The card that went out

```text
🎁 <b>Region Exclusive: Join the Affiliate Referral Festival and Refer for Rewards</b>

<b>Reward:</b> Reward not stated
<b>Who can take part:</b> Countries not stated
<b>Cost:</b> Nothing is stated as required up front

1. Log in to your Binance account and click [Join Now].
2. Invite new friends via your Referral Pro Link/ID to register on the Binance App.
3. Generate qualifying Spot, Convert or Futures trading volume from your new referrals.

Source: Binance campaigns

<i>Plain facts card: the writer could not produce a checked draft. Edit it or post it as it is.</i>
```
