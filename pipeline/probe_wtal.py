"""Candidate sources for the humor channel "We Talk A Lot" (operator 2026-11-20), probed FROM RAILWAY by pipeline/source_probe.py before anything is built on them.

Every URL here is a candidate, not a decision: a probe answers "reachable from Railway, shaped like a feed, how fresh" -- whether the CONTENT suits the channel, and whether its terms allow
reposting, is a separate judgement recorded in BACKLOG.md. Where a site has no known feed URL, the usual paths are tried (the ones that fail are reported dead, which is the point).
Sources that need a key the operator has not given yet (NewsData / FreeNewsApi, YouTube Data API) cannot be probed and are listed in BACKLOG as pending a key.
"""

from datetime import datetime, timedelta, timezone

_NOW = datetime.now(timezone.utc)
_MD = _NOW.strftime("%m/%d")
_YMD = _NOW.strftime("%Y/%m/%d")
_Y_M = _NOW.strftime("%Y/%m")
_YESTERDAY = (_NOW - timedelta(days=1)).strftime("%Y/%m/%d")

W = "weird news"
S = "satire"
H = "history / deaths / records / facts"
T = "trending"
J = "jokes"
L = "listicles / evergreen"

WTAL_PROBES = [
    # ---- weird / absurd real news
    {"id": "w_upi_odd", "dir": W, "url": "https://rss.upi.com/news/odd_news.rss"},
    {"id": "w_lemmy_nottheonion", "dir": W, "url": "https://lemmy.world/feeds/c/nottheonion.xml?sort=Hot"},
    {"id": "w_gnews_weird", "dir": W, "url": "https://news.google.com/rss/search?q=weird+OR+bizarre+OR+absurd+when:2d&hl=en-US&gl=US&ceid=US:en"},
    {"id": "w_gnews_florida_man", "dir": W, "url": "https://news.google.com/rss/search?q=%22florida+man%22+when:3d&hl=en-US&gl=US&ceid=US:en"},
    {"id": "w_fox_odd_feed1", "dir": W, "url": "https://moxie.foxnews.com/google-publisher/odd-news.xml"},
    {"id": "w_fox_odd_feed2", "dir": W, "url": "https://feeds.foxnews.com/foxnews/oddnews"},
    {"id": "w_fox_odd_page", "dir": W, "url": "https://www.foxnews.com/category/odd-news"},
    {"id": "w_wdbo_feed", "dir": W, "url": "https://www.wdbo.com/arc/outboundfeeds/rss/category/news/weird-news/?outputType=xml"},
    {"id": "w_wdbo_page", "dir": W, "url": "https://www.wdbo.com/news/weird-news/"},
    {"id": "w_time_feed", "dir": W, "url": "https://time.com/tag/weird-news/feed/"},
    {"id": "w_time_page", "dir": W, "url": "https://time.com/tag/weird-news/"},
    {"id": "w_newsnow", "dir": W, "url": "https://www.newsnow.co.uk/h/World+News/Weird+and+Wonderful"},
    {"id": "w_newsoftheweird_feed", "dir": W, "url": "https://newsoftheweird.com/feed/"},
    {"id": "w_newsoftheweird_page", "dir": W, "url": "https://newsoftheweird.com/"},
    {"id": "w_ktar_odd", "dir": W, "url": "https://ktar.com/odd-news/feed/"},
    {"id": "w_newsweek_weird", "dir": W, "url": "https://www.newsweek.com/topic/weird-news"},
    {"id": "w_yahoo_odd", "dir": W, "url": "https://news.yahoo.com/rss/odd-news"},
    {"id": "w_aol_odd", "dir": W, "url": "https://lite.aol.com/news/odd/headlines/index/ap/oddities"},
    {"id": "w_fark", "dir": W, "url": "https://www.fark.com/fark.rss"},
    # ---- satire
    {"id": "s_onion", "dir": S, "url": "https://theonion.com/feed/"},
    {"id": "s_clickhole", "dir": S, "url": "https://clickhole.com/feed/"},
    {"id": "s_dailymash", "dir": S, "url": "https://www.thedailymash.co.uk/feed"},
    {"id": "s_newsthump", "dir": S, "url": "https://newsthump.com/feed/"},
    {"id": "s_beaverton", "dir": S, "url": "https://thebeaverton.com/feed/"},
    {"id": "s_wwn", "dir": S, "url": "https://waterfordwhispersnews.com/feed/"},
    {"id": "s_shovel", "dir": S, "url": "https://theshovel.com.au/feed/"},
    {"id": "s_betoota", "dir": S, "url": "https://www.betootaadvocate.com/feed/"},
    {"id": "s_chaser", "dir": S, "url": "https://chaser.com.au/feed/"},
    {"id": "s_reductress", "dir": S, "url": "https://reductress.com/feed/"},
    {"id": "s_duffel", "dir": S, "url": "https://www.duffelblog.com/feed"},
    {"id": "s_harddrive", "dir": S, "url": "https://thehard-drive.com/feed"},
    {"id": "s_hardtimes", "dir": S, "url": "https://thehardtimes.net/feed/"},
    {"id": "s_babylonbee", "dir": S, "url": "https://babylonbee.com/feed"},
    {"id": "s_postillon", "dir": S, "url": "https://www.der-postillon.com/feeds/posts/default"},
    {"id": "s_alhudood", "dir": S, "url": "https://alhudood.net/feed/"},
    # ---- history, unusual deaths, records, facts
    {"id": "h_guinness_news", "dir": H, "url": "https://www.guinnessworldrecords.com/news"},
    {"id": "h_guinness_feed", "dir": H, "url": "https://www.guinnessworldrecords.com/news/rss"},
    {"id": "h_guinness_records", "dir": H, "url": "https://www.guinnessworldrecords.com/records/showcase"},
    {"id": "h_darwin_home", "dir": H, "url": "https://darwinawards.com/"},
    {"id": "h_darwin_feed", "dir": H, "url": "https://darwinawards.com/rss/darwin.xml"},
    {"id": "h_numbersapi", "dir": H, "url": "http://numbersapi.com/random/trivia?json"},
    {"id": "h_numbersapi_https", "dir": H, "url": "https://numbersapi.com/random/trivia?json"},
    {"id": "h_uselessfacts", "dir": H, "url": "https://uselessfacts.jsph.pl/api/v2/facts/random"},
    {"id": "h_uselessfacts_today", "dir": H, "url": "https://uselessfacts.jsph.pl/api/v2/facts/today"},
    {"id": "h_funfact_asli", "dir": H, "url": "https://asli-fun-fact-api.herokuapp.com/"},
    {"id": "h_catfact_style_factsapi", "dir": H, "url": "https://api.api-ninjas.com/v1/facts"},
    # ---- listicles and evergreen sites (feed paths guessed; the ones that fail are reported dead)
    {"id": "l_listverse", "dir": L, "url": "https://listverse.com/feed/"},
    {"id": "l_atlasobscura", "dir": L, "url": "https://www.atlasobscura.com/feeds/latest"},
    {"id": "l_mentalfloss", "dir": L, "url": "https://www.mentalfloss.com/feed"},
    {"id": "l_cracked", "dir": L, "url": "https://www.cracked.com/rss"},
    {"id": "l_cracked_feed", "dir": L, "url": "https://www.cracked.com/feed"},
    {"id": "l_todayilearned_lemmy", "dir": L, "url": "https://lemmy.world/feeds/c/todayilearned.xml?sort=TopMonth"},
    {"id": "l_showerthoughts_lemmy_world", "dir": L, "url": "https://lemmy.world/feeds/c/showerthoughts.xml?sort=TopMonth"},
    {"id": "l_showerthoughts_lemmy_ml", "dir": L, "url": "https://lemmy.ml/feeds/c/showerthoughts.xml?sort=TopMonth"},
    {"id": "l_showerthoughts_lemmy_lemmee", "dir": L, "url": "https://lemm.ee/feeds/c/showerthoughts.xml?sort=TopMonth"},
    {"id": "l_reddit_showerthoughts_rss", "dir": L, "url": "https://www.reddit.com/r/Showerthoughts/top/.rss?t=month"},
    {"id": "l_reddit_darkjokes_rss", "dir": L, "url": "https://www.reddit.com/r/darkjokes/top/.rss?t=month"},
    {"id": "l_reddit_todayilearned_rss", "dir": L, "url": "https://www.reddit.com/r/todayilearned/top/.rss?t=month"},
    {"id": "l_damninteresting", "dir": L, "url": "https://www.damninteresting.com/feed/"},
    {"id": "l_todayifoundout", "dir": L, "url": "https://www.todayifoundout.com/index.php/feed/"},
    {"id": "l_oddee", "dir": L, "url": "https://www.oddee.com/feed/"},
    {"id": "l_strangeco", "dir": L, "url": "https://strangeco.blogspot.com/feeds/posts/default"},
    {"id": "l_historycollection", "dir": L, "url": "https://historycollection.com/feed/"},
    {"id": "l_ranker_dark_history", "dir": L, "url": "https://www.ranker.com/list/dark-history/"},
    # ---- trending
    {"id": "t_google_trends_us", "dir": T, "url": "https://trends.google.com/trending/rss?geo=US"},
    {"id": "t_google_trends_old", "dir": T, "url": "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US"},
    {"id": "t_bluesky_search", "dir": T, "url": "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=weird&limit=5&sort=top"},
    {"id": "t_bluesky_trends", "dir": T, "url": "https://public.api.bsky.app/xrpc/app.bsky.unspecced.getTrendingTopics?limit=10"},
    # ---- jokes
    {"id": "j_jokeapi_dark", "dir": J, "url": "https://v2.jokeapi.dev/joke/Dark?amount=10"},
    # 2026-11-20 (operator: dark jokes only, "JokeAPI's Dark category and dark-joke sources"): Lemmy's API from Railway, for the two communities that could feed the dark library (each joke would go through the DeepSeek dark check)
    {"id": "j_lemmy_jokes_api", "dir": J, "url": "https://lemmy.world/api/v3/post/list?community_name=jokes&sort=New&limit=50"},
    {"id": "j_lemmy_darkhumor_api", "dir": J, "url": "https://lemmy.world/api/v3/post/list?community_name=darkhumor&sort=New&limit=50"},
    # 2026-11-21 (operator: 68 dark jokes is too small): bulk candidates, reachable from Railway? (Range request: the size is in Content-Range, the file is not downloaded)
    {"id": "j_taivop_reddit", "dir": J, "url": "https://raw.githubusercontent.com/taivop/joke-dataset/master/reddit_jokes.json", "headers": {"Range": "bytes=0-2000"}},
    {"id": "j_taivop_wocka", "dir": J, "url": "https://raw.githubusercontent.com/taivop/joke-dataset/master/wocka.json", "headers": {"Range": "bytes=0-2000"}},
    {"id": "j_taivop_stupidstuff", "dir": J, "url": "https://raw.githubusercontent.com/taivop/joke-dataset/master/stupidstuff.json", "headers": {"Range": "bytes=0-2000"}},
    {"id": "j_hf_funnydata_rows", "dir": J, "url": "https://datasets-server.huggingface.co/rows?dataset=SajilAwale/FunnyData&config=default&split=train&offset=0&length=20"},
    {"id": "j_mastodon_darkjokes", "dir": J, "url": "https://mastodon.social/tags/darkjokes.rss"},
    {"id": "j_jokeapi_info", "dir": J, "url": "https://v2.jokeapi.dev/info"},
    {"id": "j_chucknorris", "dir": J, "url": "https://api.chucknorris.io/jokes/random"},
    {"id": "j_chucknorris_dev", "dir": J, "url": "https://api.chucknorris.io/jokes/random?category=dev"},
    {"id": "j_excuser", "dir": J, "url": "https://excuser-three.vercel.app/v1/excuse/10"},
    {"id": "j_corporate_buzz", "dir": J, "url": "https://corporatebs-generator.sameerkumar.website/"},
    {"id": "j_official_joke", "dir": J, "url": "https://official-joke-api.appspot.com/jokes/ten"},
    {"id": "j_icanhazdadjoke", "dir": J, "url": "https://icanhazdadjoke.com/search?limit=5"},
    {"id": "j_darkjokes_api_qa", "dir": J, "url": "https://dark-jokes-api.vercel.app/"},
    # ---- alternates for the ones that failed from the dev machine, and more candidates found by search (2026-11-20)
    {"id": "s_betoota_alt1", "dir": S, "url": "https://www.betootaadvocate.com/feed"},
    {"id": "s_betoota_alt2", "dir": S, "url": "https://www.betootaadvocate.com/rss"},
    {"id": "s_harddrive_www", "dir": S, "url": "https://www.thehard-drive.com/feed"},
    {"id": "s_alhudood_www", "dir": S, "url": "https://www.alhudood.net/feed/"},
    {"id": "s_alhudood_rss", "dir": S, "url": "https://alhudood.net/rss.xml"},
    {"id": "w_time_feed_all", "dir": W, "url": "https://time.com/feed/"},
    {"id": "w_newsoftheweird_www", "dir": W, "url": "https://www.newsoftheweird.com/feed/"},
    {"id": "w_iflscience", "dir": W, "url": "https://www.iflscience.com/rss"},
    {"id": "w_snopes", "dir": W, "url": "https://www.snopes.com/feed/"},
    {"id": "h_history_com_tdih", "dir": H, "url": "https://www.history.com/.rss/full/this-day-in-history"},
    {"id": "h_britannica_onthisday", "dir": H, "url": "https://www.britannica.com/on-this-day"},
    {"id": "h_recordsetter", "dir": H, "url": "https://www.recordsetter.com/"},
    {"id": "h_vintagenews", "dir": H, "url": "https://www.thevintagenews.com/feed/"},
    {"id": "h_ancient_origins", "dir": H, "url": "https://www.ancient-origins.net/rss.xml"},
    {"id": "l_lemmy_darkhumor", "dir": J, "url": "https://lemmy.world/feeds/c/darkhumor.xml?sort=TopMonth"},
    {"id": "l_lemmy_jokes", "dir": J, "url": "https://lemmy.world/feeds/c/jokes.xml?sort=TopMonth"},
    {"id": "l_lemmy_historymemes", "dir": H, "url": "https://lemmy.world/feeds/c/historymemes.xml?sort=TopMonth"},
    {"id": "l_lemmy_unexpected", "dir": W, "url": "https://lemmy.world/feeds/c/unexpected.xml?sort=TopMonth"},
    # ---- 2026-11-21 phase 2: the exact endpoints the libraries read (collectors/wtal_library.py), so Railway confirms each before the monthly refresh relies on it
    {"id": "p2_lemmy_showerthoughts_api", "dir": L, "url": "https://lemmy.world/api/v3/post/list?community_name=showerthoughts&sort=TopAll&limit=50&page=2"},
    {"id": "p2_recordsetter_index", "dir": H, "url": "https://recordsetter.com/world-records"},
    {"id": "p2_recordsetter_food", "dir": H, "url": "https://recordsetter.com/food-and-drink-world-records"},
    # ---- 2026-11-21 EDITOR-CURATED sources (operator: replace Wikipedia in dark history / unusual deaths / weird facts / world records / conspiracies): the exact endpoints collectors/wtal_curated.py reads,
    # so Railway confirms each one (feeds and their older pages, the article pages the excerpt-only feeds need, the Darwin Awards archive, the Snopes tag the operator named, the Ranker lists).
    {"id": "c_futilitycloset_feed", "dir": H, "url": "https://www.futilitycloset.com/feed/"},
    {"id": "c_futilitycloset_feed_p6", "dir": H, "url": "https://www.futilitycloset.com/feed/?paged=6"},
    {"id": "c_damninteresting_feed", "dir": H, "url": "https://www.damninteresting.com/feed"},
    {"id": "c_damninteresting_feed_p3", "dir": H, "url": "https://www.damninteresting.com/feed?paged=3"},
    {"id": "c_damninteresting_article", "dir": H, "url": "https://www.damninteresting.com/there-once-was-a-man-called-curley/"},
    {"id": "c_ati_feed", "dir": H, "url": "https://allthatsinteresting.com/feed"},
    {"id": "c_ati_feed_p6", "dir": H, "url": "https://allthatsinteresting.com/feed?paged=6"},
    {"id": "c_tifo_feed_p3", "dir": H, "url": "https://www.todayifoundout.com/index.php/feed/?paged=3"},
    {"id": "c_listverse_feed_p3", "dir": H, "url": "https://listverse.com/feed/?paged=3"},
    {"id": "c_listverse_article", "dir": H, "url": "https://listverse.com/2026/11/18/10-bizarre-things-that-happened-during-the-siege-of-paris/"},
    {"id": "c_atlas_articles_feed", "dir": H, "url": "https://www.atlasobscura.com/feeds/articles"},
    {"id": "c_mentalfloss_article", "dir": H, "url": "https://www.mentalfloss.com/crime/burke-hare-murders-chilling-facts"},
    {"id": "c_improbable_feed", "dir": H, "url": "https://www.improbable.com/feed/"},
    {"id": "c_improbable_article", "dir": H, "url": "https://improbable.com/2026/10/27/detecting-kissing-in-movies/"},
    {"id": "c_darwin_index", "dir": H, "url": "https://darwinawards.com/darwin/"},
    {"id": "c_darwin_year", "dir": H, "url": "https://darwinawards.com/darwin/darwin2012.html"},
    {"id": "c_darwin_entry", "dir": H, "url": "https://darwinawards.com/darwin/darwin2006-10.html"},
    {"id": "c_snopes_freakish_fatalities_page", "dir": H, "url": "https://www.snopes.com/tag/freakish-fatalities/"},
    {"id": "c_snopes_freakish_fatalities_feed", "dir": H, "url": "https://www.snopes.com/tag/freakish-fatalities/feed/"},
    {"id": "c_ranker_strange_deaths", "dir": H, "url": "https://www.ranker.com/list/strange-deaths/"},
    {"id": "c_ranker_weirdest_deaths", "dir": H, "url": "https://www.ranker.com/list/weirdest-deaths-in-history/ranker-history"},
    {"id": "c_tg_mistic_history_before", "dir": H, "url": "https://t.me/s/mistic_history?before=1200"},
]

# Telegram: the two content channels the operator named plus the six meme channels (trend signals only -- never reposted).
WTAL_TELEGRAM = {
    "shockingfact": "@shockingfact (facts candidate)",
    "dark_storys": "@dark_storys (dark stories candidate)",
    "bestmemes": "@bestmemes (trend signal)",
    "funny_memes_jokes_english_gifs": "@funny_memes_jokes_english_gifs (trend signal)",
    "DailyEpicMemes": "@DailyEpicMemes (trend signal)",
    "funny_videotg": "@funny_videotg (trend signal)",
    "memesouterspace1": "@memesouterspace1 (trend signal)",
    "talesofthedark": "@talesofthedark (dark stories candidate)",
    # 2026-11-21 (operator): the two curated Telegram channels. Never to be added: @GlobalHistoryDaily, @Real_History_01, @secretscope, @forbiddenhist.
    "SHOCKING_FACTSS": "@SHOCKING_FACTSS (curated facts candidate)",
    "mistic_history": "@mistic_history (curated dark history: Horror History)",
}
