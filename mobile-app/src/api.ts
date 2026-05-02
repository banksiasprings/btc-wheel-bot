// API client — reads URL and key from localStorage

export interface StatusData {
  bot_running: boolean
  paused?: boolean
  mode: 'paper' | 'live' | 'stopped'
  uptime_seconds: number
  last_heartbeat: string | null
}

export interface HedgeData {
  enabled: boolean
  perp_position_btc: number
  avg_entry_price: number
  unrealised_pnl_usd: number | null
  realised_pnl_usd: number
  funding_paid_usd: number
  rebalance_count: number
}

export interface PositionData {
  open: boolean
  type?: string
  strike?: number
  contracts?: number
  expiry?: string
  entry_date?: string
  premium_collected?: number
  current_spot?: number
  unrealized_pnl_usd?: number
  unrealized_pnl_pct?: number
  days_to_expiry?: number
  current_delta?: number
  net_delta?: number | null
  hedge?: HedgeData | null
}

export interface EquityData {
  dates: string[]
  equity: number[]
  starting_equity: number
  current_equity: number
  total_return_pct: number
}

export interface Trade {
  timestamp: string
  instrument: string
  option_type: string
  strike: number
  entry_price: number
  exit_price: number
  contracts: number
  pnl_btc: number
  pnl_usd: number
  equity_before: number
  equity_after: number
  btc_price: number
  dte_at_entry: number
  dte_at_close: number
  reason: string
  mode: string
}

export interface OptimizerSummary {
  last_run: string | null
  best_fitness: number | null
  best_genome: Record<string, unknown> | null
  monte_carlo: Record<string, unknown> | null
  walk_forward: Record<string, unknown> | null
  reconciliation: Record<string, unknown> | null
  last_sweep_timestamp: string | null
  sweep_params_count: number
}

export interface SweepEntry {
  value: number
  fitness: number
  sharpe: number
  return_pct: number
  win_rate: number
  drawdown: number
}

export interface SweepResults {
  params: string[]
  results: Record<string, SweepEntry[]>
  best_per_param: Record<string, { value: number; fitness: number }>
  timestamp: string | null
}

export interface EvolveGenome {
  fitness: number
  sharpe: number
  return_pct: number
  win_rate: number
  drawdown: number
  num_cycles: number
  trades_per_year?: number
  avg_pnl_per_trade_usd?: number
  // Capital-efficiency metrics (optimizer.py emits these as of 2026-05-01).
  // Surfaced in the Pipeline winner card so users can pick small-capital,
  // high-margin-ROI configs — the user's stated thesis.
  annualised_margin_roi?: number     // total_return / lookback_years / avg_margin_util
  premium_on_margin?: number         // total_premium_collected / total_margin_deployed
  min_viable_capital?: number        // smallest equity at which any trade fired
  avg_margin_utilization?: number    // mean fraction of equity held as margin
}

export interface EvolveResults {
  top_genomes: EvolveGenome[]
  total_evaluated: number
  timestamp: string | null
}

export interface EvolveHistoryEntry {
  version: number
  timestamp: string
  goal: string
  fitness: number
  return_pct: number
  sharpe: number
  win_rate: number
  drawdown: number
  num_cycles?: number
  trades_per_year?: number
  avg_pnl_per_trade_usd?: number
  // Capital-efficiency metrics — see EvolveGenome.
  annualised_margin_roi?: number
  premium_on_margin?: number
  min_viable_capital?: number
  avg_margin_utilization?: number
}

export interface EvolveGoalResult {
  version: number
  timestamp: string | null
  current: EvolveHistoryEntry | null
  previous: EvolveHistoryEntry | null
  delta: { fitness: number; return_pct: number; sharpe: number } | null
  history: EvolveHistoryEntry[]
  available: boolean
}

export type EvolveAllResults = Record<EvolveGoal, EvolveGoalResult>

export interface BotConfig {
  iv_rank_threshold: number | null
  delta_target_min: number | null
  delta_target_max: number | null
  min_dte: number | null
  max_dte: number | null
  max_equity_per_leg: number | null
  min_free_equity_fraction: number | null
  premium_fraction_of_spot: number | null
  starting_equity: number | null
  use_regime_filter: boolean
  regime_ma_days: number | null
}

export interface ConfigHistoryEntry {
  timestamp: string
  preset: string
  params: Record<string, number | null>
}

