/**
 * The floating toolbar on the canvas (as in Figma / Onlook): pinned just above the selected element — below it, or inside
 * its top edge, when there's no room — following it on scroll, resize and re-render from the preview's `vrect` reports, and
 * hidden while the preview drags or edits text in place. It is studio UI drawn over the iframe (studio components and
 * theme), not part of the app. Every action is an existing editor op, so history, refusals and notices are the same as the
 * panel's; a disabled action says why in its tooltip.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Copy, CornerLeftUp, Group, Paintbrush, Scaling, Sparkles, Trash2, Type } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { Tip } from '@/shared/components/ui/tooltip'
import { ToolbarSeparator } from '@/shared/components/ui/toolbar'
import { cn } from '@/shared/lib/utils'
import { askAboutElement } from '../askAi'
import type { WrapWith } from '../structure'
import { WRAPS } from '../StructurePanel'
import type { VisualEdit } from '../useVisualEdit'
import { CanvasHandles } from './CanvasHandles'
import { placeBar, rectFor, useCanvasRect, useHandlesOn } from './canvasRect'
import { QuickStyle } from './QuickStyle'

/** An icon button that stays hoverable when refused (`why`), so its tooltip can say why. */
function Act({ label, why, kbd, onClick, testId, children, className }: { label: string; why?: string | null; kbd?: string; onClick: () => void; testId: string; children: React.ReactNode; className?: string }) {
  return (
    <Tip label={why ? `${label}: ${why}` : label} kbd={why ? undefined : kbd}>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={label}
        aria-disabled={why ? true : undefined}
        className={cn('aria-disabled:cursor-not-allowed aria-disabled:opacity-40 aria-disabled:hover:bg-transparent', className)}
        onClick={() => !why && onClick()}
        data-testid={testId}
      >
        {children}
      </Button>
    </Tip>
  )
}

/** Escape deselects unless a text box, an open menu or a dialog has the key. */
function escapeIsOurs(e: KeyboardEvent): boolean {
  if (e.key !== 'Escape' || e.defaultPrevented) return false
  const t = e.target as HTMLElement | null
  if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return false
  return !(t && t.closest?.('[role=dialog],[role=listbox],[role=menu],[data-slot=popover-content]'))
}

