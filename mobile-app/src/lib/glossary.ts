export const GLOSSARY: Record<string, { title: string; body: string }> = {
  iv_threshold: {
    title: "IV Rank Threshold",
    body: "IV Rank measures how high implied volatility (option pricing) is right now compared to the past year. 0 = cheapest options have ever been, 1 = most expensive.\n\nThis setting means: only sell options when IV Rank is above this level. Higher IV = fatter premiums = more income per trade.\n\nSet it too high and the bot rarely trades (waiting for perfect conditions). Set it too low and you're selling cheap options in calm markets — less income for the same risk.",
  },
  delta_range: {
    title: "Delta Range",
    body: "Delta has two roles here.\n\nFirst, it's the probability that an option expires in-the-money — meaning the market moves far enough against your strike to cost you money. A delta of 0.10 = roughly 10% chance; 0.30 = roughly 30%.\n\nSecond, delta is the hedge ratio: for every 1 BTC of delta, the bot shorts 1 BTC of BTC-PERPETUAL futures to cancel out directional exposure. Higher delta = larger hedge = more perp contracts held.\n\nLower delta = strike further from BTC price = smaller premium, smaller hedge. Higher delta = closer strike = bigger premium, bigger hedge.\n\nThe bot targets strikes with a delta between these two numbers.",
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
  starting_equity: {
    title: "Starting Equity",
    body: "The total capital allocated to this strategy in USD. This is the baseline the bot uses to calculate position sizes, free reserve, and performance metrics.\n\nSet it to the actual amount you plan to deploy. Too low and position sizes will be unrealistically small; too high and the bot may think it can afford trades it can't.",
  },
  regime_filter: {
    title: "Regime Filter",
    body: "When enabled, the bot checks whether BTC is currently in an uptrend or downtrend before opening new positions.\n\nIt uses a moving average over the configured number of days. If BTC's current price is below the moving average, the bot considers it a downtrend and skips new trades — selling puts in a falling market is high risk.\n\nThe MA Days setting controls how sensitive this filter is: shorter (e.g. 30 days) reacts faster to trend changes; longer (e.g. 90 days) is slower but less whipsaw-prone.",
  },
  capital_committed: {
    title: "Capital Committed",
    body: "For a short put, this is your worst-case financial obligation from the option alone: strike price × contracts.\n\nIn practice, the delta-neutral hedge offsets most of that risk — as BTC falls, the short perp position earns, partially compensating any option loss. The actual at-risk amount is the residual after the hedge.\n\nIt tells you how much of your account is 'claimed' by this trade's margin requirement.",
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
  net_delta: {
    title: "Net Delta",
    body: "The combined directional exposure of your portfolio after the hedge.\n\nA short put has positive delta (you benefit if BTC rises). The BTC-PERPETUAL short hedge has negative delta. Net delta = option delta + hedge delta.\n\nThe goal is net delta ≈ 0 — meaning BTC moving up or down has almost no effect on your total position value. What you earn is the option's time decay (theta), not a bet on direction.\n\nSmall residual delta is normal between daily rebalances.",
  },
  perp_hedge: {
    title: "Perp Hedge (BTC-PERPETUAL)",
    body: "The offsetting position the bot holds in BTC-PERPETUAL futures to cancel the option's directional risk.\n\nFor a short put: the bot shorts BTC-PERPETUAL equal to the option's delta × contracts. If BTC falls, the put loses money but the short perp earns — keeping net P&L close to flat on price moves.\n\nThe hedge is rebalanced daily as delta drifts. Two small costs apply: a funding rate (~0.01%/day on the notional perp value) and a spread cost when buying or selling BTC to rebalance (~0.02% per lot). Both are included in every backtest.",
  },
  reconcile: {
    title: "Reconcile (Optimizer Mode)",
    body: "Compares what the Black-Scholes pricing model predicted against what your actual trades produced.\n\nFor each completed trade, the bot works out what premium it should have collected (based on IV and time to expiry at entry) and whether it should have won or lost. It then compares that to what actually happened.\n\nKey metrics: Premium Accuracy (how close predicted premium was to actual), Win Accuracy (how often the model correctly called win/loss), and Overall Accuracy — a combined score.\n\nA low accuracy score means real market conditions are drifting from the model's assumptions — a signal to re-run evolution with fresh data.",
  },
  hedge_pnl: {
    title: "Hedge P&L",
    body: "The profit or loss on the BTC-PERPETUAL hedge position for a trade.\n\nAs BTC price moves between entry and expiry, the short perp position gains or loses. Daily funding costs and rebalance spread costs are also deducted here.\n\nHedge P&L is shown separately from option P&L so you can see exactly how much of your income came from premium decay vs. how much was offset by hedge costs or gains. Total trade P&L = option P&L + hedge P&L.",
  },
}
