/**
 * currency.ts — USD → AUD conversion utilities.
 *
 * Fetches the live rate from api.frankfurter.app (free, no key required)
 * and caches it in localStorage for 1 hour.
 *
 * Fallback rate of 1.58 is used when the network is unavailable.
 */

const CACHE_KEY    = 'aud_rate_cache'
const CACHE_TTL_MS = 60 * 60 * 1000  // 1 hour

interface RateCache {
  rate: number
  fetchedAt: number
}

export const FALLBACK_AUD_RATE = 1.58  // sensible default when offline

export async function fetchAUDRate(): Promise<number> {
  // Return cached value if still fresh
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (raw) {
      const cached: RateCache = JSON.parse(raw)
      if (Date.now() - cached.fetchedAt < CACHE_TTL_MS) {
        return cached.rate
      }
    }
  } catch {}

  // Fetch live from Frankfurter API (free, no key, no CORS issues)
  try {
    const res  = await fetch('https://api.frankfurter.app/latest?base=USD&symbols=AUD', { signal: AbortSignal.timeout(5000) })
    const data = await res.json()
    const rate = data?.rates?.AUD
    if (typeof rate === 'number' && rate > 0) {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ rate, fetchedAt: Date.now() } satisfies RateCache))
      return rate
    }
  } catch {}

  return FALLBACK_AUD_RATE
}

/** Format a USD amount as AUD with the given exchange rate. */
export function fmtAUD(
  usd: number,
  rate: number,
  opts?: { dp?: number; sign?: boolean },
): string {
  const aud = usd * rate
  const dp  = opts?.dp ?? 0
  const prefix = opts?.sign && aud >= 0 ? '+' : ''
  return `${prefix}A$${Math.abs(aud).toLocaleString('en-AU', {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })}${aud < 0 ? '' : ''}`
}

/** Format a signed USD amount as AUD (shows + for positive, − for negative). */
export function fmtAUDSigned(usd: number, rate: number, dp = 0): string {
  const aud = usd * rate
  const abs = Math.abs(aud).toLocaleString('en-AU', {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })
  return aud >= 0 ? `+A$${abs}` : `-A$${abs}`
}