export interface EvolutionProgress {
  running: boolean
  completed?: boolean
  generation?: number
  total_generations?: number
  elapsed_sec?: number
  best_fitness?: number | null
  best_return_pct?: number | null
  best_sharpe?: number | null
  gen_best_fitness?: number | null
  fitness_goal?: string
}

export interface NotifierConfig {
  configured: boolean
  chat_id: string
  bot_token_hint: string
}

export interface PresetParams {
  iv_rank_threshold?: number | null
  target_delta_min?: number | null
  target_delta_max?: number | null
  min_dte?: number | null
  max_dte?: number | null
  max_equity_per_leg?: number | null
  min_free_equity_fraction?: number | null
  approx_otm_offset?: number | null
  premium_fraction_of_spot?: number | null
  starting_equity?: number | null
}

export interface PresetInfo {
  available: boolean
  fitness: number | null
  timestamp: string | null
  params: PresetParams
}

export type EvolveGoal = 'balanced' | 'max_yield' | 'safest' | 'sharpe' | 'capital_roi' | 'daily_trader' | 'small_bot_specialist'
export type ActivePreset = 'sweep' | `evolve_${EvolveGoal}` | 'custom'

export interface PresetsData {
  active: ActivePreset
  sweep: PresetInfo
  evolve_balanced: PresetInfo
  evolve_max_yield: PresetInfo
  evolve_safest: PresetInfo
  evolve_sharpe: PresetInfo
  evolve_capital_roi: PresetInfo
  current: { params: PresetParams }
}

const DEFAULT_API_URL = 'https://bot.banksiaspringsfarm.com'
const DEFAULT_API_KEY = ''  // Never hardcode — entered via SetupScreen and stored in localStorage

function getBase(): string {
  return (localStorage.getItem('api_url') || DEFAULT_API_URL).replace(/\/$/, '')
}

