import { memo, useState, useCallback, useMemo, useRef, useEffect } from 'react'
import {
  ChevronRight,
  ChevronDown,
  GripVertical,
  Search,
  X,
  LayoutTemplate,
  Type,
  MousePointerClick,
  Play,
  Heading,
  ImageIcon,
  Minus,
  Square,
  TextCursorInput,
  AlignLeft,
  Tag,
  CircleUser,
  ToggleLeft,
  CaseSensitive,
  Upload,
  ExternalLink,
  CheckSquare,
  LayoutList,
  PanelTop,
  PanelLeft,
  ScrollText,
  Space,
  List,
  MessageSquare,
  Table,
  Bell,
  Zap,
  FileText,
  RotateCcw,
  Sun,
  Moon,
  Plus,
  Trash2,
  Copy,
  Send,
  Clock,
  ArrowRight,
  Pencil,
  Database,
  Component,
  Layers,
  Globe,
} from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { Button } from '@/shared/components/ui/button'
import { ValueEditor } from '@/shared/components/ui/value-editor'
import { ValueInspector } from '@/shared/components/ui/value-inspector'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import {
  useAppDocumentStore,
  useAppEditorStore,
  useRuntimeStateStore,
  useConsoleStore,
  type ConsoleEntry,
  useThemeStore,
  useActiveOverrides,
  THEME_PRESETS,
  type ThemeVar,
} from './stores'
import {
  getDefinition,
  getAllDefinitions,
  templates,
} from './types'
import type {
  AppTemplate,
  ComponentDefinition,
  ComponentTier,
  StoreDefinition,
  WebhookDefinition,
  TransformStep,
  FilterOp,
} from './types'
import { executeWebhook, type WebhookResult } from './runtime'

// ════════════════════════════════════════════════════════════════════════════════
// 1. LayerItem
// ════════════════════════════════════════════════════════════════════════════════

interface LayerItemProps {
  nodeId: string
  depth: number
  expandedIds: Set<string>
  onToggleExpand: (id: string) => void
  matchingIds: Set<string> | null
}

// Shared drag state (not reactive — just tracking)
let draggedNodeId: string | null = null

export const LayerItem = memo(function LayerItem({
  nodeId,
  depth,
  expandedIds,
  onToggleExpand,
  matchingIds,
}: LayerItemProps) {
  const node = useAppDocumentStore((s) => s.nodes[nodeId])
  const isSelected = useAppEditorStore((s) => s.selectedNodeIds.includes(nodeId))
  const isHovered = useAppEditorStore((s) => s.hoveredNodeId === nodeId)

  // Inline rename state
  const [isRenaming, setIsRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const renameRef = useRef<HTMLInputElement>(null)

  // Drag-over indicator: 'above' | 'inside' | 'below' | null
  const [dropPosition, setDropPosition] = useState<'above' | 'inside' | 'below' | null>(null)
  const rowRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (isRenaming && renameRef.current) {
      renameRef.current.focus()
      renameRef.current.select()
    }
  }, [isRenaming])

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      const editorState = useAppEditorStore.getState()
      if (e.metaKey || e.ctrlKey) {
        editorState.toggleSelectNode(nodeId)
      } else if (e.shiftKey) {
        const lastSelected = editorState.selectedNodeIds[editorState.selectedNodeIds.length - 1]
        if (lastSelected) {
          editorState.selectRange(lastSelected, nodeId, useAppDocumentStore.getState().nodes)
        } else {
          editorState.selectNode(nodeId)
        }
      } else {
        editorState.selectNode(nodeId)
      }
    },
    [nodeId]
  )

  const handleDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      if (!node || node.parentId === null) return
      const def = getDefinition(node.type)
      setRenameValue(node.props.__label as string || def?.meta.displayName || node.type)
      setIsRenaming(true)
    },
    [node]
  )

  const commitRename = useCallback(() => {
    const trimmed = renameValue.trim()
    if (trimmed) {
      useAppDocumentStore.getState().updateNodeProps(nodeId, { __label: trimmed })
    }
    setIsRenaming(false)
  }, [nodeId, renameValue])

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const editorState = useAppEditorStore.getState()
      if (!editorState.selectedNodeIds.includes(nodeId)) {
        editorState.selectNode(nodeId)
      }
      editorState.openContextMenu(e.clientX, e.clientY, nodeId)
    },
    [nodeId]
  )

  const handleMouseEnter = useCallback(() => {
    useAppEditorStore.getState().hoverNode(nodeId)
  }, [nodeId])

  const handleMouseLeave = useCallback(() => {
    useAppEditorStore.getState().hoverNode(null)
  }, [])

  const handleToggleVisibility = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      useAppDocumentStore.getState().toggleHidden(nodeId)
    },
    [nodeId]
  )

  // --- Drag handlers ---
  const handleDragStart = useCallback(
    (e: React.DragEvent) => {
      if (node?.parentId === null) {
        e.preventDefault()
        return
      }
      draggedNodeId = nodeId
      e.dataTransfer.effectAllowed = 'move'
      e.dataTransfer.setData('text/plain', nodeId)
    },
    [nodeId, node?.parentId]
  )

  const handleDragOver = useCallback(
    (e: React.DragEvent) => {
      if (!draggedNodeId || draggedNodeId === nodeId) return
      e.preventDefault()
      e.stopPropagation()

      const rect = rowRef.current?.getBoundingClientRect()
      if (!rect) return

      const y = e.clientY - rect.top
      const h = rect.height
      const def = getDefinition(node?.type || '')

      if (def?.meta.isContainer && y > h * 0.25 && y < h * 0.75) {
        setDropPosition('inside')
      } else if (y < h / 2) {
        setDropPosition('above')
      } else {
        setDropPosition('below')
      }
    },
    [nodeId, node?.type]
  )

  const handleDragLeave = useCallback(() => {
    setDropPosition(null)
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      setDropPosition(null)

      if (!draggedNodeId || draggedNodeId === nodeId || !node) return

      const docState = useAppDocumentStore.getState()

      // Prevent dropping a node into itself or its descendants
      const isDescendant = (parentId: string, childId: string): boolean => {
        const n = docState.nodes[parentId]
        if (!n) return false
        if (n.childIds.includes(childId)) return true
        return n.childIds.some((cid) => isDescendant(cid, childId))
      }
      if (isDescendant(draggedNodeId, nodeId)) return

      if (dropPosition === 'inside') {
        docState.moveNode(draggedNodeId, nodeId, 0)
      } else if (dropPosition === 'above' || dropPosition === 'below') {
        const parentId = node.parentId
        if (!parentId) return
        const parent = docState.nodes[parentId]
        if (!parent) return
        let idx = parent.childIds.indexOf(nodeId)
        if (dropPosition === 'below') idx += 1
        // Adjust if moving within same parent
        if (parent.childIds.includes(draggedNodeId)) {
          const fromIdx = parent.childIds.indexOf(draggedNodeId)
          if (fromIdx < idx) idx -= 1
        }
        docState.moveNode(draggedNodeId, parentId, Math.max(0, idx))
      }

      draggedNodeId = null
    },
    [nodeId, node, dropPosition]
  )

  const handleDragEnd = useCallback(() => {
    draggedNodeId = null
    setDropPosition(null)
  }, [])

  if (!node) return null

  const def = getDefinition(node.type)
  if (!def) return null

  const hasChildren = node.childIds.length > 0
  const isExpanded = expandedIds.has(nodeId)
  const isRoot = node.parentId === null
  const isHidden = node.hidden
  const hasVisExpr = typeof node.props.__visible === 'string' && (node.props.__visible as string).includes('{{')
  const displayName = (node.props.__label as string) || (isRoot ? 'Root' : def.meta.displayName)

  // When filtering, hide non-matching nodes that have no matching descendants
  if (matchingIds && !matchingIds.has(nodeId) && !hasChildren) return null
  if (matchingIds && !matchingIds.has(nodeId) && hasChildren) {
    const hasMatchingDescendant = node.childIds.some((childId) => {
      const check = (id: string): boolean => {
        if (matchingIds.has(id)) return true
        const child = useAppDocumentStore.getState().nodes[id]
        return child?.childIds.some(check) ?? false
      }
      return check(childId)
    })
    if (!hasMatchingDescendant) return null
  }

  const dimmed = matchingIds && !matchingIds.has(nodeId)

  // Drop indicator styles
  const dropIndicatorClass =
    dropPosition === 'above'
      ? 'border-t-2 border-t-primary'
      : dropPosition === 'below'
        ? 'border-b-2 border-b-primary'
        : dropPosition === 'inside'
          ? 'ring-1 ring-inset ring-primary bg-primary/5'
          : ''

  return (
    <>
      <div
        ref={rowRef}
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
        onContextMenu={handleContextMenu}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        draggable={!isRoot && !isRenaming}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onDragEnd={handleDragEnd}
        className={`group flex items-center gap-1 py-1 pr-2 cursor-pointer text-xs transition-colors duration-150 ${
          isSelected
            ? 'bg-primary/10 text-foreground'
            : isHovered
              ? 'bg-accent/50 text-foreground'
              : 'text-muted-foreground hover:text-foreground hover:bg-accent/30'
        } ${dropIndicatorClass}`}
        style={{ paddingLeft: depth * 16 + 4 }}
      >
        {/* Drag handle */}
        {!isRoot && (
          <GripVertical
            size={10}
            className="opacity-0 group-hover:opacity-40 shrink-0 cursor-grab active:cursor-grabbing"
          />
        )}
        {isRoot && <span className="w-[10px] shrink-0" />}

        {hasChildren ? (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onToggleExpand(nodeId)
            }}
            className="p-0.5 hover:bg-accent rounded shrink-0"
          >
            <ChevronRight
              size={12}
              className="transition-transform duration-150"
              style={{ transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }}
            />
          </button>
        ) : (
          <span className="w-5 shrink-0" />
        )}

        {/* Component type indicator */}
        <div
          className={`w-1.5 h-1.5 rounded-full shrink-0 ${
            isSelected ? 'bg-primary' : 'bg-muted-foreground/40'
          }`}
        />

        {/* Name (inline editable) */}
        {isRenaming ? (
          <input
            ref={renameRef}
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename()
              if (e.key === 'Escape') setIsRenaming(false)
            }}
            onClick={(e) => e.stopPropagation()}
            className="flex-1 min-w-0 text-[11px] font-medium bg-background border border-primary/50 rounded px-1 py-0 outline-none"
          />
        ) : (
          <span className={`truncate flex-1 text-[11px] font-medium ${isHidden ? 'opacity-40 line-through' : ''} ${dimmed ? 'opacity-50' : ''}`}>
            {displayName}
          </span>
        )}

        {/* Badges */}
        <div className="flex items-center gap-0.5 shrink-0">
          {hasVisExpr && (
            <span className="text-amber-500 text-[9px]" title="Conditional visibility">⚡</span>
          )}
          {def.meta.isContainer && (
            <span className="text-[9px] text-muted-foreground/60 font-medium">
              {node.childIds.length}
            </span>
          )}
          {/* Visibility toggle on hover */}
          {!isRoot && (
            <button
              onClick={handleToggleVisibility}
              className="opacity-0 group-hover:opacity-100 p-0.5 hover:bg-accent rounded transition-opacity"
              title={isHidden ? 'Show' : 'Hide'}
            >
              {isHidden ? (
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
                  <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
                  <line x1="1" y1="1" x2="23" y2="23" />
                </svg>
              ) : (
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
              )}
            </button>
          )}
        </div>
      </div>

      {isExpanded &&
        node.childIds.map((childId) => (
          <LayerItem
            key={childId}
            nodeId={childId}
            depth={depth + 1}
            expandedIds={expandedIds}
            onToggleExpand={onToggleExpand}
            matchingIds={matchingIds}
          />
        ))}
    </>
  )
})

