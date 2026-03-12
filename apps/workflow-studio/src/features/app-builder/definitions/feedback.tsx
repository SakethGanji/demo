import { useMemo, useEffect, useRef, useCallback } from 'react'
import type { RendererProps } from '../types'
import { defineComponent, registerComponent, shadowMap } from '../types'
import { ToolbarSection, ToolbarItem } from '../inspector'
import { useComponentState } from '../hooks'
import { useRuntimeStateStore } from '../stores'
import { useAppEditorStore } from '../stores'
import { Button } from './input'

/* ═══════════════════════════════════════════════════════════════
   Modal
   ═══════════════════════════════════════════════════════════════ */

interface ModalProps {
  triggerText: string
  triggerVariant: 'default' | 'secondary' | 'outline' | 'ghost'
  title: string
  maxWidth: string
  paddingTop: string
  paddingRight: string
  paddingBottom: string
  paddingLeft: string
  borderRadius: string
  shadow: string
  showClose: boolean
}

const ModalComponent = ({ id, props, children, onEvent }: RendererProps<ModalProps>) => {
  const { value: isOpen, setValue: setOpen } = useComponentState<boolean>(id, 'open', false)

  const hasChildren = Array.isArray(children)
    ? children.length > 0
    : !!children

  const open = (e: React.MouseEvent) => {
    e.stopPropagation()
    setOpen(true)
    onEvent?.('onOpen')
  }

  const close = (e: React.MouseEvent) => {
    e.stopPropagation()
    setOpen(false)
    onEvent?.('onClose')
  }

  return (
    <>
      <Button
        variant={props.triggerVariant}
        onClick={open}
      >
        {props.triggerText}
      </Button>

      {isOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          onClick={close}
        >
          <div className="fixed inset-0 bg-black/50" />
          <div
            className="relative z-50 bg-background border"
            onClick={(e) => e.stopPropagation()}
            style={{
              maxWidth: props.maxWidth || '500px',
              width: '90vw',
              padding: `${props.paddingTop || 24}px ${props.paddingRight || 24}px ${props.paddingBottom || 24}px ${props.paddingLeft || 24}px`,
              borderRadius: props.borderRadius ? `${props.borderRadius}px` : '12px',
              boxShadow: shadowMap[props.shadow] || shadowMap['3'],
            }}
          >
            {props.showClose && (
              <button
                onClick={close}
                className="absolute top-3 right-3 text-muted-foreground hover:text-foreground transition-colors"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            )}
            {props.title && (
              <div className="text-lg font-semibold mb-4">{props.title}</div>
            )}
            {hasChildren ? children : (
              <div className="flex items-center justify-center py-6 text-xs text-muted-foreground/60 border border-dashed border-border/60 rounded-md">
                Drop components here
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}

function ModalSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Trigger">
        <ToolbarItem nodeId={nodeId} propKey="triggerText" label="Button Text" type="text" />
        <ToolbarItem
          nodeId={nodeId}
          propKey="triggerVariant"
          label="Variant"
          type="radio"
          options={[
            { label: 'Primary', value: 'default' },
            { label: 'Secondary', value: 'secondary' },
            { label: 'Outline', value: 'outline' },
            { label: 'Ghost', value: 'ghost' },
          ]}
        />
      </ToolbarSection>
      <ToolbarSection title="Modal">
        <ToolbarItem nodeId={nodeId} propKey="title" label="Title" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="maxWidth" label="Max Width" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="showClose" label="Show Close" type="switch" />
      </ToolbarSection>
      <ToolbarSection title="Spacing" defaultOpen={false}>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
          <ToolbarItem nodeId={nodeId} propKey="paddingTop" label="Top" type="slider" max={60} />
          <ToolbarItem nodeId={nodeId} propKey="paddingBottom" label="Bottom" type="slider" max={60} />
          <ToolbarItem nodeId={nodeId} propKey="paddingLeft" label="Left" type="slider" max={60} />
          <ToolbarItem nodeId={nodeId} propKey="paddingRight" label="Right" type="slider" max={60} />
        </div>
      </ToolbarSection>
      <ToolbarSection title="Style" defaultOpen={false}>
        <ToolbarItem nodeId={nodeId} propKey="borderRadius" label="Radius" type="slider" max={32} />
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
      </ToolbarSection>
    </>
  )
}

const modalDefinition = defineComponent<ModalProps>({
  type: 'Modal',
  meta: {
    displayName: 'Modal',
    icon: 'PanelTop',
    category: 'feedback',
    isContainer: true,
    defaultProps: {
      triggerText: 'Open Modal',
      triggerVariant: 'default',
      title: 'Modal Title',
      maxWidth: '500px',
      paddingTop: '24',
      paddingRight: '24',
      paddingBottom: '24',
      paddingLeft: '24',
      borderRadius: '12',
      shadow: '3',
      showClose: true,
    },
  },
  propSchema: [
    { name: 'triggerText', label: 'Button Text', section: 'Trigger', control: 'text', defaultValue: 'Open Modal' },
    { name: 'title', label: 'Title', section: 'Modal', control: 'text', defaultValue: 'Modal Title' },
    { name: 'maxWidth', label: 'Max Width', section: 'Modal', control: 'text', defaultValue: '500px' },
    { name: 'showClose', label: 'Show Close', section: 'Modal', control: 'switch', defaultValue: true },
  ],
  eventSchema: [
    { name: 'onOpen', label: 'On Open' },
    { name: 'onClose', label: 'On Close' },
  ],
  exposedState: [
    { name: 'open', label: 'Is Open', defaultValue: false },
  ],
  Component: ModalComponent,
  SettingsPanel: ModalSettings,
  rules: {
    // Prevent nesting Modals inside other Modals
    canMoveIn: (incoming) => incoming.type !== 'Modal',
  },
})

registerComponent(modalDefinition)

/* ═══════════════════════════════════════════════════════════════
   Tabs
   ═══════════════════════════════════════════════════════════════ */

interface TabsProps {
  tabs: string
  defaultTab: string
  variant: 'default' | 'outline' | 'pills'
  fullWidth: boolean
}

const TabsComponent = ({ id, props, children, onEvent }: RendererProps<TabsProps>) => {
  const tabList = useMemo(
    () => (props.tabs || '').split(',').map((t) => t.trim()).filter(Boolean),
    [props.tabs]
  )
  const { value: rawActiveTab, setValue: setActiveTab } = useComponentState<string>(
    id, 'activeTab', props.defaultTab ?? tabList[0] ?? ''
  )

  // If stored value is empty or not in the list, fall back to defaultTab or first tab
  const activeTab = (rawActiveTab && tabList.includes(rawActiveTab))
    ? rawActiveTab
    : (props.defaultTab && tabList.includes(props.defaultTab) ? props.defaultTab : tabList[0] ?? '')

  // Find active tab index — each tab corresponds to a child panel container
  const activeIndex = Math.max(0, tabList.indexOf(activeTab))

  // Get the child panel for the active tab
  const childArray = Array.isArray(children) ? children : children ? [children] : []

  const variantClasses = {
    default: 'border-b-2 border-transparent data-[active=true]:border-primary data-[active=true]:text-foreground',
    outline: 'border border-transparent rounded-md data-[active=true]:border-border data-[active=true]:bg-background',
    pills: 'rounded-md data-[active=true]:bg-primary data-[active=true]:text-primary-foreground',
  }

  return (
    <div className="w-full">
      <div className={`flex gap-1 ${props.variant === 'default' ? 'border-b border-border' : ''} mb-3`}>
        {tabList.map((tab) => (
          <button
            key={tab}
            data-active={activeTab === tab}
            onClick={(e) => {
              e.stopPropagation()
              setActiveTab(tab)
              onEvent?.('onChange', tab)
            }}
            className={[
              'px-3 py-1.5 text-sm font-medium text-muted-foreground transition-colors cursor-pointer hover:text-foreground',
              variantClasses[props.variant] || variantClasses.default,
              props.fullWidth ? 'flex-1' : '',
            ].filter(Boolean).join(' ')}
          >
            {tab}
          </button>
        ))}
      </div>
      <div>
        {childArray.length > 0 ? (
          // Only render the active tab's panel container
          childArray[activeIndex] ?? childArray[0]
        ) : (
          <div className="flex items-center justify-center py-8 text-xs text-muted-foreground/60 border border-dashed border-border/60 rounded-md">
            Drop components here
          </div>
        )}
      </div>
    </div>
  )
}

function TabsSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Content">
        <ToolbarItem nodeId={nodeId} propKey="tabs" label="Tabs (comma sep)" type="text" />
        <ToolbarItem nodeId={nodeId} propKey="defaultTab" label="Default Tab" type="text" />
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem
          nodeId={nodeId}
          propKey="variant"
          label="Variant"
          type="radio"
          options={[
            { label: 'Underline', value: 'default' },
            { label: 'Outline', value: 'outline' },
            { label: 'Pills', value: 'pills' },
          ]}
        />
        <ToolbarItem nodeId={nodeId} propKey="fullWidth" label="Full Width" type="switch" />
      </ToolbarSection>
    </>
  )
}

