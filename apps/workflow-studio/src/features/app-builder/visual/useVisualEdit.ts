import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { AppFile } from '../sandbox/esbuild-bundler'
import type { PreviewScreen } from '../sandbox/bridge'
import type { VisualEditBridge, VisualEditPort } from '../sandbox/IframeSandbox'
import { applyOps, describe, resolveClick, textRefusal, vsrcFor, type ElementView, type Op, type Selection } from './editor'
import { setCanvasRect } from './canvas/canvasRect'
import { setPreviewSink, useEditHistory } from './history'
import type { FileMap } from './source'
import { dragTargetsFor, dropTargets, paletteFor, type InsertWhere, type PaletteItem, type WrapWith } from './structure'

export interface Crumb {
  name: string
  sel: Selection
}

export interface VisualEdit {
  editing: boolean
  setEditing: (on: boolean) => void
  selection: Selection | null
  select: (sel: Selection | null) => void
  view: ElementView | null
  /** the elements around the last click, innermost first */
  crumbs: Crumb[]
  /** apply ops to the selection as one transaction; returns an error message or null */
  edit: (ops: Op[], coalesce?: string) => string | null
  /** move the selection one place earlier (-1) or later (+1) among its siblings; an error message or null */
  move: (delta: -1 | 1) => string | null
  /** structure ops on the selection (each one undo step); an error message or null */
  remove: () => string | null
  duplicate: () => string | null
  wrap: (w: WrapWith) => string | null
  unwrap: () => string | null
  insert: (component: string, where: InsertWhere, slot?: string) => string | null
  /** the components the palette offers */
  palette: PaletteItem[]
  /** drag a palette item over the preview (client coordinates); `end` drops it (or cancels with drop=false) */
  paletteDrag: { start: (component: string) => void; move: (x: number, y: number) => void; end: (drop: boolean) => void }
  /** the last refusal from a preview gesture (drag, key), for the panel to show */
  notice: string | null
  /** the selection's preview tag when a class change can be patched into the preview at once (not a list part) */
  tag: string | null
  bridge: VisualEditBridge
  locked: boolean
  /** clear the selection (Escape) */
  deselect: () => void
  /** why the selection's text can't be edited in place (as a double-click would), or null when it can */
  textWhy: string | null
  /** start an inline text edit of the selection in the preview; an error message or null */
  editText: () => string | null
  /** what `edit(ops)` would refuse with, without applying anything (null = it would apply) */
  dryRun: (ops: Op[]) => string | null
  /** the sources the edits apply to */
  files: FileMap
  /** the preview's iframe (to map preview coordinates onto the studio), null before it is ready */
  frame: () => HTMLIFrameElement | null
  /** apply ops at a given selection (not necessarily the current one) as one undo step; an error message or null */
  editAt: (at: Selection | null, ops: Op[], coalesce?: string) => string | null
  /** outline the element tagged `vsrc` in the preview as a hover would (the Layers tree), without selecting it; null clears */
  hover: (vsrc: string | null, vpart?: string | null) => void
  /** the AppShell screen the preview shows (null: none, or not reported yet) */
  screen: PreviewScreen | null
  /**
   * A canvas handle's live preview on the selection: inline styles in the iframe, no re-bundle. null clears them at once, or
   * with `until: 'render'` once the commit's render lands.
   */
  liveStyle: (style: Record<string, string> | null, until?: 'render') => void
}

/** The part marker the kit puts on a list item (`columns.3`): the prop name and the index as written. */
function vpartFor(sel: Selection): string | null {
  return sel.part ? `${sel.part.prop}.${sel.part.index}` : null
}

/** The element name at the end of a structural key (`Top/A>B#0` → `B`). */
function keyName(key: string): string {
  const path = key.slice(key.indexOf('/') + 1, key.lastIndexOf('#'))
  return path.slice(path.lastIndexOf('>') + 1)
}

const LOCKED = 'The chat is editing the app; visual edits wait until it finishes'