function getKey(): string {
  return localStorage.getItem('api_key') || DEFAULT_API_KEY
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${getBase()}${path}`, {
    ...init,
    headers: {
      'X-API-Key': getKey(),
      'Content-Type': 'application/json',
      ...(init.headers ?? {}),
    },
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

// Backtest (cross-surface — mirrors what the dashboard's Backtest tab
// returns). All param overrides are optional; the server falls back to
// config.yaml defaults for anything omitted.
export interface BacktestParams {
  iv_rank_threshold?:        number
  target_delta_min?:         number
  target_delta_max?:         number
  min_dte?:                  number
  max_dte?:                  number
  max_equity_per_leg?:       number
  min_free_equity_fraction?: number
  lookback_months?:          number
  starting_equity?:          number
}

export interface BacktestMetrics {
  num_cycles:               number
  starting_equity:          number
  ending_equity:            number
  total_return_pct:         number
  annualized_return_pct:    number
  sharpe_ratio:             number
  sortino_ratio:            number
  max_drawdown_pct:         number
  win_rate_pct:             number
  avg_premium_yield_pct:    number
  trades_per_year:          number
  avg_pnl_per_trade_usd:    number
  total_margin_deployed:    number
  avg_margin_utilization:   number
  premium_on_margin:        number
  min_viable_capital:       number
  annualised_margin_roi:    number
}

export interface BacktestResult {
  ok:          boolean
  params_used: Required<BacktestParams>
  metrics:     BacktestMetrics
}

export const runBacktest = (params: BacktestParams) =>
  request<BacktestResult>('/backtest/run', {
    method: 'POST',
    body:   JSON.stringify(params),
  })


// Forecast snapshots (cross-surface consistency — mirrors what the
// dashboard's Forecasts tab shows). Each snapshot freezes the backtest
// forecast at a point in time; `validate_after` is when it becomes
// comparable to actual trades. Status is derived server-side.
export interface ForecastSnapshotSummary {
  bot:               string | null   // null = main bot, else farm slug
  snapshot_id:       string
  created_at:        string
  validate_after:    string
  horizon_days:      number | null
  note:              string
  status:            'pending' | 'due' | 'pass' | 'warning' | 'fail' | 'unknown'
  forecast_return:   number | null
  forecast_drawdown: number | null
  forecast_trades:   number | null
  actual_return:     number | null
  actual_drawdown:   number | null
  actual_trades:     number | null
  findings_count:    number
}

export interface ForecastSnapshotsList {
  snapshots: ForecastSnapshotSummary[]
  available: boolean
  count:     number
}

export const getForecastSnapshots = () =>
  request<ForecastSnapshotsList>('/forecasts/snapshots')

export const getForecastSnapshotDetail = (snapshotId: string, bot?: string | null) => {
  const qs = bot ? `?bot=${encodeURIComponent(bot)}` : ''
  return request<Record<string, unknown>>(`/forecasts/snapshots/${snapshotId}${qs}`)
}

export const getStatus = () => request<StatusData>('/status')
export const getPosition = () => request<PositionData>('/position')
export const getHedge = () => request<HedgeData>('/hedge')
export const getEquity = () => request<EquityData>('/equity')
export const getTrades = () => request<Trade[]>('/trades')
export const getOptimizerSummary = () => request<OptimizerSummary>('/optimizer/summary')
export const getOptimizerRunning = () => request<{ running: boolean }>('/optimizer/running')
export const getSweepResults  = () => request<SweepResults>('/optimizer/sweep_results')
export const getEvolveResults    = () => request<EvolveResults>('/optimizer/evolve_results')
export const getEvolveResultsAll = () => request<EvolveAllResults>('/optimizer/evolve_results_all')
export const getConfig = () => request<BotConfig>('/config')

export const startBot = () => request<{ ok: boolean; message: string }>('/controls/start', { method: 'POST' })
export const stopBot = () =>
  request<{ ok: boolean; message: string }>('/controls/stop', {
    method: 'POST',
    body: JSON.stringify({ confirm: 'STOP_BOT' }),
  })
export const closePosition = () =>
  request<{ ok: boolean; message: string }>('/controls/close_position', {
    method: 'POST',
    body: JSON.stringify({ confirm: 'CLOSE_POSITION' }),
  })
export const setMode = (mode: string, confirm?: string) =>
  request<{ ok: boolean; message: string }>('/controls/set_mode', {
    method: 'POST',
    body: JSON.stringify({ mode, confirm }),
  })

// Global pause toggles the KILL_SWITCH file. Bot processes stay alive; new
// entries are blocked while paused. Existing positions still settle naturally.
export const getTradingPaused = () =>
  request<{ paused: boolean }>('/controls/trading_paused')
export const pauseTrading = () =>
  request<{ ok: boolean; paused: boolean }>('/controls/pause_trading', { method: 'POST' })
export const resumeTrading = () =>
  request<{ ok: boolean; paused: boolean }>('/controls/resume_trading', { method: 'POST' })
export const getPresets = () => request<PresetsData>('/config/presets')
export const loadPreset = (preset: Exclude<ActivePreset, 'custom'>) =>
  request<{ ok: boolean; preset: string; params_updated: string[] }>('/config/load_preset', {
    method: 'POST',
    body: JSON.stringify({ preset }),
  })
export const updateConfig = (config: Partial<BotConfig>) =>
  request<{ ok: boolean; updated: string[] }>('/config', {
    method: 'POST',
    body: JSON.stringify(config),
  })
export const runOptimizer = (mode: string, param?: string, fitness_goal?: string, config_name?: string | null, seed_config_name?: string | null) =>
  request<{ ok: boolean; pid: number; mode: string }>('/optimizer/run', {
    method: 'POST',
    body: JSON.stringify({ mode, param, fitness_goal, config_name: config_name ?? undefined, seed_config_name: seed_config_name ?? undefined }),
  })

export const getBtcPrice = () => request<{ price: number; cached: boolean; age_sec: number }>('/market/btc_price')
export const getConfigHistory = () => request<ConfigHistoryEntry[]>('/config/history')
export const getOptimizerProgress = () => request<EvolutionProgress>('/optimizer/progress')
export const getNotifierConfig = () => request<NotifierConfig>('/notifications/config')
export const setupNotifier = (bot_token: string, chat_id: string) =>
  request<{ ok: boolean }>('/notifications/setup', {
    method: 'POST',
    body: JSON.stringify({ bot_token, chat_id }),
  })
export const testNotifier = () => request<{ ok: boolean }>('/notifications/test', { method: 'POST' })

export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
}

export interface TradeMarker {
  entry_time: number
  exit_time: number | null
  strike: number | null
  pnl_usd: number
  won: boolean
  reason: string
}

export interface ChartOverlays {
  zone_upper: number | null
  zone_center: number | null
  zone_lower: number | null
  active_strike: number | null
  breakeven: number | null
  expiry_ts: number | null
}

export interface ChartConfig {
  otm_offset: number
  target_delta_min: number
  target_delta_max: number
  min_dte: number
  max_dte: number
  max_equity_per_leg: number
  iv_rank_threshold: number
  premium_fraction: number
  starting_equity: number
  max_adverse_delta?: number
}

export interface ChartData {
  candles: Candle[]
  current_price: number | null
  resolution: string
  overlays: ChartOverlays
  config: ChartConfig
  trade_markers: TradeMarker[]
}

export const getChartData = (days: number, botId?: string) =>
  request<ChartData>(`/chart/btc_history?days=${days}${botId ? `&bot_id=${encodeURIComponent(botId)}` : ''}`)

// ── IV rank history (for the gauge sparkline) ────────────────────────────────
export interface IvRankPoint { ts: number; iv_rank: number }
export interface IvRankHistory {
  points:    IvRankPoint[]
  available: boolean
  current:   number | null
}
export const getIvRankHistory = (days: number, botId?: string) =>
  request<IvRankHistory>(`/chart/iv_rank?days=${days}${botId ? `&bot_id=${encodeURIComponent(botId)}` : ''}`)

// ── Farm API ──────────────────────────────────────────────────────────────────

export interface BotReadinessChecks {
  min_trades: boolean
  min_days: boolean
  sharpe: boolean
  drawdown: boolean
  win_rate: boolean
  walk_forward: boolean
  reconcile: boolean
  no_kill_switch: boolean
}

export interface BotReadiness {
  score: number
  total: number
  ready: boolean
  checks: BotReadinessChecks
}

export interface BotMetrics {
  num_trades: number
  win_rate: number
  total_return_pct: number
  sharpe: number
  max_drawdown: number
  current_equity: number
  starting_equity: number
  days_running: number
}

export interface BotFarmEntry {
  id: string
  name: string
  description: string
  status: 'running' | 'stopped' | 'error'
  pid: number | null
  uptime_hours: number
  days_running: number
  config_name: string | null     // named config currently assigned, if any
  config_summary: Record<string, number | null>
  metrics: BotMetrics
  readiness: BotReadiness
  has_open_position?: boolean
  position_risk?: 'ok' | 'caution' | 'danger'
  paused?: boolean
  open_position?: {
    type: string | null
    strike: number | null
    expiry: string | null
    dte: number | null
    pnl_usd: number | null
    pnl_pct: number | null
    current_spot: number | null
    current_delta: number | null
    premium_collected: number | null
  } | null
}

export interface FarmStatus {
  updated_at: string
  farm_running: boolean
  bots: BotFarmEntry[]
}

export interface ReadinessReport {
  bot_id: string
  ready: boolean
  checks_passed: number
  total_checks: number
  checks: BotReadinessChecks
  metrics: BotMetrics
  recommendation: 'READY FOR LIVE' | 'KEEP TESTING' | 'FAILED — REVIEW CONFIG'
  blocking_issues: string[]
}

export interface BotLiveState {
  bot_id: string
  kill_switch_active: boolean
  heartbeat_age_seconds: number | null
  position: {
    open: boolean
    type?: string
    strike?: number
    expiry?: string
    entry_date?: string
    delta?: number
    current_delta?: number
    net_delta?: number | null
    contracts?: number
    entry_price?: number
    entry_price_btc?: number
    underlying_at_entry?: number
    current_price?: number
    current_spot?: number
    premium_collected?: number
    unrealized_pnl_usd?: number
    unrealized_pnl_pct?: number
    days_to_expiry?: number
    iv_rank_at_entry?: number
    dte?: number
  }
  state: {
    mode?: string
    config_name?: string
    iv_rank?: number
    total_cycles?: number
    total_pnl_usd?: number
    equity_usd?: number
  }
  recent_trades: Trade[]
}

export const getFarmStatus = () => request<FarmStatus>('/farm/status')

export const getBotReadiness = (botId: string) =>
  request<ReadinessReport>(`/farm/bot/${botId}/readiness`)

// "Why is this bot not currently trading?" — distinct from /readiness, which
// is a live-readiness validator over historical trade quality.
export interface WhyNotTradingChecks {
  kill_switch:   { active: boolean; global: boolean; per_bot: boolean }
  heartbeat:     { fresh: boolean; age_seconds: number | null; running: boolean }
  position_open: { open: boolean; instrument: string | null }
  sizing: {
    sufficient: boolean
    equity_usd: number | null
    btc_price: number | null
    max_equity_per_leg: number
    raw_contracts_at_spot: number | null
    min_lot: number
    equity_needed_usd: number | null
  }
  iv_rank: {
    above_threshold: boolean
    current: number | null
    threshold: number
  }
  dte_range: { configured: boolean; min_dte: number; max_dte: number }
}

export interface WhyNotTrading {
  bot_id: string
  ready:  boolean
  reason: string
  checks: WhyNotTradingChecks
}

export const getBotWhyNotTrading = (botId: string) =>
  request<WhyNotTrading>(`/farm/bot/${botId}/why_not_trading`)

export const getBotLiveState = (botId: string) =>
  request<BotLiveState>(`/farm/bot/${botId}/state`)

export const startFarm = () =>
  request<{ status: string; pid: number }>('/farm/start', { method: 'POST' })

export const stopFarm = () =>
  request<{ status: string }>('/farm/stop', { method: 'POST' })

export const getFarmBotTrades = (botId: string) =>
  request<Trade[]>(`/farm/bot/${botId}/trades`)

// ── Named config API ──────────────────────────────────────────────────────────

export type ConfigSource = 'evolved' | 'manual' | 'promoted' | 'duplicated'
export type ConfigStatus = 'draft' | 'validated' | 'paper' | 'ready' | 'live' | 'archived'

export interface NamedConfig {
  name: string
  status: ConfigStatus
  source: ConfigSource
  created_at: string
  notes?: string | null
  fitness?: number | null
  total_return_pct?: number | null
  sharpe?: number | null
  goal?: string | null
  params?: PresetParams
  _meta?: {
    name?: string
    [key: string]: unknown
  }
}

export const listConfigs = (includeArchived = false) =>
  request<NamedConfig[]>(`/configs?include_archived=${includeArchived}`)

export const getConfigDetail = (name: string) =>
  request<NamedConfig>(`/configs/${encodeURIComponent(name)}`)

export const saveConfig = (payload: {
  name: string
  source?: ConfigSource
  notes?: string
  fitness?: number | null
  total_return_pct?: number | null
  sharpe?: number | null
  params: PresetParams
}) =>
  request<{ ok: boolean; name: string }>('/configs', {
    method: 'POST',
    body: JSON.stringify(payload),
  })

export const setConfigStatus = (name: string, status: ConfigStatus) =>
  request<NamedConfig>(`/configs/${encodeURIComponent(name)}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })

