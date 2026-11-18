/** EMD as written in the documents; a bare number ("2650000") in rupees with Indian grouping. */
export function money(value: string | null | undefined): string {
  const v = (value ?? '').trim()
  if (!v) return '—'
  if (/^(no|nil|none|n\/?a|0)$/i.test(v)) return 'none'
  return /^\d{4,}$/.test(v) ? `₹${Number(v).toLocaleString('en-IN')}` : v
}
