import { useEffect, useLayoutEffect, useRef, useState } from 'react'

/** Width of an element, kept current with a ResizeObserver. The element may mount later (after a loading state):
 *  the observer attaches whenever ref.current changes. */
export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [w, setW] = useState(300)
  const obs = useRef<{ el: T | null; ro: ResizeObserver | null }>({ el: null, ro: null })
  useLayoutEffect(() => {
    const el = ref.current
    if (el === obs.current.el) return
    obs.current.ro?.disconnect()
    obs.current = { el, ro: null }
    if (!el) return
    const ro = new ResizeObserver(() => setW(Math.max(40, el.clientWidth)))
    ro.observe(el)
    obs.current.ro = ro
    setW(Math.max(40, el.clientWidth))
  })
  useEffect(() => () => obs.current.ro?.disconnect(), [])
  return [ref, w] as const
}
