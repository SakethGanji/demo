import { useCallback, useEffect, useState } from 'react'

/** Fetch on mount and whenever `deps` change or `reload()` is called; `load = null` waits.
 *  `pollMs` re-fetches again after that delay: with `while`, only while `while(data)` holds and
 *  after each new `data`; without it, after every settle, errors included. */
export function useFetch<T>(load: (() => Promise<T>) | null, deps: unknown[], o: { pollMs?: number; while?: (d: T | null) => boolean; onData?: (d: T) => void } = {}) {
  const [data, setData] = useState<T | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [settled, setSettled] = useState(0)
  const reload = useCallback(() => setTick((v) => v + 1), [])
  useEffect(() => {
    if (!load) return
    let alive = true
    load().then((r) => { if (alive) { setData(r); setErr(''); o.onData?.(r) } }).catch((e) => alive && setErr((e as Error).message)).finally(() => alive && setSettled((v) => v + 1))
    return () => { alive = false }
  }, [...deps, tick]) // eslint-disable-line react-hooks/exhaustive-deps
  const on = o.pollMs != null && (!o.while || o.while(data))
  useEffect(() => {
    if (!on) return
    const t = setTimeout(reload, o.pollMs)
    return () => clearTimeout(t)
  }, [on, o.while ? data : settled]) // eslint-disable-line react-hooks/exhaustive-deps
  return { data, setData, err, setErr, reload }
}
