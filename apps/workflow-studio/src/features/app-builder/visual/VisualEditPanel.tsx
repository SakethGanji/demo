/**
 * The property panel for the visual-editing trial: the selected element's breadcrumb, its editable literals (text, enums,
 * switches, numbers), list editors for columns / fields / tiles / tabs / screens, everything else read-only as source, and
 * "Ask AI about this element". Every change is an `Op` the hook turns into a minimal source splice.
 */
import { useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, ChevronRight, Eye, EyeOff, Layers, Plus, SlidersHorizontal, Sparkles, X } from 'lucide-react'
import { Segmented } from '@/shared/components/ui/segmented'
import { Button } from '@/shared/components/ui/button'
import { Badge } from '@/shared/components/ui/badge'
import { Input } from '@/shared/components/ui/input'
import { Switch } from '@/shared/components/ui/switch'
import { Textarea } from '@/shared/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Well, WellList, WellRow } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { askPrompt, type ComponentRef, type ConstNote, type ElementView, type ItemView, type Op, type PropView, type Selection } from './editor'
import { newItem, type ListItem, type PropEditor } from './manifest'
import type { Literal } from './splice'
import type { VisualEdit } from './useVisualEdit'
import { StructurePanel } from './StructurePanel'
import { StylePanel } from './style/StylePanel'
import { askAboutElement, selectionOf, useAskAi } from './askAi'
import { AskFix } from './AskFix'
import { LayersPanel } from './layers/LayersPanel'
import { useLayerFiles } from './layers/files'
import { layerCrumbs } from './layers/tree'
import { BindPanel } from './bind/BindPanel'

const UNSET = '__unset__'

type Commit = (v: Literal | undefined) => void

/** A text / number box that commits on Enter or blur (one re-bundle per edit, not per keystroke). */
function DraftInput({ value, number, multiline, range, placeholder, onCommit, disabled, testId }: {
  value: string
  number?: boolean
  /** a textarea (Enter commits with ⌘/Ctrl, plain Enter is a new line) */
  multiline?: boolean
  range?: { min?: number; max?: number; step?: number }
  placeholder?: string
  onCommit: (s: string) => void
  disabled?: boolean
  testId?: string
}) {
  const [draft, setDraft] = useState(value)
  // a new value from the source (an undo, a chat edit) replaces the draft
  const [base, setBase] = useState(value)
  if (base !== value) {
    setBase(value)
    setDraft(value)
  }
  const commit = () => {
    if (draft !== value) onCommit(draft)
  }
  if (multiline) {
    return (
      <Textarea
        rows={3}
        className="text-[12.5px]"
        value={draft}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) commit()
          if (e.key === 'Escape') setDraft(value)
        }}
        data-testid={testId}
      />
    )
  }
  return (
    <Input
      className="h-7 text-[12.5px]"
      type={number ? 'number' : 'text'}
      min={range?.min}
      max={range?.max}
      step={range?.step}
      value={draft}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') commit()
        if (e.key === 'Escape') setDraft(value)
      }}
      data-testid={testId}
    />
  )
}

function mapText(v: Literal | undefined): string {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return ''
  return Object.entries(v).map(([k, x]) => `${k}=${String(x)}`).join(', ')
}

function parseMap(s: string): Record<string, Literal> {
  const out: Record<string, Literal> = {}
  for (const part of s.split(',')) {
    const [k, ...rest] = part.split('=')
    const raw = rest.join('=').trim()
    if (!k.trim()) continue
    out[k.trim()] = raw === 'true' ? true : raw === 'false' ? false : raw !== '' && !Number.isNaN(Number(raw)) ? Number(raw) : raw
  }
  return out
}

