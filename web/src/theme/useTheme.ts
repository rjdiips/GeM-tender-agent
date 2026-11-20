import { useCallback, useState } from 'react'

export type Theme = 'dark' | 'light'
const KEY = 'gem-agent.theme'

function stored(): Theme | null {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'dark' || v === 'light' ? v : null
  } catch {
    return null // private window or blocked storage: no pick remembered
  }
}

/** Light until the user picks one; the pick is remembered in this browser. */
export function useTheme() {
  const [choice, setChoice] = useState<Theme | null>(stored)

  const theme = choice ?? 'light'
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
