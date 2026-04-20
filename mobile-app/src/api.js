/**
 * api.js — typed fetch helpers for the Wheel Bot FastAPI server.
 * All calls read baseUrl / apiKey from localStorage.
 */

export function getSettings() {
  return {
    baseUrl: localStorage.getItem('wb_base_url') || '',
    apiKey:  localStorage.getItem('wb_api_key')  || '',
  }
}

export function saveSettings({ baseUrl, apiKey }) {
  localStorage.setItem('wb_base_url', baseUrl.replace(/\/$/, ''))
  localStorage.setItem('wb_api_key',  apiKey)
}

async function call(method, path, body) {
  const { baseUrl, apiKey } = getSettings()
  if (!baseUrl) throw new Error('API URL not configured')

  const opts = {
    method,
    headers: {
      'X-API-Key':   apiKey,
      'Content-Type': 'application/json',
    },
  }
  if (body !== undefined) opts.body = JSON.stringify(body)

  const res = await fetch(`${baseUrl}${path}`, opts)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
  return res.json()
}

const get  = (path)        => call('GET',  path)
const post = (path, body)  => call('POST', path, body ?? {})

export const api = {
  status:           ()      => get('/status'),
  position:         ()      => get('/position'),
  equity:           ()      => get('/equity'),
  trades:           ()      => get('/trades'),
  optimizerSummary: ()      => get('/optimizer/summary'),
  optimizerRunning: ()      => get('/optimizer/running'),
  sweepResults:     ()      => get('/optimizer/sweep_results'),
  evolveResults:    ()      => get('/optimizer/evolve_results'),
  config:           ()      => get('/config'),

  start:            ()      => post('/controls/start'),
  stop:             ()      => post('/controls/stop'),
  closePosition:    ()      => post('/controls/close_position'),
  setMode:   (mode, confirm) => post('/controls/set_mode', { mode, confirm }),

  updateConfig: (params) => post('/config', { params }),
  runOptimizer: (mode, param) => post('/optimizer/run', { mode, param }),
}
