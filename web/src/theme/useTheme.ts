import { useCallback, useEffect, useState } from 'react'

export type Theme = 'dark' | 'light'
const KEY = 'gem-agent.theme'
const QUERY = '(prefers-color-scheme: dark)'

function stored(): Theme | null {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'dark' || v === 'light' ? v : null
  } catch {
    return null // private window or blocked storage: follow the system
  }
}

function systemTheme(): Theme {
  return typeof window.matchMedia === 'function' && window.matchMedia(QUERY).matches ? 'dark' : 'light'
}

/** The operating system's mode until the user picks one; the pick is remembered in this browser. */
export function useTheme() {
  const [choice, setChoice] = useState<Theme | null>(stored)
  const [system, setSystem] = useState<Theme>(systemTheme)

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia(QUERY)
    const onChange = (e: MediaQueryListEvent) => setSystem(e.matches ? 'dark' : 'light')
    media.addEventListener?.('change', onChange)
    return () => media.removeEventListener?.('change', onChange)
  }, [])

  const theme = choice ?? system
  const toggle = useCallback(() => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setChoice(next)
    try {
      localStorage.setItem(KEY, next)
    } catch {
      /* not remembered, still applied */
    }
  }, [theme])

  return { theme, toggle }
}
