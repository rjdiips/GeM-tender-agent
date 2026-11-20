// PROTOTYPE: simulates a streamed run so the layouts can be judged while results arrive.
import { useEffect, useState } from 'react'
import { bids, progress } from './data'

export function useReplay() {
  const [step, setStep] = useState(progress.length + bids.length)
  const total = progress.length + bids.length
  useEffect(() => {
    if (step >= total) return
    const t = setTimeout(() => setStep(s => s + 1), 700)
    return () => clearTimeout(t)
  }, [step, total])
  const lines = progress.slice(0, Math.min(step, progress.length))
  const shown = bids.slice(0, Math.max(0, step - 2)).slice(0, bids.length)
  return { lines, shown, running: step < total, replay: () => setStep(0) }
}