/** Selection, outline and edits for the preview; `locked` while a chat turn is writing the same files. */
export function useVisualEdit(files: AppFile[] | null, locked: boolean): VisualEdit {
  const [editing, setEditing] = useState(false)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [crumbs, setCrumbs] = useState<Crumb[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [screen, setScreen] = useState<PreviewScreen | null>(null)
  const fileMap = useMemo<FileMap>(() => Object.fromEntries((files ?? []).map((f) => [f.path, f.content])), [files])
  const view = useMemo(() => (selection ? describe(fileMap, selection) : null), [fileMap, selection])
  const palette = useMemo(() => (editing ? paletteFor(fileMap) : []), [fileMap, editing])
  const port = useRef<VisualEditPort | null>(null)
  const dragging = useRef<string | null>(null)

  // a chat edit removed (or renamed) the selected element: drop the selection rather than point at something else
  if (selection && !view) {
    setSelection(null)
    setCrumbs([])
  }

  const pick = useCallback(
    (picked: Parameters<VisualEditBridge['onSelect']>[0], enter: boolean) => {
      const r = resolveClick(fileMap, picked.chain, picked.part, enter)
      setCrumbs(r.crumbs)
      setSelection(r.selection)
      setNotice(null)
    },
    [fileMap],
  )
  const onSelect = useCallback<VisualEditBridge['onSelect']>((p) => pick(p, false), [pick])
  const onDblSelect = useCallback<VisualEditBridge['onSelect']>((p) => pick(p, true), [pick])

  const vsrc = selection && view ? vsrcFor(fileMap, selection) : null
  const vpart = selection && view ? vpartFor(selection) : null
  const label = view?.name ?? ''
  const mv = view?.move.ok ? view.move : null
  const siblings = useMemo(() => (mv && !vpart ? { index: mv.index, count: mv.count, items: mv.siblings } : null), [mv, vpart])
  const drag = useMemo(() => (selection && view && !vpart ? dragTargetsFor(fileMap, selection) : null), [fileMap, selection, view, vpart])
  const highlight = useMemo(
    () => (vsrc ? { vsrc, vpart, label, siblings, parent: drag?.parent ?? null, targets: drag?.targets.map((d) => ({ vsrc: d.vsrc, items: d.items, count: d.count })) ?? [] } : null),
    [vsrc, vpart, label, siblings, drag],
  )

  /** Apply ops at `at` (the selection by default) as one undo step and follow the selection they return. */
  const editAt = useCallback(
    (at: Selection | null, ops: Op[], coalesce?: string) => {
      if (locked) return LOCKED
      if (!at) return 'Nothing selected'
      const r = applyOps(fileMap, at, ops)
      if (!r.ok) return r.error
      useEditHistory.getState().commit(r.label, r.patches, coalesce)
      if (r.select === null) {
        setSelection(null)
        setCrumbs([])
      } else if (r.select) {
        const next = r.select
        setSelection(next)
        const same = selection && at.file === selection.file && at.key === selection.key
        if (same && ops.every((o) => o.op === 'move' || o.op === 'moveTo')) {
          const prev = selection
          setCrumbs((cs) => cs.map((c) => (c.sel.file === prev.file && c.sel.key === prev.key ? { ...c, sel: next } : c)))
        } else setCrumbs([{ name: keyName(next.key), sel: next }])
      }
      return null
    },
    [fileMap, selection, locked],
  )
  const edit = useCallback((ops: Op[], coalesce?: string) => editAt(selection, ops, coalesce), [editAt, selection])
  const dryRun = useCallback(
    (ops: Op[]) => {
      if (locked) return LOCKED
      if (!selection) return 'Nothing selected'
      const r = applyOps(fileMap, selection, ops)
      return r.ok ? null : r.error
    },
    [fileMap, selection, locked],
  )

  const move = useCallback(
    (delta: -1 | 1) => {
      if (!view) return 'Nothing selected'
      if (!view.move.ok) return view.move.reason
      const to = view.move.index + delta
      if (to < 0 || to >= view.move.count) return delta < 0 ? 'Already first' : 'Already last'
      return edit([{ op: 'move', to }])
    },
    [view, edit],
  )

  // a drop in the preview: insert before child `before` (count = at the end) of the parent, or of another container
  const onMove = useCallback<VisualEditBridge['onMove']>(
    (before, target) => {
      if (target && target !== drag?.parent) {
        const t = drag?.targets.find((d) => d.vsrc === target)
        if (!t) return
        setNotice(edit([{ op: 'moveTo', target: { file: t.file, key: t.key }, index: before }]))
        return
      }
      if (!view?.move.ok) return
      const from = view.move.index
      const to = before > from ? before - 1 : before
      if (to !== from) setNotice(edit([{ op: 'move', to }]))
    },
    [view, edit, drag],
  )

  // structure ops report their refusal through `notice` too (they also run from keys, where nothing else shows it)
  const structural = useCallback((op: Op) => {
    const r = edit([op])
    setNotice(r)
    return r
  }, [edit])
  const remove = useCallback(() => structural({ op: 'delete' }), [structural])
  const duplicate = useCallback(() => structural({ op: 'duplicate' }), [structural])
  const wrap = useCallback((w: WrapWith) => structural({ op: 'wrap', with: w }), [structural])
  const unwrap = useCallback(() => structural({ op: 'unwrap' }), [structural])
  const insert = useCallback((component: string, where: InsertWhere, slot?: string) => structural({ op: 'insert', component, where, slot }), [structural])

  // a palette item dropped in the preview: insert it into container `target` before child `before`
  const onInsert = useCallback<VisualEditBridge['onInsert']>(
    (target, before) => {
      const component = dragging.current
      dragging.current = null
      const t = dropTargets(fileMap).find((d) => d.vsrc === target)
      if (!component || !t) return
      setNotice(editAt({ file: t.file, key: t.key, part: null }, [{ op: 'insert', component, where: 'inside', index: before }]))
    },
    [fileMap, editAt],
  )

  const paletteDrag = useMemo<VisualEdit['paletteDrag']>(
    () => ({
      start: (component) => {
        dragging.current = component
        const targets = dropTargets(fileMap).map((d) => ({ vsrc: d.vsrc, items: d.items, count: d.count }))
        // the parent document has to keep receiving the pointer while it is over the preview
        if (port.current) port.current.frame.style.pointerEvents = 'none'
        port.current?.post({ type: 'vpal', phase: 'start', targets })
      },
      move: (x, y) => {
        const p = port.current
        if (!p) return
        const r = p.frame.getBoundingClientRect()
        p.post({ type: 'vpal', phase: 'move', x: x - r.left, y: y - r.top, inside: x >= r.left && x <= r.right && y >= r.top && y <= r.bottom })
      },
      end: (drop) => {
        if (!drop) dragging.current = null
        if (port.current) port.current.frame.style.pointerEvents = ''
        port.current?.post({ type: 'vpal', phase: 'end', drop })
      },
    }),
    [fileMap],
  )

  // an inline text edit in the preview: `begin` checks the element's text can be edited, `commit` applies it with the panel's setText op
  const onText = useCallback<VisualEditBridge['onText']>(
    (phase, picked, value) => {
      const sel = picked.part ? null : resolveClick(fileMap, picked.chain, null, true).selection
      if (!sel) return false
      const why = locked ? LOCKED : textRefusal(fileMap, sel)
      if (why) {
        setNotice(why)
        return false
      }
      if (phase === 'begin') {
        const r = resolveClick(fileMap, picked.chain, null, true)
        setCrumbs(r.crumbs)
        setSelection(sel)
        setNotice(null)
        return true
      }
      const text = (value ?? '').trim()
      if (!text) {
        setNotice('Text can not be empty; delete the element instead')
        return false
      }
      const err = editAt(sel, [{ op: 'setText', index: 0, value: text }])
      setNotice(err)
      return !err
    },
    [fileMap, editAt, locked],
  )

  // class changes (and their undo / redo) reach the preview before the re-bundle does
  useEffect(() => {
    setPreviewSink((p) => port.current?.post({ type: 'vpatch', ...p }))
    return () => setPreviewSink(null)
  }, [])

  const select = useCallback((sel: Selection | null) => setSelection(sel), [])
  const deselect = useCallback(() => {
    setSelection(null)
    setCrumbs([])
    setNotice(null)
  }, [])

  // the toolbar's "Edit text": the preview runs the same begin / commit round trip as a double-click
  const textWhy = useMemo(() => {
    if (!selection || !view) return 'Nothing selected'
    if (selection.part) return 'A list item: edit its text in the panel'
    return textRefusal(fileMap, selection)
  }, [fileMap, selection, view])
  const editText = useCallback(() => {
    const why = locked ? LOCKED : textWhy
    if (why) return why
    if (!vsrc || !port.current) return 'The preview is not ready'
    port.current.post({ type: 'vtextstart', vsrc })
    return null
  }, [locked, textWhy, vsrc])

  const onKey = useCallback<VisualEditBridge['onKey']>(
    (k) => {
      if (k === 'deselect') deselect()
      else if (k === 'delete') remove()
      else if (k === 'duplicate') duplicate()
      else void move(k)
    },
    [move, remove, duplicate, deselect],
  )
  const connect = useCallback<VisualEditBridge['connect']>((p) => {
    port.current = p
    if (!p) {
      setCanvasRect(null)
      setScreen(null)
    }
  }, [])
  const frame = useCallback(() => port.current?.frame ?? null, [])
  const hover = useCallback((v: string | null, vp?: string | null) => port.current?.post({ type: 'vhover', vsrc: v, vpart: vp ?? null }), [])
  const liveStyle = useCallback(
    (style: Record<string, string> | null, until?: 'render') => {
      if (vsrc) port.current?.post({ type: 'vlive', vsrc, vpart, style, ...(until ? { until } : {}) })
    },
    [vsrc, vpart],
  )
  const bridge = useMemo<VisualEditBridge>(
    () => ({ editing, highlight: editing ? highlight : null, onSelect, onDblSelect, onMove, onKey, onInsert, onText, onRect: setCanvasRect, onScreen: setScreen, connect }),
    [editing, highlight, onSelect, onDblSelect, onMove, onKey, onInsert, onText, connect],
  )
  return {
    editing, setEditing, selection, select, view, crumbs, edit, move, remove, duplicate, wrap, unwrap, insert, palette, paletteDrag, notice, tag: vpart ? null : vsrc, bridge, locked,
    deselect, textWhy, editText, dryRun, files: fileMap, frame, editAt, hover, screen, liveStyle,
  }
}
