/**
 * Direct manipulation on the canvas (Figma / Webflow-style), drawn in the studio over the iframe from the preview's `vrect`:
 * hatched padding areas with a pill per side (drag to change that side; Alt: both opposite sides; Shift: all four), bars
 * between a flex / grid container's children (drag to change the gap), width / height / corner handles, the margin on
 * hover (read-only) and, with Alt held over another element, the px distance to it. A drag previews live in the iframe
 * (inline styles, no re-bundle), snaps to theme tokens and commits once on mouse-up — one class-string splice or one
 * enum-prop op, one undo step. Esc cancels; a refused handle says why.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type * as t from '@babel/types'
import { cn } from '@/shared/lib/utils'
import type { Rect } from '../../sandbox/bridge'
import { useEditHistory } from '../history'
import { elementByKey, tryParse } from '../source'
import { readClasses, type ClassChange } from '../style/classEdit'
import { takesClassName } from '../style/StylePanel'
import { SPACE } from '../style/tokens'
import type { VisualEdit } from '../useVisualEdit'
import { rectFor, useCanvasRect, useHandlesOn } from './canvasRect'
import {
  classSplice, gapBars, gapOf, handlesShown, heightOptions, marginAreas, measure, padAreas, padClass, padDragPx, padHandleAt, padHandleFits, padOf, padSides,
  sizeRefusal, snapSize, snapStep, spaceWrite, stepPx, widthOptions, SIDES, type Axis, type PadMode, type Side, type SizeOption,
} from './handles'

const LOCKED = 'The chat is editing the app; visual edits wait until it finishes'
const PAD_MIN = 24

/** One drag's result at the current pointer: the label, the live style and how to commit it (null: nothing to write). */
interface Step {
  label: string
  style: Record<string, string>
  commit: (() => string | null) | null
}

interface Drag {
  x: number
  y: number
  frame: DOMRect
  at: (dx: number, dy: number, mods: { alt: boolean; shift: boolean }) => Step
}

const pos = (r: Rect): React.CSSProperties => ({ left: r.x, top: r.y, width: r.w, height: r.h })
const HATCH = 'repeating-linear-gradient(135deg, color-mix(in srgb, var(--primary) 45%, transparent) 0 1px, color-mix(in srgb, var(--primary) 10%, transparent) 1px 5px)' // design-ok: hatched padding overlay (as in Figma)

