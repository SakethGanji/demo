/**
 * ValueEditor — A recursive, form-style editor for building structured data.
 *
 * Renders any JS value as an editable tree of inputs. Objects show key-value
 * pairs, arrays show indexed items, primitives show inline inputs. Fully
 * recursive — nested objects/arrays get their own sub-editors.
 *
 * Includes a type switcher + Visual/JSON toggle for structured values.
 */

import { useState } from 'react'
import { Plus, Trash2, ChevronRight, ChevronDown } from 'lucide-react'
import { cn } from '@/shared/lib/utils'

// ─── Types ────────────────────────────────

export type ValueEditorMode = 'visual' | 'json'

type ValueType = 'object' | 'array' | 'string' | 'number' | 'boolean' | 'null'

export interface ValueEditorProps {
  /** Current value */
  value: unknown
  /** Called with the new value on every change */
  onChange: (value: unknown) => void
  /** Optional label above the editor */
  label?: string
  /** Show a type badge (e.g. "object", "array", "string") */
  showTypeBadge?: boolean
  /** Default editor mode for structured values (default: 'visual') */
  defaultMode?: ValueEditorMode
  /** Additional className on root */
  className?: string
}

// ─── Shared styles ────────────────────────────────

const miniField = 'h-6 px-1.5 text-[10px] font-mono bg-card border border-border/60 rounded focus:outline-none focus:ring-1 focus:ring-ring text-foreground min-w-0'

// ─── Type helpers ────────────────────────────────

const TYPE_OPTIONS: { value: ValueType; label: string }[] = [
  { value: 'object', label: 'Object' },
  { value: 'array', label: 'Array' },
  { value: 'string', label: 'String' },
  { value: 'number', label: 'Number' },
  { value: 'boolean', label: 'Boolean' },
  { value: 'null', label: 'Null' },
]

function getValueType(value: unknown): ValueType {
  if (value === null || value === undefined) return 'null'
  if (Array.isArray(value)) return 'array'
  if (typeof value === 'object') return 'object'
  return typeof value as ValueType
}

function convertToType(type: ValueType): unknown {
  switch (type) {
    case 'object': return {}
    case 'array': return []
    case 'string': return ''
    case 'number': return 0
    case 'boolean': return false
    case 'null': return null
  }
}

// ─── Smart value parser ────────────────────────────────

function parseInputValue(raw: string): unknown {
  if (raw === 'null') return null
  if (raw === 'true') return true
  if (raw === 'false') return false
  const num = Number(raw)
  if (raw !== '' && !isNaN(num)) return num
  return raw
}

// ─── Type switcher ────────────────────────────────

