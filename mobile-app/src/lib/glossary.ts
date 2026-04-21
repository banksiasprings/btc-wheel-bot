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
  strategy_sweep: {
    title: "Sweep Best — How It Works",
    body: "This strategy was found by testing thousands of parameter combinations and picking the single set that scored highest on a balanced mix of return, safety, and consistency.\n\nIn practice: the bot waits for Bitcoin options to become expensive (high IV Rank), then sells a short put — meaning you're promising to buy BTC at a set price below the market if it drops there. In return, you collect a premium upfront, which you keep regardless of what happens.\n\nIf BTC stays above the strike price when the option expires, you keep the full premium and the trade closes cleanly. If BTC falls below the strike, you effectively buy BTC at that lower price — which in the wheel strategy is fine, because you then sell covered calls to recover.\n\nThe sweep parameters represent the best single balance point found across thousands of combinations — not the highest return, not the safest, but the most consistently profitable overall.",
  },
  strategy_balanced: {
    title: "Evolved: Balanced — How It Works",
    body: "This strategy was trained by a genetic algorithm — the same idea as evolution in nature. It started with hundreds of random strategies, kept the best performers, combined their traits, added small mutations, and repeated this process many times until it converged on a high-quality set of rules.\n\nBalanced means the algorithm was told to optimise equally for: good returns, low drawdowns, high win rate, and smooth performance. No single factor dominates.\n\nIn practice: moderate position sizes, mid-range strike distances from current BTC price, standard expiry windows. It aims to produce consistent income without taking outsized risks. Think of it as the 'sensible default' — steady, not flashy.\n\nThis is usually a good starting point before you've run the specialised strategies.",
  },
  strategy_max_yield: {
    title: "Evolved: Max Yield — How It Works",
    body: "This strategy was trained with one goal: earn as much premium income as possible. Risk was not part of the objective.\n\nIn practice, it will find parameters like: strikes closer to the current BTC price (higher delta), shorter expiry windows, larger position sizes relative to your account. All of these mean fatter premiums — but also more exposure.\n\nWhen it works: in a calm or rising BTC market, this strategy generates significantly more income per trade than conservative approaches. Annualised yield will look very attractive.\n\nWhen it doesn't: if BTC drops sharply, the strikes are close enough that losses can be meaningful. Drawdown periods will be steeper than other strategies.\n\nBest suited for: traders who understand the risk, have a higher risk tolerance, and are willing to ride out rough patches for higher long-term income. Not recommended as your first strategy to run.",
  },
  strategy_safest: {
    title: "Evolved: Safest — How It Works",
    body: "This strategy was trained with one goal: lose as rarely and as little as possible. Return was secondary.\n\nIn practice: strikes are set well below current BTC price (low delta — maybe 5–10% chance of the option expiring in-the-money), longer expiry windows, smaller position sizes, larger cash reserves kept on the sideline.\n\nBTC would need to fall significantly — often 15–25% or more — before this strategy takes a loss. Most trades expire safely and the premium is collected in full.\n\nThe tradeoff: because the strikes are so far away, the premiums are smaller. Annualised yield will be lower than other strategies. But the equity curve will be much smoother — fewer surprises, smaller drawdowns.\n\nBest suited for: capital preservation, first-time automated trading, or situations where you need the money to be relatively safe and predictable. Also good in uncertain or volatile BTC markets.",
  },
  strategy_sharpe: {
    title: "Evolved: Sharpe — How It Works",
    body: "The Sharpe ratio measures how much return you earn per unit of risk. A Sharpe of 1.0 is decent. 2.0 is considered excellent by professional standards. This strategy was trained to maximise that ratio — the most return possible for the least risk taken.\n\nThis is the metric that professional fund managers use to evaluate whether a strategy is actually good or just got lucky. High return with low volatility = high Sharpe.\n\nIn practice: the bot finds a middle ground — strikes not too close (avoiding unnecessary risk), not too far (keeping premiums meaningful), position sizes carefully managed, and trades only taken when conditions are genuinely favourable.\n\nThis strategy tends to perform well across different market conditions — it won't top the charts in a bull run, but it also won't blow up in a correction. It's designed to be robust.\n\nBest suited for: traders who want to be serious about this long-term. It's the most 'professionally optimised' of all four goals, and usually the best choice once you've run all four and are picking one to run live.",
  },
  // Dashboard
  bot_status: {
    title: "Bot Status",
    body: "Shows whether the automated trading bot is currently running or stopped.\n\nRunning (green dot): the bot is active, watching the market, and will open or manage positions according to your settings.\n\nStopped (red dot): the bot is inactive. No new trades will be opened and no automatic management will occur. Any open position stays open but unmonitored — you'd need to manage it manually or restart the bot.",
  },
  paper_mode: {
    title: "Paper vs Live Mode",
    body: "Paper mode means the bot is trading with simulated money — it goes through all the real motions (checking prices, selecting strikes, 'placing orders') but no real money moves and nothing happens on your actual Deribit account.\n\nPaper mode is safe to run indefinitely. Use it to observe the strategy, build confidence, and verify the bot is behaving correctly before committing real funds.\n\nLive mode uses real money on your real Deribit account. Only switch to live when you're satisfied the strategy works and you understand the risks.",
  },
  heartbeat: {
    title: "Last Heartbeat",
    body: "The bot sends a 'heartbeat' signal every minute to confirm it's still alive and running.\n\nIf the last heartbeat was recent (within the last few minutes), the bot is healthy. If it was a long time ago, the bot may have crashed or been interrupted — check the logs or restart it.\n\nThis is your early warning system that something might have gone wrong.",
  },
  // Trades
  trade_pnl: {
    title: "P&L (Profit & Loss)",
    body: "How much money this individual trade made or lost.\n\nPositive = you kept some or all of the premium collected when you sold the option.\n\nNegative = BTC moved against your position and the option expired in-the-money, meaning you had to buy BTC at the strike price when the market price was lower — a loss.\n\nThe wheel strategy aims for the vast majority of trades to be profitable, with occasional small losses that are more than offset by consistent premium income.",
  },
  trade_reason: {
    title: "Close Reason",
    body: "Why this position was closed:\n\nExpired — the option reached its expiry date with BTC above the strike price. You keep the full premium. This is the ideal outcome.\n\nStopped — the bot closed the position early, usually because risk limits were hit or market conditions changed significantly.\n\nManual — you closed the position yourself using the 'Close Position' button.\n\nAssigned — BTC fell below the strike and you were 'assigned' — meaning you effectively bought BTC at the strike price. In the wheel strategy this isn't necessarily bad; you'd then sell covered calls on that BTC.",
  },
  trade_dte: {
    title: "DTE at Entry / Close",
    body: "DTE = Days To Expiry.\n\nDTE at Entry: how many days were left on the option when the bot opened the trade. A higher number means the option had more time value.\n\nDTE at Close: how many days were left when the trade was closed. If this is 0, the option expired naturally. If it's higher, the position was closed early.\n\nThe difference between entry and close DTE tells you how long you were in the trade.",
  },
  trade_instrument: {
    title: "Instrument",
    body: "The specific option contract that was traded.\n\nFormat: BTC-DDMMMYY-STRIKE-TYPE\n\nExample: BTC-25APR25-70000-P means:\n• BTC = Bitcoin options\n• 25APR25 = expires 25 April 2025\n• 70000 = strike price of $70,000\n• P = Put option (you sold the right for someone to sell BTC to you at $70,000)\n\nA Put option profits when BTC stays above the strike price.",
  },
  // Optimizer metrics
  sharpe_ratio: {
    title: "Sharpe Ratio",
    body: "A measure of how much return a strategy earns relative to the risk it takes.\n\nSimply: how smooth and efficient is the profit curve?\n\nBelow 0.5 = poor — barely worth the risk\n0.5–1.0 = acceptable\n1.0–2.0 = good — this is what professional funds aim for\nAbove 2.0 = excellent\n\nA strategy with a high Sharpe ratio earns consistent returns without wild swings. A low Sharpe means returns are erratic — sometimes great, sometimes painful.",
  },
  max_drawdown: {
    title: "Max Drawdown",
    body: "The largest peak-to-trough loss the strategy experienced during the test period.\n\nExample: if your account grew to $10,000 then fell to $8,500 before recovering, that's a 15% drawdown.\n\nThis is one of the most important risk metrics. It tells you the worst-case pain you'd have experienced if you ran this strategy in the past.\n\nA low drawdown means the strategy is resilient. A high drawdown means you'd need a strong stomach to stick with it through rough patches.",
  },
  win_rate: {
    title: "Win Rate",
    body: "The percentage of trades that were profitable.\n\nA win rate of 80% means 8 out of every 10 trades made money.\n\nFor options selling strategies like the wheel, win rates of 70–90% are common and normal — you win most trades but occasionally take a larger loss.\n\nImportant: a high win rate doesn't guarantee profitability. If your losses are much bigger than your wins, you can still lose money overall. Always look at win rate alongside P&L and drawdown.",
  },
  fitness_score: {
    title: "Fitness Score",
    body: "An internal score the optimizer uses to rank strategies against each other.\n\nIt's a combined measure — not a single thing like return or Sharpe, but a weighted formula that scores a strategy across multiple dimensions (return, consistency, win rate, drawdown).\n\nHigher is better, but the exact number is only meaningful when comparing two strategies optimised with the same goal. A fitness of 7.0 in Max Yield mode vs a 7.0 in Safest mode don't mean the same thing.",
  },
  return_pct: {
    title: "Total Return %",
    body: "The overall percentage gain or loss over the entire test period.\n\nExample: +45% means the strategy grew a $10,000 account to $14,500 over the backtest.\n\nImportant context: this number alone doesn't tell the whole story. A 100% return with extreme volatility and 50% drawdowns is a very different experience from a 40% return with smooth, consistent growth. Always read return alongside Sharpe ratio and max drawdown.",
  },
  // Optimizer modes
  sweep_mode: {
    title: "Parameter Sweep",
    body: "Tests every combination of settings across a defined grid to find which individual values perform best.\n\nThink of it as a systematic survey: for IV Threshold, test 0.1, 0.2, 0.3... all the way up. For each value, run a full backtest and record the score. At the end, pick the best value for each parameter.\n\nStrength: thorough and easy to understand.\nLimitation: it optimises each parameter in isolation — it doesn't discover combinations where two settings work exceptionally well together. For that, use Evolution.\n\nSweep is fast and gives you a solid 'best individual settings' baseline.",
  },
  evolve_mode_desc: {
    title: "Evolution Optimizer",
    body: "Finds the best combination of all settings working together, using a genetic algorithm — the same principle as biological evolution.\n\nIt starts with hundreds of random strategy configurations. Each generation, the best performers survive, combine their settings (like breeding), and small random mutations are introduced. Over many generations, the population converges on genuinely high-quality combinations.\n\nStrength: discovers synergies between settings that a sweep can't find. Often finds significantly better results.\nLimitation: slower than sweep, and results can vary between runs due to randomness.\n\nUse sweep first to understand which parameters matter, then evolution to find the best overall combination.",
  },
  // Walk-forward specific
  is_fitness: {
    title: "In-Sample (IS) Fitness",
    body: "The strategy's score on the historical data it was trained on — the 75% of history used to find the best parameters.\n\nThis number is expected to be high — the optimizer specifically searched for settings that work well on this data. Think of it as the 'exam score when you've seen the questions before'.\n\nAlways compare IS fitness to OOS fitness to see if the strategy truly generalised.",
  },
  oos_fitness: {
    title: "Out-of-Sample (OOS) Fitness",
    body: "The strategy's score on historical data it has never seen — the 25% of history held back during optimisation.\n\nThis is the real test. The optimizer had no access to this data, so performance here reflects genuine predictive ability rather than memorising history.\n\nIf OOS fitness is close to IS fitness: robust strategy — it generalises well.\nIf OOS fitness is much lower: the strategy was 'overfit' — it learned the specific quirks of the training data and won't work as well going forward.",
  },
  robustness_score: {
    title: "Robustness Score",
    body: "OOS fitness ÷ IS fitness — a simple ratio that tells you how well the strategy held up on unseen data.\n\n1.0 = perfect: performs identically on new data as on training data (rare).\n0.7–1.0 = good: some degradation but the strategy is genuinely working.\n0.5–0.7 = marginal: significant drop-off, worth being cautious.\nBelow 0.5 = poor: the strategy likely overfit and may not work in the future.\n\nThis is one of the most important numbers for deciding whether to trust a strategy.",
  },
  // Monte Carlo
  mc_percentiles: {
    title: "Percentile Results (p5–p95)",
    body: "Monte Carlo runs your strategy 100 times on randomly selected 6-month windows from history. These percentiles show the spread of outcomes:\n\np5 (5th percentile): near-worst case — only 5% of runs did worse than this. This is roughly 'how bad could it get'.\n\np25: below-average run — a bad quarter but not catastrophic.\n\np50 (median): the typical outcome — half the runs did better, half did worse.\n\np75: an above-average run — a good stretch.\n\np95 (95th percentile): near-best case — only 5% of runs did better. This is roughly 'how good could it get'.\n\nA tight spread (p5 close to p95) means consistent performance. A wide spread means highly variable results — good sometimes, rough other times.",
  },
  prob_profit: {
    title: "Probability of Profit",
    body: "Out of the 100 random 6-month windows tested in Monte Carlo, what percentage were profitable?\n\n90% means 90 out of 100 random historical periods would have made money with this strategy.\n\nThis gives you a sense of how reliably the strategy works across different market conditions — bull markets, bear markets, sideways markets, high volatility, low volatility. A strategy with 90%+ probability of profit across random historical periods is genuinely robust.",
  },
  // Settings
  otm_offset: {
    title: "OTM Offset",
    body: "An additional buffer applied when selecting the strike price, on top of the delta-based calculation.\n\nOTM = Out of The Money — meaning the strike price is below the current BTC price (for a put option).\n\nA higher offset pushes the strike further away from current BTC price, making the trade safer (BTC needs to fall further to cause a loss) but reducing the premium collected.\n\nThink of it as an extra safety margin on top of the delta filter.",
  },
  starting_equity: {
    title: "Starting Equity",
    body: "The account size used in backtests and evolution calculations.\n\nThis should match your actual Deribit account balance for the most accurate simulation results. If you set this to $10,000 but your real account has $6,000, position sizing in backtests will be wrong and results won't translate accurately to real trading.\n\nThis setting doesn't affect live trading directly — the bot uses your real account balance for live trades. It's used purely for simulation accuracy.",
  },
  regime_filter: {
    title: "Regime Filter",
    body: "A market condition filter that tries to detect whether BTC is in a healthy uptrend (safe to sell puts) or a dangerous downtrend (avoid new trades).\n\nWhen enabled: the bot checks whether BTC's price is above its moving average. If BTC is trending down, the bot skips opening new positions until conditions improve.\n\nWhen disabled: the bot trades regardless of overall BTC trend direction.\n\nEnabling this typically reduces trade frequency but can significantly reduce losses during sustained BTC downturns. Worth enabling in volatile market conditions.",
  },
}