export function CanvasToolbar({ ve }: { ve: VisualEdit }) {
  const report = useCanvasRect((s) => s.rect)
  const layerRef = useRef<HTMLDivElement>(null)
  const barRef = useRef<HTMLDivElement>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<'wrap' | 'style' | null>(null)
  const handles = useHandlesOn()
  const { view, selection, crumbs } = ve
  const hl = ve.bridge.highlight
  const rect = rectFor(report, hl?.vsrc, hl?.vpart)
  const shown = !!(ve.editing && view && selection && rect)

  const [errFor, setErrFor] = useState(selection)
  if (errFor !== selection) {
    setErrFor(selection)
    setErr(null)
    setOpen(null)
  }

  // the parent crumb (crumbs are innermost first)
  const parentSel = useMemo(() => {
    if (!selection) return null
    const i = crumbs.findIndex((c) => c.sel.file === selection.file && c.sel.key === selection.key)
    return i >= 0 && i + 1 < crumbs.length ? crumbs[i + 1] : null
  }, [crumbs, selection])

  // what each action would refuse with, worked out once per selection (a dry run of the same op)
  const { dryRun, locked } = ve
  const why = useMemo(() => {
    if (!view || !selection) return null
    const lockedWhy = locked ? 'The chat is editing the app; visual edits wait until it finishes' : null
    const move = (d: -1 | 1) => lockedWhy ?? (!view.move.ok ? view.move.reason : view.move.index + d < 0 ? 'Already first' : view.move.index + d >= view.move.count ? 'Already last' : null)
    return {
      up: move(-1),
      down: move(1),
      dup: dryRun([{ op: 'duplicate' }]),
      del: dryRun([{ op: 'delete' }]),
      wrap: Object.fromEntries(WRAPS.map((w) => [w, dryRun([{ op: 'wrap', with: w }])])) as Record<WrapWith, string | null>,
    }
  }, [view, selection, locked, dryRun])

  // place the bar over the element: imperatively, so following a scroll costs no React work beyond this component
  const frameOf = ve.frame
  useLayoutEffect(() => {
    const layer = layerRef.current
    const bar = barRef.current
    const frame = frameOf()
    if (!layer || !bar) return
    if (!rect || !frame) {
      bar.style.visibility = 'hidden'
      return
    }
    const lr = layer.getBoundingClientRect()
    const fr = frame.getBoundingClientRect()
    const p = placeBar(rect, { x: fr.left - lr.left, y: fr.top - lr.top, w: fr.width, h: fr.height }, { w: lr.width, h: lr.height }, { w: bar.offsetWidth, h: bar.offsetHeight })
    if (!p) {
      bar.style.visibility = 'hidden'
      return
    }
    bar.style.visibility = ''
    bar.style.transform = `translate3d(${Math.round(p.left)}px, ${Math.round(p.top)}px, 0)`
    bar.dataset.side = p.side
  })

  // Escape in the studio deselects (the preview sends its own Escape as a key message)
  const deselect = ve.deselect
  const has = !!selection
  useEffect(() => {
    if (!ve.editing || !has) return
    const onKey = (e: KeyboardEvent) => {
      if (open || !escapeIsOurs(e)) return
      e.preventDefault()
      deselect()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [ve.editing, has, open, deselect])

  const run = (fn: () => string | null) => setErr(fn())

  return (
    <div ref={layerRef} className="pointer-events-none absolute inset-0 z-[15] overflow-hidden" data-testid="vtool-layer">
      <CanvasHandles ve={ve} />
      {shown && view && why && (
        <div ref={barRef} className="pointer-events-auto absolute top-0 left-0 will-change-transform" style={{ visibility: 'hidden' }}>
          <div
            className="flex h-8 items-center gap-0.5 rounded-ctl glass-overlay p-1 text-foreground animate-in fade-in-0 zoom-in-95 duration-(--d-fast)"
            role="toolbar"
            aria-label={`${view.name} actions`}
            data-testid="vtool-bar"
          >
            <span className="max-w-[140px] truncate px-1.5 font-mono text-[11.5px] font-semibold" title={`${view.file}:${view.line}`} data-testid="vtool-name">
              {view.name}
            </span>
            <Act label="Select parent" why={parentSel ? null : 'Nothing editable above this element'} onClick={() => parentSel && ve.select(parentSel.sel)} testId="vtool-parent">
              <CornerLeftUp />
            </Act>
            <ToolbarSeparator className="mx-0.5" />
            <Act label="Edit text" why={locked ? 'The chat is editing the app; visual edits wait until it finishes' : ve.textWhy} kbd="double-click" onClick={() => run(ve.editText)} testId="vtool-text">
              <Type />
            </Act>
            <Act label="Ask AI about this element" onClick={() => askAboutElement(view)} testId="vtool-ask" className="text-primary">
              <Sparkles />
            </Act>
            <ToolbarSeparator className="mx-0.5" />
            <Act label="Move earlier" why={why.up} kbd="Alt+↑" onClick={() => run(() => ve.move(-1))} testId="vtool-up">
              <ArrowUp />
            </Act>
            <Act label="Move later" why={why.down} kbd="Alt+↓" onClick={() => run(() => ve.move(1))} testId="vtool-down">
              <ArrowDown />
            </Act>
            <Act label="Duplicate" why={why.dup} kbd="⌘D" onClick={() => run(ve.duplicate)} testId="vtool-dup">
              <Copy />
            </Act>
            <Popover open={open === 'wrap'} onOpenChange={(o) => setOpen(o ? 'wrap' : null)}>
              <Tip label="Wrap in…">
                <PopoverTrigger render={<Button variant="ghost" size="icon-xs" aria-label="Wrap in" data-testid="vtool-wrap" />}>
                  <Group />
                </PopoverTrigger>
              </Tip>
              <PopoverContent align="start" className="w-auto p-1">
                <div className="flex flex-col gap-0.5" data-testid="vtool-wrap-menu">
                  {WRAPS.map((w) => (
                    <Tip key={w} label={why.wrap[w]} side="right">
                      <button
                        type="button"
                        aria-disabled={why.wrap[w] ? true : undefined}
                        className="flex h-7 items-center rounded-chip px-2 text-left font-mono text-[12px] hover:bg-raise aria-disabled:cursor-not-allowed aria-disabled:opacity-40"
                        onClick={() => {
                          if (why.wrap[w]) return
                          setOpen(null)
                          run(() => ve.wrap(w))
                        }}
                        data-testid={`vtool-wrap-${w}`}
                      >
                        {w}
                      </button>
                    </Tip>
                  ))}
                </div>
              </PopoverContent>
            </Popover>
            <Popover open={open === 'style'} onOpenChange={(o) => setOpen(o ? 'style' : null)}>
              <Tip label="Quick style">
                <PopoverTrigger render={<Button variant="ghost" size="icon-xs" aria-label="Quick style" data-testid="vtool-style" />}>
                  <Paintbrush />
                </PopoverTrigger>
              </Tip>
              <PopoverContent align="start" className="w-auto p-0">
                <QuickStyle ve={ve} box={report?.box ?? null} />
              </PopoverContent>
            </Popover>
            <Tip label={handles.on ? 'Hide padding, gap and size handles' : 'Show padding, gap and size handles'}>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="Handles"
                aria-pressed={handles.on}
                className="aria-pressed:bg-raise aria-pressed:text-primary"
                onClick={handles.toggle}
                data-testid="vtool-handles"
              >
                <Scaling />
              </Button>
            </Tip>
            <ToolbarSeparator className="mx-0.5" />
            <Act label="Delete" why={why.del} kbd="Del" onClick={() => run(ve.remove)} testId="vtool-del" className="hover:text-destructive">
              <Trash2 />
            </Act>
          </div>
          {err && (
            <p className="mt-1 max-w-[320px] rounded-ctl bg-destructive-soft px-2 py-1 text-[11.5px] text-destructive" role="status" data-testid="vtool-error">
              {err}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