/** The control for one literal value under a manifest editor. `undefined` commits mean "remove / unset". */
function LiteralControl({ editor, value, onCommit, disabled, testId }: { editor: PropEditor; value: Literal | undefined; onCommit: Commit; disabled?: boolean; testId?: string }) {
  switch (editor.kind) {
    case 'text':
      return <DraftInput value={value == null ? '' : String(value)} onCommit={(s) => onCommit(s === '' ? undefined : s)} disabled={disabled} testId={testId} />
    case 'textarea':
      return <DraftInput multiline value={value == null ? '' : String(value)} onCommit={(s) => onCommit(s === '' ? undefined : s)} disabled={disabled} testId={testId} />
    case 'color': {
      const hex = typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value) ? value : null
      return (
        <div className="flex items-center gap-1.5">
          <input
            // uncontrolled, re-mounted per source value: the browser shows its own default when the value is not a hex
            key={hex ?? 'none'}
            type="color"
            aria-label="Pick a colour"
            className="h-7 w-8 shrink-0 cursor-pointer rounded-ctl bg-transparent disabled:cursor-not-allowed"
            defaultValue={hex ?? undefined}
            disabled={disabled}
            onChange={(e) => onCommit(e.target.value)}
            data-testid={testId && `${testId}-swatch`}
          />
          <DraftInput value={value == null ? '' : String(value)} placeholder="any CSS colour" onCommit={(s) => onCommit(s === '' ? undefined : s)} disabled={disabled} testId={testId} />
        </div>
      )
    }
    case 'number': {
      const { min, max, step } = editor
      return (
        <DraftInput
          number
          range={{ min, max, step }}
          value={value == null ? '' : String(value)}
          placeholder={editor.default != null ? String(editor.default) : undefined}
          onCommit={(s) => {
            if (s === '') return onCommit(undefined)
            const n = Number(s)
            onCommit(Math.min(max ?? Infinity, Math.max(min ?? -Infinity, n)))
          }}
          disabled={disabled}
          testId={testId}
        />
      )
    }
    case 'bool':
      return <Switch checked={value === true || (value === undefined && editor.default === true)} onCheckedChange={(c) => onCommit(c)} disabled={disabled} data-testid={testId} />
    case 'enum': {
      const items: Record<string, string> = { [UNSET]: editor.default ? `default (${editor.default})` : 'default', ...Object.fromEntries(editor.options.map((o) => [o, o])) }
      return (
        <Select value={typeof value === 'string' ? value : UNSET} onValueChange={(v) => onCommit(v === UNSET ? undefined : (v as string))} items={items} disabled={disabled}>
          <SelectTrigger size="sm" className="w-full" data-testid={testId}>
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
      )
    }
    case 'tags':
      return (
        <DraftInput
          value={Array.isArray(value) ? value.join(', ') : ''}
          placeholder="everyone"
          onCommit={(s) => {
            const tags = s.split(',').map((x) => x.trim()).filter(Boolean)
            onCommit(tags.length ? tags : undefined)
          }}
          disabled={disabled}
          testId={testId}
        />
      )
    case 'map':
      return <DraftInput value={mapText(value)} placeholder="status=pending" onCommit={(s) => onCommit(s.trim() ? parseMap(s) : undefined)} disabled={disabled} testId={testId} />
    default:
      return null
  }
}

function Code({ children }: { children: string }) {
  return <pre className="max-h-24 overflow-auto rounded-ctl bg-recess px-2 py-1 font-mono text-[11px] whitespace-pre-wrap text-muted-foreground">{children}</pre>
}

const READ_ONLY: Record<string, string> = {
  custom: 'custom — edit with AI / code',
  bound: 'bound to data or code',
  event: 'event handler — edit with AI / code',
  slot: 'JSX — select it in the preview, or edit with AI / code',
}

function Row({ label, children, note }: { label: string; children: React.ReactNode; note?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 text-[11.5px] text-muted-foreground">
        <span className="font-medium text-foreground">{label}</span>
        {note}
      </div>
      {children}
    </div>
  )
}

/** Reorder the element among its siblings (also Alt+Arrow, or drag it in the preview); disabled with the reason when it can't. */
function MoveRow({ view, disabled, onMove }: { view: ElementView; disabled: boolean; onMove: (delta: -1 | 1) => void }) {
  const m = view.move
  return (
    <Row label="Position" note={m.ok ? <span>{m.index + 1} of {m.count} · Alt+↑↓ or drag</span> : null}>
      <div className="flex items-center gap-1.5">
        <Button variant="outline" size="xs" disabled={disabled || !m.ok || m.index === 0} onClick={() => onMove(-1)} data-testid="vedit-move-up">
          <ArrowUp /> Move earlier
        </Button>
        <Button variant="outline" size="xs" disabled={disabled || !m.ok || m.index === m.count - 1} onClick={() => onMove(1)} data-testid="vedit-move-down">
          <ArrowDown /> Move later
        </Button>
      </div>
      {!m.ok && (
        <p className="text-[11.5px] text-muted-foreground" data-testid="vedit-move-reason">
          {m.reason}
        </p>
      )}
      {!m.ok && <AskFix view={view} about="move it" />}
    </Row>
  )
}

