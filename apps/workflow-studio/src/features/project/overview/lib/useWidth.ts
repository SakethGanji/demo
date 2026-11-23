import { useLayoutEffect, useRef, useState } from 'react'

/** The rendered width of an element, kept current with a ResizeObserver (charts draw in real pixels, not a
 * stretched viewBox, so strokes and labels keep their size). */
export function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null) as React.RefObject<T>
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    setW(el.clientWidth)
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}
