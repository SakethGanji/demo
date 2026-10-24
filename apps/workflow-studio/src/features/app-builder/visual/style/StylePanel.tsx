/**
 * The style section of the visual editor. Design props first: when the selected component styles itself through enum
 * props (gap, padding, tone, size…), those are offered and the class controls they cover are hidden. Then token-locked
 * class controls (Layout, Spacing, Size, Typography, Fill & border) for raw elements and components that take a
 * `className`. Each change is one splice of the class string (classEdit.ts), recorded in the shared undo history; the
 * preview follows on the normal re-bundle.
 *
 * Responsive: edits land on the breakpoint of the device the preview shows (breakpoints.ts — phone → base, tablet → `md:`,
 * desktop → base or `lg:`). A control shows the value inherited from a smaller breakpoint (dimmed) and a per-breakpoint
 * reset that removes just that prefixed class. Above the style controls, the const notes: values this element shows that
 * live in a const (editing changes every use) or in a variable the editor can't follow (and why).
 */
import { useMemo, useState } from 'react'
import { ChevronRight, RotateCcw } from 'lucide-react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Switch } from '@/shared/components/ui/switch'
import { cn } from '@/shared/lib/utils'
import { useAppDocumentStore } from '../../stores'
import type { ConstNote, ElementView, Op, PropView } from '../editor'
import { useEditHistory } from '../history'
import { componentFor } from '../registry/registry'
import { elementByKey, tryParse, type FileMap } from '../source'
import type { VisualEdit } from '../useVisualEdit'
import { BP_LABEL, DEVICE_BP, editingLabel, RESPONSIVE_PROPS, RESPONSIVE_TWINS, writeBreakpoint } from './breakpoints'
import { currentAt, editClasses, effectiveAt, isResponsive, readClasses, type Breakpoint, type ClassChange } from './classEdit'
import { useDeviceStore, type Device } from './deviceStore'
import { CONTROLS, SECTIONS, STYLE_PROPS, type Control } from './tokens'

const DEFAULT = '__default__'

/** Where edits go right now: the device shown and whether desktop edits are desktop-only. */
interface BpCtx {
  device: Device
  desktopOnly: boolean
}

function Label({ children, note }: { children: React.ReactNode; note?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 text-[11.5px] text-muted-foreground">
      <span className="font-medium text-foreground">{children}</span>
      {note}
    </div>
  )
}

/** The dimmed "inherits X from <size>" line under a control. */
function Inherited({ id, value, from }: { id: string; value: string; from: string }) {
  return (
    <span className="truncate text-[10.5px] text-faint" title={`Not set at this size; ${value} comes from ${from}`} data-testid={`vstyle-${id}-inherited`}>
      inherits <span className="font-mono">{value}</span> from {from}
    </span>
  )
}

/** Remove the value set at this breakpoint only (the smaller sizes' value shows through again). */
function ResetBp({ id, bp, disabled, onReset }: { id: string; bp: Breakpoint; disabled: boolean; onReset: () => void }) {
  return (
    <button
      type="button"
      className="inline-flex shrink-0 items-center gap-0.5 rounded-chip px-1 text-[10.5px] text-muted-foreground hover:bg-raise hover:text-foreground disabled:cursor-not-allowed"
      title={`Remove the ${BP_LABEL[bp]} value`}
      disabled={disabled}
      onClick={onReset}
      data-testid={`vstyle-${id}-reset-bp`}
    >
      <RotateCcw className="size-3" /> {bp}
    </button>
  )
}

/** A prop value as the select key (`cols={3}` and `cols="3"` both read as `3`). */
const optionKey = (v: PropView['value']) => (typeof v === 'string' || typeof v === 'number' ? String(v) : null)

/** Write a numeric option as a number when every option is one (`cols={3}` matches the kit's `1 | 2 | 3` type). */
const optionValue = (options: readonly string[], v: string) => (options.every((o) => /^\d+$/.test(o)) ? Number(v) : v)