const tabsDefinition = defineComponent<TabsProps>({
  type: 'Tabs',
  meta: {
    displayName: 'Tabs',
    icon: 'LayoutList',
    category: 'navigation',
    isContainer: true,
    defaultProps: {
      tabs: 'Tab 1, Tab 2, Tab 3',
      defaultTab: 'Tab 1',
      variant: 'default',
      fullWidth: false,
    },
    // Auto-create one Container per tab as panel
    defaultChildren: [
      { type: 'Container', props: { __label: 'Tab 1 Panel', paddingTop: '8', paddingRight: '8', paddingBottom: '8', paddingLeft: '8', gap: '8', minHeight: '60px' } },
      { type: 'Container', props: { __label: 'Tab 2 Panel', paddingTop: '8', paddingRight: '8', paddingBottom: '8', paddingLeft: '8', gap: '8', minHeight: '60px' } },
      { type: 'Container', props: { __label: 'Tab 3 Panel', paddingTop: '8', paddingRight: '8', paddingBottom: '8', paddingLeft: '8', gap: '8', minHeight: '60px' } },
    ],
  },
  propSchema: [
    { name: 'tabs', label: 'Tabs', section: 'Content', control: 'text', defaultValue: 'Tab 1, Tab 2, Tab 3' },
    { name: 'defaultTab', label: 'Default Tab', section: 'Content', control: 'text', defaultValue: 'Tab 1' },
    { name: 'variant', label: 'Variant', section: 'Style', control: 'select', defaultValue: 'default', options: [{ label: 'Underline', value: 'default' }, { label: 'Outline', value: 'outline' }, { label: 'Pills', value: 'pills' }] },
    { name: 'fullWidth', label: 'Full Width', section: 'Style', control: 'switch', defaultValue: false },
  ],
  eventSchema: [
    { name: 'onChange', label: 'On Tab Change' },
  ],
  exposedState: [
    { name: 'activeTab', label: 'Active Tab', defaultValue: '' },
  ],
  Component: TabsComponent,
  SettingsPanel: TabsSettings,
  rules: {
    // Prevent nesting Tabs inside Tabs
    canMoveIn: (incoming) => incoming.type !== 'Tabs',
  },
})

