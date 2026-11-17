import { useEffect, useRef, useState } from 'react'

export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [w, setW] = useState(300)
  useEffect(() => {
    const el = ref.current!
    const ro = new ResizeObserver(() => setW(Math.max(40, el.clientWidth)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}
