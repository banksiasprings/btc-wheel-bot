interface Props {
  onClose: () => void
}

const SECTIONS = [
  {
    icon: '🎯',
    title: 'The Core Strategy — Wheel on Bitcoin Options',
    paras: [
      'The bot runs the Wheel strategy on Deribit BTC options. It repeatedly sells cash-secured PUT options — contracts that pay out to the buyer if BTC falls below a set price. In return the bot collects a cash premium upfront and keeps it regardless of what happens next.',
      'If BTC stays above the strike at expiry, the contract expires worthless and the full premium is profit. If BTC falls below the strike and the option is assigned, the bot takes delivery of BTC and transitions to selling covered CALLs above the cost basis — completing the "wheel" back to cash.',
      'The edge comes from selling options that are priced expensively relative to how much BTC actually moves (implied volatility exceeds realised volatility). Over many trades, the premium collected consistently exceeds the losses without needing to predict price direction.',
    ],
  },
  {
    icon: '📊',
    title: 'IV Rank — Only Trading When Conditions Are Favourable',
    paras: [
      'Not all options are priced equally. When Bitcoin is calm, options are cheap. When Bitcoin is volatile or fearful, options get expensive — people pay a premium for protection.',
      'The bot only sells when options are expensive — when IV Rank is high. IV Rank measures how expensive options are right now versus the past year. A rank of 0.8 means options are priced in the top 20% of their historical range. That\'s when selling is most profitable.',
      'This filter means the bot sits on its hands during low-premium conditions rather than forcing trades that don\'t cover costs. Patience is part of the edge.',
    ],
  },
  {
    icon: '🔬',
    title: 'The Pipeline — A 4-Step Workflow Before Going Live',
    paras: [
      'Before any config touches real capital it must pass through a structured validation pipeline. Each step builds on the last:',
      'Step 1 — Evolve: A genetic algorithm tests hundreds of strategy configurations across history and evolves toward whatever goal you choose (max yield, safest, best Sharpe, balanced, or capital ROI). The result is an optimised "genome" — a full set of parameters that have proven effective.',
      'Step 2 — Validate: The evolved config is stress-tested two ways. Walk-Forward splits history into In-Sample (training) and Out-of-Sample (unseen) periods and checks whether performance holds up on data the bot has never seen. Monte Carlo runs 100 backtests across 100 random 6-month windows, giving you a probability of profit and a distribution of outcomes across many market conditions.',
      'Step 2.5 — AI Review: An AI assistant reviews the config\'s key metrics and flags anything unusual — high drawdown, low trade count, unrealistic Sharpe, correlation with known market quirks. A second opinion before committing.',
      'Step 3 — Black Swan: The config is tested against 6 extreme historical scenarios: the 2020 COVID crash, the 2022 bear market, the 2021 bull run, the 2023 recovery, and 2 synthetic extremes (prolonged sideways and a flash crash with V-recovery). Critical failures block Go Live.',
      'Step 4 — Go Live: Once all gates pass, the config is promoted and the bot starts trading with it.',
    ],
  },
  {
    icon: '🧬',
    title: 'The Evolution Optimizer — Configs That Win Together',
    paras: [
      'The Evolution Optimizer uses a genetic algorithm — the same principle as biological evolution. It starts with a population of hundreds of random strategy configurations. Each generation, the worst performers are eliminated, the best ones combine their settings (like breeding), and small random mutations are introduced. Over many generations the population evolves toward genuinely high-quality strategies.',
      'You choose what you\'re optimising for: Maximum Yield (highest income), Safest (lowest drawdown), Best Sharpe (risk-adjusted returns), Balanced, Capital ROI, or Daily Trader (maximum trade frequency for pipeline testing). Each goal produces a different evolved genome.',
      'Key metrics reported for each genome: total return %, annualised return, Sharpe ratio, win rate, max drawdown, number of trades, trades per year, and average P&L per trade. Trades per year and avg P&L give you a feel for how active and how profitable each individual trade is on average.',
    ],
  },
  {
    icon: '✅',
    title: 'Walk-Forward & Monte Carlo — Proving Real-World Robustness',
    paras: [
      'Walk-Forward validation checks whether a strategy generalises beyond the data it was optimised on. The test splits history 75/25: In-Sample (training) and Out-of-Sample (never seen). The Robustness Score (OOS ÷ IS) is the key number — above 0.7 is good, above 0.9 is excellent. A strategy that collapses on unseen data was overfit to the past.',
      'Monte Carlo runs 100 backtests on 100 randomly selected 6-month windows from history, producing a distribution of outcomes. The result is a probability of profit (what % of historical periods were profitable) and a p5–p95 range showing the spread from near-worst to near-best case. A robust strategy shows consistent results across the full range.',
    ],
  },
  {
    icon: '🦢',
    title: 'Black Swan Test — Surviving Extreme Markets',
    paras: [
      'The Black Swan step tests the config against the worst markets Bitcoin has ever seen — plus two synthetic extremes the optimizer has never been trained on.',
      'The 6 scenarios: (1) COVID Crash Mar 2020 — BTC -50% in 2 weeks. (2) 2022 Bear Market — slow 12-month grind from $60k to $16k. (3) 2021 Bull Run — explosive upside. (4) 2023 Recovery — sustained trend reversal. (5) Synthetic Flash Crash — -40% over 3 days with IV spike to 200%, then V-recovery. (6) Synthetic Flatline — 90 days of sideways price action with suppressed volatility.',
      'Each scenario is labelled PASS, PARTIAL, or FAIL based on drawdown and return thresholds. Scenarios with severity weight 5 are critical gates — if the config fails them, Go Live is blocked. Lower-severity failures are advisory only.',
    ],
  },
  {
    icon: '🤖',
    title: 'The Farm — Running Multiple Configs Simultaneously',
    paras: [
      'The Farm runs multiple bot instances in parallel, each with a different config. This lets you test several strategies at once — a conservative low-delta config alongside a more aggressive high-yield one, for example — and compare real performance side by side.',
      'Each bot in the Farm is a separate process managed by a supervisor. Bots can be started, stopped, and assigned different configs independently. The Farm tab shows all active bots with live status, their current position (if any), equity, and a real-time BTC price ticker.',
      'The Performance tab ranks all Farm bots by Sharpe ratio. Expanding any bot shows its full equity curve, trade history, and key metrics — win rate, max drawdown, annualised return, and trades count. This is where you see which config is actually delivering in live conditions.',
    ],
  },
  {
    icon: '🛡',
    title: 'Risk Management & Emergency Controls',
    paras: [
      'Position sizing: Max Equity Per Leg caps how much capital any single option position uses. Min Free Equity keeps a cash reserve so you\'re never fully deployed. These limits are enforced on every trade entry.',
      'Risk warnings: The Trading tab shows real-time position health. If an open option is approaching its strike or losing premium faster than expected, a visual warning appears with the option\'s current status.',
      'Emergency close: A dedicated emergency button on the Trading tab closes the open option position immediately. Designed for moments when you need to exit fast without navigating menus.',
      'Kill switch: The Stop Bot button halts all new trade activity instantly. Existing positions stay open but no new trades will be placed. Use this if you want to pause the bot and manage positions manually.',
      'IV Rank filter: The bot won\'t trade unless conditions meet the configured threshold. This natural gate prevents trading into low-premium, unfavourable conditions.',
    ],
  },
  {
    icon: '📱',
    title: 'App Architecture — 5 Tabs',
    paras: [
      'Farm: Real-time status of all running bots. Start/stop individual bots, assign configs, view open positions. BTC price ticker and emergency close are always visible here.',
      'Trading: Live TradingView chart for market context. Monitor BTC price action while the bot runs.',
      'Performance: Per-bot equity curves and trade history. All Farm bots ranked by Sharpe. Expand any bot to see its full track record.',
      'Pipeline: The 4-step workflow (Evolve → Validate → AI Review → Black Swan → Go Live). Everything you need to develop, test, and deploy a new config. Also shows all saved configs and their validation status.',
      'Settings: API connection, Telegram notifications, Config Library (save/load/promote named configs), Trading Mode toggle (paper/live), and this guide.',
    ],
  },
  {
    icon: '🔄',
    title: 'The Full Loop — From Idea to Live Capital',
    paras: [
      '1. Run Evolution on the Pipeline tab — choose a goal and let the optimizer run for 5–20 minutes.\n2. Save the winning genome as a named config.\n3. Run Walk-Forward and Monte Carlo validation on that config.\n4. Run AI Review for a second-opinion quality check.\n5. Run Black Swan — confirm the config survives extreme scenarios.\n6. Promote the config to a Farm bot.\n7. Monitor live performance in the Performance tab.\n8. Periodically re-run the pipeline as market conditions change.',
      'The bot doesn\'t predict the future. It finds rules that have worked consistently across many historical market conditions and applies them systematically — removing emotion, discipline failures, and inconsistency from the equation.',
    ],
  },
]

export default function SystemGuide({ onClose }: Props) {
  return (
    <div className="fixed inset-0 bg-navy z-50 flex flex-col">
      {/* Header */}
      <div className="flex items-start justify-between px-4 pt-5 pb-4 border-b border-border flex-shrink-0">
        <div>
          <h2 className="text-lg font-bold text-white">Strategy & Architecture Guide</h2>
          <p className="text-xs text-slate-400 mt-0.5">How the system works — from pipeline to live trading.</p>
        </div>
        <button
          onClick={onClose}
          className="text-slate-400 hover:text-white text-lg leading-none flex-shrink-0 ml-4 mt-0.5"
        >
          ✕
        </button>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4 pb-8">
        {SECTIONS.map((s, i) => (
          <div key={i} className="bg-card border border-border rounded-2xl p-4">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-xl leading-none">{s.icon}</span>
              <div>
                <span className="text-xs text-slate-500 font-medium">{i + 1} of {SECTIONS.length}</span>
                <h3 className="text-sm font-bold text-white leading-snug">{s.title}</h3>
              </div>
            </div>
            <div className="space-y-2.5">
              {s.paras.map((p, j) => (
                <p key={j} className="text-slate-300 text-sm leading-relaxed whitespace-pre-line">{p}</p>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