export const renameConfig = (name: string, newName: string) =>
  request<NamedConfig>(`/configs/${encodeURIComponent(name)}/rename`, {
    method: 'PATCH',
    body: JSON.stringify({ new_name: newName }),
  })

export const updateConfigNotes = (name: string, notes: string) =>
  request<NamedConfig>(`/configs/${encodeURIComponent(name)}/notes`, {
    method: 'PATCH',
    body: JSON.stringify({ notes }),
  })

export const updateConfigParams = (name: string, params: Record<string, unknown>) =>
  request<NamedConfig>(`/configs/${encodeURIComponent(name)}/params`, {
    method: 'PATCH',
    body: JSON.stringify({ params }),
  })

export const duplicateConfig = (name: string, newName: string) =>
  request<NamedConfig>(`/configs/${encodeURIComponent(name)}/duplicate`, {
    method: 'POST',
    body: JSON.stringify({ new_name: newName }),
  })

export const archiveConfig = (name: string) =>
  request<NamedConfig>(`/configs/${encodeURIComponent(name)}/archive`, { method: 'POST' })

export const deleteConfig = (name: string) =>
  request<{ ok: boolean }>(`/configs/${encodeURIComponent(name)}`, { method: 'DELETE' })

export const startPaperTesting = (name: string) =>
  request<NamedConfig>(`/configs/${encodeURIComponent(name)}/start-paper`, { method: 'POST' })