export function CanvasHandles({ ve }: { ve: VisualEdit }) {
  const report = useCanvasRect((s) => s.rect)
  const on = useHandlesOn((s) => s.on)
  const wrapRef = useRef<HTMLDivElement>(null)
  // the latest editor for handlers and the drag (a commit re-reads the files as they are at mouse-up)
  const veRef = useRef(ve)
  useLayoutEffect(() => {
    veRef.current = ve
  })
  const [drag, setDrag] = useState<Drag | null>(null)
  const [label, setLabel] = useState<{ text: string; x: number; y: number; refused?: boolean } | null>(null)
  const { view, selection } = ve
  const hl = ve.bridge.highlight
  const rect = rectFor(report, hl?.vsrc, hl?.vpart)
  const shown = !!(on && ve.editing && view && selection && rect && handlesShown(report))

  // the wrapper sits exactly over the iframe, so everything inside is drawn in the preview's own coordinates
  const frameOf = ve.frame
  useLayoutEffect(() => {
    const w = wrapRef.current
    const frame = frameOf()
    const layer = w?.parentElement
    if (!w || !frame || !layer) return
    const lr = layer.getBoundingClientRect()
    const fr = frame.getBoundingClientRect()
    w.style.transform = `translate3d(${fr.left - lr.left}px, ${fr.top - lr.top}px, 0)`
    w.style.width = `${fr.width}px`
    w.style.height = `${fr.height}px`
  })

  // a refusal shown on a handle fades after a moment
  useEffect(() => {
    if (!label?.refused) return
    const id = setTimeout(() => setLabel(null), 2600)
    return () => clearTimeout(id)
  }, [label])

  // the drag: the iframe stops taking the pointer, the window follows it; Esc cancels and restores (a layout effect, so a
  // quick release right after the press isn't missed)
  useLayoutEffect(() => {
    if (!drag) return
    const frame = veRef.current.frame()
    if (frame) frame.style.pointerEvents = 'none'
    let last: Step | null = null
    let at = { x: 0, y: 0 }
    const end = (commit: boolean) => {
      setDrag(null)
      if (frame) frame.style.pointerEvents = ''
      const v = veRef.current
      const err = commit && last?.commit ? last.commit() : null
      const wrote = commit && !!last?.commit && !err
      v.liveStyle(null, wrote ? 'render' : undefined)
      setLabel(err ? { text: err, ...at, refused: true } : null)
    }
    const onMove = (e: PointerEvent) => {
      last = drag.at(e.clientX - drag.x, e.clientY - drag.y, { alt: e.altKey, shift: e.shiftKey })
      veRef.current.liveStyle(last.style)
      at = { x: e.clientX - drag.frame.left + 14, y: e.clientY - drag.frame.top + 14 }
      setLabel({ text: last.label, ...at })
    }
    const onUp = () => end(true)
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      end(false)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('keydown', onKey, true)
      if (frame) frame.style.pointerEvents = ''
    }
  }, [drag])

  if (!shown || !report || !handlesShown(report) || !view || !selection) return <div ref={wrapRef} className="pointer-events-none absolute top-0 left-0 overflow-hidden" data-testid="vhandle-layer" />
  const box = report.box
  const r = report.rect

  /** The selection's source element right now (re-read at commit time, so a change that landed mid-drag isn't overwritten). */
  const target = (): { src: string; el: t.JSXElement; name: string; accepts: boolean } | null => {
    const v = veRef.current
    const sel = v.selection
    if (!sel) return null
    const src = v.files[sel.file]
    const ast = src !== undefined ? tryParse(src) : null
    const hit = ast && elementByKey(ast, sel.key)
    return hit ? { src: src!, el: hit.node, name: hit.name, accepts: takesClassName(v.files, sel.file, hit.name) } : null
  }
  const classRead = (tg: NonNullable<ReturnType<typeof target>>) => (tg.accepts ? readClasses(tg.src, tg.el) : null)

  const commitClasses = (changes: ClassChange[]) => (): string | null => {
    const v = veRef.current
    if (v.locked) return LOCKED
    const tg = target()
    const sel = v.selection
    if (!tg || !sel) return 'The element is gone'
    const res = classSplice(tg.src, tg.el, changes)
    if (!res.ok) return res.error === 'Nothing to change' ? null : res.error
    const tag = v.tag
    useEditHistory.getState().commit(`${tg.name}: ${res.label}`, [{ path: sel.file, before: tg.src, after: res.after }], undefined, tag ? [{ vsrc: tag, add: res.add, remove: res.remove }] : undefined)
    return null
  }
  const commitProp = (prop: string, value: string) => (): string | null => veRef.current.edit([{ op: 'setProp', prop, value }])

  /** Start a drag, or show why this handle can't. */
  const begin = (e: React.PointerEvent, why: string | null, at: () => Drag['at'] | string) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.stopPropagation()
    const frame = ve.frame()
    if (!frame) return
    const fr = frame.getBoundingClientRect()
    const where = { x: e.clientX - fr.left + 14, y: e.clientY - fr.top + 14 }
    const fn = ve.locked ? LOCKED : (why ?? at())
    if (typeof fn === 'string') {
      setLabel({ text: fn, ...where, refused: true })
      return
    }
    setLabel(null)
    setDrag({ x: e.clientX, y: e.clientY, frame: fr, at: fn })
  }

  // ── Padding ──
  const padStart = (side: Side): Drag['at'] | string => {
    const tg = target()
    if (!tg) return 'The element is gone'
    if (selection.part) return 'A list item: its spacing comes from the list; style it in the panel'
    const read = classRead(tg)
    const w = spaceWrite(view, 'p', read && (read.ok ? { ok: true } : read))
    if (w.kind === 'refused') return w.reason
    const start = SIDES.map((s) => padOf(box, s))
    if (w.kind === 'prop') {
      const cur = typeof w.prop.value === 'string' ? w.prop.value : w.prop.editor?.kind === 'enum' ? w.prop.editor.default : undefined
      return (dx, dy) => {
        const step = snapStep(padDragPx(side, padOf(box, side), dx, dy), w.steps)
        const px = stepPx(step)
        return { label: `padding="${step}" · ${px}px · all sides`, style: { padding: `${px}px` }, commit: step === cur ? null : commitProp(w.prop.name, step) }
      }
    }
    return (dx, dy, mods) => {
      const mode: PadMode = mods.shift ? 'all' : mods.alt ? 'pair' : 'one'
      const step = snapStep(padDragPx(side, padOf(box, side), dx, dy), SPACE)
      const px = stepPx(step)
      const sides = padSides(side, mode)
      const cls = padClass(side, mode, step)
      const style = Object.fromEntries(sides.map((s) => [`padding-${s}`, `${px}px`]))
      const same = sides.every((s) => Math.abs(start[SIDES.indexOf(s)] - px) < 0.5)
      return { label: `${cls} · ${px}px`, style, commit: same ? null : commitClasses([{ set: cls }]) }
    }
  }

  // ── Gap ──
  const gapStart = (axis: 'x' | 'y'): Drag['at'] | string => {
    const tg = target()
    if (!tg) return 'The element is gone'
    if (selection.part) return 'A list item: its spacing comes from the list; style it in the panel'
    const read = classRead(tg)
    const w = spaceWrite(view, 'gap', read && (read.ok ? { ok: true } : read))
    if (w.kind === 'refused') return w.reason
    const start = gapOf(box, axis)
    const steps = w.kind === 'prop' ? w.steps : SPACE
    const cur = w.kind === 'prop' ? (typeof w.prop.value === 'string' ? w.prop.value : w.prop.editor?.kind === 'enum' ? w.prop.editor.default : undefined) : undefined
    return (dx, dy) => {
      const step = snapStep(Math.max(0, start + (axis === 'x' ? dx : dy)), steps)
      const px = stepPx(step)
      if (w.kind === 'prop') return { label: `gap="${step}" · ${px}px`, style: { gap: `${px}px` }, commit: step === cur ? null : commitProp(w.prop.name, step) }
      return { label: `gap-${step} · ${px}px`, style: { gap: `${px}px` }, commit: Math.abs(px - start) < 0.5 ? null : commitClasses([{ set: `gap-${step}` }]) }
    }
  }

  // ── Size ──
  const sizeWhy = (axes: Axis[]): string | null => {
    if (selection.part) return 'A list item: its size comes from the list; style it in the panel'
    for (const a of axes) {
      const why = sizeRefusal(a, box)
      if (why) return why
    }
    const tg = target()
    if (!tg) return 'The element is gone'
    if (!tg.accepts) return `${tg.name} doesn't take classes; size it with AI`
    const read = readClasses(tg.src, tg.el)
    return read.ok ? null : read.reason
  }
  const sizeStart = (axes: Axis[]): Drag['at'] => {
    const wOpts = widthOptions(box)
    const hOpts = heightOptions(box)
    return (dx, dy) => {
      const picks: { axis: Axis; o: SizeOption; start: number }[] = []
      if (axes.includes('w')) {
        const o = snapSize(Math.max(0, r.w + dx), wOpts)
        if (o) picks.push({ axis: 'w', o, start: r.w })
      }
      if (axes.includes('h')) {
        const o = snapSize(Math.max(0, r.h + dy), hOpts)
        if (o) picks.push({ axis: 'h', o, start: r.h })
      }
      const style: Record<string, string> = {}
      for (const p of picks) {
        if (p.axis === 'w') Object.assign(style, { width: `${Math.round(p.o.px)}px`, 'max-width': 'none' })
        else style.height = `${Math.round(p.o.px)}px`
      }
      const changed = picks.filter((p) => Math.abs(p.o.px - p.start) >= 1)
      return {
        label: `${picks.map((p) => p.o.label).join(' × ')} · ${picks.map((p) => `${Math.round(p.o.px)}`).join('×')}px`,
        style,
        commit: changed.length ? commitClasses(changed.flatMap((p) => p.o.changes)) : null,
      }
    }
  }
  const sizeHandle = (axes: Axis[]) => (e: React.PointerEvent) => begin(e, sizeWhy(axes), () => sizeStart(axes))

  const dragging = !!drag
  const areas = padAreas(r, box)
  const content = { x: r.x + box.border[3] + box.padding[3], y: r.y, w: Math.max(0, r.w - box.border[1] - box.border[3] - box.padding[1] - box.padding[3]), h: r.h }
  const bars = report.children && /flex|grid/.test(box.display) ? gapBars(report.children, content) : []
  const hover = report.hover && !dragging ? report.hover : null
  const guides = hover?.alt && !hover.self ? measure(r, hover.rect) : []

  return (
    <div ref={wrapRef} className="pointer-events-none absolute top-0 left-0 overflow-hidden" data-testid="vhandle-layer">
      {/* margin: read-only, on hover */}
      {hover?.self && !hover.alt && marginAreas(r, box).map((a, i) => <div key={i} className="absolute bg-warning/20" style={pos(a)} data-testid="vhandle-margin" />)}
      {/* padding areas, hatched */}
      {SIDES.map((s) => (areas[s].w > 0 && areas[s].h > 0 ? <div key={s} className="absolute" style={{ ...pos(areas[s]), backgroundImage: HATCH }} /> : null))}
      {/* gap bars */}
      {bars.map((b, i) => (
        <div
          key={i}
          className={cn('group pointer-events-auto absolute flex items-center justify-center bg-primary/15 hover:bg-primary/30', b.axis === 'x' ? 'cursor-ew-resize' : 'cursor-ns-resize')}
          style={pos(b)}
          aria-label="Drag to change the gap"
          onPointerDown={(e) => begin(e, null, () => gapStart(b.axis))}
          data-testid="vhandle-gap"
        >
          <span className={cn('rounded-full bg-primary', b.axis === 'x' ? 'h-4 w-0.5' : 'h-0.5 w-4')} />
        </div>
      ))}
      {/* padding pills */}
      {SIDES.filter((s) => padHandleFits(r, s)).map((s) => {
        const p = padHandleAt(r, box, s)
        if ((p.vertical ? r.h : r.w) < PAD_MIN) return null
        return (
          <div
            key={s}
            className={cn('pointer-events-auto absolute flex items-center justify-center', p.vertical ? 'h-6 w-3 cursor-ew-resize' : 'h-3 w-6 cursor-ns-resize')}
            style={{ left: p.x, top: p.y, transform: 'translate(-50%, -50%)' }}
            aria-label={`Drag to change the ${s} padding (Alt: both sides, Shift: all)`}
            title={`Padding ${s} · Alt: both sides · Shift: all`}
            onPointerDown={(e) => begin(e, null, () => padStart(s))}
            data-testid={`vhandle-pad-${s}`}
          >
            <span className={cn('rounded-full bg-primary shadow-sm', p.vertical ? 'h-4 w-1' : 'h-1 w-4')} />
          </div>
        )
      })}
      {/* size handles */}
      <div
        className="pointer-events-auto absolute size-2 -translate-1/2 cursor-ew-resize rounded-kbd bg-primary-foreground"
        style={{ left: r.x + r.w, top: r.y + r.h / 2, boxShadow: '0 0 0 1.5px var(--primary)' }}
        aria-label="Drag to change the width"
        title="Width"
        onPointerDown={sizeHandle(['w'])}
        data-testid="vhandle-w"
      />
      <div
        className="pointer-events-auto absolute size-2 -translate-1/2 cursor-ns-resize rounded-kbd bg-primary-foreground"
        style={{ left: r.x + r.w / 2, top: r.y + r.h, boxShadow: '0 0 0 1.5px var(--primary)' }}
        aria-label="Drag to change the height"
        title="Height"
        onPointerDown={sizeHandle(['h'])}
        data-testid="vhandle-h"
      />
      <div
        className="pointer-events-auto absolute size-2.5 -translate-1/2 cursor-nwse-resize rounded-kbd bg-primary-foreground"
        style={{ left: r.x + r.w, top: r.y + r.h, boxShadow: '0 0 0 1.5px var(--primary)' }}
        aria-label="Drag to change the width and height"
        title="Width and height"
        onPointerDown={sizeHandle(['w', 'h'])}
        data-testid="vhandle-wh"
      />
      {/* Alt + hover: the distance to the hovered element */}
      {guides.length > 0 && hover && (
        <>
          <div className="absolute outline-1 outline-destructive outline-dashed" style={pos(hover.rect)} />
          {guides.map((g, i) => (
            <div key={i} className="absolute bg-destructive" style={{ left: Math.min(g.x1, g.x2), top: Math.min(g.y1, g.y2), width: Math.max(1, Math.abs(g.x2 - g.x1)), height: Math.max(1, Math.abs(g.y2 - g.y1)) }} data-testid="vhandle-measure">
              <span className="absolute top-1/2 left-1/2 -translate-1/2 rounded-chip bg-destructive px-1 font-mono text-[10px] leading-4 whitespace-nowrap text-destructive-foreground">{g.px}</span>
            </div>
          ))}
        </>
      )}
      {label && (
        <span
          className={cn('absolute max-w-[280px] rounded-chip px-1.5 py-0.5 font-mono text-[10.5px] leading-4 shadow-sm', label.refused ? 'bg-destructive-soft font-sans whitespace-normal text-destructive' : 'bg-primary whitespace-nowrap text-primary-foreground')}
          style={{ left: label.x, top: label.y }}
          role="status"
          data-refused={label.refused ? '' : undefined}
          data-testid="vhandle-label"
        >
          {label.text}
        </span>
      )}
    </div>
  )
}