function ItemFields({ spec, item, prop, edit, disabled, owner }: { owner: ElementView; spec: Extract<ListItem, { kind: 'object' }>; item: ItemView; prop: string; edit: (ops: Op[], coalesce?: string) => void; disabled?: boolean }) {
  return (
    <div className="flex flex-col gap-2 px-3 pb-3" data-testid={`vedit-item-fields-${item.index}`}>
      {Object.entries(spec.fields).map(([field, editor]) => {
        const f = item.fields?.[field]
        return (
          <Row key={field} label={field}>
            {f?.state === 'custom' ? (
              <>
                <Code>{f.src ?? ''}</Code>
                <AskFix view={owner} about={`${prop} item ${item.index + 1}, ${field}`} />
              </>
            ) : (
              <LiteralControl
                editor={editor}
                value={f?.value}
                disabled={disabled}
                onCommit={(v) => edit([{ op: 'listSet', prop, index: item.index, field, value: v }], `${prop}.${item.index}.${field}`)}
                testId={`vedit-item-${item.index}-${field}`}
              />
            )}
          </Row>
        )
      })}
      {Object.entries(item.fields ?? {})
        .filter(([k]) => !(k in spec.fields))
        .map(([k, f]) => (
          <Row key={k} label={k} note={<span>{f.state === 'custom' ? READ_ONLY.custom : 'not a known field — edit with AI / code'}</span>}>
            <Code>{f.state === 'custom' ? (f.src ?? '') : JSON.stringify(f.value)}</Code>
            <AskFix view={owner} about={`${prop} item ${item.index + 1}, ${k}`} />
          </Row>
        ))}
    </div>
  )
}

function ListEditor({ owner, view, editor, edit, focus, disabled }: { owner: ElementView; view: PropView; editor: Extract<PropEditor, { kind: 'list' }>; edit: (ops: Op[], coalesce?: string) => void; focus: number | null; disabled?: boolean }) {
  const items = view.items ?? []
  const [open, setOpen] = useState<number | null>(focus)
  const [lastFocus, setLastFocus] = useState(focus)
  if (lastFocus !== focus) {
    setLastFocus(focus)
    setOpen(focus)
  }
  const spec = editor.item
  const fresh = newItem(spec, Array.isArray(view.value) ? view.value : [])
  return (
    <div className="flex flex-col gap-1.5" data-testid={`vedit-list-${view.name}`}>
      {items.length > 0 && (
        <WellList>
          {items.map((it, i) => {
            const isOpen = open === it.index && spec.kind === 'object'
            const labelField = spec.kind === 'object' ? spec.label : null
            const canRename = !it.custom && (spec.kind === 'string' || (labelField && it.fields?.[labelField]?.state !== 'custom'))
            return (
              <div key={it.index} className={cn(focus === it.index && 'bg-raise')}>
                <WellRow className="gap-1 px-2 py-1">
                  {spec.kind === 'object' && !it.custom && (
                    <Button variant="ghost" size="icon-xs" aria-label="Details" onClick={() => setOpen(isOpen ? null : it.index)}>
                      <ChevronRight className={cn('transition-transform', isOpen && 'rotate-90')} />
                    </Button>
                  )}
                  <div className={cn('min-w-0 flex-1', it.hidden && 'opacity-50')}>
                    {canRename ? (
                      <DraftInput
                        value={it.label}
                        disabled={disabled}
                        onCommit={(s) =>
                          edit(spec.kind === 'string' ? [{ op: 'listSet', prop: view.name, index: it.index, value: s }] : [{ op: 'listSet', prop: view.name, index: it.index, field: labelField!, value: s }])
                        }
                        testId={`vedit-${view.name}-${it.index}-label`}
                      />
                    ) : (
                      <span className="block truncate font-mono text-[11px] text-muted-foreground" title={it.custom ?? it.label}>
                        {it.label} · custom
                      </span>
                    )}
                  </div>
                  {spec.kind === 'object' && spec.hideable && !it.custom && (
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={it.hidden ? 'Show' : 'Hide'}
                      disabled={disabled}
                      onClick={() => edit([{ op: 'listSet', prop: view.name, index: it.index, field: 'hidden', value: it.hidden ? undefined : true }])}
                      data-testid={`vedit-${view.name}-${it.index}-hide`}
                    >
                      {it.hidden ? <EyeOff /> : <Eye />}
                    </Button>
                  )}
                  <Button variant="ghost" size="icon-xs" aria-label="Move up" disabled={disabled || i === 0} onClick={() => edit([{ op: 'listMove', prop: view.name, from: it.index, to: items[i - 1].index }])} data-testid={`vedit-${view.name}-${it.index}-up`}>
                    <ArrowUp />
                  </Button>
                  <Button variant="ghost" size="icon-xs" aria-label="Move down" disabled={disabled || i === items.length - 1} onClick={() => edit([{ op: 'listMove', prop: view.name, from: it.index, to: items[i + 1].index }])} data-testid={`vedit-${view.name}-${it.index}-down`}>
                    <ArrowDown />
                  </Button>
                  <Button variant="ghost" size="icon-xs" aria-label="Remove" disabled={disabled} onClick={() => edit([{ op: 'listRemove', prop: view.name, index: it.index }])} data-testid={`vedit-${view.name}-${it.index}-remove`}>
                    <X />
                  </Button>
                </WellRow>
                {isOpen && spec.kind === 'object' && <ItemFields owner={owner} spec={spec} item={it} prop={view.name} edit={edit} disabled={disabled} />}
              </div>
            )
          })}
        </WellList>
      )}
      <Button
        variant="ghost"
        size="xs"
        className="self-start"
        disabled={disabled || fresh === null}
        title={fresh === null ? `A new ${spec.noun} needs code; ask AI` : undefined}
        onClick={() => fresh !== null && edit([{ op: 'listInsert', prop: view.name, index: items.length, item: fresh }])}
        data-testid={`vedit-${view.name}-add`}
      >
        <Plus /> Add {spec.noun}
      </Button>
      {fresh === null && <AskFix view={owner} about={`add a ${spec.noun} to ${view.name}`} />}
    </div>
  )
}