registerComponent(tabsDefinition)

/* ═══════════════════════════════════════════════════════════════
   Table
   ═══════════════════════════════════════════════════════════════ */

/**
 * Table — purpose-built data table component.
 *
 * Set `data` to an array of objects. Optionally specify columns
 * (comma-separated field names). Auto-detects columns from data if omitted.
 */

interface TableProps {
  data: string
  columns: string
  striped: boolean
  bordered: boolean
  compact: boolean
  headerBg: string
  hoverRow: boolean
  emptyText: string
  maxHeight: string
}

interface Row {
  [key: string]: unknown
}

function parseColumns(columnsProp: string, data: Row[]): string[] {
  if (columnsProp && columnsProp.trim()) {
    return columnsProp.split(',').map((c) => c.trim()).filter(Boolean)
  }
  // Auto-detect from first row
  if (data.length > 0) {
    return Object.keys(data[0])
  }
  return []
}

function formatHeader(field: string): string {
  // snake_case or camelCase → Title Case
  return field
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '\u2014'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'object') {
    try { return JSON.stringify(value) } catch { return String(value) }
  }
  return String(value)
}

const TableComponent = ({ props }: RendererProps<TableProps>) => {
  let rows: Row[] = []
  if (Array.isArray(props.data)) {
    rows = props.data as Row[]
  } else if (typeof props.data === 'string') {
    try { rows = JSON.parse(props.data) } catch { /* empty */ }
  }

  const columns = parseColumns(props.columns as string, rows)
  const compact = props.compact
  const striped = props.striped
  const bordered = props.bordered
  const hoverRow = props.hoverRow !== false
  const cellPad = compact ? '6px 10px' : '10px 14px'
  const fontSize = compact ? '12px' : '13px'
  const headerBg = props.headerBg || 'var(--muted, #f5f5f5)'
  const maxHeight = props.maxHeight || undefined

  if (rows.length === 0 && columns.length === 0) {
    return (
      <div style={{
        padding: '32px 16px',
        textAlign: 'center',
        fontSize: '13px',
        color: 'var(--muted-foreground, #999)',
      }}>
        {props.emptyText || 'No data'}
      </div>
    )
  }

  return (
    <div style={{
      width: '100%',
      overflow: 'auto',
      maxHeight,
      borderRadius: 'var(--radius, 6px)',
      border: bordered ? '1px solid var(--border, #e5e7eb)' : undefined,
    }}>
      <table style={{
        width: '100%',
        borderCollapse: 'collapse',
        fontSize,
      }}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col}
                style={{
                  padding: cellPad,
                  textAlign: 'left',
                  fontWeight: 600,
                  fontSize: compact ? '11px' : '12px',
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px',
                  color: 'var(--muted-foreground, #666)',
                  backgroundColor: headerBg,
                  borderBottom: '1px solid var(--border, #e5e7eb)',
                  position: 'sticky',
                  top: 0,
                  zIndex: 1,
                  whiteSpace: 'nowrap',
                }}
              >
                {formatHeader(col)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              style={{
                backgroundColor: striped && i % 2 === 1
                  ? 'var(--muted, rgba(0,0,0,0.02))'
                  : undefined,
                transition: hoverRow ? 'background-color 0.1s' : undefined,
              }}
              onMouseEnter={(e) => {
                if (hoverRow) (e.currentTarget.style.backgroundColor = 'var(--accent, rgba(0,0,0,0.04))')
              }}
              onMouseLeave={(e) => {
                if (hoverRow) {
                  e.currentTarget.style.backgroundColor = striped && i % 2 === 1
                    ? 'var(--muted, rgba(0,0,0,0.02))'
                    : ''
                }
              }}
            >
              {columns.map((col) => (
                <td
                  key={col}
                  style={{
                    padding: cellPad,
                    borderBottom: '1px solid var(--border, #e5e7eb)',
                    color: 'var(--foreground, #1a1a1a)',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    maxWidth: '300px',
                  }}
                >
                  {formatCell(row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TableSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Data">
        <ToolbarItem nodeId={nodeId} propKey="data" label="Data Source" type="text" placeholder="{{ stores.users }}" />
        <ToolbarItem nodeId={nodeId} propKey="columns" label="Columns" type="text" placeholder="auto-detect, or: name, email, role" />
        <ToolbarItem nodeId={nodeId} propKey="emptyText" label="Empty Text" type="text" placeholder="No data" />
      </ToolbarSection>
      <ToolbarSection title="Style">
        <ToolbarItem nodeId={nodeId} propKey="striped" label="Striped Rows" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="bordered" label="Bordered" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="compact" label="Compact" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="hoverRow" label="Hover Highlight" type="switch" />
        <ToolbarItem nodeId={nodeId} propKey="headerBg" label="Header Color" type="color" />
      </ToolbarSection>
      <ToolbarSection title="Dimensions" defaultOpen={false}>
        <ToolbarItem nodeId={nodeId} propKey="maxHeight" label="Max Height" type="text" placeholder="auto, or 400px" />
      </ToolbarSection>
    </>
  )
}

const tableDefinition = defineComponent<TableProps>({
  type: 'Table',
  meta: {
    displayName: 'Table',
    icon: 'Table',
    category: 'data',
    defaultProps: {
      data: '',
      columns: '',
      striped: true,
      bordered: true,
      compact: false,
      headerBg: '',
      hoverRow: true,
      emptyText: 'No data',
      maxHeight: '',
    },
  },
  propSchema: [
    { name: 'data', label: 'Data Source', section: 'Data', control: 'expression', defaultValue: '' },
    { name: 'columns', label: 'Columns', section: 'Data', control: 'text', defaultValue: '' },
  ],
  eventSchema: [],
  exposedState: [],
  Component: TableComponent,
  SettingsPanel: TableSettings,
})

registerComponent(tableDefinition)

/* ═══════════════════════════════════════════════════════════════
   AlertHost
   ═══════════════════════════════════════════════════════════════ */

interface AlertItem {
  id: string
  message: string
  variant: 'success' | 'error' | 'warning' | 'info'
  timestamp: number
}

interface AlertHostProps {
  store: string
  position: 'top-right' | 'top-left' | 'top-center' | 'bottom-right' | 'bottom-left' | 'bottom-center'
  duration: number
  maxVisible: number
}

const positionStyles: Record<string, React.CSSProperties> = {
  'top-right': { top: 16, right: 16, alignItems: 'flex-end' },
  'top-left': { top: 16, left: 16, alignItems: 'flex-start' },
  'top-center': { top: 16, left: '50%', transform: 'translateX(-50%)', alignItems: 'center' },
  'bottom-right': { bottom: 16, right: 16, alignItems: 'flex-end' },
  'bottom-left': { bottom: 16, left: 16, alignItems: 'flex-start' },
  'bottom-center': { bottom: 16, left: '50%', transform: 'translateX(-50%)', alignItems: 'center' },
}

const variantStyles: Record<string, { bg: string; border: string; text: string; icon: string }> = {
  success: { bg: '#f0fdf4', border: '#bbf7d0', text: '#166534', icon: '\u2713' },
  error: { bg: '#fef2f2', border: '#fecaca', text: '#991b1b', icon: '\u2715' },
  warning: { bg: '#fffbeb', border: '#fed7aa', text: '#92400e', icon: '!' },
  info: { bg: '#eff6ff', border: '#bfdbfe', text: '#1e40af', icon: 'i' },
}

const AlertHostComponent = ({ props }: RendererProps<AlertHostProps>) => {
  const isEditMode = useAppEditorStore((s) => s.mode === 'edit')
  const storeName = props.store || 'alerts'
  const alerts = useRuntimeStateStore((s) => {
    const val = s.globalStores[storeName]
    return Array.isArray(val) ? (val as AlertItem[]) : []
  })
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  const removeAlert = useCallback((id: string) => {
    const store = useRuntimeStateStore.getState()
    const current = store.globalStores[storeName]
    if (Array.isArray(current)) {
      const idx = (current as AlertItem[]).findIndex((a) => a.id === id)
      if (idx !== -1) {
        store.removeFromArray(storeName, '', idx)
      }
    }
    timersRef.current.delete(id)
  }, [storeName])

  // Auto-dismiss
  const duration = props.duration || 4000
  useEffect(() => {
    for (const alert of alerts) {
      if (!timersRef.current.has(alert.id)) {
        const timer = setTimeout(() => removeAlert(alert.id), duration)
        timersRef.current.set(alert.id, timer)
      }
    }
    // Clean up timers for removed alerts
    for (const [id, timer] of timersRef.current) {
      if (!alerts.find((a) => a.id === id)) {
        clearTimeout(timer)
        timersRef.current.delete(id)
      }
    }
  }, [alerts, duration, removeAlert])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      for (const timer of timersRef.current.values()) {
        clearTimeout(timer)
      }
    }
  }, [])

  const maxVisible = props.maxVisible || 5
  const visible = alerts.slice(-maxVisible)
  const pos = positionStyles[props.position] || positionStyles['top-right']

  // Edit mode placeholder
  if (isEditMode) {
    return (
      <div
        style={{
          padding: '12px 16px',
          borderRadius: 8,
          border: '1px dashed var(--border)',
          background: 'var(--muted)',
          fontSize: 12,
          color: 'var(--muted-foreground)',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <span style={{ fontSize: 16 }}>&#128276;</span>
        <span>AlertHost &mdash; {props.position || 'top-right'} &middot; store: {storeName}</span>
      </div>
    )
  }

  if (visible.length === 0) return null

  return (
    <div
      style={{
        position: 'fixed',
        zIndex: 9999,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        pointerEvents: 'none',
        maxWidth: 400,
        width: '100%',
        ...pos,
      }}
    >
      {visible.map((alert) => {
        const v = variantStyles[alert.variant] || variantStyles.info
        return (
          <div
            key={alert.id}
            style={{
              pointerEvents: 'auto',
              display: 'flex',
              alignItems: 'flex-start',
              gap: 10,
              padding: '12px 16px',
              borderRadius: 8,
              backgroundColor: v.bg,
              border: `1px solid ${v.border}`,
              color: v.text,
              fontSize: 14,
              lineHeight: '1.4',
              boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
              animation: 'alertSlideIn 0.2s ease-out',
            }}
          >
            <span
              style={{
                width: 20,
                height: 20,
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 11,
                fontWeight: 700,
                backgroundColor: v.border,
                color: v.text,
                flexShrink: 0,
                marginTop: 1,
              }}
            >
              {v.icon}
            </span>
            <span style={{ flex: 1 }}>{alert.message}</span>
            <button
              onClick={() => removeAlert(alert.id)}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: v.text,
                opacity: 0.6,
                fontSize: 16,
                lineHeight: 1,
                padding: 0,
                flexShrink: 0,
              }}
            >
              &times;
            </button>
          </div>
        )
      })}
      <style>{`
        @keyframes alertSlideIn {
          from { opacity: 0; transform: translateY(-8px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  )
}

function AlertHostSettings({ nodeId }: { nodeId: string }) {
  return (
    <>
      <ToolbarSection title="Data">
        <ToolbarItem nodeId={nodeId} propKey="store" label="Store" type="text" />
      </ToolbarSection>
      <ToolbarSection title="Display">
        <ToolbarItem
          nodeId={nodeId}
          propKey="position"
          label="Position"
          type="select"
          options={[
            { label: 'Top Right', value: 'top-right' },
            { label: 'Top Left', value: 'top-left' },
            { label: 'Top Center', value: 'top-center' },
            { label: 'Bottom Right', value: 'bottom-right' },
            { label: 'Bottom Left', value: 'bottom-left' },
            { label: 'Bottom Center', value: 'bottom-center' },
          ]}
        />
        <ToolbarItem nodeId={nodeId} propKey="duration" label="Duration (ms)" type="number" />
        <ToolbarItem nodeId={nodeId} propKey="maxVisible" label="Max Visible" type="number" />
      </ToolbarSection>
    </>
  )
}

const alertHostDefinition = defineComponent<AlertHostProps>({
  type: 'AlertHost',
  meta: {
    displayName: 'Alert Host',
    icon: 'Bell',
    category: 'feedback',
    defaultProps: {
      store: 'alerts',
      position: 'top-right',
      duration: 4000,
      maxVisible: 5,
    },
  },
  propSchema: [
    { name: 'store', label: 'Store', section: 'Data', control: 'text', defaultValue: 'alerts' },
    { name: 'position', label: 'Position', section: 'Display', control: 'select', defaultValue: 'top-right', options: [{ label: 'Top Right', value: 'top-right' }, { label: 'Top Left', value: 'top-left' }, { label: 'Top Center', value: 'top-center' }, { label: 'Bottom Right', value: 'bottom-right' }, { label: 'Bottom Left', value: 'bottom-left' }, { label: 'Bottom Center', value: 'bottom-center' }] },
    { name: 'duration', label: 'Duration (ms)', section: 'Display', control: 'number', defaultValue: 4000, min: 500, max: 30000 },
    { name: 'maxVisible', label: 'Max Visible', section: 'Display', control: 'number', defaultValue: 5, min: 1, max: 10 },
  ],
  eventSchema: [],
  exposedState: [],
  Component: AlertHostComponent,
  SettingsPanel: AlertHostSettings,
})

registerComponent(alertHostDefinition)
