import { useState, useRef, useEffect, useCallback, useMemo, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { MousePointer2, Component, Zap, ToggleLeft, ToggleRight, ChevronRight, Plus, Trash2, X } from 'lucide-react'
import { Input } from '@/shared/components/ui/input'
import { Switch } from '@/shared/components/ui/switch'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/shared/components/ui/select'
import { useAppEditorStore, useAppDocumentStore, useRuntimeStateStore, useBreakpointStore, useStyleClassStore } from './stores'
import { getDefinition } from './types'
import type { PropField, EventHandlerConfig, EventAction, ElementState } from './types'
import { useNodeProp } from './hooks'
import { IconRenderer, allIconNames, iconGroups } from './icons'

// ════════════════════════════════════════════════════════════════════════════
// ToolbarSection
// ════════════════════════════════════════════════════════════════════════════

interface ToolbarSectionProps {
  title: string
  defaultOpen?: boolean
  children: ReactNode
}

export function ToolbarSection({
  title,
  defaultOpen = true,
  children,
}: ToolbarSectionProps) {
  const [open, setOpen] = useState(defaultOpen)
  const contentRef = useRef<HTMLDivElement>(null)
  const [height, setHeight] = useState<number | undefined>(undefined)

  useEffect(() => {
    if (contentRef.current) {
      setHeight(contentRef.current.scrollHeight)
    }
  }, [open, children])

  return (
    <div className="border-b border-border/60">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 w-full px-3 py-2 hover:bg-accent/50 transition-colors"
      >
        <ChevronRight
          size={10}
          className="text-muted-foreground/60 transition-transform duration-200"
          style={{ transform: open ? 'rotate(90deg)' : 'rotate(0deg)' }}
        />
        <h3 className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
          {title}
        </h3>
      </button>
      <div
        style={{
          maxHeight: open ? (height ?? 1000) : 0,
          opacity: open ? 1 : 0,
          overflow: 'hidden',
          transition: 'max-height 0.25s cubic-bezier(0.19, 1, 0.22, 1), opacity 0.2s ease',
        }}
      >
        <div ref={contentRef} className="px-3 pb-3 pt-0.5 space-y-2.5 bg-muted/15">
          {children}
        </div>
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// ToolbarRadio
// ════════════════════════════════════════════════════════════════════════════

interface ToolbarRadioProps {
  value: string
  onChange: (value: string) => void
  options: { label: string; value: string }[]
}

export function ToolbarRadio({ value, onChange, options }: ToolbarRadioProps) {
  return (
    <div className="flex rounded-md border border-input overflow-hidden bg-muted/50">
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`flex-1 px-2 py-1 text-[10px] font-medium transition-all duration-150 ${
            value === opt.value
              ? 'bg-primary text-primary-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground hover:bg-accent/80'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// ToolbarItem
// ════════════════════════════════════════════════════════════════════════════

type ControlType = 'text' | 'number' | 'slider' | 'radio' | 'select' | 'color' | 'switch' | 'icon'

interface ToolbarItemProps {
  nodeId: string
  propKey: string
  label: string
  type: ControlType
  options?: { label: string; value: string }[]
  min?: number
  max?: number
  placeholder?: string
}

function isExpression(value: unknown): boolean {
  return typeof value === 'string' && value.includes('{{')
}

export function ToolbarItem({
  nodeId,
  propKey,
  label,
  type,
  options,
  min = 0,
  max = 100,
  placeholder,
}: ToolbarItemProps) {
  const [value, setValue] = useNodeProp(nodeId, propKey)
  const [expressionMode, setExpressionMode] = useState(() => isExpression(value))

  const hasExpression = isExpression(value)
  const showExpression = expressionMode || hasExpression

  // Switch/boolean fields don't support expression binding
  const supportsExpression = type !== 'switch'

  const toggleExpression = useCallback(() => {
    setExpressionMode((prev) => {
      if (prev && hasExpression) {
        // Switching back to static — clear the expression
        setValue('')
      }
      return !prev
    })
  }, [hasExpression, setValue])

  // Expression mode: show labeled expression input
  if (showExpression && supportsExpression) {
    return (
      <div>
        <label className="text-[11px] text-muted-foreground mb-1 flex items-center justify-between">
          <span className="flex items-center gap-1.5">
            {label}
            <span className="text-[9px] font-mono px-1 py-px rounded bg-amber-500/15 text-amber-600 dark:text-amber-400 leading-none">
              expr
            </span>
          </span>
          <button
            onClick={toggleExpression}
            className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-400 hover:bg-amber-500/25 transition-colors"
            title="Switch to static value"
          >
            {'{ }'}
          </button>
        </label>
        <Input
          value={String(value ?? '')}
          onChange={(e) => setValue(e.target.value)}
          placeholder="{{ item.field }}"
          className="h-7 text-xs font-mono border-amber-500/30 bg-amber-500/5"
          title={String(value ?? '')}
        />
        {!hasExpression && (
          <p className="text-[9px] text-muted-foreground mt-1 leading-relaxed">
            Use <code className="bg-muted px-0.5 rounded">{'{{ }}'}</code> for dynamic values.
            {' '}e.g. <code className="bg-muted px-0.5 rounded">{"{{ item.role === 'user' ? 'flex-end' : 'flex-start' }}"}</code>
          </p>
        )}
      </div>
    )
  }

  const expressionToggle = supportsExpression ? (
    <button
      onClick={toggleExpression}
      className="text-[9px] font-mono px-1.5 py-0.5 rounded text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
      title="Bind to expression"
    >
      {'{ }'}
    </button>
  ) : null

  const renderControl = () => {
    switch (type) {
      case 'text':
        return (
          <Input
            value={String(value ?? '')}
            onChange={(e) => setValue(e.target.value)}
            placeholder={placeholder}
            className="h-7 text-xs"
          />
        )

      case 'number':
        return (
          <Input
            type="number"
            value={String(value ?? '')}
            onChange={(e) => setValue(e.target.value)}
            placeholder={placeholder}
            className="h-7 text-xs"
          />
        )

      case 'slider':
        return (
          <div className="flex items-center gap-2.5">
            <input
              type="range"
              min={min}
              max={max}
              value={Number(value ?? 0)}
              onChange={(e) => setValue(e.target.value)}
              className="app-builder-slider flex-1"
            />
            <span className="text-[10px] text-muted-foreground tabular-nums w-7 text-right font-mono">
              {String(value ?? 0)}
            </span>
          </div>
        )

      case 'radio':
        return (
          <ToolbarRadio
            value={String(value ?? '')}
            onChange={(v) => setValue(v)}
            options={options ?? []}
          />
        )

      case 'select':
        return (
          <Select value={String(value ?? '')} onValueChange={(v) => setValue(v)}>
            <SelectTrigger size="sm">
              <SelectValue placeholder={placeholder} />
            </SelectTrigger>
            <SelectContent>
              {options?.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )

      case 'color':
        return (
          <div className="flex items-center gap-2">
            <div className="relative">
              <input
                type="color"
                value={String(value || '#000000')}
                onChange={(e) => setValue(e.target.value)}
                className="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
              />
              <div
                className="w-7 h-7 rounded-md border border-input shadow-sm cursor-pointer"
                style={{ backgroundColor: String(value || 'transparent') }}
              />
            </div>
            <Input
              value={String(value ?? '')}
              onChange={(e) => setValue(e.target.value)}
              placeholder="transparent"
              className="h-7 text-xs flex-1"
            />
          </div>
        )

      case 'switch':
        return (
          <Switch
            checked={Boolean(value)}
            onCheckedChange={(checked) => setValue(checked)}
          />
        )

      case 'icon':
        return (
          <IconPicker
            value={String(value ?? '')}
            onChange={(v) => setValue(v)}
          />
        )

      default:
        return null
    }
  }

  return (
    <div>
      <label className="text-[11px] text-muted-foreground mb-1 flex items-center justify-between">
        {label}
        {expressionToggle}
      </label>
      {renderControl()}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// DimensionInput
// ════════════════════════════════════════════════════════════════════════════

interface DimensionInputProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  allowNegative?: boolean
}

const UNITS = [
  { label: '—', value: '' },
  { label: 'px', value: 'px' },
  { label: '%', value: '%' },
  { label: 'vw', value: 'vw' },
  { label: 'vh', value: 'vh' },
  { label: 'auto', value: 'auto' },
]

function parseDimension(raw: string): { num: string; unit: string } {
  if (!raw || raw === '') return { num: '', unit: '' }
  if (raw === 'auto') return { num: '', unit: 'auto' }

  const match = raw.match(/^(-?[\d.]+)\s*(px|%|vw|vh|em|rem|fr)?$/)
  if (match) {
    return { num: match[1], unit: match[2] || 'px' }
  }
  // Fallback: treat as raw CSS (e.g. "calc(...)", "fit-content", "100%")
  return { num: raw, unit: '' }
}

function formatDimension(num: string, unit: string): string {
  if (unit === 'auto') return 'auto'
  if (unit === '' && num === '') return ''
  if (unit === '') return num // raw value pass-through
  if (num === '') return ''
  return `${num}${unit}`
}

export function DimensionInput({
  value,
  onChange,
  placeholder = 'auto',
  allowNegative = false,
}: DimensionInputProps) {
  const parsed = useMemo(() => parseDimension(value || ''), [value])
  const isKeyword = parsed.unit === 'auto'
  const isRaw = parsed.unit === '' && parsed.num !== '' && !/^-?[\d.]+$/.test(parsed.num)

  const handleNumChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = e.target.value
      if (!allowNegative && v.startsWith('-')) return
      onChange(formatDimension(v, parsed.unit || 'px'))
    },
    [parsed.unit, onChange, allowNegative]
  )

  const handleUnitChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const newUnit = e.target.value
      if (newUnit === 'auto') {
        onChange('auto')
      } else if (newUnit === '') {
        onChange('')
      } else {
        onChange(formatDimension(parsed.num || '', newUnit))
      }
    },
    [parsed.num, onChange]
  )

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault()
        const step = e.shiftKey ? 10 : 1
        const current = parseFloat(parsed.num) || 0
        const next = e.key === 'ArrowUp' ? current + step : current - step
        if (!allowNegative && next < 0) return
        const unit = parsed.unit || 'px'
        onChange(formatDimension(String(next), unit))
      }
    },
    [parsed, onChange, allowNegative]
  )

  // For raw CSS values, show a plain text input
  if (isRaw) {
    return (
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-7 text-xs font-mono"
      />
    )
  }

  return (
    <div className="flex items-center gap-0">
      <Input
        type="number"
        value={parsed.num}
        onChange={handleNumChange}
        onKeyDown={handleKeyDown}
        disabled={isKeyword}
        placeholder={isKeyword ? '—' : placeholder}
        className="h-7 text-xs rounded-r-none border-r-0 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
      />
      <select
        value={parsed.unit}
        onChange={handleUnitChange}
        className="h-7 rounded-l-none rounded-r-md border border-input bg-muted/50 px-1 text-[10px] font-medium text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring/20 cursor-pointer"
      >
        {UNITS.map((u) => (
          <option key={u.value} value={u.value}>
            {u.label}
          </option>
        ))}
      </select>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// BoxModelEditor
// ════════════════════════════════════════════════════════════════════════════

function InlineValue({
  nodeId,
  propKey,
  placeholder,
}: {
  nodeId: string
  propKey: string
  placeholder: string
}) {
  const [value, setValue] = useNodeProp<string>(nodeId, propKey)
  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState('')

  const startEdit = useCallback(() => {
    setDraft(value || '0')
    setIsEditing(true)
  }, [value])

  const commit = useCallback(() => {
    setValue(draft || '0')
    setIsEditing(false)
  }, [draft, setValue])

  if (isEditing) {
    return (
      <input
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') setIsEditing(false)
        }}
        className="w-8 text-center text-[10px] bg-transparent outline-none border-b border-current"
      />
    )
  }

  return (
    <button
      onClick={startEdit}
      className="text-[10px] min-w-[16px] text-center hover:bg-white/10 rounded px-0.5 transition-colors"
      title={`${placeholder}: ${value || '0'}px`}
    >
      {value || '0'}
    </button>
  )
}

export function BoxModelEditor({ nodeId }: { nodeId: string }) {
  return (
    <div className="px-3 py-3">
      <label className="text-[10px] text-muted-foreground/60 mb-2 block uppercase tracking-wider font-medium">
        Box Model
      </label>
      {/* Margin layer */}
      <div className="relative border border-dashed border-amber-500/30 rounded-md bg-amber-500/5 p-1">
        <span className="absolute top-0.5 left-1.5 text-[8px] text-amber-600/50 uppercase">margin</span>
        <div className="flex flex-col items-center gap-0.5 text-amber-700 dark:text-amber-400">
          <InlineValue nodeId={nodeId} propKey="marginTop" placeholder="Top" />
          <div className="flex items-center gap-1 w-full">
            <InlineValue nodeId={nodeId} propKey="marginLeft" placeholder="Left" />
            {/* Padding layer */}
            <div className="flex-1 border border-dashed border-green-500/30 rounded bg-green-500/5 p-1 relative">
              <span className="absolute top-0.5 left-1.5 text-[8px] text-green-600/50 uppercase">padding</span>
              <div className="flex flex-col items-center gap-0.5 text-green-700 dark:text-green-400">
                <InlineValue nodeId={nodeId} propKey="paddingTop" placeholder="Top" />
                <div className="flex items-center justify-between w-full">
                  <InlineValue nodeId={nodeId} propKey="paddingLeft" placeholder="Left" />
                  {/* Content */}
                  <div className="flex-1 mx-1 py-2 rounded bg-primary/5 border border-dashed border-primary/20 flex items-center justify-center">
                    <span className="text-[8px] text-muted-foreground/40 uppercase">content</span>
                  </div>
                  <InlineValue nodeId={nodeId} propKey="paddingRight" placeholder="Right" />
                </div>
                <InlineValue nodeId={nodeId} propKey="paddingBottom" placeholder="Bottom" />
              </div>
            </div>
            <InlineValue nodeId={nodeId} propKey="marginRight" placeholder="Right" />
          </div>
          <InlineValue nodeId={nodeId} propKey="marginBottom" placeholder="Bottom" />
        </div>
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// IconPicker
// ════════════════════════════════════════════════════════════════════════════

interface IconPickerProps {
  value: string
  onChange: (value: string) => void
}

export function IconPicker({ value, onChange }: IconPickerProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const buttonRef = useRef<HTMLButtonElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ top: 0, left: 0, width: 0 })

  // Position dropdown below the button
  useEffect(() => {
    if (!open || !buttonRef.current) return
    const rect = buttonRef.current.getBoundingClientRect()
    setPos({ top: rect.bottom + 2, left: rect.left, width: Math.max(rect.width, 220) })
  }, [open])

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      const target = e.target as Node
      if (
        buttonRef.current?.contains(target) ||
        dropdownRef.current?.contains(target)
      ) return
      setOpen(false)
      setSearch('')
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // Reposition on scroll so it stays anchored to the button
  useEffect(() => {
    if (!open || !buttonRef.current) return
    const handler = () => {
      if (!buttonRef.current) return
      const rect = buttonRef.current.getBoundingClientRect()
      setPos({ top: rect.bottom + 2, left: rect.left, width: Math.max(rect.width, 220) })
    }
    document.addEventListener('scroll', handler, true)
    return () => document.removeEventListener('scroll', handler, true)
  }, [open])

  const filtered = useMemo(() => {
    if (!search) return null // show grouped view
    const q = search.toLowerCase()
    return allIconNames.filter((n) => n.includes(q))
  }, [search])

  const select = useCallback((name: string) => {
    onChange(name)
    setOpen(false)
    setSearch('')
  }, [onChange])

  return (
    <div>
      <button
        ref={buttonRef}
        onClick={() => setOpen(!open)}
        className="h-7 w-full rounded-md border border-input bg-card px-2 text-xs flex items-center gap-2 hover:bg-accent/50 transition-colors cursor-pointer"
      >
        {value ? (
          <>
            <IconRenderer name={value} size={14} />
            <span className="truncate flex-1 text-left">{value}</span>
          </>
        ) : (
          <span className="text-muted-foreground">No icon</span>
        )}
      </button>

      {open && createPortal(
        <div
          ref={dropdownRef}
          className="fixed z-[100] bg-popover border border-border rounded-md shadow-lg max-h-[280px] flex flex-col"
          style={{ top: pos.top, left: pos.left, width: pos.width }}
        >
          <div className="p-1.5 border-b border-border shrink-0">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search icons..."
              className="h-6 text-xs"
              autoFocus
            />
          </div>
          <div className="flex-1 overflow-y-auto p-1.5">
            {/* None option */}
            <button
              onClick={() => select('')}
              className="w-full text-left px-2 py-1 text-xs rounded hover:bg-accent/50 text-muted-foreground mb-1"
            >
              None
            </button>

            {filtered ? (
              // Search results — flat grid
              <>
                <div className="grid grid-cols-6 gap-0.5">
                  {filtered.map((name) => (
                    <button
                      key={name}
                      onClick={() => select(name)}
                      className={`p-1.5 rounded flex items-center justify-center transition-colors cursor-pointer ${value === name ? 'bg-primary/15 text-primary' : 'text-foreground hover:bg-accent/50'}`}
                      title={name}
                    >
                      <IconRenderer name={name} size={16} />
                    </button>
                  ))}
                </div>
                {filtered.length === 0 && (
                  <p className="text-xs text-muted-foreground text-center py-3">No icons found</p>
                )}
              </>
            ) : (
              // Grouped view
              iconGroups.map((group) => (
                <div key={group.label} className="mb-2">
                  <p className="text-[10px] text-muted-foreground font-medium px-1 mb-1">{group.label}</p>
                  <div className="grid grid-cols-6 gap-0.5">
                    {group.icons.map((name) => (
                      <button
                        key={name}
                        onClick={() => select(name)}
                        className={`p-1.5 rounded flex items-center justify-center transition-colors cursor-pointer ${value === name ? 'bg-primary/15 text-primary' : 'text-foreground hover:bg-accent/50'}`}
                        title={name}
                      >
                        <IconRenderer name={name} size={16} />
                      </button>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// StateSelector
// ════════════════════════════════════════════════════════════════════════════

const STATES: { id: ElementState; label: string }[] = [
  { id: 'default', label: 'Default' },
  { id: 'hover', label: 'Hover' },
  { id: 'focus', label: 'Focus' },
  { id: 'active', label: 'Active' },
]

export function StateSelector() {
  const active = useAppEditorStore((s) => s.activeElementState)
  const setActive = useAppEditorStore((s) => s.setActiveElementState)

  return (
    <div className="flex items-center gap-0.5 px-3 py-1.5 border-b border-border/60">
      <span className="text-[10px] text-muted-foreground/60 mr-1.5 shrink-0">State</span>
      {STATES.map((state) => {
        const isActive = active === state.id
        return (
          <button
            key={state.id}
            onClick={() => setActive(state.id)}
            className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${
              isActive
                ? state.id === 'default'
                  ? 'bg-muted text-foreground'
                  : 'bg-primary/10 text-primary'
                : 'text-muted-foreground/60 hover:text-foreground hover:bg-accent/50'
            }`}
          >
            {state.label}
          </button>
        )
      })}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// StyleClassSelector
