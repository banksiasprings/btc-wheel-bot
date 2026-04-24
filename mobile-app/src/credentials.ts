/**
 * credentials.ts — Persistent storage for the API key.
 *
 * The URL is hardcoded (always https://bot.banksiaspringsfarm.com).
 * The API key is saved to BOTH localStorage and a 1-year cookie so that
 * clearing the browser cache (but not cookies) doesn't require re-setup.
 * On load we check localStorage first, then fall back to the cookie.
 */

export const DEFAULT_URL = 'https://bot.banksiaspringsfarm.com'

const COOKIE_NAME = 'wheel_api_key'
const COOKIE_MAX_AGE = 365 * 24 * 60 * 60  // 1 year in seconds

// ── Cookie helpers ─────────────────────────────────────────────────────────────

function readCookie(name: string): string {
  const pairs = document.cookie.split(';')
  for (const pair of pairs) {
    const [k, ...v] = pair.trim().split('=')
    if (k === name) return v.join('=')
  }
  return ''
}

function writeCookie(name: string, value: string) {
  document.cookie = `${name}=${encodeURIComponent(value)}; max-age=${COOKIE_MAX_AGE}; SameSite=Lax; Secure`
}

function deleteCookie(name: string) {
  document.cookie = `${name}=; max-age=0; SameSite=Lax`
}

// ── Public API ─────────────────────────────────────────────────────────────────

/** Returns the saved API key, or '' if not set. Checks localStorage then cookie. */
export function loadApiKey(): string {
  const fromStorage = localStorage.getItem('api_key') ?? ''
  if (fromStorage) return fromStorage

  // Fallback to cookie (survives cache clears)
  const fromCookie = decodeURIComponent(readCookie(COOKIE_NAME))
  if (fromCookie) {
    // Restore to localStorage so future reads are fast
    localStorage.setItem('api_key', fromCookie)
    localStorage.setItem('api_url', DEFAULT_URL)
  }
  return fromCookie
}

/** Save the API key to both localStorage and a long-lived cookie. */
export function saveApiKey(key: string) {
  localStorage.setItem('api_url', DEFAULT_URL)
  localStorage.setItem('api_key', key)
  writeCookie(COOKIE_NAME, key)
}

/** Clear credentials from all stores. */
export function clearCredentials() {
  localStorage.removeItem('api_url')
  localStorage.removeItem('api_key')
  deleteCookie(COOKIE_NAME)
}

/** True if a key is saved in any store. */
export function hasCredentials(): boolean {
  return !!loadApiKey()
}
