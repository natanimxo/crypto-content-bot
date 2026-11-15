"""Regression table for the deterministic voice / guard checks -- awkward real-world cases that a check
must NOT misfire on (and obvious violations it must still catch).

    python scripts/check_voice_checks.py            # exits 1 on any mismatch

Why this exists (2026-11-15): the third-person check matched "US" (the country) case-insensitively as the
pronoun "us", so every Wall Street story mentioning the US failed all its retries -- a check that rejects
a good draft does not warn anyone, the post just never generates. Each case below is either a false
positive that was found by looking (and is now fixed) or a true positive that must keep firing. Add a
case here whenever a real draft is rejected for a bad reason. No database or network needed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import channel_voice  # noqa: E402
from pipeline.write_post import _find_reader_advice  # noqa: E402

PROFILES = channel_voice.load_profiles_from_yaml()
FAILS = []


def _issues(channel: str, title: str, paragraphs: list[str], facts_have_figures: bool = True) -> list[str]:
    profile = PROFILES[channel]
    # pad/trim to the profile's paragraph count so only the check under test can fire
    n = len(profile.paragraphs)
    paragraphs = (paragraphs + ["Plain filler sentence."] * n)[:n]
    return channel_voice.find_voice_issues(profile, {"title": title, "paragraphs": paragraphs}, None, facts_have_figures)


def expect(label: str, got_issue: bool, want_issue: bool, detail=""):
    if got_issue != want_issue:
        FAILS.append(f"{label}: expected {'REJECT' if want_issue else 'ACCEPT'}, got {'REJECT' if got_issue else 'ACCEPT'} {detail}")


def main():
    # ---- third person (Crypto Wall Street): the country "US" is not the pronoun "us"
    for text, bad in [
        ("US spot bitcoin funds lost $1.91 billion over seven sessions.", False),
        ("The U.S. Treasury sold $40 billion of notes.", False),
        ("US-based exchanges paused withdrawals.", False),
        ("Funds flowed out of the US and into Europe.", False),
        ("The decision leaves us waiting for the data.", True),
        ("We expect outflows to continue.", True),
        ("Our tracking shows a rise.", True),
        ("Let's look at the flows.", True),
        ("Analysts at Wells Fargo said rates will hold.", False),
    ]:
        got = [i for i in _issues("crypto_wall_street", "Bitcoin ETF outflows reach $1.91B", [text]) if "third person" in i]
        expect(f"third-person {text!r}", bool(got), bad, got)

    # ---- banned evaluative words (Crypto Wall Street): proper nouns and technical terms are not evaluation
    for text, bad in [
        ("Hackers used a compromised private key to drain the pool.", False),
        ("Key compromise was the cause DefiLlama recorded.", False),
        ("Big Tech earnings lifted the Nasdaq.", False),
        ("The One Big Beautiful Bill passed the Senate.", False),
        ("Major League Baseball announced a token partnership.", False),
        ("The API key was exposed in a public repository.", False),
        ("The exchange reported a major outflow.", True),
        ("Bitcoin reached a key level at $76,000.", True),
        ("A huge sell-off hit altcoins.", True),
        ("Strong demand lifted the token.", True),
        ("Prices were surging on Monday.", True),
    ]:
        got = [i for i in _issues("crypto_wall_street", "Bitcoin ETF outflows reach $1.91B", [text]) if "banned word" in i]
        expect(f"banned-word {text!r}", bool(got), bad, got)

    # ---- title: question or concept (Crypto Notebook). A concept title is not a news headline.
    for title, bad in [
        ("Why a hidden owner can still mint tokens", False),
        ("How an arithmetic error drains a lending pool", False),
        ("What a honeypot flag on a Uniswap pool actually means", False),
        ("Is a pool with a hidden owner safe to read about?", False),
        ("When a creator wallet holds 30% of supply", False),
        ("Inside a honeypot: tokens you can buy but never sell", False),
        ("Spotting a hidden owner before you deposit", False),
        ("Reading a GoPlus honeypot flag", False),
        ("The anatomy of a mint exploit", False),
        ("Honeypots, explained", False),
        ("Hidden owners explained", False),
        ("Kava Lend loses $3.7M in exploit", True),
        ("Bitcoin falls to four-month low", True),
        ("Protocol X hit for $2.3M", True),
    ]:
        got = [i for i in _issues("crypto_notebook", title, ["A paragraph."]) if "question or name a concept" in i]
        expect(f"concept-title {title!r}", bool(got), bad, got)

    # ---- second person (Hustle): must contain "you"/"your" in any case or contraction; absent -> reject
    for text, bad in [
        ("You can try it free this week.", False),
        ("Your team can ship it by Friday.", False),
        ("If you're a founder, this saves a day.", False),
        ("YOU get a free tier.", False),
        ("Founders can try it free this week.", True),
        ("The tool is free this week.", True),
    ]:
        got = [i for i in _issues("hustle_to_million", "A free tool for builders", [text, "Plain filler."]) if "second person" in i]
        # a draft is acceptable when ANY paragraph addresses the reader, so put the sentence in paragraph 1 only
        expect(f"second-person {text!r}", bool(got), bad, got)

    # ---- reader advice (macro_news on Hustle): nominal uses of an advisory verb are not advice
    for text, bad in [
        ("Increase in oil prices lifted shipping costs across Asia.", False),
        ("Review of the tariff schedule begins Monday.", False),
        ("Spend on data centers rose 12% in the quarter.", False),
        ("Delay in port clearances pushed up costs.", False),
        ("Build-out of the grid is expected to take a decade.", False),
        ("Cut in policy rates was widely expected.", False),
        ("Plan for higher costs on any credit you rely on.", True),
        ("Review your supplier contracts this quarter.", True),
        ("Increase your cash buffer before the next release.", True),
        ("You should hedge your exposure.", True),
        ("Don't wait for the data.", True),
        ("Watch the next inflation print for the answer.", False),
    ]:
        got = _find_reader_advice({"title": "t", "paragraphs": [text]})
        expect(f"reader-advice {text!r}", bool(got), bad, got)

    if FAILS:
        print(f"{len(FAILS)} MISMATCH(ES):")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("all voice-check cases behave as expected")


if __name__ == "__main__":
    main()