// ════════════════════════════════════════════════════════════════════════════

export function StyleClassSelector({ nodeId }: { nodeId: string }) {
  const classIds = useAppDocumentStore((s) => s.nodes[nodeId]?.classIds ?? [])
  const allClasses = useStyleClassStore((s) => s.classes)
  const addClass = useStyleClassStore((s) => s.addClass)
  const addClassToNode = useAppDocumentStore((s) => s.addClassToNode)
  const removeClassFromNode = useAppDocumentStore((s) => s.removeClassFromNode)

  const [isOpen, setIsOpen] = useState(false)
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isOpen && inputRef.current) inputRef.current.focus()
  }, [isOpen])

  const appliedClasses = classIds
    .map((id) => allClasses.find((c) => c.id === id))
    .filter(Boolean)

  const availableClasses = allClasses.filter(
    (c) => !classIds.includes(c.id) && c.name.toLowerCase().includes(query.toLowerCase())
  )

  const handleAddExisting = (classId: string) => {
    addClassToNode(nodeId, classId)
    setQuery('')
    setIsOpen(false)
  }

  const handleCreateNew = () => {
    if (!query.trim()) return
    const id = addClass(query.trim())
    addClassToNode(nodeId, id)
    setQuery('')
    setIsOpen(false)
  }

  if (classIds.length === 0 && !isOpen) {
    return (
      <button
        onClick={() => setIsOpen(true)}
        className="flex items-center gap-1 px-2 py-0.5 text-[10px] text-muted-foreground/60 hover:text-foreground hover:bg-accent/50 rounded transition-colors"
      >
        <Plus size={10} />
        <span>Add class</span>
      </button>
    )
  }

  return (
    <div className="px-3 py-1.5 border-b border-border/60">
      <div className="flex items-center gap-1 flex-wrap">
        {appliedClasses.map((cls) =>
          cls ? (
            <span
              key={cls.id}
              className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-primary/10 text-primary text-[10px] font-medium"
            >
              {cls.name}
              <button
                onClick={() => removeClassFromNode(nodeId, cls.id)}
                className="hover:bg-primary/20 rounded-sm"
              >
                <X size={10} />
              </button>
            </span>
          ) : null
        )}
        {!isOpen ? (
          <button
            onClick={() => setIsOpen(true)}
            className="p-0.5 text-muted-foreground/40 hover:text-foreground rounded transition-colors"
          >
            <Plus size={12} />
          </button>
        ) : (
          <div className="relative">
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  setIsOpen(false)
                  setQuery('')
                }
                if (e.key === 'Enter') {
                  if (availableClasses.length > 0) {
                    handleAddExisting(availableClasses[0].id)
                  } else if (query.trim()) {
                    handleCreateNew()
                  }
                }
              }}
              onBlur={() => {
                // Delay to allow click on dropdown
                setTimeout(() => {
                  setIsOpen(false)
                  setQuery('')
                }, 150)
              }}
              placeholder="Class name..."
              className="w-24 px-1.5 py-0.5 text-[10px] bg-transparent border border-border rounded outline-none focus:border-primary"
            />
            {(availableClasses.length > 0 || query.trim()) && (
              <div className="absolute top-full left-0 mt-1 w-40 py-1 bg-popover border border-border rounded-md shadow-lg z-50">
                {availableClasses.slice(0, 5).map((cls) => (
                  <button
                    key={cls.id}
                    onMouseDown={() => handleAddExisting(cls.id)}
                    className="w-full text-left px-2 py-1 text-[11px] hover:bg-accent transition-colors"
                  >
                    {cls.name}
                  </button>
                ))}
                {query.trim() && !allClasses.some((c) => c.name === query.trim()) && (
                  <button
                    onMouseDown={handleCreateNew}
                    className="w-full text-left px-2 py-1 text-[11px] text-primary hover:bg-accent transition-colors"
                  >
                    Create "{query.trim()}"
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// ExpressionEditor
// ════════════════════════════════════════════════════════════════════════════

interface Suggestion {
  path: string
  label: string
  detail: string
}

interface ExpressionEditorProps {
  value: string
  onChange: (value: string) => void
}

/**
 * Build autocomplete suggestions from the current document state.
 * Provides paths like:
 *   components.<nodeId>.<exposedField>
 *   stores.<storeName>
 */
function useExpressionSuggestions(): Suggestion[] {
  const nodes = useAppDocumentStore((s) => s.nodes)
  const storeDefs = useAppDocumentStore((s) => s.storeDefinitions)

  return useMemo(() => {
    const suggestions: Suggestion[] = []

    // Component exposed state
    for (const [nodeId, node] of Object.entries(nodes)) {
      if (node.parentId === null) continue // skip root
      const def = getDefinition(node.type)
      if (!def?.exposedState?.length) continue
      for (const field of def.exposedState) {
        suggestions.push({
          path: `components.${nodeId}.${field.name}`,
          label: `${nodeId}.${field.name}`,
          detail: `${def.meta.displayName} → ${field.label}`,
        })
      }
    }

    // Global stores
    for (const store of storeDefs) {
      suggestions.push({
        path: `stores.${store.name}`,
        label: `stores.${store.name}`,
        detail: 'Global store',
      })
    }

    return suggestions
  }, [nodes, storeDefs])
}

export function ExpressionEditor({ value, onChange }: ExpressionEditorProps) {
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dropdownPos, setDropdownPos] = useState<{ top: number; left: number; width: number } | null>(null)
  const allSuggestions = useExpressionSuggestions()

  // Extract the current expression fragment being typed (after last {{ )
  const cursorContext = useMemo(() => {
    const textarea = textareaRef.current
    if (!textarea) return ''
    const cursorPos = textarea.selectionStart
    const textBefore = value.slice(0, cursorPos)
    const lastOpen = textBefore.lastIndexOf('{{')
    if (lastOpen === -1) return ''
    const lastClose = textBefore.lastIndexOf('}}')
    if (lastClose > lastOpen) return ''
    return textBefore.slice(lastOpen + 2).trim()
  }, [value])

  const filteredSuggestions = useMemo(() => {
    if (!cursorContext && !showSuggestions) return []
    const query = cursorContext.toLowerCase()
    return allSuggestions.filter(
      (s) =>
        s.path.toLowerCase().includes(query) ||
        s.label.toLowerCase().includes(query) ||
        s.detail.toLowerCase().includes(query)
    )
  }, [cursorContext, allSuggestions, showSuggestions])

  const updateDropdownPos = useCallback(() => {
    if (!containerRef.current) return
    const rect = containerRef.current.getBoundingClientRect()
    setDropdownPos({
      top: rect.bottom + 2,
      left: rect.left,
      width: rect.width,
    })
  }, [])

  const handleInput = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const newValue = e.target.value
      onChange(newValue)

      // Check if we're inside {{ }}
      const cursorPos = e.target.selectionStart
      const textBefore = newValue.slice(0, cursorPos)
      const lastOpen = textBefore.lastIndexOf('{{')
      const lastClose = textBefore.lastIndexOf('}}')

      if (lastOpen !== -1 && lastOpen > lastClose) {
        setShowSuggestions(true)
        setSelectedIndex(0)
        updateDropdownPos()
      } else {
        setShowSuggestions(false)
      }
    },
    [onChange, updateDropdownPos]
  )

  const insertSuggestion = useCallback(
    (suggestion: Suggestion) => {
      const textarea = textareaRef.current
      if (!textarea) return

      const cursorPos = textarea.selectionStart
      const textBefore = value.slice(0, cursorPos)
      const textAfter = value.slice(cursorPos)
      const lastOpen = textBefore.lastIndexOf('{{')

      if (lastOpen === -1) return

      // Replace from after {{ to cursor with the suggestion path
      const beforeExpr = value.slice(0, lastOpen + 2)
      const hasClosing = textAfter.trimStart().startsWith('}}')
      const newValue = beforeExpr + ' ' + suggestion.path + (hasClosing ? '' : ' }}') + (hasClosing ? textAfter : textAfter)

      onChange(newValue)
      setShowSuggestions(false)

      // Restore focus
      requestAnimationFrame(() => {
        textarea.focus()
        const newPos = (beforeExpr + ' ' + suggestion.path + ' }}').length
        textarea.setSelectionRange(newPos, newPos)
      })
    },
    [value, onChange]
  )

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (!showSuggestions || filteredSuggestions.length === 0) return

      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIndex((i) => Math.min(i + 1, filteredSuggestions.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIndex((i) => Math.max(i - 1, 0))
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        insertSuggestion(filteredSuggestions[selectedIndex])
      } else if (e.key === 'Escape') {
        e.preventDefault()
        setShowSuggestions(false)
      }
    },
    [showSuggestions, filteredSuggestions, selectedIndex, insertSuggestion]
  )

  // Close suggestions when clicking outside
  useEffect(() => {
    if (!showSuggestions) return
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowSuggestions(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [showSuggestions])

  return (
    <div ref={containerRef} className="relative">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={handleInput}
        onKeyDown={handleKeyDown}
        onFocus={() => {
          // Show suggestions if already inside {{ }}
          const cursorPos = textareaRef.current?.selectionStart ?? 0
          const textBefore = value.slice(0, cursorPos)
          const lastOpen = textBefore.lastIndexOf('{{')
          const lastClose = textBefore.lastIndexOf('}}')
          if (lastOpen !== -1 && lastOpen > lastClose) {
            setShowSuggestions(true)
            updateDropdownPos()
          }
        }}
        placeholder="{{ components.nodeId.value }}"
        spellCheck={false}
        className="w-full min-h-[60px] rounded-md border border-input bg-card px-2 py-1.5 text-xs font-mono text-foreground focus:outline-none focus:ring-2 focus:ring-ring/20 resize-y"
      />
      {showSuggestions && filteredSuggestions.length > 0 && dropdownPos &&
        createPortal(
          <div
            className="fixed z-[100] rounded-md border border-border bg-popover shadow-lg overflow-hidden"
            style={{
              top: dropdownPos.top,
              left: dropdownPos.left,
              width: dropdownPos.width,
              maxHeight: 200,
            }}
          >
            <div className="overflow-y-auto max-h-[200px]">
              {filteredSuggestions.map((suggestion, i) => (
                <button
                  key={suggestion.path}
                  className={`w-full text-left px-2.5 py-1.5 text-xs flex items-center justify-between gap-2 transition-colors ${
                    i === selectedIndex
                      ? 'bg-accent text-accent-foreground'
                      : 'hover:bg-accent/50'
                  }`}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    insertSuggestion(suggestion)
                  }}
                  onMouseEnter={() => setSelectedIndex(i)}
                >
                  <span className="font-mono truncate">{suggestion.label}</span>
                  <span className="text-[10px] text-muted-foreground shrink-0">
                    {suggestion.detail}
                  </span>
                </button>
              ))}
            </div>
          </div>,
          document.body
        )}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// PropControl
// ════════════════════════════════════════════════════════════════════════════

interface PropControlProps {
  field: PropField
  value: unknown
  onChange: (name: string, value: unknown) => void
}

export function PropControl({ field, value, onChange }: PropControlProps) {
  const [expressionMode, setExpressionMode] = useState(() => isExpression(value))

  const handleChange = useCallback(
    (newValue: unknown) => {
      onChange(field.name, newValue)
    },
    [field.name, onChange]
  )

  // Switch/boolean fields don't support expression binding
  const supportsExpression = field.control !== 'switch'

  const toggleExpression = useCallback(() => {
    setExpressionMode((prev) => {
      if (prev) {
        // Switching back to static — if value is a pure expression, clear it
        const str = String(value ?? '')
        if (str.trim().startsWith('{{') && str.trim().endsWith('}}')) {
          onChange(field.name, field.defaultValue ?? '')
        }
      }
      return !prev
    })
  }, [value, field.name, field.defaultValue, onChange])

  // Expression mode: show the expression editor
  if (expressionMode && supportsExpression) {
    return (
      <div className="space-y-1">
        <div className="flex items-center justify-end">
          <button
            onClick={toggleExpression}
            className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
            title="Switch to static value"
          >
            {'{ }'}
          </button>
        </div>
        <ExpressionEditor
          value={String(value ?? '')}
          onChange={(v) => handleChange(v)}
        />
      </div>
    )
  }

  const expressionToggle = supportsExpression ? (
    <div className="flex items-center justify-end mb-1">
      <button
        onClick={toggleExpression}
        className="text-[10px] font-mono px-1.5 py-0.5 rounded text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
        title="Switch to expression"
      >
        {'{ }'}
      </button>
    </div>
  ) : null

  switch (field.control) {
    case 'text':
    case 'expression':
      return (
        <div>
          {expressionToggle}
          <Input
            value={String(value ?? '')}
            onChange={(e) => handleChange(e.target.value)}
            className="h-7 text-xs"
          />
        </div>
      )

    case 'number':
      return (
        <div>
          {expressionToggle}
          <Input
            type="number"
            value={String(value ?? '')}
            min={field.min}
            max={field.max}
            onChange={(e) => {
              let v = e.target.value
              if (v !== '' && field.min !== undefined && Number(v) < field.min) v = String(field.min)
              if (v !== '' && field.max !== undefined && Number(v) > field.max) v = String(field.max)
              handleChange(v)
            }}
            className="h-7 text-xs"
          />
        </div>
      )

    case 'switch':
      return (
        <Switch
          checked={Boolean(value)}
          onCheckedChange={(checked) => handleChange(checked)}
        />
      )

    case 'select':
      return (
        <div>
          {expressionToggle}
          <select
            value={String(value ?? '')}
            onChange={(e) => handleChange(e.target.value)}
            className="h-7 w-full rounded-md border border-input bg-card px-2 text-xs focus:outline-none focus:ring-2 focus:ring-ring/20"
          >
            {field.options?.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      )

    case 'color':
      return (
        <div>
          {expressionToggle}
          <div className="flex items-center gap-2">
            <input
              type="color"
              value={String(value || '#000000')}
              onChange={(e) => handleChange(e.target.value)}
              className="h-7 w-7 rounded border border-input cursor-pointer p-0.5"
            />
            <Input
              value={String(value ?? '')}
              onChange={(e) => handleChange(e.target.value)}
              placeholder="transparent"
              className="h-7 text-xs flex-1"
            />
          </div>
        </div>
      )

    case 'icon':
      return <IconPicker value={String(value ?? '')} onChange={(v) => handleChange(v)} />

    default:
      return null
  }
}

// ════════════════════════════════════════════════════════════════════════════
// LayoutSettings
// ════════════════════════════════════════════════════════════════════════════

// ── Flex Sizing Presets (Webflow-style) ─────────────────────────────────────

function FlexChildPresets({ nodeId }: { nodeId: string }) {
  const [fillSpace, setFillSpace] = useNodeProp<string>(nodeId, 'fillSpace')
  const [, setFlexGrow] = useNodeProp<string>(nodeId, 'flexGrow')
  const [, setFlexShrink] = useNodeProp<string>(nodeId, 'flexShrink')
  const [, setFlexBasis] = useNodeProp<string>(nodeId, 'flexBasis')

  const current =
    fillSpace === 'yes'
      ? 'fill'
      : 'hug'

  const handlePreset = (preset: string) => {
    switch (preset) {
      case 'fill':
        setFillSpace('yes')
        setFlexGrow('1')
        setFlexShrink('1')
        setFlexBasis('0%')
        break
      case 'hug':
        setFillSpace('no')
        setFlexGrow('0')
        setFlexShrink('0')
        setFlexBasis('auto')
        break
      case 'fixed':
        setFillSpace('no')
        setFlexGrow('0')
        setFlexShrink('0')
        setFlexBasis('')
        break
    }
  }

  return (
    <div>
      <label className="text-[11px] text-muted-foreground mb-1 block">Sizing</label>
      <ToolbarRadio
        value={current}
        onChange={handlePreset}
        options={[
          { label: 'Fill', value: 'fill' },
          { label: 'Hug', value: 'hug' },
          { label: 'Fixed', value: 'fixed' },
        ]}
      />
    </div>
  )
}

// ── Dimension Row ───────────────────────────────────────────────────────────

function DimensionRow({
  nodeId,
  propKey,
  label,
  placeholder,
  allowNegative,
}: {
  nodeId: string
  propKey: string
  label: string
  placeholder?: string
  allowNegative?: boolean
}) {
  const [value, setValue] = useNodeProp<string>(nodeId, propKey)
  return (
    <div>
      <label className="text-[11px] text-muted-foreground mb-1 block">{label}</label>
      <DimensionInput
        value={value ?? ''}
        onChange={setValue}
        placeholder={placeholder}
        allowNegative={allowNegative}
      />
    </div>
  )
}

// ── Position Insets ─────────────────────────────────────────────────────────

function PositionInsets({ nodeId }: { nodeId: string }) {
  const [position] = useNodeProp<string>(nodeId, 'position')
  if (!position || position === 'static') return null

  return (
    <ToolbarSection title="Position Offsets" defaultOpen>
      <div className="grid grid-cols-2 gap-x-2 gap-y-2">
        <DimensionRow nodeId={nodeId} propKey="top" label="Top" placeholder="auto" allowNegative />
        <DimensionRow nodeId={nodeId} propKey="bottom" label="Bottom" placeholder="auto" allowNegative />
        <DimensionRow nodeId={nodeId} propKey="left" label="Left" placeholder="auto" allowNegative />
        <DimensionRow nodeId={nodeId} propKey="right" label="Right" placeholder="auto" allowNegative />
      </div>
    </ToolbarSection>
  )
}

// ── Main Export ──────────────────────────────────────────────────────────────

interface LayoutSettingsProps {
  nodeId: string
  /** Hide dimension section (e.g. for root node) */
  hideDimensions?: boolean
}

export function LayoutSettings({
  nodeId,
  hideDimensions = false,
}: LayoutSettingsProps) {

  return (
    <>
      {/* ── Dimensions ─────────────────────────────────── */}
      {!hideDimensions && (
        <ToolbarSection title="Dimensions">
          <div className="grid grid-cols-2 gap-x-2 gap-y-2">
            <DimensionRow nodeId={nodeId} propKey="width" label="Width" />
            <DimensionRow nodeId={nodeId} propKey="height" label="Height" />
          </div>
          <div className="grid grid-cols-2 gap-x-2 gap-y-2">
            <DimensionRow nodeId={nodeId} propKey="minWidth" label="Min W" />
            <DimensionRow nodeId={nodeId} propKey="maxWidth" label="Max W" />
          </div>
          <div className="grid grid-cols-2 gap-x-2 gap-y-2">
            <DimensionRow nodeId={nodeId} propKey="minHeight" label="Min H" />
            <DimensionRow nodeId={nodeId} propKey="maxHeight" label="Max H" />
          </div>
          <ToolbarItem
            nodeId={nodeId}
            propKey="overflow"
            label="Overflow"
            type="radio"
            options={[
              { label: 'Visible', value: 'visible' },
              { label: 'Hidden', value: 'hidden' },
              { label: 'Scroll', value: 'scroll' },
              { label: 'Auto', value: 'auto' },
            ]}
          />
        </ToolbarSection>
      )}

      {/* ── Layout Mode ────────────────────────────────── */}
      <ToolbarSection title="Layout">
        <ToolbarItem
          nodeId={nodeId}
          propKey="flexDirection"
          label="Direction"
          type="radio"
          options={[
            { label: 'Row', value: 'row' },
            { label: 'Column', value: 'column' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="flexWrap"
          label="Wrap"
          type="radio"
          options={[
            { label: 'No Wrap', value: 'nowrap' },
            { label: 'Wrap', value: 'wrap' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="alignItems"
          label="Align"
          type="radio"
          options={[
            { label: 'Start', value: 'flex-start' },
            { label: 'Center', value: 'center' },
            { label: 'End', value: 'flex-end' },
            { label: 'Stretch', value: 'stretch' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="justifyContent"
          label="Justify"
          type="radio"
          options={[
            { label: 'Start', value: 'flex-start' },
            { label: 'Center', value: 'center' },
            { label: 'End', value: 'flex-end' },
            { label: 'Between', value: 'space-between' },
            { label: 'Around', value: 'space-around' },
            { label: 'Evenly', value: 'space-evenly' },
          ]}
        />
        <ToolbarItem nodeId={nodeId} propKey="gap" label="Gap" type="slider" max={100} />
      </ToolbarSection>

      {/* ── Flex Child / Sizing ─────────────────────────── */}
      <ToolbarSection title="Sizing" defaultOpen={false}>
        <FlexChildPresets nodeId={nodeId} />
        <div className="grid grid-cols-3 gap-x-2 gap-y-2">
          <ToolbarItem nodeId={nodeId} propKey="flexGrow" label="Grow" type="number" />
          <ToolbarItem nodeId={nodeId} propKey="flexShrink" label="Shrink" type="number" />
          <ToolbarItem nodeId={nodeId} propKey="flexBasis" label="Basis" type="text" />
        </div>
        <ToolbarItem
          nodeId={nodeId}
          propKey="alignSelf"
          label="Align Self"
          type="radio"
          options={[
            { label: 'Auto', value: 'auto' },
            { label: 'Start', value: 'flex-start' },
            { label: 'Center', value: 'center' },
            { label: 'End', value: 'flex-end' },
            { label: 'Stretch', value: 'stretch' },
          ]}
        />
      </ToolbarSection>

      {/* ── Padding ────────────────────────────────────── */}
      <ToolbarSection title="Padding">
        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
          <ToolbarItem nodeId={nodeId} propKey="paddingTop" label="Top" type="slider" max={100} />
          <ToolbarItem nodeId={nodeId} propKey="paddingBottom" label="Bottom" type="slider" max={100} />
          <ToolbarItem nodeId={nodeId} propKey="paddingLeft" label="Left" type="slider" max={100} />
          <ToolbarItem nodeId={nodeId} propKey="paddingRight" label="Right" type="slider" max={100} />
        </div>
      </ToolbarSection>

      {/* ── Margin ─────────────────────────────────────── */}
      <ToolbarSection title="Margin" defaultOpen={false}>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
          <ToolbarItem nodeId={nodeId} propKey="marginTop" label="Top" type="slider" max={100} />
          <ToolbarItem nodeId={nodeId} propKey="marginBottom" label="Bottom" type="slider" max={100} />
          <ToolbarItem nodeId={nodeId} propKey="marginLeft" label="Left" type="slider" max={100} />
          <ToolbarItem nodeId={nodeId} propKey="marginRight" label="Right" type="slider" max={100} />
        </div>
      </ToolbarSection>

      {/* ── Colors ─────────────────────────────────────── */}
      <ToolbarSection title="Colors">
        <ToolbarItem nodeId={nodeId} propKey="background" label="Background" type="color" />
        <ToolbarItem nodeId={nodeId} propKey="color" label="Text Color" type="color" />
        <ToolbarItem nodeId={nodeId} propKey="opacity" label="Opacity" type="slider" max={100} />
      </ToolbarSection>

      {/* ── Border ─────────────────────────────────────── */}
      <ToolbarSection title="Border" defaultOpen={false}>
        <ToolbarItem
          nodeId={nodeId}
          propKey="borderStyle"
          label="Style"
          type="radio"
          options={[
            { label: 'None', value: 'none' },
            { label: 'Solid', value: 'solid' },
            { label: 'Dashed', value: 'dashed' },
            { label: 'Dotted', value: 'dotted' },
          ]}
        />
        <div className="grid grid-cols-2 gap-x-2 gap-y-2">
          <ToolbarItem nodeId={nodeId} propKey="borderWidth" label="Width" type="slider" max={10} />
          <ToolbarItem nodeId={nodeId} propKey="borderRadius" label="Radius" type="slider" max={50} />
        </div>
        <ToolbarItem nodeId={nodeId} propKey="borderColor" label="Color" type="color" />
      </ToolbarSection>

      {/* ── Effects ────────────────────────────────────── */}
      <ToolbarSection title="Effects" defaultOpen={false}>
        <ToolbarItem
          nodeId={nodeId}
          propKey="shadow"
          label="Shadow"
          type="radio"
          options={[
            { label: 'None', value: '0' },
            { label: 'Sm', value: '1' },
            { label: 'Md', value: '2' },
            { label: 'Lg', value: '3' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="cursor"
          label="Cursor"
          type="select"
          options={[
            { label: 'Default', value: 'default' },
            { label: 'Pointer', value: 'pointer' },
            { label: 'Move', value: 'move' },
            { label: 'Not Allowed', value: 'not-allowed' },
          ]}
        />
        <ToolbarItem
          nodeId={nodeId}
          propKey="position"
          label="Position"
          type="radio"
          options={[
            { label: 'Static', value: 'static' },
            { label: 'Relative', value: 'relative' },
            { label: 'Absolute', value: 'absolute' },
            { label: 'Sticky', value: 'sticky' },
          ]}
        />
        <ToolbarItem nodeId={nodeId} propKey="zIndex" label="Z-Index" type="number" />
      </ToolbarSection>

      {/* ── Position Insets (when not static) ──────────── */}
      <PositionInsets nodeId={nodeId} />

      {/* ── Custom CSS ─────────────────────────────────── */}
      <ToolbarSection title="Custom CSS" defaultOpen={false}>
        <ToolbarItem nodeId={nodeId} propKey="customStyles" label="Inline Styles (JSON)" type="text" placeholder='{"key":"value"}' />
        <p className="text-[10px] text-muted-foreground leading-relaxed">
          JSON object, e.g. {`{"backdropFilter":"blur(8px)"}`}
        </p>
      </ToolbarSection>
    </>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// EventPanel
// ════════════════════════════════════════════════════════════════════════════

const eventFieldClass =
  'w-full h-7 px-2 text-[11px] bg-card border border-input rounded-md focus:outline-none focus:border-ring focus:ring-2 focus:ring-ring/20 text-foreground placeholder:text-muted-foreground font-mono transition-colors'

function defaultAction(type: string): EventAction {
  switch (type) {
    case 'setState': return { type: 'setState', storeId: '', value: '' }
    case 'mergeState': return { type: 'mergeState', storeId: '', value: '' }
    case 'setProperty': return { type: 'setProperty', storeId: '', path: '', value: '' }
    case 'appendArray': return { type: 'appendArray', storeId: '', path: '', value: '' }
    case 'removeArrayIndex': return { type: 'removeArrayIndex', storeId: '', path: '', index: '' }
    case 'setComponentState': return { type: 'setComponentState', nodeId: '', property: '', value: '' }
    case 'runWebhook': return { type: 'runWebhook', webhookId: '', resultStore: '' }
    case 'condition': return { type: 'condition', expression: '', thenActions: [], elseActions: [] }
    default: return { type: 'alert', message: '' }
  }
}

const ACTION_OPTIONS = [
  { value: 'alert', label: 'Alert' },
  { value: 'setState', label: 'Set Store' },
  { value: 'mergeState', label: 'Merge Store' },
  { value: 'setProperty', label: 'Set Property' },
  { value: 'appendArray', label: 'Append to Array' },
  { value: 'removeArrayIndex', label: 'Remove from Array' },
  { value: 'setComponentState', label: 'Set Component State' },
  { value: 'runWebhook', label: 'Run API Call' },
  { value: 'condition', label: 'If / Else' },
]

function StoreSelect({ value, onChange, storeDefs, placeholder = "Select store..." }: {
  value: string
  onChange: (v: string) => void
  storeDefs: { id: string; name: string }[]
  placeholder?: string
}) {
  return (
    <Select value={value || undefined} onValueChange={onChange}>
      <SelectTrigger size="sm">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {storeDefs.map((s) => (
          <SelectItem key={s.id} value={s.name}>{s.name}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export function EventPanel() {
  const selectedId = useAppEditorStore((s) => s.selectedNodeIds[0] ?? null)
  const node = useAppDocumentStore((s) => selectedId ? s.nodes[selectedId] : null)
  const updateEventHandlers = useAppDocumentStore((s) => s.updateNodeEventHandlers)
  const webhookDefs = useAppDocumentStore((s) => s.webhookDefinitions)
  const storeDefs = useAppDocumentStore((s) => s.storeDefinitions)

  const def = useMemo(() => (node ? getDefinition(node.type) : null), [node?.type])
  const eventSchema = def?.eventSchema ?? []
  const handlers: EventHandlerConfig[] = (node?.props.__eventHandlers as EventHandlerConfig[]) ?? []

  if (!node || !def || eventSchema.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center py-12 text-center px-6">
        <div className="w-10 h-10 rounded-full bg-muted/60 flex items-center justify-center mb-3">
          <Zap size={16} className="text-muted-foreground/60" />
        </div>
        <p className="text-[11px] font-medium text-muted-foreground mb-1">
          {!node ? 'No component selected' : 'No events available'}
        </p>
        <p className="text-[10px] text-muted-foreground/60 leading-relaxed">
          {!node
            ? 'Select a component to configure its event handlers'
            : 'This component does not expose any events'
          }
        </p>
      </div>
    )
  }

  const addAction = (eventName: string) => {
    const existing = handlers.find((h) => h.event === eventName)
    if (existing) {
      const updated = handlers.map((h) =>
        h.event === eventName ? { ...h, actions: [...h.actions, defaultAction('alert')] } : h
      )
      updateEventHandlers(selectedId!, updated)
    } else {
      updateEventHandlers(selectedId!, [
        ...handlers,
        { event: eventName, actions: [defaultAction('alert')] },
      ])
    }
  }

  const removeAction = (eventName: string, idx: number) => {
    const updated = handlers
      .map((h) => h.event !== eventName ? h : { ...h, actions: h.actions.filter((_, i) => i !== idx) })
      .filter((h) => h.actions.length > 0)
    updateEventHandlers(selectedId!, updated)
  }

  const changeType = (eventName: string, idx: number, newType: string) => {
    const updated = handlers.map((h) => {
      if (h.event !== eventName) return h
      return { ...h, actions: h.actions.map((a, i) => i === idx ? defaultAction(newType) : a) }
    })
    updateEventHandlers(selectedId!, updated)
  }

  const updateAction = (eventName: string, idx: number, patch: Partial<EventAction>) => {
    const updated = handlers.map((h) => {
      if (h.event !== eventName) return h
      return { ...h, actions: h.actions.map((a, i) => i === idx ? { ...a, ...patch } as EventAction : a) }
    })
    updateEventHandlers(selectedId!, updated)
  }

  return (
    <div>
      {eventSchema.map((event) => {
        const actions = handlers.find((h) => h.event === event.name)?.actions ?? []

        return (
          <ToolbarSection key={event.name} title={event.label}>
            <div className="space-y-2">
              {actions.map((action, idx) => (
                <div
                  key={idx}
                  className="rounded-lg border border-border/50 bg-muted/30 overflow-hidden"
                >
                  {/* Action header */}
                  <div className="flex items-center gap-1.5 px-2 py-1.5 bg-muted/40 border-b border-border/40">
                    <span className="text-[10px] font-medium text-muted-foreground tabular-nums w-4 shrink-0">
                      {idx + 1}
                    </span>
                    <Select value={action.type} onValueChange={(v) => changeType(event.name, idx, v)}>
                      <SelectTrigger size="sm" className="h-6 border-none shadow-none bg-transparent px-1 text-[10px] font-medium">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ACTION_OPTIONS.map((opt) => (
                          <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <div className="flex-1" />
                    <button
                      onClick={() => removeAction(event.name, idx)}
                      className="w-5 h-5 flex items-center justify-center rounded text-muted-foreground/50 hover:text-destructive hover:bg-destructive/10 transition-colors"
                    >
                      <Trash2 size={10} />
                    </button>
                  </div>

                  {/* Action body */}
                  <div className="px-2.5 py-2 space-y-1.5">
                    {action.type === 'alert' && (
                      <input
                        className={eventFieldClass}
                        value={action.message}
                        onChange={(e) => updateAction(event.name, idx, { type: 'alert', message: e.target.value })}
                        placeholder="Message"
                      />
                    )}

                    {action.type === 'setState' && (
                      <>
                        <StoreSelect value={action.storeId} onChange={(v) => updateAction(event.name, idx, { type: 'setState', storeId: v, value: action.value })} storeDefs={storeDefs} />
                        <input className={eventFieldClass} value={action.value} onChange={(e) => updateAction(event.name, idx, { type: 'setState', storeId: action.storeId, value: e.target.value })} placeholder="Value" />
                      </>
                    )}

                    {action.type === 'mergeState' && (
                      <>
                        <StoreSelect value={action.storeId} onChange={(v) => updateAction(event.name, idx, { type: 'mergeState', storeId: v, value: action.value })} storeDefs={storeDefs} />
                        <input className={eventFieldClass} value={action.value} onChange={(e) => updateAction(event.name, idx, { type: 'mergeState', storeId: action.storeId, value: e.target.value })} placeholder='{"key": "value"} or {{ expression }}' />
                      </>
                    )}

                    {action.type === 'setProperty' && (
                      <>
                        <StoreSelect value={action.storeId} onChange={(v) => updateAction(event.name, idx, { type: 'setProperty', storeId: v, path: action.path, value: action.value })} storeDefs={storeDefs} />
                        <input className={eventFieldClass} value={action.path} onChange={(e) => updateAction(event.name, idx, { type: 'setProperty', storeId: action.storeId, path: e.target.value, value: action.value })} placeholder="Path (e.g. user.name)" />
                        <input className={eventFieldClass} value={action.value} onChange={(e) => updateAction(event.name, idx, { type: 'setProperty', storeId: action.storeId, path: action.path, value: e.target.value })} placeholder="Value" />
                      </>
                    )}

                    {action.type === 'appendArray' && (
                      <>
                        <StoreSelect value={action.storeId} onChange={(v) => updateAction(event.name, idx, { type: 'appendArray', storeId: v, path: action.path, value: action.value })} storeDefs={storeDefs} />
                        <input className={eventFieldClass} value={action.path} onChange={(e) => updateAction(event.name, idx, { type: 'appendArray', storeId: action.storeId, path: e.target.value, value: action.value })} placeholder="Array path (empty = root)" />
                        <input className={eventFieldClass} value={action.value} onChange={(e) => updateAction(event.name, idx, { type: 'appendArray', storeId: action.storeId, path: action.path, value: e.target.value })} placeholder="Item to append" />
                      </>
                    )}

                    {action.type === 'removeArrayIndex' && (
                      <>
                        <StoreSelect value={action.storeId} onChange={(v) => updateAction(event.name, idx, { type: 'removeArrayIndex', storeId: v, path: action.path, index: action.index })} storeDefs={storeDefs} />
                        <input className={eventFieldClass} value={action.path} onChange={(e) => updateAction(event.name, idx, { type: 'removeArrayIndex', storeId: action.storeId, path: e.target.value, index: action.index })} placeholder="Array path (empty = root)" />
                        <input className={eventFieldClass} value={action.index} onChange={(e) => updateAction(event.name, idx, { type: 'removeArrayIndex', storeId: action.storeId, path: action.path, index: e.target.value })} placeholder="Index (or {{ expression }})" />
                      </>
                    )}

                    {action.type === 'setComponentState' && (
                      <>
                        <input className={eventFieldClass} value={action.nodeId} onChange={(e) => updateAction(event.name, idx, { type: 'setComponentState', nodeId: e.target.value, property: action.property, value: action.value })} placeholder="Node ID" />
                        <input className={eventFieldClass} value={action.property} onChange={(e) => updateAction(event.name, idx, { type: 'setComponentState', nodeId: action.nodeId, property: e.target.value, value: action.value })} placeholder="Property" />
                        <input className={eventFieldClass} value={action.value} onChange={(e) => updateAction(event.name, idx, { type: 'setComponentState', nodeId: action.nodeId, property: action.property, value: e.target.value })} placeholder="Value" />
                      </>
                    )}

                    {action.type === 'runWebhook' && (
                      <>
                        <Select value={action.webhookId || undefined} onValueChange={(v) => updateAction(event.name, idx, { type: 'runWebhook', webhookId: v, resultStore: action.resultStore })}>
                          <SelectTrigger size="sm">
                            <SelectValue placeholder="Select API call..." />
                          </SelectTrigger>
                          <SelectContent>
                            {webhookDefs.map((w) => (
                              <SelectItem key={w.id} value={w.id}>{w.name || w.id}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <StoreSelect value={action.resultStore} onChange={(v) => updateAction(event.name, idx, { type: 'runWebhook', webhookId: action.webhookId, resultStore: v })} storeDefs={storeDefs} placeholder="Result store..." />
                      </>
                    )}

                    {action.type === 'condition' && (
                      <>
                        <input
                          className={eventFieldClass}
                          value={action.expression}
                          onChange={(e) => updateAction(event.name, idx, { type: 'condition', expression: e.target.value, thenActions: action.thenActions, elseActions: action.elseActions })}
                          placeholder="Condition expression"
                        />
                        <div className="ml-1 pl-2 border-l-2 border-[var(--success)]/40 space-y-1 py-1">
                          <span className="text-[10px] text-[var(--success)] font-semibold uppercase tracking-wider">
                            Then · {action.thenActions.length}
                          </span>
                          <button
                            onClick={() => updateAction(event.name, idx, { type: 'condition', expression: action.expression, thenActions: [...action.thenActions, defaultAction('alert')], elseActions: action.elseActions })}
                            className="w-full h-6 flex items-center justify-center gap-1 rounded text-[10px] text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                          >
                            <Plus size={10} /> Add action
                          </button>
                        </div>
                        <div className="ml-1 pl-2 border-l-2 border-destructive/40 space-y-1 py-1">
                          <span className="text-[10px] text-destructive font-semibold uppercase tracking-wider">
                            Else · {action.elseActions.length}
                          </span>
                          <button
                            onClick={() => updateAction(event.name, idx, { type: 'condition', expression: action.expression, thenActions: action.thenActions, elseActions: [...action.elseActions, defaultAction('alert')] })}
                            className="w-full h-6 flex items-center justify-center gap-1 rounded text-[10px] text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                          >
                            <Plus size={10} /> Add action
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* Add action button */}
            <button
              onClick={() => addAction(event.name)}
              className="w-full h-7 flex items-center justify-center gap-1.5 rounded-md border border-dashed border-border/60 text-[11px] text-muted-foreground hover:text-foreground hover:border-border hover:bg-accent/50 transition-colors mt-1"
            >
              <Plus size={12} />
              Add action
            </button>
          </ToolbarSection>
        )
      })}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// PropertyPanel
// ════════════════════════════════════════════════════════════════════════════

// ── Exposed State Toggles ───────────────────────────────────────────────────

function ExposedStateToggles({ nodeId }: { nodeId: string }) {
  const node = useAppDocumentStore((s) => s.nodes[nodeId])
  const def = node ? getDefinition(node.type) : null
  const componentState = useRuntimeStateStore((s) => s.componentState[nodeId])
  const setComponentState = useRuntimeStateStore((s) => s.setComponentState)

  if (!def?.exposedState?.length) return null

  return (
    <ToolbarSection title="Component State" defaultOpen>
      <div className="flex flex-col gap-1.5">
        {def.exposedState.map((field) => {
          const value = componentState?.[field.name]
          const isBool = typeof field.defaultValue === 'boolean'

          if (isBool) {
            const isOn = !!value
            return (
              <button
                key={field.name}
                onClick={() => setComponentState(nodeId, field.name, !isOn)}
                className="flex items-center justify-between px-2 py-1.5 rounded-md hover:bg-muted/60 transition-colors text-left"
              >
                <span className="text-[11px] text-muted-foreground">{field.label}</span>
                {isOn ? (
                  <ToggleRight size={16} className="text-primary" />
                ) : (
                  <ToggleLeft size={16} className="text-muted-foreground/50" />
                )}
              </button>
            )
          }

          // Non-boolean: show as read-only value
          return (
            <div key={field.name} className="flex items-center justify-between px-2 py-1.5">
              <span className="text-[11px] text-muted-foreground">{field.label}</span>
              <span className="text-[11px] font-mono text-foreground/70 truncate max-w-[120px]">
                {String(value ?? field.defaultValue)}
              </span>
            </div>
          )
        })}
      </div>
    </ToolbarSection>
  )
}

// Style-related prop names that can have breakpoint/state overrides
const STYLE_PROPS = new Set([
  'width', 'minWidth', 'maxWidth', 'height', 'minHeight', 'maxHeight',
  'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
  'marginTop', 'marginRight', 'marginBottom', 'marginLeft',
  'gap', 'flexDirection', 'alignItems', 'justifyContent',
  'background', 'color', 'opacity', 'fontSize', 'fontWeight',
  'borderRadius', 'borderWidth', 'borderColor', 'borderStyle',
  'shadow', 'display',
])

export function PropertyPanel() {
  const selectedNodeIds = useAppEditorStore((s) => s.selectedNodeIds)
  const selectedId = selectedNodeIds[0] ?? null
  const activeElementState = useAppEditorStore((s) => s.activeElementState)
  const activeBreakpoint = useBreakpointStore((s) => s.activeBreakpoint)

  const node = useAppDocumentStore((s) =>
    selectedId ? s.nodes[selectedId] : null
  )

  const updateNodeProps = useAppDocumentStore((s) => s.updateNodeProps)
  const updateNodeBreakpointProps = useAppDocumentStore((s) => s.updateNodeBreakpointProps)
  const updateNodeStateStyle = useAppDocumentStore((s) => s.updateNodeStateStyle)

  const def = useMemo(
    () => (node ? getDefinition(node.type) : null),
    [node?.type]
  )

  const handleChange = useCallback(
    (name: string, value: unknown) => {
      if (!selectedId) return

      if (activeElementState !== 'default' && STYLE_PROPS.has(name)) {
        updateNodeStateStyle(selectedId, activeElementState, { [name]: value })
      } else if (activeBreakpoint !== 'desktop' && STYLE_PROPS.has(name)) {
        updateNodeBreakpointProps(selectedId, activeBreakpoint, { [name]: value })
      } else {
        updateNodeProps(selectedId, { [name]: value })
      }
    },
    [selectedId, updateNodeProps, updateNodeBreakpointProps, updateNodeStateStyle, activeBreakpoint, activeElementState]
  )

  // Get the effective value for a prop (checking state/breakpoint overrides)
  const getEffectiveValue = useCallback(
    (propName: string) => {
      if (!node) return undefined

      // Check state override
      if (activeElementState !== 'default') {
        const stateVal = node.stateStyles?.[activeElementState]?.[propName]
        if (stateVal !== undefined) return stateVal
      }

      // Check breakpoint override
      if (activeBreakpoint !== 'desktop') {
        const bpVal = node.breakpointOverrides?.[activeBreakpoint]?.[propName]
        if (bpVal !== undefined) return bpVal
      }

      return node.props[propName]
    },
    [node, activeBreakpoint, activeElementState]
  )

  // Check if a prop has an override at current breakpoint/state
  const hasOverride = useCallback(
    (propName: string) => {
      if (!node) return false
      if (activeElementState !== 'default') {
        return node.stateStyles?.[activeElementState]?.[propName] !== undefined
      }
      if (activeBreakpoint !== 'desktop') {
        return node.breakpointOverrides?.[activeBreakpoint]?.[propName] !== undefined
      }
      return false
    },
    [node, activeBreakpoint, activeElementState]
  )

  // Group fields by section
  const sections = useMemo(() => {
    if (!def) return {}
    const grouped: Record<string, PropField[]> = {}
    for (const field of def.propSchema) {
      if (!grouped[field.section]) grouped[field.section] = []
      grouped[field.section].push(field)
    }
    return grouped
  }, [def])

  // Check if this component has spacing props (for box model editor)
  const hasSpacingProps = node?.props && ('paddingTop' in node.props || 'marginTop' in node.props)

  // Multi-select
  if (selectedNodeIds.length > 1) {
    return (
      <div className="h-full flex flex-col items-center justify-center px-6 text-center">
        <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center mb-3">
          <Component size={16} className="text-primary" />
        </div>
        <p className="text-[12px] font-semibold text-foreground mb-1">
          {selectedNodeIds.length} components selected
        </p>
        <p className="text-[10px] text-muted-foreground/60 leading-relaxed">
          Select a single component to edit its properties
        </p>
      </div>
    )
  }

  if (!node || !def) {
    return (
      <div className="h-full flex flex-col items-center justify-center px-6 text-center">
        <div className="w-10 h-10 rounded-full bg-muted/60 flex items-center justify-center mb-3">
          <MousePointer2 size={16} className="text-muted-foreground/60" />
        </div>
        <p className="text-[11px] font-medium text-muted-foreground mb-1">
          No component selected
        </p>
        <p className="text-[10px] text-muted-foreground/60 leading-relaxed">
          Click a component on the canvas to inspect and edit its properties
        </p>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      {/* Component identity header */}
      <div className="px-3 py-2.5 border-b border-border/60 shrink-0 bg-muted/30">
        <div className="flex items-center gap-2.5">
          <div className="w-6 h-6 rounded-md bg-primary/10 flex items-center justify-center shrink-0">
            <Component size={12} className="text-primary" />
          </div>
          <div className="min-w-0">
            <p className="text-[12px] font-semibold text-foreground leading-tight">
              {def.meta.displayName}
            </p>
            <p className="text-[10px] text-muted-foreground/70 font-mono truncate">
              {node.id}
            </p>
          </div>
        </div>
      </div>

      {/* Style class selector */}
      <StyleClassSelector nodeId={selectedId!} />

      {/* Element state selector */}
      <StateSelector />

      {/* Property controls */}
      <div className="flex-1 overflow-y-auto">
        {/* Exposed state toggles (e.g. Modal open, Tooltip visible) */}
        <ExposedStateToggles nodeId={selectedId!} />

        {/* Box model editor for components with spacing */}
        {hasSpacingProps && activeElementState === 'default' && (
          <BoxModelEditor nodeId={selectedId!} />
        )}

        {def.SettingsPanel ? (
          <def.SettingsPanel nodeId={selectedId!} />
        ) : (
          Object.entries(sections).map(([section, fields]) => (
            <ToolbarSection key={section} title={section}>
              {fields.map((field) => (
                <div key={field.name} className="relative">
                  <label className="text-[11px] text-muted-foreground mb-1 flex items-center gap-1">
                    {field.label}
                    {hasOverride(field.name) && (
                      <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" title="Has override" />
                    )}
                    {isExpression(node?.props[field.name]) && (
                      <Zap size={10} className="text-amber-500 shrink-0" />
                    )}
                  </label>
                  <PropControl
                    field={field}
                    value={getEffectiveValue(field.name)}
                    onChange={handleChange}
                  />
                </div>
              ))}
            </ToolbarSection>
          ))
        )}
      </div>
    </div>
  )
}
