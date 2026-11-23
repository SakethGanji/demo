/**
 * Pan and zoom for the knowledge canvas. The wheel zooms around the cursor (a trackpad pinch arrives as ctrl+wheel
 * and gets more gain; a sideways or shift scroll pans); dragging anywhere pans. A drag past 4px swallows the click
 * that ends it, so letting go over a node never selects it. Camera moves (`flyTo`) ease, or jump with motion off.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { easeOut, IDENTITY, lerpTransform, wheelFactor, zoomAround, type Size, type Transform } from '../lib/viewport'

const DRAG_PX = 4

export function usePanZoom(off: boolean) {
  const ref = useRef<HTMLDivElement>(null)
  const [t, setT] = useState<Transform>(IDENTITY)
  const [size, setSize] = useState<Size>({ width: 0, height: 0 })
  const [panning, setPanning] = useState(false)
  const tRef = useRef(t)
  const anim = useRef(0)
  const drag = useRef<{ id: number; x: number; y: number; tx: number; ty: number; moved: boolean } | null>(null)
  const swallow = useRef(false)

  useEffect(() => {
    tRef.current = t
  }, [t])

  useEffect(() => {
    const el = ref.current
    const camera = anim
    if (!el) return
    const ro = new ResizeObserver(([e]) => setSize({ width: e.contentRect.width, height: e.contentRect.height }))
    ro.observe(el)
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      cancelAnimationFrame(camera.current)
      const r = el.getBoundingClientRect()
      const p = { x: e.clientX - r.left, y: e.clientY - r.top }
      if (!e.ctrlKey && !e.metaKey && (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY))) {
        const dx = e.shiftKey && !e.deltaX ? e.deltaY : e.deltaX
        const dy = e.shiftKey ? 0 : e.deltaY
        setT((prev) => ({ ...prev, x: prev.x - dx, y: prev.y - dy }))
        return
      }
      const f = wheelFactor(e.deltaY, e.deltaMode, e.ctrlKey || e.metaKey)
      setT((prev) => zoomAround(prev, f, p))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      ro.disconnect()
      el.removeEventListener('wheel', onWheel)
      cancelAnimationFrame(camera.current)
    }
  }, [])

  /** Ease the camera to `to` (or jump, with motion off). */
  const flyTo = useCallback(
    (to: Transform, ms = 560) => {
      cancelAnimationFrame(anim.current)
      if (off || ms <= 0) return setT(to)
      const from = tRef.current
      let t0 = -1
      const step = (now: number) => {
        if (t0 < 0) t0 = now
        const u = easeOut((now - t0) / ms)
        setT(lerpTransform(from, to, u))
        if (u < 1) anim.current = requestAnimationFrame(step)
      }
      anim.current = requestAnimationFrame(step)
    },
    [off],
  )

  const zoomBy = useCallback(
    (factor: number) => {
      const c = { x: size.width / 2, y: size.height / 2 }
      flyTo(zoomAround(tRef.current, factor, c), 240)
    },
    [flyTo, size.width, size.height],
  )

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    cancelAnimationFrame(anim.current)
    swallow.current = false
    drag.current = { id: e.pointerId, x: e.clientX, y: e.clientY, tx: tRef.current.x, ty: tRef.current.y, moved: false }
  }
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    const dx = e.clientX - d.x
    const dy = e.clientY - d.y
    if (!d.moved && Math.hypot(dx, dy) < DRAG_PX) return
    if (!d.moved) {
      d.moved = true
      // capture only once it is a drag, so a plain click still reaches the node under the pointer
      ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
      setPanning(true)
    }
    setT((prev) => ({ ...prev, x: d.tx + dx, y: d.ty + dy }))
  }
  const onPointerUp = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    swallow.current = d.moved
    drag.current = null
    if (d.moved) setPanning(false)
  }

  /** True once after a drag: the click that ends a pan is not a selection. */
  const consumeDrag = useCallback(() => {
    const was = swallow.current
    swallow.current = false
    return was
  }, [])

  return {
    ref,
    t,
    size,
    panning,
    setT,
    flyTo,
    zoomBy,
    consumeDrag,
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp },
  }
}