// ════════════════════════════════════════════════════════════════════════════════
// 2. LayersPanel
// ════════════════════════════════════════════════════════════════════════════════

export function LayersPanel() {
  const rootNodeId = useAppDocumentStore((s) => s.rootNodeId)
  const nodes = useAppDocumentStore((s) => s.nodes)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(
    () => new Set([rootNodeId])
  )
  const [search, setSearch] = useState('')

  const handleToggleExpand = useCallback((id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  // Expand all ancestors of matching nodes when searching
  const { matchingIds, expandedForSearch } = useMemo(() => {
    if (!search.trim()) return { matchingIds: null, expandedForSearch: null }

    const query = search.toLowerCase()
    const matching = new Set<string>()
    const ancestors = new Set<string>()

    for (const [id, node] of Object.entries(nodes)) {
      const def = getDefinition(node.type)
      const name = def?.meta.displayName?.toLowerCase() || node.type.toLowerCase()
      const nodeId = id.toLowerCase()
      if (name.includes(query) || nodeId.includes(query)) {
        matching.add(id)
        // Walk up to root and expand ancestors
        let current = node.parentId
        while (current) {
          ancestors.add(current)
          current = nodes[current]?.parentId ?? null
        }
      }
    }

    return { matchingIds: matching, expandedForSearch: ancestors }
  }, [search, nodes])

  // When searching, use search-expanded set; otherwise use manual expanded set
  const effectiveExpanded = expandedForSearch
    ? new Set([...expandedForSearch, ...expandedIds])
    : expandedIds

  return (
    <div className="h-full flex flex-col">
      {/* Search bar */}
      <div className="px-2 py-1.5 border-b border-border/50 shrink-0">
        <div className="relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground/60" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter layers..."
            className="w-full h-7 pl-7 pr-7 text-[11px] bg-muted/50 border border-border/60 rounded-md outline-none focus:border-primary/50 text-foreground placeholder:text-muted-foreground/50"
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground/60 hover:text-foreground p-0.5"
            >
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto py-1">
        <LayerItem
          nodeId={rootNodeId}
          depth={0}
          expandedIds={effectiveExpanded}
          onToggleExpand={handleToggleExpand}
          matchingIds={matchingIds}
        />
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════════
// 3. ComponentPalette
// ════════════════════════════════════════════════════════════════════════════════

const paletteIconMap: Record<string, React.FC<{ size?: number | string; className?: string }>> = {
  LayoutTemplate,
  Type,
  MousePointerClick,
  Play,
  Heading,
  ImageIcon,
  Minus,
  Square,
  TextCursorInput,
  AlignLeft,
  Tag,
  CircleUser,
  ToggleLeft,
  CaseSensitive,
  Upload,
  ExternalLink,
  ChevronDown,
  CheckSquare,
  LayoutList,
  PanelTop,
  ScrollText,
  Space,
  List,
  MessageSquare,
  Table,
  PanelLeft,
  Bell,
  Zap,
  FileText,
}

const categoryLabels: Record<string, string> = {
  layout: 'Layout',
  content: 'Content',
  input: 'Input',
  data: 'Data',
  feedback: 'Feedback',
  navigation: 'Navigation',
}

const categoryOrder = ['layout', 'content', 'input', 'data', 'feedback', 'navigation']

const PALETTE_TABS: { key: ComponentTier; label: string }[] = [
  { key: 'component', label: 'Components' },
  { key: 'template', label: 'Templates' },
  { key: 'layout', label: 'Layouts' },
]

function PaletteItem({ def }: { def: ComponentDefinition }) {
  const Icon = paletteIconMap[def.meta.icon]

  const handleDragStart = useCallback(
    (e: React.DragEvent) => {
      e.dataTransfer.setData(
        'application/x-app-builder-new',
        def.type
      )
      useAppEditorStore.getState().setDragSource({
        type: 'new',
        componentType: def.type,
      })
    },
    [def.type]
  )

  const handleDragEnd = useCallback(() => {
    useAppEditorStore.getState().clearDrag()
  }, [])

  return (
    <div
      draggable
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      className="flex flex-col items-center gap-1.5 p-2.5 rounded-lg border border-transparent bg-muted/50 hover:bg-accent hover:border-border cursor-grab active:cursor-grabbing active:scale-95 transition-all duration-150 select-none"
    >
      {Icon && <Icon size={18} className="text-muted-foreground" />}
      <span className="text-[10px] font-medium text-foreground">
        {def.meta.displayName}
      </span>
    </div>
  )
}

function CategoryGroup({ category, defs, defaultOpen = true }: { category: string; defs: ComponentDefinition[]; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 w-full px-0.5 mb-1.5 group"
      >
        <ChevronRight
          size={12}
          className={cn(
            'text-muted-foreground transition-transform duration-150',
            open && 'rotate-90'
          )}
        />
        <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
          {categoryLabels[category] || category}
        </span>
        <span className="text-[10px] text-muted-foreground/60 ml-auto pr-0.5">
          {defs.length}
        </span>
      </button>
      {open && (
        <div className="grid grid-cols-2 gap-1.5">
          {defs.map((def) => (
            <PaletteItem key={def.type} def={def} />
          ))}
        </div>
      )}
    </div>
  )
}

function TemplateCard({ template }: { template: AppTemplate }) {
  const Icon = paletteIconMap[template.icon]

  const handleClick = useCallback(() => {
    const editorState = useAppEditorStore.getState()
    const docState = useAppDocumentStore.getState()
    // Insert into selected node or root
    const parentId = editorState.selectedNodeIds[0] || docState.rootNodeId
    const parent = docState.nodes[parentId]
    const targetParent = parent?.isCanvas ? parentId : (parent?.parentId || docState.rootNodeId)

    const newId = docState.insertTemplate(template, targetParent)
    if (newId) {
      editorState.selectNode(newId)
    }
  }, [template])

  return (
    <button
      onClick={handleClick}
      className="flex items-start gap-3 w-full p-3 rounded-lg border border-border bg-card hover:bg-accent hover:border-primary/30 transition-all duration-150 text-left group"
    >
      <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0 group-hover:bg-primary/20 transition-colors">
        {Icon && <Icon size={18} className="text-primary" />}
      </div>
      <div className="min-w-0">
        <p className="text-xs font-medium text-foreground">{template.name}</p>
        <p className="text-[10px] text-muted-foreground leading-relaxed mt-0.5">
          {template.description}
        </p>
      </div>
    </button>
  )
}

export function ComponentPalette() {
  const [search, setSearch] = useState('')
  const [activeTab, setActiveTab] = useState<ComponentTier>('component')
  // No deps — registry is populated by side-effect imports at module load time.
  // Safe to read on every render; the array reference is stable once registered.
  const definitions = getAllDefinitions()

  const filtered = useMemo(() => {
    let defs = definitions.filter(
      (def) => (def.meta.tier || 'component') === activeTab
    )
    if (search.trim()) {
      const q = search.toLowerCase()
      defs = defs.filter(
        (def) =>
          def.meta.displayName.toLowerCase().includes(q) ||
          def.type.toLowerCase().includes(q) ||
          def.meta.category.toLowerCase().includes(q)
      )
    }
    return defs
  }, [definitions, search, activeTab])

  const grouped = useMemo(() => {
    const groups: Record<string, ComponentDefinition[]> = {}
    for (const def of filtered) {
      const cat = def.meta.category
      if (!groups[cat]) groups[cat] = []
      groups[cat].push(def)
    }
    return groups
  }, [filtered])

  const isSearching = search.trim().length > 0

  return (
    <div className="h-full flex flex-col">
      {/* Tab bar */}
      <div className="flex border-b border-border shrink-0">
        {PALETTE_TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={cn(
              'flex-1 px-2 py-2 text-[11px] font-medium transition-colors relative',
              activeTab === tab.key
                ? 'text-foreground'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {tab.label}
            {activeTab === tab.key && (
              <div className="absolute bottom-0 left-2 right-2 h-0.5 bg-primary rounded-full" />
            )}
          </button>
        ))}
      </div>

      {/* Search */}
      <div className="px-2.5 pt-2.5 pb-2 shrink-0">
        <div className="relative">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search..."
            className="w-full h-8 pl-8 pr-3 text-[12px] rounded-md border border-border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
      </div>

      {/* Component grid */}
      <div className="flex-1 overflow-y-auto p-2.5 pt-0 space-y-3">
        {activeTab === 'template' ? (
          <div className="space-y-2">
            {templates.map((t) => (
              <TemplateCard key={t.id} template={t} />
            ))}
            {templates.length === 0 && (
              <div className="text-[11px] text-muted-foreground text-center py-8">
                No templates yet
              </div>
            )}
          </div>
        ) : activeTab === 'layout' ? (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
            <div className="w-10 h-10 rounded-full bg-muted flex items-center justify-center mb-3">
              <LayoutTemplate size={18} className="text-muted-foreground" />
            </div>
            <p className="text-xs font-medium text-foreground mb-1">Page Layouts</p>
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              Pre-built page templates like dashboards, landing pages, and admin panels. Coming soon.
            </p>
          </div>
        ) : (
          <>
            {categoryOrder.map((cat) => {
              const defs = grouped[cat]
              if (!defs?.length) return null
              return (
                <CategoryGroup
                  key={cat}
                  category={cat}
                  defs={defs}
                  defaultOpen={isSearching ? true : undefined}
                />
              )
            })}
            {filtered.length === 0 && (
              <div className="text-[11px] text-muted-foreground text-center py-8">
                No components found
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════════
// 4. ThemePanel
// ════════════════════════════════════════════════════════════════════════════════

// ── Shared controls ──

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
      {children}
    </div>
  )
}

function CollapsibleSection({
  title,
  defaultOpen = true,
  children,
}: {
  title: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="space-y-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 w-full text-left"
      >
        <ChevronDown
          size={10}
          className={cn(
            'text-muted-foreground transition-transform duration-150',
            !open && '-rotate-90'
          )}
        />
        <SectionLabel>{title}</SectionLabel>
      </button>
      {open && children}
    </div>
  )
}

function ColorSwatch({ varKey, label }: { varKey: ThemeVar; label?: string }) {
  const overrides = useActiveOverrides()
  const setVar = useThemeStore((s) => s.setVar)
  const value = overrides[varKey] || ''

  const handleColorChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setVar(varKey, e.target.value)
    },
    [varKey, setVar]
  )

  return (
    <div className="flex items-center gap-2">
      <label className="relative flex-shrink-0 cursor-pointer">
        <div
          className="w-7 h-7 rounded-md border border-border shadow-sm"
          style={{ backgroundColor: value || `var(--${varKey})` }}
        />
        <input
          type="color"
          value={value || '#000000'}
          onChange={handleColorChange}
          className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
        />
      </label>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] text-foreground truncate">
          {label || varKey}
        </div>
      </div>
      <input
        type="text"
        value={value}
        placeholder="default"
        onChange={(e) => setVar(varKey, e.target.value)}
        className="w-20 h-6 px-1.5 text-[10px] font-mono bg-muted/50 border border-border rounded text-foreground placeholder:text-muted-foreground/40 text-right"
        spellCheck={false}
      />
    </div>
  )
}

function SliderControl({
  varKey,
  label,
  min,
  max,
  step,
  unit,
}: {
  varKey: ThemeVar
  label: string
  min: number
  max: number
  step: number
  unit: string
}) {
  const overrides = useActiveOverrides()
  const setVar = useThemeStore((s) => s.setVar)
  const value = overrides[varKey] || ''
  const numValue = value ? parseFloat(value) : min

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-foreground">{label}</span>
        <div className="flex items-center gap-1">
          <input
            type="number"
            min={min}
            max={max}
            step={step}
            value={value ? parseFloat(value) : ''}
            placeholder={String(min)}
            onChange={(e) => {
              const v = parseFloat(e.target.value)
              if (!isNaN(v)) setVar(varKey, `${v}${unit}`)
            }}
            className="w-14 h-5 px-1 text-[10px] text-right font-mono bg-muted/50 border border-border rounded text-foreground"
          />
          <span className="text-[9px] text-muted-foreground w-5">{unit}</span>
        </div>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={numValue}
        onChange={(e) => setVar(varKey, `${e.target.value}${unit}`)}
        className="app-builder-slider"
      />
    </div>
  )
}

// ── Mode toggle ──

function ModeToggle() {
  const themeMode = useThemeStore((s) => s.mode)
  const toggleMode = useThemeStore((s) => s.toggleMode)

  return (
    <button
      onClick={toggleMode}
      className={cn(
        'flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[10px] font-medium transition-all',
        themeMode === 'dark'
          ? 'bg-slate-800 border-slate-600 text-slate-200'
          : 'bg-amber-50 border-amber-200 text-amber-800'
      )}
    >
      {themeMode === 'light' ? <Sun size={11} /> : <Moon size={11} />}
      {themeMode === 'light' ? 'Light' : 'Dark'}
    </button>
  )
}

// ── Preset selector ──

function PresetSelector() {
  const activePreset = useThemeStore((s) => s.activePreset)
  const applyPreset = useThemeStore((s) => s.applyPreset)
  const themeMode = useThemeStore((s) => s.mode)

  return (
    <div className="grid grid-cols-2 gap-2">
      {THEME_PRESETS.map((preset) => {
        const colors = themeMode === 'light' ? preset.light : preset.dark
        // Fallback to CSS defaults for Default preset
        const bg = colors.background || (themeMode === 'light' ? '#ffffff' : '#1a1a1a')
        const fg = colors.foreground || (themeMode === 'light' ? '#0a0a0a' : '#f0f0f0')
        const primary = colors.primary || (themeMode === 'light' ? '#171717' : '#e0e0e0')
        const secondary = colors.secondary || (themeMode === 'light' ? '#f5f5f5' : '#2a2a2a')
        const muted = colors.muted || (themeMode === 'light' ? '#f5f5f5' : '#2a2a2a')
        const accent = colors.accent || (themeMode === 'light' ? '#f5f5f5' : '#3a3a3a')
        const border = colors.border || (themeMode === 'light' ? '#e5e5e5' : '#333333')
        const radius = colors.radius || '0.625rem'

        return (
          <button
            key={preset.name}
            onClick={() => applyPreset(preset)}
            className={cn(
              'flex flex-col gap-1.5 p-2.5 rounded-lg border transition-all text-left',
              activePreset === preset.name
                ? 'border-primary ring-1 ring-primary/30'
                : 'border-border/50 hover:border-border'
            )}
          >
            {/* Mini preview card */}
            <div
              className="w-full rounded-md overflow-hidden p-2 space-y-1.5"
              style={{ backgroundColor: bg, border: `1px solid ${border}`, borderRadius: radius }}
            >
              {/* Fake heading */}
              <div className="h-1.5 w-10 rounded-full" style={{ backgroundColor: fg, opacity: 0.8 }} />
              {/* Fake text lines */}
              <div className="h-1 w-full rounded-full" style={{ backgroundColor: muted }} />
              <div className="h-1 w-3/4 rounded-full" style={{ backgroundColor: muted }} />
              {/* Fake buttons row */}
              <div className="flex gap-1 pt-0.5">
                <div className="h-3 w-8 rounded-sm" style={{ backgroundColor: primary, borderRadius: `calc(${radius} * 0.5)` }} />
                <div className="h-3 w-8 rounded-sm" style={{ backgroundColor: secondary, borderRadius: `calc(${radius} * 0.5)` }} />
              </div>
            </div>
            <span className="text-[10px] font-medium text-foreground leading-tight">{preset.name}</span>
          </button>
        )
      })}
    </div>
  )
}

// ── Tab: Colors ──

const COLOR_GROUPS: { label: string; keys: { key: ThemeVar; label: string }[]; defaultOpen?: boolean }[] = [
  {
    label: 'Page',
    defaultOpen: true,
    keys: [
      { key: 'background', label: 'Background' },
      { key: 'foreground', label: 'Text' },
    ],
  },
  {
    label: 'Primary',
    keys: [
      { key: 'primary', label: 'primary' },
      { key: 'primary-foreground', label: 'primary-foreground' },
    ],
  },
  {
    label: 'Secondary',
    keys: [
      { key: 'secondary', label: 'secondary' },
      { key: 'secondary-foreground', label: 'secondary-foreground' },
    ],
  },
  {
    label: 'Card',
    keys: [
      { key: 'card', label: 'card' },
      { key: 'card-foreground', label: 'card-foreground' },
    ],
  },
  {
    label: 'Muted',
    keys: [
      { key: 'muted', label: 'muted' },
      { key: 'muted-foreground', label: 'muted-foreground' },
    ],
  },
  {
    label: 'Accent',
    keys: [
      { key: 'accent', label: 'accent' },
      { key: 'accent-foreground', label: 'accent-foreground' },
    ],
  },
  {
    label: 'Destructive',
    defaultOpen: false,
    keys: [
      { key: 'destructive', label: 'destructive' },
      { key: 'destructive-foreground', label: 'destructive-foreground' },
    ],
  },
  {
    label: 'Border & Input',
    defaultOpen: false,
    keys: [
      { key: 'border', label: 'border' },
      { key: 'input', label: 'input' },
      { key: 'ring', label: 'ring' },
    ],
  },
  {
    label: 'Popover',
    defaultOpen: false,
    keys: [
      { key: 'popover', label: 'popover' },
      { key: 'popover-foreground', label: 'popover-foreground' },
    ],
  },
  {
    label: 'Sidebar',
    defaultOpen: false,
    keys: [
      { key: 'sidebar', label: 'sidebar' },
      { key: 'sidebar-foreground', label: 'sidebar-foreground' },
      { key: 'sidebar-primary', label: 'sidebar-primary' },
      { key: 'sidebar-primary-foreground', label: 'sidebar-primary-fg' },
      { key: 'sidebar-accent', label: 'sidebar-accent' },
      { key: 'sidebar-accent-foreground', label: 'sidebar-accent-fg' },
      { key: 'sidebar-border', label: 'sidebar-border' },
      { key: 'sidebar-ring', label: 'sidebar-ring' },
    ],
  },
  {
    label: 'Chart',
    defaultOpen: false,
    keys: [
      { key: 'chart-1', label: 'chart-1' },
      { key: 'chart-2', label: 'chart-2' },
      { key: 'chart-3', label: 'chart-3' },
      { key: 'chart-4', label: 'chart-4' },
      { key: 'chart-5', label: 'chart-5' },
    ],
  },
]

function ColorsTab() {
  return (
    <div className="space-y-3">
      {COLOR_GROUPS.map((group) => (
        <CollapsibleSection key={group.label} title={group.label} defaultOpen={group.defaultOpen !== false}>
          <div className="space-y-1.5 pl-3">
            {group.keys.map((item) => (
              <ColorSwatch key={item.key} varKey={item.key} label={item.label} />
            ))}
          </div>
        </CollapsibleSection>
      ))}
    </div>
  )
}

// ── Tab: Typography ──

const FONT_SUGGESTIONS = [
  'Inter, ui-sans-serif, system-ui, sans-serif',
  'Geist, ui-sans-serif, system-ui, sans-serif',
  'DM Sans, ui-sans-serif, sans-serif',
  'Poppins, ui-sans-serif, sans-serif',
  'Open Sans, ui-sans-serif, sans-serif',
  'Montserrat, ui-sans-serif, sans-serif',
]

const SERIF_SUGGESTIONS = [
  'Georgia, ui-serif, serif',
  'Merriweather, ui-serif, serif',
  'Playfair Display, ui-serif, serif',
  'Lora, ui-serif, serif',
]

const MONO_SUGGESTIONS = [
  'JetBrains Mono, ui-monospace, monospace',
  'Fira Code, ui-monospace, monospace',
  'IBM Plex Mono, ui-monospace, monospace',
  'Geist Mono, ui-monospace, monospace',
]

function FontSelect({
  varKey,
  label,
  suggestions,
  placeholder,
}: {
  varKey: ThemeVar
  label: string
  suggestions: string[]
  placeholder: string
}) {
  const overrides = useActiveOverrides()
  const setVar = useThemeStore((s) => s.setVar)
  const value = overrides[varKey] || ''
  const [showSuggestions, setShowSuggestions] = useState(false)

  return (
    <div className="space-y-1.5">
      <span className="text-[11px] text-foreground font-medium">{label}</span>
      <div className="relative">
        <input
          type="text"
          value={value}
          placeholder={placeholder}
          onChange={(e) => setVar(varKey, e.target.value)}
          onFocus={() => setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
          className="w-full h-7 px-2 text-[11px] font-mono bg-muted/50 border border-border rounded text-foreground placeholder:text-muted-foreground/50"
        />
        {showSuggestions && (
          <div className="absolute z-10 top-full left-0 right-0 mt-1 bg-popover border border-border rounded-md shadow-lg max-h-40 overflow-y-auto">
            {suggestions.map((font) => {
              const familyName = font.split(',')[0].trim()
              return (
                <button
                  key={font}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    setVar(varKey, font)
                    setShowSuggestions(false)
                  }}
                  className={cn(
                    'w-full text-left px-2 py-1.5 text-[11px] hover:bg-accent transition-colors',
                    value === font ? 'bg-accent text-accent-foreground' : 'text-foreground'
                  )}
                  style={{ fontFamily: font }}
                >
                  {familyName}
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function TypographyTab() {
  return (
    <div className="space-y-4">
      <CollapsibleSection title="Font Family">
        <div className="space-y-3 pl-3">
          <FontSelect varKey="font-sans" label="Sans-Serif" suggestions={FONT_SUGGESTIONS} placeholder="Inter, system-ui, sans-serif" />
          <FontSelect varKey="font-serif" label="Serif" suggestions={SERIF_SUGGESTIONS} placeholder="Georgia, serif" />
          <FontSelect varKey="font-mono" label="Monospace" suggestions={MONO_SUGGESTIONS} placeholder="JetBrains Mono, monospace" />
        </div>
      </CollapsibleSection>

      <CollapsibleSection title="Letter Spacing">
        <div className="pl-3">
          <SliderControl varKey="letter-spacing" label="Tracking" min={-0.5} max={0.5} step={0.025} unit="em" />
        </div>
      </CollapsibleSection>
    </div>
  )
}

// ── Tab: Other ──

function OtherTab() {
  return (
    <div className="space-y-4">
      <CollapsibleSection title="Border Radius">
        <div className="pl-3">
          <SliderControl varKey="radius" label="Radius" min={0} max={2} step={0.025} unit="rem" />
          <div className="flex flex-wrap gap-1 mt-2">
            {['0rem', '0.25rem', '0.5rem', '0.625rem', '0.75rem', '1rem'].map((r) => (
              <RadiusChip key={r} value={r} />
            ))}
          </div>
        </div>
      </CollapsibleSection>

      <CollapsibleSection title="Spacing">
        <div className="pl-3">
          <SliderControl varKey="spacing" label="Base Spacing" min={0.15} max={0.35} step={0.01} unit="rem" />
        </div>
      </CollapsibleSection>
    </div>
  )
}

function RadiusChip({ value }: { value: string }) {
  const overrides = useActiveOverrides()
  const setVar = useThemeStore((s) => s.setVar)
  const current = overrides.radius || ''

  return (
    <button
      onClick={() => setVar('radius', value)}
      className={cn(
        'px-2 py-0.5 text-[10px] rounded border transition-colors',
        current === value
          ? 'bg-primary text-primary-foreground border-primary'
          : 'bg-muted/50 text-muted-foreground border-transparent hover:border-border'
      )}
    >
      {value === '0rem' ? 'Sharp' : value}
    </button>
  )
}

// ── Main ThemePanel ──

const THEME_TABS = [
  { id: 'presets', label: 'Presets' },
  { id: 'colors', label: 'Colors' },
  { id: 'typography', label: 'Type' },
  { id: 'other', label: 'Other' },
] as const

type ThemeTab = (typeof THEME_TABS)[number]['id']

export function ThemePanel() {
  const [tab, setTab] = useState<ThemeTab>('presets')
  const reset = useThemeStore((s) => s.reset)
  const lightOverrides = useThemeStore((s) => s.lightOverrides)
  const darkOverrides = useThemeStore((s) => s.darkOverrides)
  const hasChanges = Object.keys(lightOverrides).length > 0 || Object.keys(darkOverrides).length > 0

  return (
    <div className="h-full flex flex-col bg-card">
      {/* Header */}
      <div className="shrink-0 border-b border-border">
        <div className="px-3 py-2 flex items-center justify-between">
          <ModeToggle />
          {hasChanges && (
            <button
              onClick={reset}
              className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
              title="Reset to defaults"
            >
              <RotateCcw size={10} />
              Reset
            </button>
          )}
        </div>
        <div className="flex px-1">
          {THEME_TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                'flex-1 px-2 py-1.5 text-[10px] font-medium transition-all duration-200 rounded-t',
                tab === t.id
                  ? 'text-foreground bg-muted/50'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-3">
        {tab === 'presets' && <PresetSelector />}
        {tab === 'colors' && <ColorsTab />}
        {tab === 'typography' && <TypographyTab />}
        {tab === 'other' && <OtherTab />}
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════════
// 5. StoresPanel
// ════════════════════════════════════════════════════════════════════════════════

const storeFieldClass = 'w-full h-7 px-2 text-[11px] font-mono bg-background border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-ring text-foreground placeholder:text-muted-foreground'

const STORE_PRESETS: { label: string; desc: string; value: unknown }[] = [
  { label: 'Object', desc: '{ key: value }', value: {} },
  { label: 'Array', desc: '[ items ]', value: [] },
  { label: 'String', desc: '"text"', value: '' },
  { label: 'Number', desc: '0', value: 0 },
  { label: 'Boolean', desc: 'true / false', value: false },
  { label: 'Null', desc: 'empty', value: null },
]

// ─── Store item ────────────────────────────────

function StoreItem({ def }: { def: StoreDefinition }) {
  const updateStoreDef = useAppDocumentStore((s) => s.updateStoreDefinition)
  const removeStoreDef = useAppDocumentStore((s) => s.removeStoreDefinition)

  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)

  const handleCopyRef = useCallback(() => {
    navigator.clipboard.writeText(`{{ stores.${def.name} }}`)
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }, [def.name])

  const typeBadge = def.initialValue === null ? 'null'
    : Array.isArray(def.initialValue) ? `[${(def.initialValue as unknown[]).length}]`
    : typeof def.initialValue === 'object' ? `{${Object.keys(def.initialValue as object).length}}`
    : typeof def.initialValue

  return (
    <div className="border-b border-border/50">
      <div
        className="flex items-center gap-1.5 px-3 py-2 cursor-pointer hover:bg-accent/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <ChevronRight
          size={11}
          className={cn('text-muted-foreground shrink-0 transition-transform duration-150', expanded && 'rotate-90')}
        />
        <span className="flex-1 text-[12px] font-medium text-foreground truncate">{def.name}</span>
        <span className="text-[9px] text-muted-foreground/60 font-mono shrink-0">{typeBadge}</span>
      </div>

      {expanded && (
        <div className="px-3 pb-3 pt-0.5 space-y-2">
          <div className="space-y-1">
            <label className="text-[10px] font-medium text-muted-foreground">Name</label>
            <input
              className={storeFieldClass}
              value={def.name}
              onChange={(e) => updateStoreDef(def.id, { name: e.target.value })}
              spellCheck={false}
            />
          </div>

          <ValueEditor
            value={def.initialValue}
            onChange={(v) => updateStoreDef(def.id, { initialValue: v })}
            label="Initial value"
            showTypeBadge
          />

          <div className="flex items-center justify-between pt-1">
            <button
              onClick={handleCopyRef}
              className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
            >
              <Copy size={10} />
              {copied ? 'Copied!' : `{{ stores.${def.name} }}`}
            </button>
            <button
              onClick={() => removeStoreDef(def.id)}
              className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-destructive transition-colors"
            >
              <Trash2 size={10} />
              Delete
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Stores panel ────────────────────────────────

export function StoresPanel() {
  const storeDefs = useAppDocumentStore((s) => s.storeDefinitions)
  const addStoreDef = useAppDocumentStore((s) => s.addStoreDefinition)

  const uniqueName = (base: string) => {
    const existing = new Set(storeDefs.map((s) => s.name))
    if (!existing.has(base)) return base
    let i = 2
    while (existing.has(`${base}${i}`)) i++
    return `${base}${i}`
  }

  const handleAdd = (initialValue: unknown) => {
    const id = `store_${Date.now().toString(36)}`
    addStoreDef({ id, name: uniqueName('store'), initialValue })
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 overflow-y-auto">
        {storeDefs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
            <p className="text-[12px] text-muted-foreground mb-3">
              Stores hold shared state that components read via <span className="font-mono text-[11px]">{'{{ stores.name }}'}</span> expressions.
            </p>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="h-7 text-[11px] gap-1.5">
                  <Plus size={12} />
                  Add Store
                  <ChevronDown size={10} className="text-muted-foreground" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="center">
                {STORE_PRESETS.map((p) => (
                  <DropdownMenuItem key={p.label} onClick={() => handleAdd(p.value)}>
                    <span className="flex-1">{p.label}</span>
                    <span className="text-[10px] text-muted-foreground font-mono ml-3">{p.desc}</span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ) : (
          <>
            {storeDefs.map((def) => <StoreItem key={def.id} def={def} />)}
            <div className="p-2.5">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full h-7 text-[11px] text-muted-foreground"
                  >
                    <Plus size={12} className="mr-1" />
                    Add Store
                    <ChevronDown size={10} className="ml-1 text-muted-foreground" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="center">
                  {STORE_PRESETS.map((p) => (
                    <DropdownMenuItem key={p.label} onClick={() => handleAdd(p.value)}>
                      <span className="flex-1">{p.label}</span>
                      <span className="text-[10px] text-muted-foreground font-mono ml-3">{p.desc}</span>
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════════
// 6. WebhooksPanel
// ════════════════════════════════════════════════════════════════════════════════

const webhookFieldClass = 'w-full h-7 px-2 text-[11px] font-mono bg-background border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-ring text-foreground placeholder:text-muted-foreground'
const selectClass = 'w-full h-7 px-2 text-[11px] bg-background border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-ring text-foreground'
const labelClass = 'text-[10px] font-medium text-muted-foreground'

const METHOD_COLORS: Record<string, string> = {
  GET: 'text-emerald-600 dark:text-emerald-400',
  POST: 'text-blue-600 dark:text-blue-400',
  PUT: 'text-amber-600 dark:text-amber-400',
  DELETE: 'text-red-600 dark:text-red-400',
  PATCH: 'text-purple-600 dark:text-purple-400',
}

const STATUS_COLORS: Record<string, string> = {
  '2': 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10',
  '3': 'text-blue-600 dark:text-blue-400 bg-blue-500/10',
  '4': 'text-amber-600 dark:text-amber-400 bg-amber-500/10',
  '5': 'text-red-600 dark:text-red-400 bg-red-500/10',
}

const STEP_TYPES = [
  { value: 'pick', label: 'Pick Field' },
  { value: 'filter', label: 'Filter' },
  { value: 'sort', label: 'Sort' },
  { value: 'map', label: 'Select Fields' },
  { value: 'slice', label: 'Slice' },
  { value: 'find', label: 'Find One' },
  { value: 'count', label: 'Count' },
  { value: 'expression', label: 'Expression' },
] as const

const FILTER_OPS: { value: FilterOp; label: string }[] = [
  { value: 'eq', label: '=' },
  { value: 'neq', label: '!=' },
  { value: 'gt', label: '>' },
  { value: 'lt', label: '<' },
  { value: 'gte', label: '>=' },
  { value: 'lte', label: '<=' },
  { value: 'contains', label: 'contains' },
  { value: 'startsWith', label: 'starts with' },
  { value: 'exists', label: 'exists' },
]

function defaultStep(type: string): TransformStep {
  switch (type) {
    case 'pick': return { type: 'pick', path: '' }
    case 'filter': return { type: 'filter', field: '', op: 'eq', value: '' }
    case 'sort': return { type: 'sort', field: '', direction: 'asc' }
    case 'map': return { type: 'map', fields: '' }
    case 'slice': return { type: 'slice', start: '0', end: '' }
    case 'find': return { type: 'find', field: '', op: 'eq', value: '' }
    case 'count': return { type: 'count' }
    case 'expression': return { type: 'expression', expr: '' }
    default: return { type: 'pick', path: '' }
  }
}

// ─── Inline step editor ────────────────────────────────

function StepFields({ step, onChange }: { step: TransformStep; onChange: (patch: Partial<TransformStep>) => void }) {
  switch (step.type) {
    case 'pick':
      return (
        <input className={webhookFieldClass} value={step.path} onChange={(e) => onChange({ path: e.target.value })} placeholder="e.g. data.items" />
      )

    case 'filter':
    case 'find':
      return (
        <>
          <input className={webhookFieldClass} value={step.field} onChange={(e) => onChange({ field: e.target.value })} placeholder="Field (e.g. status)" />
          <div className="flex gap-1">
            <select className={selectClass} value={step.op} onChange={(e) => onChange({ op: e.target.value as FilterOp })}>
              {FILTER_OPS.map((op) => <option key={op.value} value={op.value}>{op.label}</option>)}
            </select>
            {step.op !== 'exists' && (
              <input className={webhookFieldClass} value={step.value} onChange={(e) => onChange({ value: e.target.value })} placeholder="Value" />
            )}
          </div>
        </>
      )

    case 'sort':
      return (
        <div className="flex gap-1">
          <input className={cn(webhookFieldClass, 'flex-1')} value={step.field} onChange={(e) => onChange({ field: e.target.value })} placeholder="Field" />
          <select className={selectClass} style={{ width: '70px' }} value={step.direction} onChange={(e) => onChange({ direction: e.target.value as 'asc' | 'desc' })}>
            <option value="asc">A-Z</option>
            <option value="desc">Z-A</option>
          </select>
        </div>
      )

    case 'map':
      return (
        <input className={webhookFieldClass} value={step.fields} onChange={(e) => onChange({ fields: e.target.value })} placeholder="Fields (e.g. name, email)" />
      )

    case 'slice':
      return (
        <div className="flex gap-1">
          <input className={webhookFieldClass} value={step.start} onChange={(e) => onChange({ start: e.target.value })} placeholder="Start" />
          <input className={webhookFieldClass} value={step.end} onChange={(e) => onChange({ end: e.target.value })} placeholder="End" />
        </div>
      )

    case 'expression':
      return (
        <input className={webhookFieldClass} value={step.expr} onChange={(e) => onChange({ expr: e.target.value })} placeholder="{{ stores.$value }}" />
      )

    case 'count':
      return <span className="text-[10px] text-muted-foreground">Returns the array length</span>

    default:
      return null
  }
}

// ─── Response viewer ────────────────────────────────

function ResponseViewer({ result, hasTransform, onClose }: { result: WebhookResult; hasTransform: boolean; onClose: () => void }) {
  const [responseTab, setResponseTab] = useState<'result' | 'raw'>(hasTransform ? 'result' : 'raw')
  const statusColor = STATUS_COLORS[String(result.status)[0]] ?? 'text-muted-foreground bg-muted/50'

  return (
    <div className="border border-border/60 rounded-md overflow-hidden">
      {/* Response header bar */}
      <div className="flex items-center gap-2 px-2.5 py-1.5 bg-muted/30 border-b border-border/50">
        {result.error ? (
          <span className="text-[10px] font-semibold text-red-500">Error</span>
        ) : (
          <span className={cn('text-[10px] font-bold font-mono px-1.5 py-0.5 rounded', statusColor)}>
            {result.status} {result.statusText}
          </span>
        )}
        <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
          <Clock size={9} />
          <span className="font-mono">{result.durationMs}ms</span>
        </div>
        {hasTransform && !result.error && (
          <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
            <ArrowRight size={9} />
            <span className="font-mono">
              {Array.isArray(result.transformedData)
                ? `[${result.transformedData.length} items]`
                : typeof result.transformedData}
            </span>
          </div>
        )}
        <div className="flex-1" />
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground p-0.5">
          <X size={10} />
        </button>
      </div>

      {/* Tab switcher (only if transform steps exist) */}
      {hasTransform && !result.error && (
        <div className="flex border-b border-border/50">
          {(['result', 'raw'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setResponseTab(t)}
              className={cn(
                'px-3 py-1 text-[10px] font-medium transition-colors',
                responseTab === t
                  ? 'text-foreground border-b border-primary'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              {t === 'result' ? 'Transformed' : 'Raw Response'}
            </button>
          ))}
        </div>
      )}

      {/* Response body */}
      <div className="max-h-[200px] overflow-auto p-2">
        {result.error ? (
          <span className="text-[11px] text-red-500 font-mono">{result.error}</span>
        ) : (
          <ValueInspector
            value={responseTab === 'result' ? result.transformedData : result.rawData}
            defaultExpandDepth={2}
            size="compact"
          />
        )}
      </div>
    </div>
  )
}

// ─── API item ────────────────────────────────

function WebhookItem({ def }: { def: WebhookDefinition }) {
  const updateWebhookDef = useAppDocumentStore((s) => s.updateWebhookDefinition)
  const removeWebhookDef = useAppDocumentStore((s) => s.removeWebhookDefinition)
  const [expanded, setExpanded] = useState(false)
  const [testing, setTesting] = useState(false)
  const [showTransform, setShowTransform] = useState(false)
  const [testResult, setTestResult] = useState<WebhookResult | null>(null)
  const [editingName, setEditingName] = useState(false)
  const nameInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editingName) nameInputRef.current?.focus()
  }, [editingName])

  const handleTest = async (e: React.MouseEvent) => {
    e.stopPropagation()
    setTesting(true)
    setTestResult(null)
    try {
      const result = await executeWebhook(def.id, '')
      setTestResult(result)
      // Auto-expand to show the result
      if (!expanded) setExpanded(true)
    } finally {
      setTesting(false)
    }
  }

  const steps = def.steps ?? []

  const updateStep = (idx: number, patch: Partial<TransformStep>) => {
    const updated = steps.map((s, i) => i === idx ? { ...s, ...patch } as TransformStep : s)
    updateWebhookDef(def.id, { steps: updated })
  }

  const changeStepType = (idx: number, type: string) => {
    const updated = steps.map((s, i) => i === idx ? defaultStep(type) : s)
    updateWebhookDef(def.id, { steps: updated })
  }

  const removeStep = (idx: number) => {
    updateWebhookDef(def.id, { steps: steps.filter((_, i) => i !== idx) })
  }

  const addStep = () => {
    updateWebhookDef(def.id, { steps: [...steps, defaultStep('pick')] })
  }

  return (
    <div className="border-b border-border">
      <div
        className="flex items-center gap-1.5 px-3 py-2 cursor-pointer hover:bg-accent/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <ChevronRight
          size={11}
          className={cn('text-muted-foreground shrink-0 transition-transform duration-150', expanded && 'rotate-90')}
        />
        <span className={cn('text-[10px] font-bold font-mono shrink-0', METHOD_COLORS[def.method] || 'text-foreground')}>
          {def.method}
        </span>
        {editingName ? (
          <input
            ref={nameInputRef}
            className="flex-1 h-6 px-1.5 text-[12px] font-medium bg-background border border-border rounded text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            value={def.name}
            onChange={(e) => updateWebhookDef(def.id, { name: e.target.value })}
            onBlur={() => setEditingName(false)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === 'Escape') setEditingName(false) }}
            onClick={(e) => e.stopPropagation()}
            placeholder="Untitled"
            spellCheck={false}
          />
        ) : (
          <span className="group/name flex-1 flex items-center gap-1 min-w-0">
            <span className="text-[12px] font-medium text-foreground truncate">{def.name || 'Untitled'}</span>
            <button
              onClick={(e) => { e.stopPropagation(); setEditingName(true) }}
              className="opacity-0 group-hover/name:opacity-100 text-muted-foreground hover:text-foreground p-0.5 shrink-0 transition-opacity"
            >
              <Pencil size={10} />
            </button>
          </span>
        )}
        {testResult && !expanded && (
          <span className={cn(
            'text-[9px] font-mono font-bold px-1 py-0.5 rounded shrink-0',
            testResult.ok ? 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10' : 'text-red-500 bg-red-500/10'
          )}>
            {testResult.error ? 'ERR' : testResult.status}
          </span>
        )}
        {steps.length > 0 && (
          <span className="text-[9px] text-muted-foreground/60 font-mono shrink-0">
            {steps.length} step{steps.length !== 1 ? 's' : ''}
          </span>
        )}
        {testing && (
          <div className="h-3 w-3 border-2 border-primary border-t-transparent rounded-full animate-spin shrink-0" />
        )}
      </div>

      {expanded && (
        <div className="px-3 pb-3 pt-0.5 space-y-2">
          {/* URL bar — primary input, like Postman */}
          <div className="flex gap-1">
            <select
              className="h-8 text-[11px] font-mono font-bold bg-background border border-border rounded-md px-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
              value={def.method}
              onChange={(e) => updateWebhookDef(def.id, { method: e.target.value as WebhookDefinition['method'] })}
            >
              {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <input
              className="flex-1 h-8 px-2 text-[12px] font-mono bg-background border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-ring text-foreground placeholder:text-muted-foreground"
              value={def.url}
              onChange={(e) => updateWebhookDef(def.id, { url: e.target.value })}
              placeholder="https://api.example.com/data"
              spellCheck={false}
            />
            <Button
              variant="default"
              size="sm"
              onClick={(e) => { e.stopPropagation(); handleTest(e) }}
              disabled={testing || !def.url}
              className="h-8 px-3 text-[11px] font-medium gap-1.5 shrink-0"
            >
              {testing ? (
                <div className="h-3 w-3 border-2 border-current border-t-transparent rounded-full animate-spin" />
              ) : (
                <Send size={11} />
              )}
              Send
            </Button>
          </div>


          {def.method !== 'GET' && (
            <div className="space-y-1">
              <label className={labelClass}>Body</label>
              <textarea
                className="w-full text-[11px] font-mono bg-background border border-border rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground resize-none placeholder:text-muted-foreground"
                rows={3}
                value={def.body}
                onChange={(e) => updateWebhookDef(def.id, { body: e.target.value })}
                placeholder='{"key": "{{ stores.value }}"}'
                spellCheck={false}
              />
            </div>
          )}

          {/* Transform steps — collapsible */}
          <div className="border border-border/60 rounded-md">
            <button
              className="flex items-center gap-1.5 w-full px-2 py-1.5 text-left hover:bg-accent/30 transition-colors rounded-md"
              onClick={() => setShowTransform(!showTransform)}
            >
              {showTransform ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
              <span className={labelClass}>Transform response</span>
              {steps.length > 0 && (
                <span className="text-[9px] text-primary font-mono ml-auto">
                  {steps.length} step{steps.length !== 1 ? 's' : ''}
                </span>
              )}
            </button>

            {showTransform && (
              <div className="px-2 pb-2 space-y-1.5">
                {steps.map((step, idx) => (
                  <div key={idx} className="flex items-start gap-1">
                    <GripVertical size={9} className="text-muted-foreground/30 mt-2 shrink-0" />
                    <div className="flex-1 space-y-1 bg-accent/20 rounded p-1.5">
                      <div className="flex items-center gap-1">
                        <select
                          className={cn(selectClass, 'flex-1')}
                          value={step.type}
                          onChange={(e) => changeStepType(idx, e.target.value)}
                        >
                          {STEP_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                        </select>
                        <button onClick={() => removeStep(idx)} className="text-muted-foreground hover:text-destructive p-0.5">
                          <Trash2 size={10} />
                        </button>
                      </div>
                      <StepFields step={step} onChange={(patch) => updateStep(idx, patch)} />
                    </div>
                  </div>
                ))}
                <Button variant="ghost" size="sm" onClick={addStep} className="w-full h-6 text-[10px] text-muted-foreground">
                  <Plus size={10} className="mr-1" /> Add step
                </Button>
              </div>
            )}
          </div>

          {/* Test result — inline response viewer */}
          {testResult && (
            <ResponseViewer
              result={testResult}
              hasTransform={steps.length > 0}
              onClose={() => setTestResult(null)}
            />
          )}

          <div className="flex items-center justify-between pt-1">
            <span className="text-[9px] text-muted-foreground font-mono">{def.id}</span>
            <button
              onClick={() => removeWebhookDef(def.id)}
              className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-destructive transition-colors"
            >
              <Trash2 size={10} />
              Delete
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export function WebhooksPanel() {
  const webhookDefs = useAppDocumentStore((s) => s.webhookDefinitions)
  const addWebhookDef = useAppDocumentStore((s) => s.addWebhookDefinition)

  const handleAdd = () => {
    const id = `wh_${Date.now().toString(36)}`
    addWebhookDef({ id, name: '', url: '', method: 'GET', headers: {}, body: '', steps: [] })
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 overflow-y-auto">
        {webhookDefs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
            <p className="text-[12px] text-muted-foreground mb-3">
              Define API calls that components can trigger. Add transform steps to filter or reshape the response.
            </p>
            <Button variant="outline" size="sm" onClick={handleAdd} className="h-7 text-[11px] gap-1.5">
              <Plus size={12} />
              Add API Call
            </Button>
          </div>
        ) : (
          <>
            {webhookDefs.map((def) => <WebhookItem key={def.id} def={def} />)}
            <div className="p-2.5">
              <Button
                variant="ghost"
                size="sm"
                onClick={handleAdd}
                className="w-full h-7 text-[11px] text-muted-foreground"
              >
                <Plus size={12} className="mr-1" />
                Add API Call
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════════
// 7. BottomPanel
// ════════════════════════════════════════════════════════════════════════════════

// ─── State tab ────────────────────────────────

function StoreEntry({ name, value }: { name: string; value: unknown }) {
  const setGlobalStore = useRuntimeStateStore((s) => s.setGlobalStore)

  return (
    <div className="px-3 py-1.5 border-b border-border/50">
      <span className="text-[11px] font-medium text-foreground">{name}</span>
      <div className="text-[10px] mt-0.5">
        <ValueInspector value={value} onEdit={(v) => setGlobalStore(name, v)} size="compact" />
      </div>
    </div>
  )
}

function ComponentEntry({ nodeId, state }: { nodeId: string; state: Record<string, unknown> }) {
  const node = useAppDocumentStore((s) => s.nodes[nodeId])
  const def = node ? getDefinition(node.type) : null
  const label = def?.meta.displayName || node?.type || nodeId
  const setComponentState = useRuntimeStateStore((s) => s.setComponentState)

  return (
    <div className="px-3 py-1.5 border-b border-border/50">
      <div className="flex items-center gap-1.5 mb-0.5">
        <span className="text-[11px] font-medium text-foreground truncate">{label}</span>
        <span className="text-[9px] text-muted-foreground font-mono truncate">{nodeId}</span>
      </div>
      <div className="text-[10px] space-y-px">
        {Object.entries(state).map(([key, val]) => (
          <div key={key} className="flex gap-1.5 items-start">
            <span className="text-muted-foreground font-mono text-[10px] shrink-0">{key}:</span>
            <ValueInspector value={val} onEdit={(v) => setComponentState(nodeId, key, v)} size="compact" />
          </div>
        ))}
      </div>
    </div>
  )
}

function StateTab() {
  const globalStores = useRuntimeStateStore((s) => s.globalStores)
  const componentState = useRuntimeStateStore((s) => s.componentState)

  const storeEntries = Object.entries(globalStores)
  const componentEntries = Object.entries(componentState).filter(([, s]) => Object.keys(s).length > 0)
  const isEmpty = storeEntries.length === 0 && componentEntries.length === 0

  if (isEmpty) {
    return (
      <div className="flex items-center justify-center h-full text-[11px] text-muted-foreground">
        No state yet
      </div>
    )
  }

  return (
    <div className="flex gap-0 h-full">
      {/* Stores column */}
      <div className="flex-1 min-w-0 border-r border-border/50 overflow-y-auto">
        <div className="px-3 py-1.5 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider border-b border-border/50 bg-muted/30 flex items-center gap-1">
          <Database size={10} />
          Stores ({storeEntries.length})
        </div>
        {storeEntries.map(([name, value]) => (
          <StoreEntry key={name} name={name} value={value} />
        ))}
      </div>
      {/* Components column */}
      <div className="flex-1 min-w-0 overflow-y-auto">
        <div className="px-3 py-1.5 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider border-b border-border/50 bg-muted/30 flex items-center gap-1">
          <Component size={10} />
          Components ({componentEntries.length})
        </div>
        {componentEntries.map(([nodeId, state]) => (
          <ComponentEntry key={nodeId} nodeId={nodeId} state={state} />
        ))}
      </div>
    </div>
  )
}

// ─── Console tab ────────────────────────────────

const levelColors: Record<string, string> = {
  info: 'text-blue-500 dark:text-blue-400',
  warn: 'text-amber-500 dark:text-amber-400',
  error: 'text-red-500 dark:text-red-400',
  success: 'text-emerald-500 dark:text-emerald-400',
}

const levelBg: Record<string, string> = {
  error: 'bg-red-500/5',
  warn: 'bg-amber-500/5',
}

function ConsoleEntryRow({ entry }: { entry: ConsoleEntry }) {
  const time = new Date(entry.timestamp)
  const timeStr = time.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })

  return (
    <div className={cn('flex items-start gap-2 px-3 py-1 border-b border-border/30 font-mono text-[10px]', levelBg[entry.level])}>
      <span className="text-muted-foreground/60 shrink-0">{timeStr}</span>
      <span className={cn('shrink-0 uppercase font-semibold w-10', levelColors[entry.level])}>
        {entry.level === 'success' ? 'ok' : entry.level}
      </span>
      <span className="text-muted-foreground shrink-0">[{entry.source}]</span>
      <span className="text-foreground break-all">{entry.message}</span>
      {entry.detail !== undefined && (
        <span className="ml-auto shrink-0">
          <ValueInspector value={entry.detail} defaultExpandDepth={0} size="compact" />
        </span>
      )}
    </div>
  )
}

function ConsoleTab() {
  const entries = useConsoleStore((s) => s.entries)
  const clear = useConsoleStore((s) => s.clear)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [entries.length])

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center px-3 py-1 border-b border-border/50 bg-muted/30 shrink-0">
        <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
          Console ({entries.length})
        </span>
        <div className="flex-1" />
        <button
          onClick={clear}
          className="text-muted-foreground hover:text-foreground transition-colors p-0.5"
          title="Clear console"
        >
          <Trash2 size={11} />
        </button>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {entries.length === 0 ? (
          <div className="flex items-center justify-center h-full text-[11px] text-muted-foreground">
            No logs yet
          </div>
        ) : (
          entries.map((entry) => (
            <ConsoleEntryRow key={entry.id} entry={entry} />
          ))
        )}
      </div>
    </div>
  )
}

// ─── Bottom panel ────────────────────────────────

type BottomTab = 'stores' | 'api' | 'layers' | 'state' | 'console'

export function BottomPanel() {
  const [tab, setTab] = useState<BottomTab>('stores')
  const entryCount = useConsoleStore((s) => s.entries.length)
  const resetRuntime = useRuntimeStateStore((s) => s.reset)
  const nodes = useAppDocumentStore((s) => s.nodes)
  const storeDefs = useAppDocumentStore((s) => s.storeDefinitions)
  const initializeRuntime = useRuntimeStateStore((s) => s.initialize)

  const handleResetState = () => {
    resetRuntime()
    initializeRuntime(nodes, storeDefs)
  }

  const authoringTabs: { id: BottomTab; label: string; icon: typeof Database }[] = [
    { id: 'stores', label: 'Stores', icon: Database },
    { id: 'api', label: 'API', icon: Globe },
  ]

  const debugTabs: { id: BottomTab; label: string; icon: typeof Database }[] = [
    { id: 'layers', label: 'Layers', icon: Layers },
    { id: 'state', label: 'State', icon: Component },
    { id: 'console', label: 'Console', icon: ScrollText },
  ]

  const renderTab = (t: { id: BottomTab; label: string; icon: typeof Database }) => (
    <button
      key={t.id}
      onClick={() => setTab(t.id)}
      className={cn(
        'flex items-center gap-1 px-2.5 h-full text-[11px] font-medium transition-colors',
        tab === t.id
          ? 'text-foreground border-b-2 border-primary -mb-px'
          : 'text-muted-foreground hover:text-foreground'
      )}
    >
      <t.icon size={11} />
      {t.label}
      {t.id === 'console' && entryCount > 0 && (
        <span className="text-[9px] text-muted-foreground/60 tabular-nums">{entryCount}</span>
      )}
    </button>
  )

  return (
    <div className="editor-chrome h-full flex flex-col bg-card">
      {/* Tab bar */}
      <div className="flex items-center h-8 border-b border-border shrink-0 px-1">
        {/* Authoring group */}
        {authoringTabs.map(renderTab)}

        {/* Separator */}
        <div className="w-px h-3.5 bg-border/60 mx-1.5 shrink-0" />

        {/* Debug group */}
        {debugTabs.map(renderTab)}

        <div className="flex-1" />

        {tab === 'state' && (
          <button
            onClick={handleResetState}
            className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors px-1.5"
            title="Reset state"
          >
            <RotateCcw size={10} />
          </button>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {tab === 'stores' ? (
          <StoresPanel />
        ) : tab === 'api' ? (
          <WebhooksPanel />
        ) : tab === 'layers' ? (
          <LayersPanel />
        ) : tab === 'state' ? (
          <StateTab />
        ) : (
          <ConsoleTab />
        )}
      </div>
    </div>
  )
}
