"""Plain-English description of each strategy for the dashboard. Rules mirror strategies/*.md and paper_rules.py."""
from __future__ import annotations

INFO: dict[str, dict] = {
    "momentum-quality": {
        "name": "Momentum quality",
        "one": "Buys trending leaders with strong earnings, but only while the whole market is healthy.",
        "plain": "This is your skill. It first asks whether the market itself is safe (SPY trend, distribution days). Only then does it look for stocks that are already strong, growing earnings, and sitting in a clean uptrend, and it buys on a breakout with volume. Every trade has a stop set in advance, and it sells if the trend breaks.",
        "entry": ["Market is RISK-ON: SPY above its 50-day and 200-day averages, EMA50 above EMA100, no new 4-week low, fewer than 4 distribution days in 4 weeks",
                  "Fundamentals: earnings up 20%+ (or sales 40%+), market cap over $2B, sector leader, no earnings within 10 days",
                  "Trend template: price above 50, 150 and 200-day averages in order, 200-day rising, within 25% of the 52-week high, 30%+ above the low",
                  "Not stretched: no more than 1.5 ATR above the 21-day EMA",
                  "Stage 2 and momentum: base breakout on 40%+ volume, MACD never falling below zero"],
        "exit": ["Hard stop 6.25% under your cost (1% account risk at a 16% position)",
                 "Close below the 50-day average, or the 200-day average rolling over",
                 "Sell half at +15%, the rest at +25%",
                 "Sell or halve before earnings; time stop after 15 quiet trading days"],
        "size": ["16% of the account per position", "Max 5 positions, one per sector, at most 2 AI names, 80% invested at most",
                 "Limit orders only; never average down"],
    },
    "mean-reversion": {
        "name": "Mean reversion",
        "one": "Buys sharp short-term dips in stocks that are in long uptrends, expecting a quick bounce.",
        "plain": "Stocks in a strong uptrend that drop several days in a row usually snap back within days. This strategy waits for an extreme dip (RSI over 2 days below 10, or below the lower Bollinger band, after 3 down days) and sells as soon as it bounces.",
        "entry": ["Price above the 200-day average (long-term uptrend)", "RSI(2) under 10, or close below the lower Bollinger band",
                  "At least 3 down closes in a row", "No earnings in the next 3 days"],
        "exit": ["RSI(2) back above 60, or close above the 5-day average", "Time stop after 7 trading days", "Hard stop 6% under the entry price"],
        "size": ["10% of the account per position, max 6 positions"],
    },
    "breakout": {
        "name": "Breakout",
        "one": "Buys stocks breaking out of tight ranges to new highs on heavy volume.",
        "plain": "When a stock that has been quiet for weeks closes above its 55-day high on much higher volume, buyers have taken control. This strategy buys that day and trails a stop under the price.",
        "entry": ["Close above the prior 55-day high", "Volume at least 1.5x its 50-day average", "Tight base: ATR under 4% of price"],
        "exit": ["Close below the prior 20-day low", "Trailing stop 2.5 ATR under the highest close since entry", "Hard stop 8% under entry"],
        "size": ["Risk 1% of the account per trade (stop distance sets the size), capped at 10%, max 8 positions"],
    },
    "trend-following": {
        "name": "Trend following",
        "one": "Rides long, slow trends in big ETFs and mega-caps and gets out when the trend ends.",
        "plain": "Looks once a week at 13 liquid ETFs and large companies. If the 50-day average is above the 200-day and rising, and the trend is strong (ADX over 20), it buys and holds until the trend breaks.",
        "entry": ["50-day average crossed above the 200-day, or price above both with the 50-day rising", "ADX(14) above 20", "New entries are checked once a week"],
        "exit": ["50-day average falls below the 200-day", "Two closes in a row below the 100-day average"],
        "size": ["10% of the account per position, max 8 positions"],
    },
    "earnings-drift": {
        "name": "Earnings drift",
        "one": "Buys companies that beat earnings and jumped, expecting the move to keep going for weeks.",
        "plain": "After a strong earnings beat and a gap up that holds, prices often keep drifting up. This strategy buys within 2 days of the report and holds up to 30 days unless the gap fills.",
        "entry": ["Earnings beat estimates by more than 5%", "Gapped up more than 3% and held above the gap-day low", "Entry within 2 trading days of the report"],
        "exit": ["Close back below the pre-earnings close (gap filled)", "Trailing stop 8% under the high close", "Time stop after 30 trading days"],
        "size": ["10% of the account per position, max 5 positions"],
    },
}
