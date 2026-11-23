/**
 * The quick style popover of the canvas toolbar: 3–4 token controls for the selection. Writes go through the same paths as
 * the style panel — `ve.edit` for design props, one class-string splice committed to the shared history (with the instant
 * preview patch) for classes — and the same coalesce keys, so a change here and one in the panel undo the same way.
 */
import { useMemo, useState } from 'react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Switch } from '@/shared/components/ui/switch'
import { cn } from '@/shared/lib/utils'
import type { BoxMetrics } from '../../sandbox/bridge'
import { useAppDocumentStore } from '../../stores'
import type { Op, PropView } from '../editor'
import { useEditHistory } from '../history'
import { elementByKey, tryParse, type FileMap } from '../source'
import { takesClassName } from '../style/StylePanel'
import { writeBreakpoint } from '../style/breakpoints'
import { currentOf, editClasses, isResponsive, readClasses, type ClassChange } from '../style/classEdit'
import { useDeviceStore } from '../style/deviceStore'
import { controlById, type Control } from '../style/tokens'
import type { VisualEdit } from '../useVisualEdit'
import { QUICK, quickItems, quickKind } from './quickStyle'

const DEFAULT = '__default__'

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-[56px] shrink-0 text-[11.5px] text-muted-foreground">{label}</span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