function TypeSwitcher({
  value,
  onChange,
}: {
  value: ValueType
  onChange: (type: ValueType) => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-0.5 text-[9px] font-mono text-muted-foreground hover:text-foreground transition-colors px-1 py-0.5 rounded hover:bg-accent/50"
      >
        {value}
        <ChevronDown size={8} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-0.5 z-50 bg-popover border border-border rounded-md shadow-lg py-0.5 min-w-[90px]">
            {TYPE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => {
                  onChange(opt.value)
                  setOpen(false)
                }}
                className={cn(
                  'w-full text-left px-2.5 py-1 text-[10px] transition-colors',
                  opt.value === value
                    ? 'text-foreground bg-accent/50 font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/30'
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ─── Recursive tree editor ────────────────────────────────

function TreeNode({
  value,
  onChange,
  depth = 0,
}: {
  value: unknown
  onChange: (v: unknown) => void
  depth?: number
}) {
  const [collapsed, setCollapsed] = useState(depth > 2)

  // ── Primitive ──
  if (value === null || value === undefined || typeof value !== 'object') {
    const display = value === null ? 'null'
      : value === undefined ? 'null'
      : String(value)

    return (
      <input
        className={cn(miniField, 'flex-1')}
        value={display}
        onChange={(e) => onChange(parseInputValue(e.target.value))}
        spellCheck={false}
      />
    )
  }

  const isArray = Array.isArray(value)

  // ── Array ──
  if (isArray) {
    const arr = value as unknown[]
    return (
      <div className="w-full">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground hover:text-foreground mb-0.5"
        >
          {collapsed ? <ChevronRight size={10} /> : <ChevronDown size={10} />}
          <span className="font-mono">Array({arr.length})</span>
        </button>
        {!collapsed && (
          <div className="ml-2 border-l-2 border-border/30 pl-2 space-y-1">
            {arr.map((item, i) => (
              <div key={i} className="flex items-start gap-1">
                <span className="text-[9px] font-mono text-muted-foreground/60 shrink-0 mt-1.5 w-4 text-right">{i}</span>
                <div className="flex-1 min-w-0">
                  <TreeNode
                    value={item}
                    onChange={(v) => { const copy = [...arr]; copy[i] = v; onChange(copy) }}
                    depth={depth + 1}
                  />
                </div>
                <button
                  onClick={() => { const copy = [...arr]; copy.splice(i, 1); onChange(copy) }}
                  className="text-muted-foreground hover:text-destructive transition-colors p-0.5 shrink-0 mt-0.5"
                >
                  <Trash2 size={9} />
                </button>
              </div>
            ))}
            <div className="flex gap-1 pt-0.5">
              <button
                onClick={() => onChange([...arr, ''])}
                className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
              >
                <Plus size={9} />
                item
              </button>
              <span className="text-muted-foreground/30">|</span>
              <button
                onClick={() => onChange([...arr, {}])}
                className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
              >
                + object
              </button>
              <span className="text-muted-foreground/30">|</span>
              <button
                onClick={() => onChange([...arr, []])}
                className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
              >
                + array
              </button>
            </div>
          </div>
        )}
      </div>
    )
  }

  // ── Object ──
  const obj = value as Record<string, unknown>
  const entries = Object.entries(obj)

  const updateKey = (oldKey: string, newKey: string) => {
    if (newKey === oldKey) return
    const result: Record<string, unknown> = {}
    for (const [k, v] of entries) {
      result[k === oldKey ? newKey : k] = v
    }
    onChange(result)
  }

  const removePair = (key: string) => {
    const copy = { ...obj }
    delete copy[key]
    onChange(copy)
  }

  const addPair = (initialValue: unknown = '') => {
    let key = 'key'
    let i = 1
    while (key in obj) { key = `key${i++}` }
    onChange({ ...obj, [key]: initialValue })
  }

  return (
    <div className="w-full">
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground hover:text-foreground mb-0.5"
      >
        {collapsed ? <ChevronRight size={10} /> : <ChevronDown size={10} />}
        <span className="font-mono">{`{${entries.length}}`}</span>
      </button>
      {!collapsed && (
        <div className="ml-2 border-l-2 border-border/30 pl-2 space-y-1">
          {entries.map(([key, val]) => {
            const isNested = val !== null && typeof val === 'object'
            return (
              <div key={key} className={cn('flex gap-1', isNested ? 'flex-col' : 'items-center')}>
                <div className="flex items-center gap-1 shrink-0">
                  <input
                    className={cn(miniField, 'w-[80px] text-blue-500 dark:text-blue-400 shrink-0')}
                    value={key}
                    onChange={(e) => updateKey(key, e.target.value)}
                    spellCheck={false}
                  />
                  {!isNested && <span className="text-muted-foreground/40 text-[10px]">:</span>}
                  {isNested && (
                    <button
                      onClick={() => removePair(key)}
                      className="text-muted-foreground hover:text-destructive transition-colors p-0.5 shrink-0"
                    >
                      <Trash2 size={9} />
                    </button>
                  )}
                </div>
                <div className="flex items-start gap-1 flex-1 min-w-0">
                  <TreeNode
                    value={val}
                    onChange={(v) => onChange({ ...obj, [key]: v })}
                    depth={depth + 1}
                  />
                  {!isNested && (
                    <button
                      onClick={() => removePair(key)}
                      className="text-muted-foreground hover:text-destructive transition-colors p-0.5 shrink-0"
                    >
                      <Trash2 size={9} />
                    </button>
                  )}
                </div>
              </div>
            )
          })}
          <div className="flex gap-1 pt-0.5">
            <button
              onClick={() => addPair('')}
              className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
            >
              <Plus size={9} />
              field
            </button>
            <span className="text-muted-foreground/30">|</span>
            <button
              onClick={() => addPair({})}
              className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
            >
              + object
            </button>
            <span className="text-muted-foreground/30">|</span>
            <button
              onClick={() => addPair([])}
              className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
            >
              + array
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Public component ────────────────────────────────

export function ValueEditor({
  value,
  onChange,
  label,
  showTypeBadge = false,
  defaultMode = 'visual',
  className,
}: ValueEditorProps) {
  const isStructured = value !== null && typeof value === 'object'
  const [mode, setMode] = useState<ValueEditorMode>(isStructured ? defaultMode : 'json')
  const currentType = getValueType(value)

  const displayValue = value === null || value === undefined
    ? 'null'
    : typeof value === 'object'
      ? JSON.stringify(value, null, 2)
      : String(value)

  const handleRawChange = (raw: string) => {
    let parsed: unknown = raw
    try { parsed = JSON.parse(raw) } catch { /* keep as string */ }
    onChange(parsed)
  }

  const handleTypeChange = (newType: ValueType) => {
    if (newType === currentType) return
    const converted = convertToType(newType)
    onChange(converted)
    // Switch to visual mode if we converted to structured
    if (newType === 'object' || newType === 'array') {
      setMode('visual')
    }
  }

  return (
    <div className={cn('space-y-1.5', className)}>
      {/* Header row */}
      <div className="flex items-center justify-between">
        {label && <label className="text-[10px] font-medium text-muted-foreground">{label}</label>}
        <div className="flex items-center gap-1">
          {showTypeBadge && (
            <TypeSwitcher value={currentType} onChange={handleTypeChange} />
          )}
          {isStructured && (
            <div className="flex bg-muted/50 rounded overflow-hidden border border-border/50 ml-1">
              <button
                className={cn('text-[9px] px-1.5 py-0.5 transition-colors', mode === 'visual' ? 'bg-primary/10 text-foreground' : 'text-muted-foreground hover:text-foreground')}
                onClick={() => setMode('visual')}
              >
                Visual
              </button>
              <button
                className={cn('text-[9px] px-1.5 py-0.5 transition-colors', mode === 'json' ? 'bg-primary/10 text-foreground' : 'text-muted-foreground hover:text-foreground')}
                onClick={() => setMode('json')}
              >
                JSON
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Editor body */}
      <div className="rounded-lg border border-border/50 bg-muted/20 p-2">
        {isStructured && mode === 'visual' ? (
          <TreeNode value={value} onChange={onChange} />
        ) : (
          <textarea
            className="w-full text-[11px] font-mono bg-card border border-border/60 rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground resize-vertical placeholder:text-muted-foreground"
            rows={typeof value === 'object' && value !== null ? Math.min(displayValue.split('\n').length + 1, 10) : 1}
            value={displayValue}
            onChange={(e) => handleRawChange(e.target.value)}
            placeholder='null, "string", 42, {"key": "val"}, [1, 2, 3]'
            spellCheck={false}
          />
        )}
      </div>
    </div>
  )
}
