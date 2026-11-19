import { useEffect, useState } from 'react'

const read = (name: string) => new URLSearchParams(location.hash.split('?')[1] ?? '').get(name)

/** The `?name=` value of the hash route, kept in sync with `hashchange`. */
export function useHashParam(name: string) {
  const [v, setV] = useState<string | null>(() => read(name))
  useEffect(() => { const f = () => setV(read(name)); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, [name])
  return v
}
