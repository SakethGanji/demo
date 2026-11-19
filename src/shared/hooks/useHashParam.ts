import { useEffect, useState } from 'react'

const read = (name: string) => new URLSearchParams(location.hash.split('?')[1] ?? '').get(name)

/** The `?name=` value of the hash route, kept in sync with `hashchange`. */
export function useHashParam(name: string) {
  const [v, setV] = useState<string | null>(() => read(name))
  useEffect(() => { const f = () => setV(read(name)); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, [name])
  return v
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '')

/** A `?tab=` value matched to one of `tabs`: exact, then case- and punctuation-insensitive (`doctor`, `batch-drift`),
 *  then an alias (old tab names, short slugs like `batch`), then a unique prefix. */
export function matchTab<T extends string>(tabs: readonly T[], raw: string | null, aliases: Record<string, T> = {}): T | null {
  if (!raw) return null
  const s = slug(raw)
  return tabs.find((t) => t === raw) ?? tabs.find((t) => slug(t) === s)
    ?? Object.entries(aliases).find(([k]) => slug(k) === s)?.[1]
    ?? (s ? (tabs.filter((t) => slug(t).startsWith(s)).length === 1 ? tabs.find((t) => slug(t).startsWith(s))! : null) : null)
}

/** The `?tab=` of the hash route as one of `tabs` (agent deep links use slugs, links in docs use old names). */
export function useTabParam<T extends string>(tabs: readonly T[], fallback: T, aliases: Record<string, T> = {}): [T, (t: T) => void] {
  const [tab, setTab] = useState<T>(() => matchTab(tabs, read('tab'), aliases) ?? fallback)
  useEffect(() => {
    const f = () => { const t = matchTab(tabs, read('tab'), aliases); if (t) setTab(t) }
    addEventListener('hashchange', f); return () => removeEventListener('hashchange', f)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return [tab, setTab]
}
