import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { fetchAUDRate, fmtAUD as _fmtAUD, fmtAUDSigned as _fmtAUDSigned, FALLBACK_AUD_RATE } from './currency'

interface CurrencyCtx {
  /** Live USD → AUD rate (refreshes every hour) */
  rate: number
  /** Format a USD amount as "A$X,XXX". dp defaults to 0. */
  fmtAUD: (usd: number, opts?: { dp?: number }) => string
  /** Format a signed USD amount as "+A$X,XXX" or "-A$X,XXX". */
  fmtAUDSigned: (usd: number, dp?: number) => string
}

const CurrencyContext = createContext<CurrencyCtx>({
  rate: FALLBACK_AUD_RATE,
  fmtAUD: (usd, opts) => _fmtAUD(usd, FALLBACK_AUD_RATE, opts),
  fmtAUDSigned: (usd, dp) => _fmtAUDSigned(usd, FALLBACK_AUD_RATE, dp),
})

export function CurrencyProvider({ children }: { children: ReactNode }) {
  const [rate, setRate] = useState(FALLBACK_AUD_RATE)

  useEffect(() => {
    fetchAUDRate().then(setRate)
    const interval = setInterval(() => fetchAUDRate().then(setRate), 60 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  return (
    <CurrencyContext.Provider value={{
      rate,
      fmtAUD:       (usd, opts) => _fmtAUD(usd, rate, opts),
      fmtAUDSigned: (usd, dp)   => _fmtAUDSigned(usd, rate, dp),
    }}>
      {children}
    </CurrencyContext.Provider>
  )
}

export const useCurrency = () => useContext(CurrencyContext)
