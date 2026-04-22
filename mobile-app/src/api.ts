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
}

export interface EvolveResults {
  top_genomes: EvolveGenome[]
  total_evaluated: number
  timestamp: string | null
}

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

export type EvolveGoal = 'balanced' | 'max_yield' | 'safest' | 'sharpe'
export type ActivePreset = 'sweep' | `evolve_${EvolveGoal}` | 'custom'

export interface PresetsData {
  active: ActivePreset
  sweep: PresetInfo
  evolve_balanced: PresetInfo
  evolve_max_yield: PresetInfo
  evolve_safest: PresetInfo
  evolve_sharpe: PresetInfo
  current: { params: PresetParams }
}

function getBase(): string {
  return (localStorage.getItem('api_url') || '').replace(/\/$/, '')
}

function getKey(): string {
  return localStorage.getItem('api_key') || ''
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

export const getStatus = () => request<StatusData>('/status')
export const getPosition = () => request<PositionData>('/position')
export const getHedge = () => request<HedgeData>('/hedge')
export const getEquity = () => request<EquityData>('/equity')
export const getTrades = () => request<Trade[]>('/trades')
export const getOptimizerSummary = () => request<OptimizerSummary>('/optimizer/summary')
export const getOptimizerRunning = () => request<{ running: boolean }>('/optimizer/running')
export const getSweepResults  = () => request<SweepResults>('/optimizer/sweep_results')
export const getEvolveResults = () => request<EvolveResults>('/optimizer/evolve_results')
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
export const runOptimizer = (mode: string, param?: string, fitness_goal?: string) =>
  request<{ ok: boolean; pid: number; mode: string }>('/optimizer/run', {
    method: 'POST',
    body: JSON.stringify({ mode, param, fitness_goal }),
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

export async function testConnection(): Promise<boolean> {
  try {
    await getStatus()
    return true
  } catch {
    return false
  }
}