export const stopPaperTesting = (name: string) =>
  request<NamedConfig>(`/configs/${encodeURIComponent(name)}/stop-paper`, { method: 'POST' })

export const assignBotConfig = (botId: string, configName: string) =>
  request<{ ok: boolean }>(`/farm/bot/${botId}/assign-config`, {
    method: 'POST',
    body: JSON.stringify({ config_name: configName }),
  })

export const closeFarmBotPosition = (botId: string) =>
  request<{ ok: boolean; bot_id: string; command: string }>(`/farm/bot/${botId}/close_position`, {
    method: 'POST',
  })

// Per-bot pause — creates farm/{botId}/PAUSED. Bot continues to manage existing
// positions but skips opening new entries while the file is present.
export const getFarmBotPaused = (botId: string) =>
  request<{ paused: boolean }>(`/farm/bot/${botId}/paused`)
export const pauseFarmBot = (botId: string) =>
  request<{ ok: boolean; bot_id: string; paused: boolean }>(`/farm/bot/${botId}/pause`, { method: 'POST' })
export const resumeFarmBot = (botId: string) =>
  request<{ ok: boolean; bot_id: string; paused: boolean }>(`/farm/bot/${botId}/resume`, { method: 'POST' })

export const promoteConfig = (configName: string, startingEquity: number) =>
  request<{ ok: boolean; message: string; starting_equity: number }>(`/configs/${encodeURIComponent(configName)}/promote`, {
    method: 'POST',
    body: JSON.stringify({ starting_equity: startingEquity }),
  })

