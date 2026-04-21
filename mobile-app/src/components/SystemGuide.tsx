interface Props {
  onClose: () => void
}

const SECTIONS = [
  {
    icon: '🎯',
    title: 'The Core Strategy — Selling Options',
    paras: [
      'The bot runs a strategy called the "Wheel" — one of the most popular income-generating strategies in options trading.',
      'Here\'s the core idea: when people are nervous about a market, they pay more for insurance. On Bitcoin, that insurance takes the form of "put options" — contracts that pay out if BTC falls below a certain price. The bot sells that insurance. In return, it collects a premium upfront, which it keeps regardless of what happens next.',
      'Specifically, the bot sells "short puts." You promise to buy Bitcoin at a set price (the strike price) if it falls there — and you\'re paid immediately for making that promise. If BTC stays above your strike price when the contract expires, you keep the entire premium and the trade is done. That\'s the win condition.',
      'If BTC falls below your strike, you buy BTC at the strike price (usually above market price at that moment). This sounds bad, but in the wheel strategy it\'s just the next step — you then sell "covered calls" on that BTC until you\'ve recovered the difference. The wheel keeps turning.',
    ],
  },
  {
    icon: '📊',
    title: 'Why Implied Volatility Matters',
    paras: [
      'Not all options are priced equally. When Bitcoin is calm and steady, options are cheap — there\'s little fear, so insurance isn\'t expensive. When Bitcoin is volatile or uncertain, options get expensive — people are scared and willing to pay a lot for protection.',
      'The bot only sells options when they\'re expensive — when "IV Rank" is high. IV Rank (Implied Volatility Rank) measures how expensive options are right now compared to the past year. A rank of 0.8 means options are in the top 20% of their most expensive ever. That\'s when you want to be selling.',
      'Selling expensive options and letting them decay to zero is the core edge of this strategy. The bot waits for the right conditions rather than trading constantly.',
    ],
  },
  {
    icon: '⚙️',
    title: 'The Parameter Sweep — Finding Good Starting Points',
    paras: [
      'Before the bot trades, it needs to know what settings work best. The Parameter Sweep is like a systematic survey.',
      'It tests thousands of combinations: What IV Rank threshold should we require? How close to BTC\'s price should the strike be? How many days until expiry? For each setting, it runs a full historical simulation (backtest) and records how well it performed.',
      'The sweep finds the best value for each individual setting in isolation. It gives you a solid, data-backed starting point — the "Sweep Best" preset. It\'s fast, transparent, and easy to understand: "this IV threshold tested best, this delta range tested best."',
    ],
  },
  {
    icon: '🧬',
    title: 'The Evolution Optimizer — Finding Combinations That Win Together',
    paras: [
      'The Parameter Sweep finds the best individual settings — but settings interact. A great IV threshold combined with a poor delta range might underperform. The Evolution Optimizer finds combinations that work well together.',
      'It uses a genetic algorithm — the same principle as biological evolution. It starts with hundreds of random strategy configurations. Each "generation," the worst performers are eliminated, the best ones combine their settings (like breeding), and small random mutations are introduced. Over many generations, the population evolves toward genuinely high-quality strategies.',
      'You choose what you\'re optimising for: Maximum Yield (highest income), Safest (lowest drawdown), Best Risk-Adjusted (Sharpe ratio), or Balanced. Each goal produces a different strategy — a different evolved "genome" — that you can load and run.',
    ],
  },
  {
    icon: '✅',
    title: 'Walk-Forward Validation — Testing for Real-World Robustness',
    paras: [
      'A strategy that works perfectly on historical data might just have memorised the past. Walk-Forward validation checks whether the strategy generalises to data it\'s never seen.',
      'The test splits history in two: the bot uses the first 75% to optimise parameters (In-Sample), then tests those exact parameters on the remaining 25% it has never seen (Out-of-Sample). If performance holds up on the unseen data, the strategy is genuinely robust. If it collapses, it was overfit — it learned quirks of the past that won\'t repeat.',
      'The Robustness Score (OOS ÷ IS) is the key number: above 0.7 is good, above 0.9 is excellent.',
    ],
  },
  {
    icon: '🎲',
    title: 'Monte Carlo Simulation — Understanding the Range of Outcomes',
    paras: [
      'One backtest tells you what would have happened in one specific historical period. Monte Carlo runs 100 backtests on 100 randomly selected 6-month windows from history — giving you a distribution of outcomes across many different market conditions.',
      'The result: a range of outcomes from near-worst-case (p5) to near-best-case (p95), and a probability of profit — what percentage of historical periods would have been profitable. A strategy with 90%+ probability of profit across random historical periods is genuinely durable.',
      'This is your stress test. A strategy that only works in one type of market will show a wide, uneven distribution. A truly robust strategy will show consistent results across the range.',
    ],
  },
  {
    icon: '🔄',
    title: 'The Feedback Loop — How It All Connects',
    paras: [
      'The system is designed as a continuous improvement loop:',
      '1. Run a Parameter Sweep to find good individual settings.\n2. Run Evolution (with your chosen goal) to find the best combination.\n3. Validate with Walk-Forward and Monte Carlo to confirm robustness.\n4. Load the best preset into the live config.\n5. Start the bot — it trades using those settings in paper mode first.\n6. Monitor real results in the Trades and Dashboard tabs.\n7. Periodically re-run the optimizers as market conditions change.',
      'The bot doesn\'t predict the future. It finds rules that have worked consistently across many historical conditions and applies them systematically — removing emotion, discipline failures, and inconsistency from the equation.',
    ],
  },
  {
    icon: '🛡',
    title: 'Risk Management',
    paras: [
      'Several layers of protection run at all times:',
      'Position sizing limits ensure no single trade risks too much of your account. The Max Leg Size setting caps how much capital any one position can use.',
      'The free equity buffer (Min Free Equity) keeps a cash reserve at all times — so you\'re never fully deployed and always have room to absorb a loss.',
      'The IV Rank filter means the bot only trades when conditions are favourable — it sits on its hands during calm, low-premium periods rather than force trades.',
      'The Kill Switch (Stop Bot) instantly halts all new activity. Any open position stays open but no new trades will be placed. The position can then be closed manually or left to expire.',
    ],
  },
  {
    icon: '📱',
    title: 'Using This App',
    paras: [
      'Dashboard: your real-time command centre. See the bot\'s status, active config, current position, and quick action buttons.',
      'Trades: full history of every trade the bot has placed — premium collected, outcome, P&L.',
      'Optimizer: run sweep, evolution, walk-forward, and Monte Carlo. Each builds on the last.',
      'Settings: load a strategy preset (Sweep Best, or any of the four evolved goals), or fine-tune individual parameters manually. Always restart the bot after changing settings.',
    ],
  },
]

export default function SystemGuide({ onClose }: Props) {
  return (
    <div className="fixed inset-0 bg-navy z-50 flex flex-col">
      {/* Header */}
      <div className="flex items-start justify-between px-4 pt-5 pb-4 border-b border-border flex-shrink-0">
        <div>
          <h2 className="text-lg font-bold text-white">How This System Works</h2>
          <p className="text-xs text-slate-400 mt-0.5">A plain-English guide to everything the bot does and why.</p>
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