function PropRow({ owner, view, edit, focus, disabled }: { owner: ElementView; view: PropView; edit: (ops: Op[], coalesce?: string) => void; focus: number | null; disabled?: boolean }) {
  const e = view.editor
  const via = view.via && (
    <span title={`Defined as ${view.via.name} in ${view.via.file}; editing it changes every use`}>
      via <span className="font-mono">{view.via.name}</span>
    </span>
  )
  const editable = e && (view.state === 'literal' || view.state === 'unset') && !['bound', 'event', 'slot'].includes(e.kind)
  if (!editable) {
    if (view.state === 'unset') return null
    return (
      <Row label={view.name} note={<span>{view.reason ?? READ_ONLY[view.state] ?? READ_ONLY.custom}</span>}>
        <Code>{view.src ?? ''}</Code>
        <AskFix view={owner} about={`its ${view.name} prop`} />
      </Row>
    )
  }
  if (e.kind === 'list') {
    if (view.state === 'unset') return null
    return (
      <Row label={view.name} note={via}>
        <ListEditor owner={owner} view={view} editor={e} edit={edit} focus={focus} disabled={disabled} />
      </Row>
    )
  }
  return (
    <Row label={view.name} note={via}>
      <LiteralControl
        editor={e}
        value={view.value}
        disabled={disabled}
        onCommit={(v) => edit(v === undefined ? [{ op: 'unsetProp', prop: view.name }] : [{ op: 'setProp', prop: view.name, value: v }], `prop:${view.name}`)}
        testId={`vedit-prop-${view.name}`}
      />
    </Row>
  )
}