/**
 * One enum design prop (`gap`, `tone`…) as a select; booleans as a switch. Grid's `cols` follows the breakpoint through its
 * `colsMd` / `colsLg` twins; other props can't differ by size, so they're disabled (with the reason) off the base breakpoint.
 */
function StyleProp({ view, all, bp, edit, disabled }: { view: PropView; all: PropView[]; bp: BpCtx; edit: (ops: Op[], coalesce?: string) => void; disabled: boolean }) {
  const twins = RESPONSIVE_PROPS[view.name]
  const byName = (n: string) => all.find((p) => p.name === n)
  const set = (p: PropView | undefined) => p !== undefined && p.state === 'literal' && optionKey(p.value) !== null
  const responsive = !!twins && (set(byName(twins.md)) || set(byName(twins.lg)))
  const target = writeBreakpoint(bp.device, bp.desktopOnly, responsive)
  const own = twins ? (byName(twins[target]) ?? { ...view, name: twins[target], state: 'unset' as const, value: undefined }) : view
  const refused = !twins && target !== 'base'
  const e = view.editor!
  const commit = (v: string | number | boolean | undefined) => edit(v === undefined ? [{ op: 'unsetProp', prop: own.name }] : [{ op: 'setProp', prop: own.name, value: v }], `prop:${own.name}`)
  if (e.kind === 'bool') {
    return (
      <div className="flex items-center justify-between gap-2">
        <Label>{view.name}</Label>
        <Switch checked={view.value === true || (view.value === undefined && e.default === true)} onCheckedChange={(c) => commit(c)} disabled={disabled || refused} data-testid={`vstyle-prop-${view.name}`} />
      </div>
    )
  }
  if (e.kind !== 'enum') return null
  // what shows through when this breakpoint sets nothing: the nearest smaller breakpoint's twin, else the base prop
  let inherited: { value: string; from: string } | null = null
  if (twins && target !== 'base' && !set(own)) {
    const lower: Breakpoint[] = target === 'lg' ? ['md', 'base'] : ['base']
    for (const b of lower) {
      const p = byName(twins[b])
      if (set(p)) {
        inherited = { value: optionKey(p!.value)!, from: BP_LABEL[b] }
        break
      }
    }
    if (!inherited && e.default) inherited = { value: e.default, from: 'the default' }
  }
  const base = target === 'base' ? (e.default ? `default (${e.default})` : 'default') : 'inherit'
  const items: Record<string, string> = { [DEFAULT]: base, ...Object.fromEntries(e.options.map((o) => [o, o])) }
  const cur = optionKey(own.value)
  return (
    <div className="flex flex-col gap-1">
      <Label note={target !== 'base' && twins ? <span className="font-mono">{own.name}</span> : null}>{view.name}</Label>
      <div className="flex items-center gap-1">
        <Select
          value={cur ?? DEFAULT}
          onValueChange={(v) => commit(v === DEFAULT ? undefined : optionValue(e.options, v as string))}
          items={items}
          disabled={disabled || refused}
        >
          <SelectTrigger size="sm" className="min-w-0 flex-1" data-testid={`vstyle-prop-${view.name}`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(items).map(([k, label]) => (
              <SelectItem key={k} value={k}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {twins && target !== 'base' && cur !== null && <ResetBp id={`prop-${view.name}`} bp={target} disabled={disabled} onReset={() => commit(undefined)} />}
      </div>
      {inherited && <Inherited id={`prop-${view.name}`} value={inherited.value} from={inherited.from} />}
      {refused && (
        <span className="text-[10.5px] text-muted-foreground" data-testid={`vstyle-prop-${view.name}-refused`}>
          {view.name} applies to all sizes (the kit has no per-size {view.name}); switch to Phone to change it, or ask AI
        </span>
      )}
    </div>
  )
}

/** A class control: swatches for colours, a select for scales — at the breakpoint the device's edits land on. */
function ClassControl({ control, classes, bp, disabled, onChange }: { control: Control; classes: string[]; bp: BpCtx; disabled: boolean; onChange: (c: ClassChange, label: string) => void }) {
  const all = control.options.map((o) => o.cls)
  const target = writeBreakpoint(bp.device, bp.desktopOnly, isResponsive(classes, all))
  const cur = currentAt(classes, all, target)
  const shown = effectiveAt(classes, all, DEVICE_BP[bp.device])
  const inherited = !cur && shown && shown.from !== target ? shown : null
  const clear = () => onChange({ clear: all, bp: target }, `clear ${control.label.toLowerCase()}${target === 'base' ? '' : ` (${target})`}`)
  const set = (cls: string, label: string) => onChange({ set: cls, bp: target }, `${control.label.toLowerCase()} ${label}${target === 'base' ? '' : ` (${target})`}`)
  const note = cur && !cur.known ? <span className="font-mono" title="Not a theme value; picking one replaces it">{cur.cls}</span> : null
  const reset = target !== 'base' && cur ? <ResetBp id={control.id} bp={target} disabled={disabled} onReset={clear} /> : null
  const inh = inherited ? <Inherited id={control.id} value={inherited.cls} from={BP_LABEL[inherited.from]} /> : null
  if (control.color) {
    return (
      <div className="flex flex-col gap-1">
        <Label note={<>{note}{reset}</>}>{control.label}</Label>
        <div className="flex flex-wrap gap-1" role="radiogroup" aria-label={control.label} data-testid={`vstyle-${control.id}`}>
          <button
            type="button"
            role="radio"
            aria-checked={!cur}
            disabled={disabled || !cur}
            title={target === 'base' ? 'default' : 'inherit from smaller sizes'}
            onClick={clear}
            className={cn('h-5 rounded-chip px-1.5 text-[10.5px] text-muted-foreground hover:bg-raise disabled:cursor-default', !cur && 'bg-raise text-foreground')}
          >
            {target === 'base' ? 'default' : 'inherit'}
          </button>
          {control.options.map((o) => {
            const on = cur?.cls === o.cls
            const ghost = !cur && inherited?.cls === o.cls
            return (
              <button
                key={o.cls}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={o.label}
                title={ghost ? `${o.label} (inherited)` : o.label}
                disabled={disabled}
                onClick={() => !on && set(o.cls, o.label)}
                className={cn(
                  'size-5 rounded-chip shadow-[inset_0_0_0_1px_var(--hairline)] disabled:cursor-not-allowed',
                  on && 'outline-2 outline-offset-1 outline-ring',
                  ghost && 'outline-1 outline-offset-1 outline-dashed outline-ring/60',
                )}
                style={{ background: o.swatch }}
                data-testid={`vstyle-${control.id}-${o.label}`}
              />
            )
          })}
        </div>
        {inh}
      </div>
    )
  }
  const items: Record<string, string> = { [DEFAULT]: target === 'base' ? 'default' : 'inherit', ...Object.fromEntries(control.options.map((o) => [o.cls, o.label])) }
  if (cur && !cur.known) items[cur.cls] = `${cur.cls} (custom)`
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center gap-2">
        <span className="w-[84px] shrink-0 text-[11.5px] font-medium">{control.label}</span>
        <Select
          value={cur?.cls ?? DEFAULT}
          onValueChange={(v) => {
            if (v === DEFAULT) clear()
            else if (v !== cur?.cls) set(v as string, items[v as string])
          }}
          items={items}
          disabled={disabled}
        >
          <SelectTrigger size="sm" className="min-w-0 flex-1" data-testid={`vstyle-${control.id}`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(items).map(([k, label]) => (
              <SelectItem key={k} value={k} disabled={k === cur?.cls && !cur.known}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {reset}
      </div>
      {inh && <div className="pl-[92px]">{inh}</div>}
    </div>
  )
}

function Section({ label, open, onToggle, children, id }: { label: string; open: boolean; onToggle: () => void; children: React.ReactNode; id: string }) {
  return (
    <div className="flex flex-col gap-2">
      <button type="button" className="flex items-center gap-1 text-[11.5px] font-semibold" onClick={onToggle} aria-expanded={open} data-testid={`vstyle-section-${id}`}>
        <ChevronRight className={cn('size-3 transition-transform', open && 'rotate-90')} />
        {label}
      </button>
      {open && <div className="flex flex-col gap-2 pl-4">{children}</div>}
    </div>
  )
}

/** "Editing: Tablet and up", and at desktop the switch between all-sizes edits and desktop-only (`lg:`) ones. */
function BreakpointBar({ bp }: { bp: BpCtx }) {
  const setDesktopOnly = useDeviceStore((s) => s.setDesktopOnly)
  return (
    <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
      <span className="min-w-0 flex-1 truncate" data-testid="vstyle-breakpoint" title="Switch the device in the preview toolbar to style another screen size">
        Editing: <span className="font-medium text-foreground">{editingLabel(bp.device, bp.desktopOnly)}</span>
      </span>
      {bp.device === 'desktop' && (
        <label className="flex shrink-0 items-center gap-1">
          Desktop only
          <Switch checked={bp.desktopOnly} onCheckedChange={(c) => setDesktopOnly(c)} data-testid="vstyle-desktop-only" />
        </label>
      )}
    </div>
  )
}

/** Values this element shows that live in a const (editing changes every use), or in a variable that can't be followed. */
function ConstNotes({ notes }: { notes: ConstNote[] }) {
  return (
    <div className="flex flex-col gap-1" data-testid="vedit-const-note">
      {notes.map((n, i) => (
        <p key={`${n.what}:${n.name}:${i}`} className="text-[11px] text-muted-foreground" data-testid={`vedit-const-note-${n.what}`}>
          <span className="font-medium text-foreground">{n.what === 'element' ? 'This element' : n.what === 'text' ? 'Text' : n.what}</span>
          {' '}
          {n.reason ? (
            <>is read-only: {n.reason}</>
          ) : (
            <>
              comes from <span className="font-mono">{n.name}</span>
              {n.line ? ` (line ${n.line})` : ''}
              {n.uses > 1 ? <span className="text-foreground"> · used in {n.uses} places: editing changes all</span> : null}
            </>
          )}
        </p>
      ))}
    </div>
  )
}

/** Whether a component merges a caller's `className` (it declares the prop, or its definition mentions it). */
export function takesClassName(files: FileMap, file: string, name: string): boolean {
  if (/^[a-z]/.test(name)) return true
  const comp = componentFor(files, file, name)
  if (!comp) return false
  if (comp.props.some((p) => p.name === 'className')) return true
  return (files[comp.file] ?? '').slice(comp.start, comp.end).includes('className')
}

/** The const notes worth showing: shared consts, refused variables, and an element defined in a const. */
function notesOf(view: ElementView): ConstNote[] {
  return (view.consts ?? []).filter((n) => n.reason || n.uses > 1 || n.what === 'element' || n.what === 'text')
}

export function StylePanel({ ve }: { ve: VisualEdit }) {
  const docFiles = useAppDocumentStore((s) => s.files)
  const files = useMemo<FileMap>(() => Object.fromEntries((docFiles ?? []).map((f) => [f.path, f.content])), [docFiles])
  const device = useDeviceStore((s) => s.device)
  const desktopOnly = useDeviceStore((s) => s.desktopOnly)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({ typography: true, spacing: true, fill: true })
  const { view, selection } = ve
  const [errorFor, setErrorFor] = useState(selection)
  if (errorFor !== selection) {
    setErrorFor(selection)
    setError(null)
  }
  const disabled = ve.locked
  const bp: BpCtx = { device, desktopOnly }

  const target = useMemo(() => {
    if (!selection || !view) return null
    const src = files[selection.file]
    const ast = src !== undefined ? tryParse(src) : null
    const hit = ast && elementByKey(ast, selection.key)
    if (!hit) return null
    return { src, el: hit.node, name: hit.name, accepts: takesClassName(files, selection.file, hit.name) }
  }, [files, selection, view])

  const notes = view ? notesOf(view) : []
  if (!view || !target) return notes.length ? <ConstNotes notes={notes} /> : null

  const styleProps = view.props.filter(
    (p) => p.name in STYLE_PROPS && !RESPONSIVE_TWINS.has(p.name) && p.editor && (p.editor.kind === 'enum' || p.editor.kind === 'bool') && (p.state === 'literal' || p.state === 'unset'),
  )
  const covered = new Set(view.props.filter((p) => p.name in STYLE_PROPS && p.editor).flatMap((p) => STYLE_PROPS[p.name]))
  const read = target.accepts ? readClasses(target.src, target.el) : null
  const classes = read?.ok ? read.classes : []

  const change = (c: ClassChange, label: string, key: string) => {
    if (disabled) return setError('The chat is editing the app; visual edits wait until it finishes')
    const r = editClasses(target.src, target.el, c)
    if (!r.ok) return setError(r.error === 'Nothing to change' ? null : r.error)
    setError(null)
    // show the new classes in the preview now; the splice's re-bundle follows and matches
    const add = r.classes.filter((c) => !classes.includes(c))
    const remove = classes.filter((c) => !r.classes.includes(c))
    const tag = ve.tag
    useEditHistory.getState().commit(`${target.name}: ${label}`, [{ path: selection!.file, before: target.src, after: r.after }], `style:${selection!.key}:${key}:${c.bp ?? 'base'}`, tag ? [{ vsrc: tag, add, remove }] : undefined)
  }

  const raw = /^[a-z]/.test(target.name)
  return (
    <div className="flex flex-col gap-3" data-testid="vstyle-panel">
      {notes.length > 0 && <ConstNotes notes={notes} />}
      <div className="flex items-center gap-2 text-[11.5px]">
        <span className="font-semibold">Style</span>
        <span className="text-muted-foreground">{raw ? 'this element' : `this ${target.name}`} · theme values only</span>
      </div>
      <BreakpointBar bp={bp} />
      {error && (
        <p className="text-[11.5px] text-destructive" data-testid="vstyle-error">
          {error}
        </p>
      )}
      {styleProps.length > 0 && (
        <div className="flex flex-col gap-2" data-testid="vstyle-props">
          {styleProps.map((p) => (
            <StyleProp key={p.name} view={p} all={view.props} bp={bp} edit={(ops, co) => setError(ve.edit(ops, co))} disabled={disabled} />
          ))}
        </div>
      )}
      {!target.accepts ? (
        styleProps.length === 0 && <p className="text-[11.5px] text-muted-foreground">{target.name} doesn't take classes; style it through its props or with AI.</p>
      ) : !read?.ok ? (
        <p className="text-[11.5px] text-muted-foreground" data-testid="vstyle-refused">
          {read?.reason}
        </p>
      ) : (
        <>
          {SECTIONS.map((s) => {
            const controls = CONTROLS.filter((c) => c.section === s.id && !covered.has(c.id))
            if (!controls.length) return null
            return (
              <Section key={s.id} id={s.id} label={s.label} open={!!open[s.id]} onToggle={() => setOpen((o) => ({ ...o, [s.id]: !o[s.id] }))}>
                {controls.map((c) => (
                  <ClassControl key={c.id} control={c} classes={classes} bp={bp} disabled={disabled} onChange={(ch, label) => change(ch, label, c.id)} />
                ))}
              </Section>
            )
          })}
          {classes.length > 0 && (
            <p className="font-mono text-[10.5px] break-all text-muted-foreground" data-testid="vstyle-classes">
              {classes.join(' ')}
            </p>
          )}
        </>
      )}
    </div>
  )
}
