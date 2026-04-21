export const GLOSSARY: Record<string, { title: string; body: string }> = {
  iv_threshold: {
    title: "IV Rank Threshold",
    body: "IV Rank measures how high implied volatility (option pricing) is right now compared to the past year. 0 = cheapest options have ever been, 1 = most expensive.\n\nThis setting means: only sell options when IV Rank is above this level. Higher IV = fatter premiums = more income per trade.\n\nSet it too high and the bot rarely trades (waiting for perfect conditions). Set it too low and you're selling cheap options in calm markets — less income for the same risk.",
  },
  delta_range: {
    title: "Delta Range",
    body: "Delta is the probability that an option expires in-the-money — meaning the market moves against your position.\n\nA delta of 0.10 = roughly 10% chance of losing on this trade. A delta of 0.30 = roughly 30% chance.\n\nLower delta = strike price is further from current BTC price = safer, but smaller premium. Higher delta = closer to BTC price = bigger premium, higher risk of loss.\n\nThe bot targets strikes with a delta between these two numbers.",
  },
  dte_range: {
    title: "Days To Expiry (DTE)",
    body: "How many days until the option contract expires.\n\nShorter DTE (e.g. 7 days): premium decays faster (good for you), less time for BTC to move against you, but you need to trade more frequently.\n\nLonger DTE (e.g. 30 days): larger premium upfront, but BTC has more time to move, and you're tied up longer.\n\nThe sweet spot for wheel strategies is usually 7–21 days.",
  },
  max_leg_size: {
    title: "Max Leg Size",
    body: "The maximum percentage of your total equity you'll commit to a single trade.\n\n11% means no single trade can use more than 11% of your account. This prevents you from going all-in on one position.\n\nLower = more conservative, more diversification possible. Higher = larger individual trades, higher income potential but more concentrated risk.",
  },
  free_equity: {
    title: "Min Free Equity",
    body: "The minimum fraction of your account that must stay undeployed at all times.\n\n0.0 = the bot can use 100% of your equity. 0.20 = always keep 20% in cash reserve.\n\nA reserve protects against sudden margin calls and leaves room to manage a position if BTC moves against you. Setting this to 0 maximises capital efficiency but leaves no buffer.",
  },
  premium_fraction: {
    title: "Premium Fraction of Spot",
    body: "The minimum premium you'll accept as a fraction of BTC's current price.\n\nIf BTC is at $70,000 and this is set to 0.008, you need at least $560 in premium to take the trade.\n\nThis filters out trades that aren't worth the risk — if the premium is too small, the income doesn't justify tying up your capital.",
  },
  capital_committed: {
    title: "Capital Committed",
    body: "For a short put, this is your worst-case financial obligation: if BTC dropped to zero, you'd owe strike price × contracts.\n\nIn practice BTC won't go to zero, and you'd close the position long before that — but this is the theoretical maximum at-risk amount.\n\nIt tells you how much of your account is 'claimed' by this trade.",
  },
  free_reserve: {
    title: "Free Reserve",
    body: "Your current total equity minus the capital committed to open positions.\n\nThis is your available buffer — money not tied up in any trade. A healthy reserve means you can absorb a loss, manage an open position, or take a new trade without stress.\n\nIf this gets too small, you're overexposed.",
  },
  est_annual_yield: {
    title: "Est. Annual Yield",
    body: "If this trade wins (premium fully collected at expiry) and you repeated a similar trade every cycle for a full year, what annual return would that produce?\n\nFormula: (premium ÷ capital committed) × (365 ÷ days to expiry)\n\nThis is a best-case projection — it assumes every trade wins and conditions stay similar. Real returns will be lower due to losing trades and varying premiums. Use it as a rough benchmark to compare trade quality, not as a forecast.",
  },
  fitness_balanced: {
    title: "Balanced Evolution",
    body: "Optimises across all metrics equally — return, Sharpe ratio, win rate, and drawdown all count.\n\nThis is the default all-rounder. It won't find the absolute highest returns or the absolute safest parameters, but produces a solid, well-rounded strategy.\n\nGood starting point if you're not sure which goal to use.",
  },
  fitness_max_yield: {
    title: "Max Yield Evolution",
    body: "Optimises purely for the highest possible return, ignoring risk.\n\nWill find aggressive parameters: higher delta, shorter DTE, larger position sizes. Expects to generate more income — but will also find the hardest losses when conditions turn.\n\nBest used when you're comfortable with volatility and willing to ride out drawdowns for higher long-term returns.",
  },
  fitness_safest: {
    title: "Safest Evolution",
    body: "Optimises to minimise drawdown and maximise win rate — how often you come out ahead.\n\nWill find conservative parameters: deep out-of-the-money strikes, smaller positions, longer DTE, bigger cash reserves. Lower income, but a much smoother equity curve.\n\nBest if capital preservation is your priority and you want to sleep soundly.",
  },
  fitness_sharpe: {
    title: "Sharpe Evolution",
    body: "Optimises for the best risk-adjusted return — maximising how much you earn per unit of risk taken.\n\nThe Sharpe ratio is the standard metric used by professional fund managers. A Sharpe of 1.0 is decent, 2.0 is excellent.\n\nThis is usually the smartest default after Balanced — you're not leaving money on the table, but you're not gambling either.",
  },
  iv_rank_badge: {
    title: "IV Threshold",
    body: "The minimum IV Rank required before the bot will open a new position.\n\nIV Rank measures how expensive options are right now vs. the past year. Higher = more premium available.\n\nThe bot sits on its hands when IV is below this level and waits for better-priced opportunities.",
  },
  walk_forward: {
    title: "Walk-Forward Validation",
    body: "A robustness test for your evolved strategy.\n\nThe bot splits historical data into two periods: In-Sample (IS, 75%) used to find the best parameters, and Out-of-Sample (OOS, 25%) used to test if those parameters actually work on data they've never seen.\n\nA high IS fitness but low OOS fitness means the strategy was 'overfit' — it learned the past perfectly but won't work in the future. A strategy that performs similarly on both is genuinely robust.",
  },
  monte_carlo: {
    title: "Monte Carlo Simulation",
    body: "Tests your strategy against 100 randomly selected 6-month windows from history.\n\nRather than one backtest, you get a distribution of outcomes — best case, worst case, and everything in between. Shows you the p5 (5th percentile, near-worst case) through p95 (95th percentile, near-best case) range.\n\nUseful for understanding how variable the strategy's performance is. A tight distribution = consistent. A wide one = highly dependent on market conditions.",
  },
}