function PropControl({ view, label, edit, disabled }: { view: PropView; label: string; edit: (ops: Op[], coalesce?: string) => void; disabled: boolean }) {
  const e = view.editor!
  const commit = (v: string | boolean | undefined) => edit(v === undefined ? [{ op: 'unsetProp', prop: view.name }] : [{ op: 'setProp', prop: view.name, value: v }], `prop:${view.name}`)
  if (e.kind === 'bool') {
    return (
      <Row label={label}>
        <Switch checked={view.value === true || (view.value === undefined && e.default === true)} onCheckedChange={(c) => commit(c)} disabled={disabled} data-testid={`vtool-style-prop-${view.name}`} />
      </Row>
    )
  }
  if (e.kind !== 'enum') return null
  const items: Record<string, string> = { [DEFAULT]: e.default ? `default (${e.default})` : 'default', ...Object.fromEntries(e.options.map((o) => [o, o])) }
  return (
    <Row label={label}>
      <Select value={typeof view.value === 'string' ? view.value : DEFAULT} onValueChange={(v) => commit(v === DEFAULT ? undefined : (v as string))} items={items} disabled={disabled}>
        <SelectTrigger size="sm" className="w-full" data-testid={`vtool-style-prop-${view.name}`}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.entries(items).map(([k, l]) => (
            <SelectItem key={k} value={k}>
              {l}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Row>
  )
}

function ClassControl({ control, classes, disabled, onChange }: { control: Control; classes: string[]; disabled: boolean; onChange: (c: ClassChange, label: string) => void }) {
  const all = control.options.map((o) => o.cls)
  const cur = currentOf(classes, all)
  const clear = () => onChange({ clear: all }, `clear ${control.label.toLowerCase()}`)
  if (control.color) {
    return (
      <Row label={control.label.replace(/ colour$/, '')}>
        <div className="flex flex-wrap gap-1" role="radiogroup" aria-label={control.label} data-testid={`vtool-style-${control.id}`}>
          <button
            type="button"
            role="radio"
            aria-checked={!cur}
            disabled={disabled || !cur}
            title="default"
            onClick={clear}
            className={cn('h-4 rounded-chip px-1 text-[10px] text-muted-foreground hover:bg-raise disabled:cursor-default', !cur && 'bg-raise text-foreground')}
          >
            default
          </button>
          {control.options.map((o) => {
            const on = cur?.cls === o.cls
            return (
              <button
                key={o.cls}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={o.label}
                title={o.label}
                disabled={disabled}
                onClick={() => !on && onChange({ set: o.cls }, `${control.label.toLowerCase()} ${o.label}`)}
                className={cn('size-4 rounded-chip shadow-[inset_0_0_0_1px_var(--hairline)] disabled:cursor-not-allowed', on && 'outline-2 outline-offset-1 outline-ring')}
                style={{ background: o.swatch }}
                data-testid={`vtool-style-${control.id}-${o.label}`}
              />
            )
          })}
        </div>
      </Row>
    )
  }
  const items: Record<string, string> = { [DEFAULT]: 'default', ...Object.fromEntries(control.options.map((o) => [o.cls, o.label])) }
  if (cur && !cur.known) items[cur.cls] = `${cur.cls} (custom)`
  return (
    <Row label={control.label}>
      <Select
        value={cur?.cls ?? DEFAULT}
        onValueChange={(v) => {
          if (v === DEFAULT) clear()
          else if (v !== cur?.cls) onChange({ set: v as string }, `${control.label.toLowerCase()} ${items[v as string]}`)
        }}
        items={items}
        disabled={disabled}
      >
        <SelectTrigger size="sm" className="w-full" data-testid={`vtool-style-${control.id}`}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.entries(items).map(([k, l]) => (
            <SelectItem key={k} value={k} disabled={k === cur?.cls && !cur.known}>
              {l}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Row>
  )
}

export function QuickStyle({ ve, box }: { ve: VisualEdit; box: BoxMetrics | null }) {
  const docFiles = useAppDocumentStore((s) => s.files)
  const files = useMemo<FileMap>(() => Object.fromEntries((docFiles ?? []).map((f) => [f.path, f.content])), [docFiles])
  const [error, setError] = useState<string | null>(null)
  const device = useDeviceStore((s) => s.device)
  const desktopOnly = useDeviceStore((s) => s.desktopOnly)
  const { view, selection } = ve
  const disabled = ve.locked

  const target = useMemo(() => {
    if (!selection || !view) return null
    const src = files[selection.file]
    const ast = src !== undefined ? tryParse(src) : null
    const hit = ast && elementByKey(ast, selection.key)
    if (!hit) return null
    return { src, el: hit.node, name: hit.name, accepts: takesClassName(files, selection.file, hit.name) }
  }, [files, selection, view])

  if (!view || !selection || !target) return null
  const kind = quickKind(view, box, ve.textWhy)
  const read = target.accepts ? readClasses(target.src, target.el) : null
  const classes = read?.ok ? read.classes : []
  const items = quickItems(view, QUICK[kind], !!read?.ok)

  // the style panel's commit path: one splice of the class string, the instant preview patch, one coalesced undo step
  const change = (c: ClassChange, label: string, key: string) => {
    if (disabled) return setError('The chat is editing the app; visual edits wait until it finishes')
    // at the previewed device's breakpoint, the same rule as the style panel (breakpoints.ts)
    const opts = controlById(key)?.options.map((o) => o.cls) ?? []
    const r = editClasses(target.src, target.el, { ...c, bp: writeBreakpoint(device, desktopOnly, isResponsive(classes, opts)) })
    if (!r.ok) return setError(r.error === 'Nothing to change' ? null : r.error)
    setError(null)
    const add = r.classes.filter((x) => !classes.includes(x))
    const remove = classes.filter((x) => !r.classes.includes(x))
    const tag = ve.tag
    useEditHistory.getState().commit(`${target.name}: ${label}`, [{ path: selection.file, before: target.src, after: r.after }], `style:${selection.key}:${key}`, tag ? [{ vsrc: tag, add, remove }] : undefined)
  }

  return (
    <div className="flex w-[248px] flex-col gap-2 p-2.5" data-testid="vtool-style-panel">
      <div className="flex items-center gap-2 text-[11.5px]">
        <span className="font-semibold">{kind === 'text' ? 'Text' : 'Box'}</span>
        <span className="text-muted-foreground">theme values · more in the panel</span>
      </div>
      {error && (
        <p className="text-[11.5px] text-destructive" data-testid="vtool-style-error">
          {error}
        </p>
      )}
      {items.length === 0 ? (
        <p className="text-[11.5px] text-muted-foreground" data-testid="vtool-style-empty">
          {read && !read.ok ? read.reason : `${target.name} has no quick style here; use the panel or Ask AI.`}
        </p>
      ) : (
        items.map((it) =>
          it.kind === 'prop' ? (
            <PropControl key={it.id} view={it.prop} label={it.prop.name} edit={(ops, co) => setError(ve.edit(ops, co))} disabled={disabled} />
          ) : (
            <ClassControl key={it.id} control={it.control} classes={classes} disabled={disabled} onChange={(ch, label) => change(ch, label, it.control.id)} />
          ),
        )
      )}
    </div>
  )
}
