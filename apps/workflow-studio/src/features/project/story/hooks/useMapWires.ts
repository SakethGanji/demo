/**
 * The map's wires and packets. Wires are measured from the laid-out plates (no layout maths of our own), so
 * they are drawn straight into the SVG, re-measured when the lanes resize. A packet is `pulse()` along each
 * wire a new run travels; a failed run ends in a red spark on the object that failed.
 */
import { useEffect, useRef, type RefObject } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { pulse } from '@/shared/components/art/pulse'
import { qk } from '@/shared/api/queryKeys'
import type { RunListItem } from '@/shared/api/types'
import { runTone } from '@/shared/components/ui/status-dot'
import { objKey, runPath, wireWidth, type Edge } from '../lib/model'

const SVG = 'http://www.w3.org/2000/svg'
/** at most this many new runs animate per poll */
const MAX_PACKETS = 4
const HOP_MS = 560
const STAGGER_MS = 320
/** the gap between a wire's end and the lane edge / plate it meets */
const GAP = 3

const wireId = (e: Pick<Edge, 'from' | 'to'>) => `${e.from}>${e.to}`
/** edges as one string, so the effect re-runs only when the wiring changes */
export const edgeSignature = (edges: Edge[]) => edges.map((e) => `${e.from}>${e.to}>${e.volume}`).join('|')

function parseSignature(sig: string): Edge[] {
  if (!sig) return []
  return sig.split('|').map((s) => {
    const [from, to, volume] = s.split('>')
    return { from, to, volume: Number(volume) || 0 }
  })
}

const objEl = (root: HTMLElement, key: string) => root.querySelector<HTMLElement>(`[data-obj="${CSS.escape(key)}"]`)

/**
 * Objects are cards that fill their lane, so a wire runs from the source card's right edge to the target card's
 * left edge (every wire out of a lane starts on one line), at the height of each card's icon. Redrawn when the lanes or
 * any object change size (facts re-wrap on a poll, the focus panel opens, the window resizes) and once the
 * fonts have loaded.
 */
export function useWires(lanes: RefObject<HTMLDivElement | null>, svg: RefObject<SVGSVGElement | null>, signature: string) {
  useEffect(() => {
    const root = lanes.current
    const out = svg.current
    if (!root || !out) return
    const edges = parseSignature(signature)
    const draw = () => {
      const box = root.getBoundingClientRect()
      const paths: SVGPathElement[] = []
      for (const e of edges) {
        const a = objEl(root, e.from)
        const b = objEl(root, e.to)
        const plateA = a?.querySelector('.story-plate')
        const plateB = b?.querySelector('.story-plate')
        if (!a || !b || !plateA || !plateB) continue
        const ra = a.getBoundingClientRect()
        const rb = b.getBoundingClientRect()
        const pa = plateA.getBoundingClientRect()
        const pb = plateB.getBoundingClientRect()
        const p = { x: ra.right - box.left + GAP, y: pa.top + pa.height / 2 - box.top }
        const q = { x: rb.left - box.left - GAP, y: pb.top + pb.height / 2 - box.top }
        if (q.x <= p.x) continue // stacked lanes (narrow): no wires
        const dx = Math.min((q.x - p.x) / 2, 64)
        const path = document.createElementNS(SVG, 'path')
        path.setAttribute('d', `M${p.x} ${p.y} C ${p.x + dx} ${p.y}, ${q.x - dx} ${q.y}, ${q.x} ${q.y}`)
        path.setAttribute('class', 'story-wire')
        path.setAttribute('stroke-width', String(wireWidth(e.volume)))
        if (!e.volume) path.setAttribute('stroke-dasharray', '3 4')
        path.dataset.wire = wireId(e)
        paths.push(path)
      }
      out.replaceChildren(...paths)
    }
    let frame = requestAnimationFrame(draw)
    const redraw = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(draw)
    }
    const ro = new ResizeObserver(redraw)
    ro.observe(root)
    root.querySelectorAll('[data-obj]').forEach((el) => ro.observe(el))
    // objects added or removed without a signature change (a fact appears) still re-measure
    const mo = new MutationObserver((list) => {
      if (list.every((m) => out.contains(m.target))) return // our own redraw
      root.querySelectorAll('[data-obj]').forEach((el) => ro.observe(el))
      redraw()
    })
    mo.observe(root, { childList: true, subtree: true })
    // the entrance stagger moves the cards; measure again once they have settled
    root.addEventListener('animationend', redraw)
    let live = true
    void document.fonts?.ready.then(() => live && redraw())
    return () => {
      live = false
      cancelAnimationFrame(frame)
      ro.disconnect()
      mo.disconnect()
      root.removeEventListener('animationend', redraw)
    }
  }, [lanes, svg, signature])
}

/** One packet per new run (the first load only records what is there). */
export function useRunPackets(
  lanes: RefObject<HTMLDivElement | null>,
  svg: RefObject<SVGSVGElement | null>,
  runs: RunListItem[],
  loaded: boolean,
  signature: string,
  pid: string,
  motionOff: boolean,
) {
  const qc = useQueryClient()
  const seen = useRef<Set<string> | null>(null)
  useEffect(() => {
    if (!loaded) return
    if (!seen.current) {
      seen.current = new Set(runs.map((r) => r.id))
      return
    }
    const fresh = runs.filter((r) => !seen.current!.has(r.id)).reverse()
    for (const r of runs) seen.current.add(r.id)
    if (!fresh.length) return
    void qc.invalidateQueries({ queryKey: qk.summary(pid) })
    const root = lanes.current
    const wires = svg.current
    if (!root || !wires || document.hidden) return
    const edges = parseSignature(signature)
    fresh.slice(0, MAX_PACKETS).forEach((r, i) => {
      const failed = runTone(r.status) === 'err'
      const hops = runPath(r, edges)
      const start = i * STAGGER_MS
      hops.forEach((e, h) => {
        const path = wires.querySelector<SVGPathElement>(`[data-wire="${CSS.escape(wireId(e))}"]`)
        pulse(path, { color: failed && h === hops.length - 1 ? 'error' : 'brand', delay: start + h * HOP_MS })
      })
      if (motionOff) return
      // where the run landed rings once: the plate's own hue, red when it failed
      const at = hops.at(-1)?.to ?? (r.route_id ? objKey('route', r.route_id) : r.workflow_id ? objKey('workflow', r.workflow_id) : null)
      const ring = at ? objEl(root, at)?.querySelector(failed ? '.story-spark' : '.story-ring') : null
      setTimeout(() => {
        if (document.hidden) return
        ring?.animate([{ opacity: failed ? 0.9 : 0.6, transform: 'scale(0.92)' }, { opacity: 0, transform: 'scale(1.3)' }], {
          duration: failed ? 650 : 560,
          easing: 'cubic-bezier(.16, 1, .3, 1)', // --ease-out
        })
      }, start + hops.length * HOP_MS)
    })
  }, [runs, loaded, lanes, svg, signature, pid, motionOff, qc])
}
