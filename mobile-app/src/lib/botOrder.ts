/**
 * Shared bot ordering utility.
 *
 * The user can drag-reorder bot cards in the Farm tab. That preference is
 * stored in localStorage and applied everywhere bots are listed or selected —
 * Farm cards, Trading dropdown, Pipeline selectors, etc.
 *
 * Performance views sort by metrics (Sharpe/return) but use this order as a
 * tiebreaker when scores are equal.
 */

const STORAGE_KEY = 'farm_bot_order'

/** Return the saved bot ID order array (may be empty if never set). */
export function loadBotOrder(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as string[]) : []
  } catch {
    return []
  }
}

/** Persist a new bot ID order to localStorage. */
export function saveBotOrder(order: string[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(order))
  } catch { /* ignore quota errors */ }
}

/**
 * Sort an array of objects that have an `id: string` field according to the
 * saved custom order. Items not in the saved order appear at the end in their
 * original sequence.
 */
export function applyBotOrder<T extends { id: string }>(bots: T[], order?: string[]): T[] {
  const ord = order ?? loadBotOrder()
  if (ord.length === 0) return bots
  return [...bots].sort((a, b) => {
    const ia = ord.indexOf(a.id)
    const ib = ord.indexOf(b.id)
    if (ia === -1 && ib === -1) return 0
    if (ia === -1) return 1
    if (ib === -1) return -1
    return ia - ib
  })
}

/**
 * Sort bots by a numeric metric (descending) with the custom order as a
 * tiebreaker. Useful for Performance views where ranking matters but the
 * user's preference breaks ties (or applies when all scores are zero/null).
 */
export function sortBotsByMetric<T extends { id: string }>(
  bots: T[],
  metric: (b: T) => number | null | undefined,
): T[] {
  const ord = loadBotOrder()
  return [...bots].sort((a, b) => {
    const ma = metric(a) ?? -Infinity
    const mb = metric(b) ?? -Infinity
    if (mb !== ma) return mb - ma   // higher metric wins
    // tiebreak by custom order
    const ia = ord.indexOf(a.id)
    const ib = ord.indexOf(b.id)
    if (ia === -1 && ib === -1) return 0
    if (ia === -1) return 1
    if (ib === -1) return -1
    return ia - ib
  })
}