/** Values this element shows that live in a const (editing changes every use), or in a variable that can't be followed. */
function ConstNotes({ notes, testId }: { notes: ConstNote[]; testId?: string }) {
  if (!notes.length) return null
  return (
    <div className="flex flex-col gap-1" data-testid={testId}>
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

/** The const notes worth showing: shared consts, refused variables, and an element defined in a const. */
function notesOf(view: ElementView): ConstNote[] {
  return (view.consts ?? []).filter((n) => n.reason || n.uses > 1 || n.what === 'element' || n.what === 'text')
}

const times = (n: number) => (n === 1 ? 'once' : `${n} times`)

/**
 * An app-defined component. On an instance: its props below edit THIS usage, and "Edit inside" enters the definition.
 * Inside a definition: every edit changes all its instances, so say how many.
 */
function ComponentBanner({ kind, ref_, onEnter }: { kind: 'instance' | 'inside'; ref_: ComponentRef; onEnter: (sel: Selection) => void }) {
  const where = `${times(ref_.usedBy)}${ref_.files > 1 ? ` in ${ref_.files} files` : ''}`
  return (
    <Well className="flex items-center gap-2 px-2.5 py-1.5 text-[11.5px]" data-testid={`vedit-${kind}`}>
      <span className="min-w-0 flex-1 text-muted-foreground">
        {kind === 'instance' ? (
          <>
            This <span className="font-mono text-foreground">{ref_.name}</span> · used {where}
          </>
        ) : (
          <>
            Inside <span className="font-mono text-foreground">{ref_.name}</span> · changes apply to all {ref_.usedBy} uses
          </>
        )}
      </span>
      {kind === 'instance' && ref_.enter && (
        <Button variant="outline" size="xs" onClick={() => onEnter(ref_.enter!)} title="Edit the component's definition (double-click in the preview)" data-testid="vedit-enter">
          Edit inside
        </Button>
      )}
    </Well>
  )
}

function AskAi({ view, onAskAi, busy }: { view: ElementView; onAskAi?: (prompt: string) => void; busy: boolean }) {
  const [text, setText] = useState('')
  if (!onAskAi) return null
  const send = () => {
    if (!text.trim() || busy) return
    onAskAi(askPrompt(view, text))
    setText('')
  }
  return (
    <Well className="flex flex-col gap-2 p-2.5">
      <span className="text-[11.5px] font-medium">Ask AI about this element</span>
      <Textarea
        rows={2}
        className="text-[12.5px]"
        value={text}
        placeholder={`e.g. only show this ${view.name} to approvers`}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send()
        }}
        data-testid="vedit-ask-input"
      />
      <Button size="xs" variant="ai" className="self-end" disabled={busy || !text.trim()} onClick={send} data-testid="vedit-ask-send">
        <Sparkles /> Ask AI
      </Button>
    </Well>
  )
}

type Tab = 'props' | 'layers'