export interface WalkForwardPeriod {
  fitness: number
  sharpe: number
  return_pct: number
  win_rate: number
  max_drawdown: number
  num_cycles: number
}

export interface WalkForwardResults {
  available?: boolean
  timestamp?: string
  split?: { is_start: string; is_end: string; is_days: number; oos_start: string; oos_end: string; oos_days: number }
  in_sample?: WalkForwardPeriod
  out_of_sample?: WalkForwardPeriod
  robustness_score?: number
  verdict?: string
}

export interface MCRun {
  run: number; start_date: string; end_date: string
  fitness: number; sharpe: number; return_pct: number; win_rate: number; max_drawdown: number; num_cycles: number
}

export interface MCDistribution {
  p5: number; p25: number; p50: number; p75: number; p95: number; mean: number; std: number
}

export interface MonteCarloResults {
  available?: boolean
  timestamp?: string
  n_runs?: number
  sim_months?: number
  prob_profit_pct?: number
  distributions?: {
    fitness: MCDistribution; sharpe: MCDistribution; return_pct: MCDistribution
    win_rate: MCDistribution; max_drawdown: MCDistribution; num_cycles: MCDistribution
  }
  runs?: MCRun[]
}

export const getWalkForwardResults = () => request<WalkForwardResults>('/optimizer/walk_forward_results')
export const getMonteCarloResults  = () => request<MonteCarloResults>('/optimizer/monte_carlo_results')

export async function testConnection(): Promise<boolean> {
  try {
    await getStatus()
    return true
  } catch {
    return false
  }
}

// ── Black Swan stress test ─────────────────────────────────────────────────────

export interface BlackSwanScenarioResult {
  scenario_id: string
  scenario_name: string
  scenario_type: 'historical' | 'synthetic'
  description: string
  severity_weight: number
  max_drawdown_pct: number
  total_return_pct: number
  num_trades: number
  win_rate_pct: number
  sharpe_ratio: number
  drawdown_pass: boolean
  return_pass: boolean
  passed: boolean
  max_drawdown_threshold: number
  min_return_threshold: number | null
  error: string
  sim_days: number
}

export interface BlackSwanReport {
  config_name: string
  run_at: string
  scenarios: BlackSwanScenarioResult[]
  verdict: 'PASS' | 'PARTIAL' | 'FAIL' | 'BLOCKED' | 'UNKNOWN'
  passed_count: number
  failed_count: number
  critical_failures: string[]
  prereqs_met: boolean
  prereqs_missing: string[]
}

export interface BlackSwanJobStatus {
  job_id: string
  config_name: string
  status: 'running' | 'done' | 'error' | 'already_running'
  started_at?: string
  verdict?: string
  error?: string | null
}

export const getBlackSwanPrereqs = (configName: string) =>
  request<{ met: boolean; missing: string[] }>(`/black_swan/prereqs/${encodeURIComponent(configName)}`)

export const runBlackSwan = (configName: string, skipPrereqs = false) =>
  request<BlackSwanJobStatus>('/black_swan/run', {
    method: 'POST',
    body: JSON.stringify({ config_name: configName, skip_prereqs: skipPrereqs }),
  })

export const getBlackSwanStatus = (jobId: string) =>
  request<BlackSwanJobStatus>(`/black_swan/status/${jobId}`)

export const getBlackSwanResults = (configName: string) =>
  request<BlackSwanReport>(`/black_swan/results/${encodeURIComponent(configName)}`)
