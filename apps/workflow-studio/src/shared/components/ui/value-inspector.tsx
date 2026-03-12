/**
 * ValueInspector — A recursive, tree-style value viewer with optional inline editing.
 *
 * Renders any JS value (primitives, objects, arrays, nested) as a collapsible
 * tree with syntax-colored types — similar to Chrome DevTools / React DevTools.
 *
 * @example Read-only
 * ```tsx
 * <ValueInspector value={{ users: [{ name: "Alice" }], count: 1 }} />
 * ```
 *
 * @example Editable (click any value to edit, booleans toggle on click)
 * ```tsx
 * const [data, setData] = useState({ name: "Alice", active: true })
 * <ValueInspector value={data} onEdit={setData} />
 * ```
 *
 * @example Compact mode (smaller text, tighter spacing for panels)
 * ```tsx
 * <ValueInspector value={data} size="compact" />
 * ```
 *
 * @example Controlled expansion depth
 * ```tsx
 * <ValueInspector value={data} defaultExpandDepth={3} />
 * ```
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { ChevronRight, Pencil, Check, X } from 'lucide-react'
import { cn } from '@/shared/lib/utils'

// ─── Types ────────────────────────────────

export interface ValueInspectorProps {
  /** The value to display */
  value: unknown
  /** Called with the new root value when any nested value is edited. Omit for read-only. */
  onEdit?: (newValue: unknown) => void
  /** How many levels to auto-expand (default: 1) */
  defaultExpandDepth?: number
  /** Size variant */
  size?: 'default' | 'compact'
  /** Max entries to show per object/array before "N more..." (default: 50) */
  maxEntries?: number
  /** Additional className on the root wrapper */
  className?: string
}

interface InternalProps {
  value: unknown
  onEdit?: (newValue: unknown) => void
  depth: number
  defaultExpandDepth: number
  size: 'default' | 'compact'
  maxEntries: number
}

// ─── Inline editor ────────────────────────────────

function parseRawValue(raw: string): unknown {
  const trimmed = raw.trim()
  if (trimmed === 'null') return null
  if (trimmed === 'undefined') return undefined
  if (trimmed === 'true') return true
  if (trimmed === 'false') return false
  const num = Number(trimmed)
  if (trimmed !== '' && !isNaN(num)) return num
  try { return JSON.parse(trimmed) } catch { /* not JSON */ }
  return trimmed
}

function InlineEdit({
  value,
  onSave,
  size,
}: {
  value: unknown
  onSave: (v: unknown) => void
  size: 'default' | 'compact'
}) {
  const display = value === null ? 'null'
    : value === undefined ? 'undefined'
    : typeof value === 'object' ? JSON.stringify(value, null, 2)
    : String(value)

  const [text, setText] = useState(display)
  const ref = useRef<HTMLTextAreaElement>(null)
  const isCompact = size === 'compact'

  useEffect(() => { ref.current?.focus(); ref.current?.select() }, [])

  const commit = useCallback(() => {
    onSave(parseRawValue(text))
  }, [text, onSave])

  const isMultiline = display.includes('\n') || display.length > 60
  const textSize = isCompact ? 'text-[10px]' : 'text-xs'
  const iconSize = isCompact ? 10 : 12

  return (
    <div className="flex items-start gap-1">
      <textarea
        ref={ref}
        className={cn(
          'flex-1 font-mono bg-background border border-primary/50 rounded px-1.5 py-0.5 text-foreground resize-vertical focus:outline-none focus:ring-1 focus:ring-primary',
          textSize
        )}
        rows={isMultiline ? Math.min(display.split('\n').length + 1, 8) : 1}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commit() }
          if (e.key === 'Escape') onSave(value) // cancel — return original
        }}
        spellCheck={false}
      />
      <button onClick={commit} className="text-emerald-500 hover:text-emerald-400 p-0.5 shrink-0" title="Save (Enter)">
        <Check size={iconSize} />
      </button>
      <button onClick={() => onSave(value)} className="text-muted-foreground hover:text-foreground p-0.5 shrink-0" title="Cancel (Esc)">
        <X size={iconSize} />
      </button>
    </div>
  )
}

// ─── Node renderer ────────────────────────────────