export function VisualEditPanel({ ve, onAskAi, busy, onClose }: { ve: VisualEdit; onAskAi?: (prompt: string) => void; busy: boolean; onClose: () => void }) {
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('props')
  const { view, selection } = ve
  const files = useLayerFiles()
  // a selection made in the Layers tree has no click chain: its crumbs come from the tree
  const crumbs = useMemo(() => {
    if (!selection || ve.crumbs.some((c) => c.sel.file === selection.file && c.sel.key === selection.key)) return ve.crumbs
    return layerCrumbs(files, selection)
  }, [ve.crumbs, selection, files])
  const [errorFor, setErrorFor] = useState(selection)
  if (errorFor !== selection) {
    setErrorFor(selection)
    setError(null)
  }
  const edit = (ops: Op[], coalesce?: string) => setError(ve.edit(ops, coalesce))
  const publish = useAskAi((s) => s.publish)
  const sel = view ? selectionOf(view) : null
  const selFile = sel?.file
  const selLine = sel?.line
  const selName = sel?.name
  const selSnippet = sel?.snippet
  useEffect(() => {
    publish(selFile && selLine != null && selName && selSnippet != null ? { file: selFile, line: selLine, name: selName, snippet: selSnippet } : null)
  }, [publish, selFile, selLine, selName, selSnippet])
  useEffect(() => () => publish(null), [publish])
  const disabled = ve.locked
  const focus = selection?.part ?? null
  // const notes sit next to the rows they explain (text, each prop); the Style section gets the view without them
  const notes = view ? notesOf(view) : []
  const propNames = new Set(view?.props.map((p) => p.name) ?? [])
  const styleView = useMemo(() => (view ? { ...view, consts: [] } : null), [view])

  return (
    <div className="flex h-full w-[300px] shrink-0 flex-col overflow-hidden bg-recess shadow-[inset_1px_0_0_var(--hairline)]" data-testid="vedit-panel">
      <div className="flex h-9 shrink-0 items-center gap-1 px-2.5 hairline-b">
        <nav className="flex min-w-0 flex-1 items-center gap-0.5 overflow-hidden text-[11.5px]" aria-label="Selected element" data-testid="vedit-breadcrumb">
          {crumbs.length === 0 && <span className="text-muted-foreground">Nothing selected</span>}
          {[...crumbs].reverse().slice(-4).map((c, i, arr) => {
            const on = selection && c.sel.file === selection.file && c.sel.key === selection.key
            return (
              <span key={`${c.sel.file}:${c.sel.key}`} className="flex min-w-0 items-center gap-0.5">
                <button type="button" className={cn('truncate rounded-chip px-1 hover:bg-raise', on ? 'font-semibold text-foreground' : 'text-muted-foreground')} onClick={() => ve.select(c.sel)}>
                  {c.name}
                </button>
                {i < arr.length - 1 && <ChevronRight className="size-3 shrink-0 text-muted-foreground" />}
              </span>
            )
          })}
        </nav>
        <Button variant="ghost" size="icon-xs" aria-label="Close" onClick={onClose}>
          <X />
        </Button>
      </div>
      <div className="shrink-0 px-2.5 pt-2">
        <Segmented<Tab>
          size="xs"
          aria-label="Inspector view"
          value={tab}
          onChange={setTab}
          options={[
            { key: 'props', label: 'Properties', icon: <SlidersHorizontal />, testId: 'vlayers-tab-props' },
            { key: 'layers', label: 'Layers', icon: <Layers />, testId: 'vlayers-tab' },
          ]}
        />
      </div>
      {tab === 'layers' && <LayersPanel ve={ve} />}
      {tab === 'props' && (
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3" data-testid="vedit-props">
        <StructurePanel ve={ve} disabled={disabled} />
        {!view ? (
          <p className="text-[12.5px] text-muted-foreground">Click an element in the preview to edit it. Alt-click uses the app as normal.</p>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <span className="truncate font-mono text-[12.5px] font-semibold" data-testid="vedit-name">
                {view.name}
              </span>
              <Badge variant={view.kit ? 'secondary' : 'outline'}>{view.kit ? 'kit' : 'custom'}</Badge>
              <div className="flex-1" />
              <Button variant="ai" size="xs" onClick={() => askAboutElement(view)} title="Ask AI about this element" data-testid="vedit-ask-header">
                <Sparkles /> Ask AI
              </Button>
              <span className="truncate font-mono text-[10.5px] text-muted-foreground" title={view.file}>
                {view.file.split('/').pop()}:{view.line}
              </span>
            </div>
            {view.instance && <ComponentBanner kind="instance" ref_={view.instance} onEnter={ve.select} />}
            {view.inside && <ComponentBanner kind="inside" ref_={view.inside} onEnter={ve.select} />}
            {disabled && <p className="text-[11.5px] text-muted-foreground">The chat is changing the app; editing resumes when it finishes.</p>}
            {error && (
              <p className="text-[11.5px] text-destructive" data-testid="vedit-error">
                {error}
              </p>
            )}
            {error && <AskFix view={view} />}
            <ConstNotes notes={notes.filter((n) => n.what === 'element')} testId="vedit-const-note" />
            <MoveRow view={view} disabled={disabled} onMove={(d) => setError(ve.move(d))} />
            {view.texts.map((t, i) => (
              <Row key={i} label={view.texts.length > 1 ? `Text ${i + 1}` : 'Text'}>
                <DraftInput value={t} disabled={disabled} onCommit={(s) => edit([{ op: 'setText', index: i, value: s }])} testId={`vedit-text-${i}`} />
              </Row>
            ))}
            <ConstNotes notes={notes.filter((n) => n.what === 'text')} testId="vedit-const-note-texts" />
            {view.props.map((p) => (
              <div key={p.name} className="contents">
                <PropRow owner={view} view={p} edit={edit} focus={focus && focus.prop === p.name ? focus.index : null} disabled={disabled} />
                <ConstNotes notes={notes.filter((n) => n.what === p.name)} />
              </div>
            ))}
            <ConstNotes notes={notes.filter((n) => n.what !== 'element' && n.what !== 'text' && !propNames.has(n.what))} />
            {view.spreads > 0 && <p className="text-[11.5px] text-muted-foreground">Also takes props from a spread ({'{...}'}): edit with AI / code.</p>}
            {view.spreads > 0 && <AskFix view={view} about="props from a spread" />}
            {!view.kit && view.props.length === 0 && view.texts.length === 0 && <Code>{view.source}</Code>}
            <BindPanel ve={ve} />
            <StylePanel ve={{ ...ve, view: styleView }} />
            <AskAi view={view} onAskAi={onAskAi} busy={busy} />
          </>
        )}
      </div>
      )}
    </div>
  )
}