function ValueNode({ value, onEdit, depth, defaultExpandDepth, size, maxEntries }: InternalProps) {
  const [expanded, setExpanded] = useState(depth < defaultExpandDepth)
  const [editing, setEditing] = useState(false)

  const isCompact = size === 'compact'
  const textSize = isCompact ? 'text-[10px]' : 'text-xs'
  const iconSize = isCompact ? (editing ? 10 : 8) : (editing ? 12 : 10)
  const chevronSize = isCompact ? 10 : 12

  // Inline edit mode
  if (editing && onEdit) {
    return (
      <InlineEdit
        value={value}
        size={size}
        onSave={(v) => { setEditing(false); if (v !== value) onEdit(v) }}
      />
    )
  }

  // Edit pencil button (primitives only, or whole object via JSON)
  const editButton = onEdit ? (
    <button
      onClick={(e) => { e.stopPropagation(); setEditing(true) }}
      className="opacity-0 group-hover/val:opacity-100 text-muted-foreground hover:text-foreground transition-opacity p-0.5 shrink-0"
      title={typeof value === 'object' && value !== null ? 'Edit as JSON' : 'Edit value'}
    >
      <Pencil size={iconSize} />
    </button>
  ) : null

  // ── Primitives ──

  if (value === null) return (
    <span className={cn('group/val inline-flex items-center gap-1', textSize)}>
      <span className="text-muted-foreground font-mono cursor-pointer" onClick={() => onEdit && setEditing(true)}>null</span>
      {editButton}
    </span>
  )

  if (value === undefined) return (
    <span className={cn('group/val inline-flex items-center gap-1', textSize)}>
      <span className="text-muted-foreground font-mono">undefined</span>
      {editButton}
    </span>
  )

  if (typeof value === 'boolean') return (
    <span className={cn('group/val inline-flex items-center gap-1', textSize)}>
      <span
        className="text-purple-500 dark:text-purple-400 font-mono cursor-pointer"
        onClick={() => onEdit?.(!value)}
      >
        {String(value)}
      </span>
      {editButton}
    </span>
  )

  if (typeof value === 'number') return (
    <span className={cn('group/val inline-flex items-center gap-1', textSize)}>
      <span className="text-amber-600 dark:text-amber-400 font-mono cursor-pointer" onClick={() => onEdit && setEditing(true)}>{value}</span>
      {editButton}
    </span>
  )

  if (typeof value === 'string') {
    const maxLen = isCompact ? 60 : 80
    const display = value.length > maxLen ? value.slice(0, maxLen) + '\u2026' : value
    return (
      <span className={cn('group/val inline-flex items-center gap-1', textSize)}>
        <span className="text-emerald-600 dark:text-emerald-400 font-mono break-all cursor-pointer" onClick={() => onEdit && setEditing(true)}>&quot;{display}&quot;</span>
        {editButton}
      </span>
    )
  }

  // ── Objects & Arrays ──

  const isArray = Array.isArray(value)
  const entries = isArray
    ? (value as unknown[]).map((v, i) => [String(i), v] as const)
    : Object.entries(value as Record<string, unknown>)

  // Empty
  if (entries.length === 0) return (
    <span className={cn('group/val inline-flex items-center gap-1', textSize)}>
      <span className="text-muted-foreground font-mono cursor-pointer" onClick={() => onEdit && setEditing(true)}>
        {isArray ? '[]' : '{}'}
      </span>
      {editButton}
    </span>
  )

  const childOnEdit = (key: string) => {
    if (!onEdit) return undefined
    return (newChildVal: unknown) => {
      if (isArray) {
        const copy = [...(value as unknown[])]
        copy[Number(key)] = newChildVal
        onEdit(copy)
      } else {
        onEdit({ ...(value as Record<string, unknown>), [key]: newChildVal })
      }
    }
  }

  return (
    <div className={textSize}>
      <div className="group/val inline-flex items-center gap-1">
        <button
          onClick={() => setExpanded(!expanded)}
          className="inline-flex items-center gap-0.5 text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronRight size={chevronSize} className={cn('transition-transform', expanded && 'rotate-90')} />
          <span className="font-mono">{isArray ? `Array(${entries.length})` : `{${entries.length}}`}</span>
        </button>
        {editButton}
      </div>
      {expanded && (
        <div className={cn('ml-3 border-l border-border/40 pl-2 mt-0.5', isCompact ? 'space-y-px' : 'space-y-0.5')}>
          {entries.slice(0, maxEntries).map(([key, val]) => (
            <div key={key} className="flex gap-1.5 items-start">
              <span className="text-blue-500 dark:text-blue-400 font-mono shrink-0">{key}:</span>
              <ValueNode
                value={val}
                depth={depth + 1}
                defaultExpandDepth={defaultExpandDepth}
                size={size}
                maxEntries={maxEntries}
                onEdit={childOnEdit(key)}
              />
            </div>
          ))}
          {entries.length > maxEntries && (
            <span className="text-muted-foreground font-mono">... {entries.length - maxEntries} more</span>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Public component ────────────────────────────────

export function ValueInspector({
  value,
  onEdit,
  defaultExpandDepth = 1,
  size = 'default',
  maxEntries = 50,
  className,
}: ValueInspectorProps) {
  return (
    <div className={className}>
      <ValueNode
        value={value}
        onEdit={onEdit}
        depth={0}
        defaultExpandDepth={defaultExpandDepth}
        size={size}
        maxEntries={maxEntries}
      />
    </div>
  )
}
