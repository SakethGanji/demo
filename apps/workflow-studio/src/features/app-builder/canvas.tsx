import { memo, useCallback, useRef, useEffect, useState, useMemo } from 'react'
import { createPortal } from 'react-dom'
import {
  GripVertical,
  ArrowUp,
  ArrowDown,
  Trash2,
  Copy,
  EyeOff,
  Eye,
  ChevronRight,
  Monitor,
  Tablet,
  Smartphone,
  Scissors,
  ClipboardPaste,
  CopyPlus,
  WrapText,
  Minimize2,
  ChevronsUp,
  ChevronsDown,
  Search,
} from 'lucide-react'
import { useAppDocumentStore, useAppEditorStore, setNodeRef, getNodeRef, useRuntimeStateStore, useBreakpointStore, isDescendant } from './stores'
import { getDefinition, getAllDefinitions, RepeatContext, useRepeatScope, BREAKPOINTS } from './types'
import type { BreakpointId, ComponentDefinition } from './types'
import { useResolvedProps } from './hooks'
import { useEventHandlers } from './hooks'
import { resolveExpression } from './runtime'

// ─── getDOMInfo ──────────────────────────────────────────────────────────────

/**
 * Ported from craft.js packages/utils/src/getDOMInfo.ts
 * Returns detailed DOM information including flow detection.
 */

export interface DOMInfo {
  x: number
  y: number
  top: number
  left: number
  bottom: number
  right: number
  width: number
  height: number
  outerWidth: number
  outerHeight: number
  margin: { top: number; left: number; bottom: number; right: number }
  padding: { top: number; left: number; bottom: number; right: number }
  inFlow: boolean
}

export function getDOMInfo(el: HTMLElement): DOMInfo {
  const { x, y, top, left, bottom, right, width, height } =
    el.getBoundingClientRect()

  const style = window.getComputedStyle(el)

  const margin = {
    left: parseInt(style.marginLeft),
    right: parseInt(style.marginRight),
    bottom: parseInt(style.marginBottom),
    top: parseInt(style.marginTop),
  }

  const padding = {
    left: parseInt(style.paddingLeft),
    right: parseInt(style.paddingRight),
    bottom: parseInt(style.paddingBottom),
    top: parseInt(style.paddingTop),
  }

  const styleInFlow = (parent: HTMLElement): boolean | undefined => {
    const parentStyle = getComputedStyle(parent)

    if (style.overflow && style.overflow !== 'visible') return

    if (parentStyle.float !== 'none') return

    if (parentStyle.display === 'grid') return

    if (
      parentStyle.display === 'flex' &&
      parentStyle.flexDirection !== 'column'
    ) {
      return
    }

    switch (style.position) {
      case 'static':
      case 'relative':
        break
      default:
        return
    }

    switch (el.tagName) {
      case 'TR':
      case 'TBODY':
      case 'THEAD':
      case 'TFOOT':
        return true
    }

    switch (style.display) {
      case 'block':
      case 'list-item':
      case 'table':
      case 'flex':
      case 'grid':
        return true
    }

    return
  }

  return {
    x,
    y,
    top,
    left,
    bottom,
    right,
    width,
    height,
    outerWidth: Math.round(width + margin.left + margin.right),
    outerHeight: Math.round(height + margin.top + margin.bottom),
    margin,
    padding,
    inFlow: el.parentElement ? !!styleInFlow(el.parentElement) : true,
  }
}

// ─── findDropPosition ────────────────────────────────────────────────────────

/**
 * Ported from craft.js packages/core/src/events/findPosition.ts
 * Handles both flow (vertical) and non-flow (horizontal) elements.
 */

export interface NodeInfo extends DOMInfo {
  id: string
}

export interface DropPosition {
  parentId: string
  index: number
  where: 'before' | 'after'
}

export function findDropPosition(
  parentId: string,
  dims: NodeInfo[],
  posX: number,
  posY: number
): DropPosition {
  const result: DropPosition = {
    parentId,
    index: 0,
    where: 'before',
  }

  let leftLimit = 0,
    xLimit = 0,
    dimRight = 0,
    yLimit = 0,
    xCenter = 0,
    yCenter = 0,
    dimDown = 0

  for (let i = 0, len = dims.length; i < len; i++) {
    const dim = dims[i]

    dimRight = dim.left + dim.outerWidth
    dimDown = dim.top + dim.outerHeight
    xCenter = dim.left + dim.outerWidth / 2
    yCenter = dim.top + dim.outerHeight / 2

    if (
      (xLimit && dim.left > xLimit) ||
      (yLimit && yCenter >= yLimit) ||
      (leftLimit && dimRight < leftLimit)
    )
      continue

    result.index = i

    if (!dim.inFlow) {
      if (posY < dimDown) yLimit = dimDown
      if (posX < xCenter) {
        xLimit = xCenter
        result.where = 'before'
      } else {
        leftLimit = xCenter
        result.where = 'after'
      }
    } else {
      if (posY < yCenter) {
        result.where = 'before'
        break
      } else {
        result.where = 'after'
      }
    }
  }

  return result
}

export function dropPositionToIndex(pos: DropPosition): number {
  return pos.where === 'before' ? pos.index : pos.index + 1
}

// ─── movePlaceholder ─────────────────────────────────────────────────────────

/**
 * Ported from craft.js packages/core/src/events/movePlaceholder.ts
 * Computes pixel position for the fixed-position drop indicator overlay.
 */

interface PlaceholderPosition {
  top: string
  left: string
  width: string
  height: string
}

export function movePlaceholder(
  pos: DropPosition,
  canvasDOMInfo: DOMInfo,
  targetDOMInfo: DOMInfo | null,
  thickness: number = 2
): PlaceholderPosition {
  let t = 0,
    l = 0,
    w = 0,
    h = 0
  const where = pos.where

  if (targetDOMInfo) {
    if (!targetDOMInfo.inFlow) {
      // Horizontal layout — vertical line
      w = thickness
      h = targetDOMInfo.outerHeight
      t = targetDOMInfo.top
      l =
        where === 'before'
          ? targetDOMInfo.left
          : targetDOMInfo.left + targetDOMInfo.outerWidth
    } else {
      // Vertical layout — horizontal line
      w = targetDOMInfo.outerWidth
      h = thickness
      t = where === 'before' ? targetDOMInfo.top : targetDOMInfo.bottom
      l = targetDOMInfo.left
    }
  } else {
    // Empty canvas
    t = canvasDOMInfo.top + canvasDOMInfo.padding.top
    l = canvasDOMInfo.left + canvasDOMInfo.padding.left
    w =
      canvasDOMInfo.outerWidth -
      canvasDOMInfo.padding.right -
      canvasDOMInfo.padding.left -
      canvasDOMInfo.margin.left -
      canvasDOMInfo.margin.right
    h = thickness
  }

  return {
    top: `${t}px`,
    left: `${l}px`,
    width: `${w}px`,
    height: `${h}px`,
  }
}

// ─── FloatingToolbar ─────────────────────────────────────────────────────────

interface FloatingToolbarProps {
  nodeId: string
  variant: 'selected' | 'hovered'
}

export function FloatingToolbar({ nodeId, variant }: FloatingToolbarProps) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const rafRef = useRef<number>(0)
  const selectedCount = useAppEditorStore((s) => s.selectedNodeIds.length)

  const updatePosition = useCallback(() => {
    const el = getNodeRef(nodeId)
    if (!el) return
    const rect = el.getBoundingClientRect()
    setPos({ top: rect.top - 30, left: rect.left })
  }, [nodeId])

  useEffect(() => {
    updatePosition()

    const handleScroll = () => {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = requestAnimationFrame(updatePosition)
    }

    window.addEventListener('scroll', handleScroll, true)
    window.addEventListener('resize', handleScroll)
    return () => {
      window.removeEventListener('scroll', handleScroll, true)
      window.removeEventListener('resize', handleScroll)
      cancelAnimationFrame(rafRef.current)
    }
  }, [updatePosition])

  const node = useAppDocumentStore((s) => s.nodes[nodeId])
  if (!node || !pos) return null

  const def = getDefinition(node.type)
  if (!def) return null

  const overlay = document.getElementById('app-builder-overlay')
  if (!overlay) return null

  const isSelected = variant === 'selected'
  const isMultiSelect = isSelected && selectedCount > 1

  const handleSelectParent = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (node.parentId) {
      useAppEditorStore.getState().selectNode(node.parentId)
    }
  }

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation()
    const editorState = useAppEditorStore.getState()
    if (isMultiSelect) {
      useAppDocumentStore.getState().deleteNodes([...editorState.selectedNodeIds])
    } else {
      useAppDocumentStore.getState().deleteNode(nodeId)
    }
    editorState.clearSelection()
  }

  const handleDuplicate = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (isMultiSelect) {
      const editorState = useAppEditorStore.getState()
      const newIds = useAppDocumentStore.getState().duplicateNodes([...editorState.selectedNodeIds])
      if (newIds.length > 0) editorState.selectNode(newIds[0])
    } else {
      const newId = useAppDocumentStore.getState().duplicateNode(nodeId)
      if (newId) useAppEditorStore.getState().selectNode(newId)
    }
  }

  const handleToggleVisibility = (e: React.MouseEvent) => {
    e.stopPropagation()
    useAppDocumentStore.getState().toggleHidden(nodeId)
  }

  const handleDragStart = (e: React.DragEvent) => {
    e.stopPropagation()
    e.dataTransfer.setData('application/x-app-builder-move', nodeId)
    useAppEditorStore.getState().setDragSource({ type: 'move', nodeId })
  }

  const handleDragEnd = () => {
    useAppEditorStore.getState().clearDrag()
  }

  const label = isMultiSelect
    ? `${selectedCount} selected`
    : def.meta.displayName

  return createPortal(
    <div
      className="pointer-events-auto app-builder-toolbar-enter"
      style={{
        position: 'fixed',
        top: Math.max(2, pos.top),
        left: pos.left,
        zIndex: 51,
      }}
    >
      <div
        className="flex items-center gap-px rounded-md shadow-md"
        style={{
          backgroundColor: isSelected
            ? 'var(--primary)'
            : 'color-mix(in srgb, var(--primary) 80%, transparent)',
          color: 'var(--primary-foreground)',
          backdropFilter: isSelected ? undefined : 'blur(4px)',
        }}
      >
        {isSelected && !isMultiSelect && (
          <div
            draggable
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
            className="cursor-grab active:cursor-grabbing p-1.5 hover:bg-white/20 rounded-l-md transition-colors"
            title="Drag to move"
          >
            <GripVertical size={12} />
          </div>
        )}
        <span className="px-2 py-1 text-[11px] font-medium select-none">
          {label}
        </span>
        {isSelected && node.parentId && (
          <>
            {!isMultiSelect && (
              <button
                onClick={handleSelectParent}
                className="p-1.5 hover:bg-white/20 transition-colors"
                title="Select parent"
              >
                <ArrowUp size={12} />
              </button>
            )}
            <button
              onClick={handleToggleVisibility}
              className="p-1.5 hover:bg-white/20 transition-colors"
              title={node.hidden ? 'Show' : 'Hide'}
            >
              {node.hidden ? <Eye size={12} /> : <EyeOff size={12} />}
            </button>
            <button
              onClick={handleDuplicate}
              className="p-1.5 hover:bg-white/20 transition-colors"
              title="Duplicate"
            >
              <Copy size={12} />
            </button>
            <button
              onClick={handleDelete}
              className="p-1.5 hover:bg-white/20 rounded-r-md transition-colors"
              title="Delete"
            >
              <Trash2 size={12} />
            </button>
          </>
        )}
      </div>
    </div>,
    overlay
  )
}

// ─── DropIndicator ───────────────────────────────────────────────────────────

export function DropIndicator() {
  const dropIndicator = useAppEditorStore((s) => s.dropIndicator)
  const dragSource = useAppEditorStore((s) => s.dragSource)

  if (!dropIndicator || !dragSource) return null

  // Resolve the display name of what's being dragged
  let dragLabel = ''
  if (dragSource.type === 'new') {
    const def = getDefinition(dragSource.componentType)
    dragLabel = def?.meta.displayName ?? dragSource.componentType
  } else {
    const node = useAppDocumentStore.getState().nodes[dragSource.nodeId]
    if (node) {
      const def = getDefinition(node.type)
      dragLabel = def?.meta.displayName ?? node.type
    }
  }

  return (
    <>
      <DropLine parentId={dropIndicator.parentId} index={dropIndicator.index} dragLabel={dragLabel} />
      <ContainerHighlight parentId={dropIndicator.parentId} />
    </>
  )
}

/** Highlights the receiving container with a subtle tinted overlay */
function ContainerHighlight({ parentId }: { parentId: string }) {
  const canvasEl = getNodeRef(parentId)
  if (!canvasEl) return null

  const rect = canvasEl.getBoundingClientRect()

  return (
    <div
      style={{
        position: 'fixed',
        top: rect.top,
        left: rect.left,
        width: rect.width,
        height: rect.height,
        zIndex: 99998,
        pointerEvents: 'none',
        border: '2px solid var(--primary)',
        borderRadius: '4px',
        backgroundColor: 'color-mix(in srgb, var(--primary) 5%, transparent)',
        transition: 'top 0.1s ease, left 0.1s ease, width 0.1s ease, height 0.1s ease',
      }}
    />
  )
}

function DropLine({ parentId, index, dragLabel }: { parentId: string; index: number; dragLabel: string }) {
  const childIds = useAppDocumentStore((s) => s.nodes[parentId]?.childIds)

  if (!childIds) return null

  const canvasEl = getNodeRef(parentId)
  if (!canvasEl) return null

  const canvasDOMInfo = getDOMInfo(canvasEl)

  let targetDOMInfo = null
  if (childIds.length > 0) {
    const targetIndex = Math.min(index, childIds.length - 1)
    const targetId = childIds[targetIndex]
    const targetEl = getNodeRef(targetId)
    if (targetEl) {
      targetDOMInfo = getDOMInfo(targetEl)
    }
  }

  const where = index >= childIds.length ? 'after' as const : 'before' as const
  const pos = { parentId, index: Math.min(index, childIds.length - 1), where }

  const style = movePlaceholder(pos, canvasDOMInfo, targetDOMInfo)

  // Determine if this is a horizontal or vertical line
  const lineWidth = parseFloat(style.width)
  const lineHeight = parseFloat(style.height)
  const isHorizontal = lineWidth > lineHeight

  return (
    <>
      {/* The line itself — thicker and more visible */}
      <div
        style={{
          position: 'fixed',
          zIndex: 99999,
          pointerEvents: 'none',
          transition: 'top 0.08s ease-out, left 0.08s ease-out, width 0.08s ease-out, height 0.08s ease-out',
          top: style.top,
          left: style.left,
          width: isHorizontal ? style.width : '3px',
          height: isHorizontal ? '3px' : style.height,
          borderRadius: '2px',
          backgroundColor: 'var(--primary)',
          boxShadow: '0 0 8px color-mix(in srgb, var(--primary) 40%, transparent)',
        }}
      >
        {/* End dots */}
        <div
          style={{
            position: 'absolute',
            [isHorizontal ? 'left' : 'top']: '-3px',
            [isHorizontal ? 'top' : 'left']: '-3px',
            width: '9px',
            height: '9px',
            borderRadius: '50%',
            backgroundColor: 'var(--primary)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            [isHorizontal ? 'right' : 'bottom']: '-3px',
            [isHorizontal ? 'top' : 'left']: '-3px',
            width: '9px',
            height: '9px',
            borderRadius: '50%',
            backgroundColor: 'var(--primary)',
          }}
        />
      </div>

      {/* Label pill showing what's being dropped */}
      {dragLabel && (
        <div
          style={{
            position: 'fixed',
            zIndex: 100000,
            pointerEvents: 'none',
            top: isHorizontal
              ? `calc(${style.top} - 22px)`
              : style.top,
            left: isHorizontal
              ? `calc(${style.left} + ${lineWidth / 2}px)`
              : `calc(${style.left} + 8px)`,
            transform: isHorizontal ? 'translateX(-50%)' : 'none',
            transition: 'top 0.08s ease-out, left 0.08s ease-out',
          }}
        >
          <div
            className="px-2 py-0.5 rounded-full text-[10px] font-medium whitespace-nowrap shadow-md"
            style={{
              backgroundColor: 'var(--primary)',
              color: 'var(--primary-foreground)',
            }}
          >
            {dragLabel}
          </div>
        </div>
      )}
    </>
  )
}

// ─── BreakpointBar ───────────────────────────────────────────────────────────

const breakpointIcons: Record<string, React.ReactNode> = {
  Monitor: <Monitor size={14} />,
  Tablet: <Tablet size={14} />,
  Smartphone: <Smartphone size={14} />,
}

export function BreakpointBar() {
  const active = useBreakpointStore((s) => s.activeBreakpoint)
  const setActive = useBreakpointStore((s) => s.setActiveBreakpoint)

  // Only show desktop for now
  const enabledBreakpoints = BREAKPOINTS.filter((bp) => bp.id === 'desktop')

  return (
    <div className="flex items-center justify-center gap-1 py-1 shrink-0 border-b border-border/50 bg-muted/30">
      {enabledBreakpoints.map((bp) => {
        const isActive = active === bp.id
        return (
          <button
            key={bp.id}
            onClick={() => setActive(bp.id as BreakpointId)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${
              isActive
                ? 'bg-primary/10 text-primary'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
            }`}
            title={`${bp.label}${bp.maxWidth ? ` (${bp.maxWidth}px)` : ''}`}
          >
            {breakpointIcons[bp.icon]}
            <span>{bp.label}</span>
            {bp.maxWidth && isActive && (
              <span className="text-[9px] text-muted-foreground/60">{bp.maxWidth}px</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

// ─── Breadcrumbs ─────────────────────────────────────────────────────────────

export const Breadcrumbs = memo(function Breadcrumbs() {
  const selectedId = useAppEditorStore((s) => s.selectedNodeIds[0])
  const nodes = useAppDocumentStore((s) => s.nodes)

  if (!selectedId || !nodes[selectedId]) return null

  // Build ancestor chain from root to selected
  const chain: { id: string; label: string }[] = []
  let current = nodes[selectedId]
  while (current) {
    const def = getDefinition(current.type)
    chain.unshift({
      id: current.id,
      label: current.parentId === null ? 'Page' : (def?.meta.displayName ?? current.type),
    })
    current = current.parentId ? nodes[current.parentId] : (undefined as never)
    if (!current?.parentId && current) {
      // Add root
      chain.unshift({ id: current.id, label: 'Page' })
      break
    }
  }

  // Deduplicate root
  const seen = new Set<string>()
  const unique = chain.filter((item) => {
    if (seen.has(item.id)) return false
    seen.add(item.id)
    return true
  })

  return (
    <div className="flex items-center gap-0.5 px-3 py-1 text-[11px] text-muted-foreground select-none overflow-x-auto shrink-0 bg-muted/30 border-b border-border/50">
      {unique.map((item, i) => {
        const isLast = i === unique.length - 1
        return (
          <span key={item.id} className="flex items-center gap-0.5 shrink-0">
            {i > 0 && <ChevronRight size={10} className="text-muted-foreground/40" />}
            <button
              onClick={() => useAppEditorStore.getState().selectNode(item.id)}
              className={`px-1 py-0.5 rounded transition-colors ${
                isLast
                  ? 'text-foreground font-medium'
                  : 'hover:text-foreground hover:bg-accent/50'
              }`}
            >
              {item.label}
            </button>
          </span>
        )
      })}
    </div>
  )
})

// ─── ContextMenu ─────────────────────────────────────────────────────────────

interface ContextMenuItem {
  label: string
  icon: React.ReactNode
  shortcut?: string
  action: () => void
  disabled?: boolean
  destructive?: boolean
}

interface ContextMenuSeparator {
  type: 'separator'
}

type ContextMenuEntry = ContextMenuItem | ContextMenuSeparator

function isContextMenuSeparator(entry: ContextMenuEntry): entry is ContextMenuSeparator {
  return 'type' in entry && entry.type === 'separator'
}

export function ContextMenu() {
  const contextMenu = useAppEditorStore((s) => s.contextMenu)
  const clipboard = useAppEditorStore((s) => s.clipboard)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!contextMenu) return

    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        useAppEditorStore.getState().closeContextMenu()
      }
    }
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') useAppEditorStore.getState().closeContextMenu()
    }
    const handleScroll = () => useAppEditorStore.getState().closeContextMenu()

    // Delay to avoid the context menu event itself triggering close
    requestAnimationFrame(() => {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleEscape)
      window.addEventListener('scroll', handleScroll, true)
    })

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
      window.removeEventListener('scroll', handleScroll, true)
    }
  }, [contextMenu])

  if (!contextMenu) return null

  const overlay = document.getElementById('app-builder-overlay')
  if (!overlay) return null

  const { nodeId, x, y } = contextMenu
  const node = useAppDocumentStore.getState().nodes[nodeId]
  if (!node) return null

  const isRoot = node.parentId === null
  const hasChildren = node.childIds.length > 0
  const close = () => useAppEditorStore.getState().closeContextMenu()

  const runAction = (action: () => void) => {
    action()
    close()
  }

  const entries: ContextMenuEntry[] = [
    {
      label: 'Copy',
      icon: <Copy size={13} />,
      shortcut: '\u2318C',
      disabled: isRoot,
      action: () => runAction(() => {
        useAppEditorStore.getState().selectNode(nodeId)
        useAppEditorStore.getState().copySelection()
      }),
    },
    {
      label: 'Cut',
      icon: <Scissors size={13} />,
      shortcut: '\u2318X',
      disabled: isRoot,
      action: () => runAction(() => {
        useAppEditorStore.getState().selectNode(nodeId)
        useAppEditorStore.getState().cutSelection()
      }),
    },
    {
      label: 'Paste',
      icon: <ClipboardPaste size={13} />,
      shortcut: '\u2318V',
      disabled: !clipboard,
      action: () => runAction(() => {
        const editorState = useAppEditorStore.getState()
        const docStore = useAppDocumentStore.getState()
        if (!editorState.clipboard) return
        const { nodeIds, mode } = editorState.clipboard

        // Determine paste target: if nodeId is a canvas, paste into it; otherwise paste as sibling
        const targetNode = docStore.nodes[nodeId]
        const parentId = targetNode?.isCanvas ? nodeId : (targetNode?.parentId ?? nodeId)

        for (const id of nodeIds) {
          if (mode === 'copy') {
            docStore.duplicateNode(id)
          } else {
            // For cut, move to new parent
            const parent = docStore.nodes[parentId]
            if (parent) {
              docStore.moveNode(id, parentId, parent.childIds.length)
            }
          }
        }
        if (mode === 'cut') editorState.clearClipboard()
      }),
    },
    {
      label: 'Duplicate',
      icon: <CopyPlus size={13} />,
      shortcut: '\u2318D',
      disabled: isRoot,
      action: () => runAction(() => {
        const newId = useAppDocumentStore.getState().duplicateNode(nodeId)
        if (newId) useAppEditorStore.getState().selectNode(newId)
      }),
    },
    { type: 'separator' },
    {
      label: 'Wrap in Container',
      icon: <WrapText size={13} />,
      disabled: isRoot,
      action: () => runAction(() => {
        const selectedIds = useAppEditorStore.getState().selectedNodeIds
        const ids = selectedIds.includes(nodeId) ? selectedIds : [nodeId]
        const newId = useAppDocumentStore.getState().wrapInContainer(ids)
        if (newId) useAppEditorStore.getState().selectNode(newId)
      }),
    },
    {
      label: 'Unwrap',
      icon: <Minimize2 size={13} />,
      disabled: isRoot || !hasChildren,
      action: () => runAction(() => {
        const childIds = [...node.childIds]
        useAppDocumentStore.getState().unwrapNode(nodeId)
        if (childIds[0]) useAppEditorStore.getState().selectNode(childIds[0])
      }),
    },
    { type: 'separator' },
    {
      label: 'Move Up',
      icon: <ArrowUp size={13} />,
      disabled: isRoot,
      action: () => runAction(() => useAppDocumentStore.getState().reorderNode(nodeId, 'up')),
    },
    {
      label: 'Move Down',
      icon: <ArrowDown size={13} />,
      disabled: isRoot,
      action: () => runAction(() => useAppDocumentStore.getState().reorderNode(nodeId, 'down')),
    },
    {
      label: 'Move to Top',
      icon: <ChevronsUp size={13} />,
      disabled: isRoot,
      action: () => runAction(() => useAppDocumentStore.getState().reorderNode(nodeId, 'top')),
    },
    {
      label: 'Move to Bottom',
      icon: <ChevronsDown size={13} />,
      disabled: isRoot,
      action: () => runAction(() => useAppDocumentStore.getState().reorderNode(nodeId, 'bottom')),
    },
    { type: 'separator' },
    {
      label: node.hidden ? 'Show' : 'Hide',
      icon: node.hidden ? <Eye size={13} /> : <EyeOff size={13} />,
      disabled: isRoot,
      action: () => runAction(() => useAppDocumentStore.getState().toggleHidden(nodeId)),
    },
    {
      label: 'Delete',
      icon: <Trash2 size={13} />,
      shortcut: '\u232B',
      disabled: isRoot,
      destructive: true,
      action: () => runAction(() => {
        useAppDocumentStore.getState().deleteNode(nodeId)
        useAppEditorStore.getState().clearSelection()
      }),
    },
  ]

  // Clamp position to viewport
  const menuWidth = 220
  const menuHeight = entries.length * 32
  const clampedX = Math.min(x, window.innerWidth - menuWidth - 8)
  const clampedY = Math.min(y, window.innerHeight - menuHeight - 8)

  return createPortal(
    <div
      ref={menuRef}
      className="pointer-events-auto"
      style={{
        position: 'fixed',
        top: clampedY,
        left: clampedX,
        zIndex: 9999,
      }}
    >
      <div className="min-w-[200px] py-1 bg-popover border border-border rounded-lg shadow-lg">
        {entries.map((entry, i) => {
          if (isContextMenuSeparator(entry)) {
            return <div key={i} className="my-1 h-px bg-border" />
          }
          return (
            <button
              key={i}
              onClick={entry.action}
              disabled={entry.disabled}
              className={`w-full flex items-center gap-2 px-3 py-1.5 text-[12px] transition-colors disabled:opacity-30 disabled:cursor-not-allowed ${
                entry.destructive
                  ? 'text-destructive hover:bg-destructive/10'
                  : 'text-foreground hover:bg-accent'
              }`}
            >
              <span className="text-muted-foreground">{entry.icon}</span>
              <span className="flex-1 text-left">{entry.label}</span>
              {entry.shortcut && (
                <span className="text-[10px] text-muted-foreground/60">{entry.shortcut}</span>
              )}
            </button>
          )
        })}
      </div>
    </div>,
    overlay
  )
}

// ─── QuickAddPalette ─────────────────────────────────────────────────────────

export function QuickAddPalette() {
  const isOpen = useAppEditorStore((s) => s.quickAddOpen)
  const [search, setSearch] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isOpen) {
      setSearch('')
      setSelectedIndex(0)
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [isOpen])

  const definitions = getAllDefinitions()

  const filtered = useMemo(() => {
    const defs = definitions.filter((d) => (d.meta.tier || 'component') === 'component')
    if (!search.trim()) return defs
    const q = search.toLowerCase()
    return defs.filter(
      (d) =>
        d.meta.displayName.toLowerCase().includes(q) ||
        d.type.toLowerCase().includes(q) ||
        d.meta.category.toLowerCase().includes(q)
    )
  }, [definitions, search])

  useEffect(() => {
    setSelectedIndex(0)
  }, [search])

  const insertComponent = useCallback(
    (def: ComponentDefinition) => {
      const editorState = useAppEditorStore.getState()
      const docState = useAppDocumentStore.getState()

      const parentId = editorState.selectedNodeIds[0] || docState.rootNodeId
      const parent = docState.nodes[parentId]
      const targetParent = parent?.isCanvas ? parentId : (parent?.parentId || docState.rootNodeId)

      const newId = docState.addNode(def.type, targetParent)
      if (newId) {
        editorState.selectNode(newId)
      }
      editorState.closeQuickAdd()
    },
    []
  )

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        useAppEditorStore.getState().closeQuickAdd()
        return
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIndex((i) => Math.max(i - 1, 0))
        return
      }
      if (e.key === 'Enter') {
        e.preventDefault()
        if (filtered[selectedIndex]) {
          insertComponent(filtered[selectedIndex])
        }
        return
      }
    },
    [filtered, selectedIndex, insertComponent]
  )

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[20vh]" onClick={() => useAppEditorStore.getState().closeQuickAdd()}>
      <div
        className="w-[400px] bg-popover border border-border rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search input */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
          <Search size={16} className="text-muted-foreground shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search components..."
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none"
          />
          <kbd className="text-[10px] text-muted-foreground/60 bg-muted px-1.5 py-0.5 rounded border border-border/60 font-mono">/</kbd>
        </div>

        {/* Results */}
        <div className="max-h-[300px] overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">
              No components found
            </div>
          ) : (
            filtered.map((def, i) => (
              <button
                key={def.type}
                onClick={() => insertComponent(def)}
                onMouseEnter={() => setSelectedIndex(i)}
                className={`flex items-center gap-3 w-full px-4 py-2 text-left transition-colors ${
                  i === selectedIndex ? 'bg-accent text-foreground' : 'text-foreground hover:bg-accent/50'
                }`}
              >
                <div className="w-7 h-7 rounded-md bg-muted/80 flex items-center justify-center shrink-0">
                  <span className="text-[11px] font-bold text-muted-foreground">
                    {def.meta.displayName[0]}
                  </span>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-medium truncate">{def.meta.displayName}</p>
                  <p className="text-[10px] text-muted-foreground capitalize">{def.meta.category}</p>
                </div>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  )
}

// ─── NodeWrapper ─────────────────────────────────────────────────────────────

// Map prop names to CSS property names
const propToCss: Record<string, string> = {
  background: 'background-color',
  color: 'color',
  opacity: 'opacity',
  borderRadius: 'border-radius',
  borderWidth: 'border-width',
  borderColor: 'border-color',
  shadow: 'box-shadow',
  fontSize: 'font-size',
  fontWeight: 'font-weight',
}

function generateStateCSS(
  nodeId: string,
  stateStyles: Partial<Record<string, Record<string, unknown>>>
): string {
  const rules: string[] = []
  const stateMap: Record<string, string> = { hover: ':hover', focus: ':focus', active: ':active' }

  for (const [state, styles] of Object.entries(stateStyles)) {
    const pseudo = stateMap[state]
    if (!pseudo || !styles) continue

    const declarations: string[] = []
    for (const [prop, value] of Object.entries(styles)) {
      const cssProp = propToCss[prop] || prop.replace(/([A-Z])/g, '-$1').toLowerCase()
      let cssValue = String(value)
      // Add px for numeric properties
      if (['border-radius', 'border-width', 'font-size'].includes(cssProp) && !isNaN(Number(cssValue))) {
        cssValue = `${cssValue}px`
      }
      if (cssProp === 'opacity' && Number(cssValue) > 1) {
        cssValue = String(Number(cssValue) / 100)
      }
      declarations.push(`${cssProp}: ${cssValue} !important`)
    }

    if (declarations.length > 0) {
      rules.push(`[data-node-id="${nodeId}"]${pseudo} > * { ${declarations.join('; ')}; transition: all 0.15s ease; }`)
    }
  }
  return rules.join('\n')
}

function findCanvasAncestor(nodeId: string): string | null {
  const nodes = useAppDocumentStore.getState().nodes
  let current = nodes[nodeId]
  while (current) {
    if (current.isCanvas) return current.id
    if (!current.parentId) return null
    current = nodes[current.parentId]
  }
  return null
}

function getChildDims(parentId: string, excludeIds?: Set<string>): NodeInfo[] {
  const nodes = useAppDocumentStore.getState().nodes
  const parent = nodes[parentId]
  if (!parent) return []

  const dims: NodeInfo[] = []
  for (const childId of parent.childIds) {
    if (excludeIds?.has(childId)) continue
    const el = getNodeRef(childId)
    if (!el) continue
    const info = getDOMInfo(el)
    dims.push({ ...info, id: childId })
  }
  return dims
}

export const NodeWrapper = memo(function NodeWrapper({ nodeId }: { nodeId: string }) {
  const node = useAppDocumentStore((s) => s.nodes[nodeId])
  const mode = useAppEditorStore((s) => s.mode)
  const isEditMode = mode === 'edit'
  const isSelected = useAppEditorStore(
    (s) => s.selectedNodeIds.includes(nodeId)
  )
  const isHovered = useAppEditorStore((s) => s.hoveredNodeId === nodeId)
  const isDropTarget = useAppEditorStore((s) => s.dropIndicator?.parentId === nodeId)
  const selectNode = useAppEditorStore((s) => s.selectNode)
  const hoverNode = useAppEditorStore((s) => s.hoverNode)
  const wrapperRef = useRef<HTMLDivElement>(null)

  // Register DOM ref
  useEffect(() => {
    const el = wrapperRef.current
    if (el) setNodeRef(nodeId, el)
    return () => setNodeRef(nodeId, null)
  }, [nodeId])

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      if (!isEditMode) return
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
        selectNode(nodeId)
      }
    },
    [nodeId, selectNode, isEditMode]
  )

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      if (!isEditMode) return
      e.preventDefault()
      e.stopPropagation()
      const editorState = useAppEditorStore.getState()
      // If right-clicking a node not in selection, select it first
      if (!editorState.selectedNodeIds.includes(nodeId)) {
        editorState.selectNode(nodeId)
      }
      editorState.openContextMenu(e.clientX, e.clientY, nodeId)
    },
    [nodeId, isEditMode]
  )

  const handleMouseEnter = useCallback(() => {
    if (!isEditMode) return
    hoverNode(nodeId)
  }, [nodeId, hoverNode, isEditMode])

  const handleMouseLeave = useCallback(() => {
    if (!isEditMode) return
    hoverNode(null)
  }, [hoverNode, isEditMode])

  // --- Drag source (move existing node) ---
  const handleDragStart = useCallback(
    (e: React.DragEvent) => {
      // Check canDrag rule
      const currentNode = useAppDocumentStore.getState().nodes[nodeId]
      if (currentNode) {
        const nodeDef = getDefinition(currentNode.type)
        if (nodeDef?.rules?.canDrag && !nodeDef.rules.canDrag(currentNode)) {
          e.preventDefault()
          return
        }
      }
      e.stopPropagation()
      e.dataTransfer.setData('application/x-app-builder-move', nodeId)
      useAppEditorStore.getState().setDragSource({ type: 'move', nodeId })
    },
    [nodeId]
  )

  const handleDragEnd = useCallback(() => {
    useAppEditorStore.getState().clearDrag()
  }, [])

  // --- All nodes handle dragover/dragenter to find canvas ancestor ---
  const handleDragOver = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()

      const editorState = useAppEditorStore.getState()
      if (editorState.mode === 'preview') return

      const canvasId = findCanvasAncestor(nodeId)
      if (!canvasId) return

      // Prevent dropping a node into itself or its descendants
      const { dragSource } = editorState
      const nodes = useAppDocumentStore.getState().nodes
      if (dragSource?.type === 'move') {
        if (dragSource.nodeId === canvasId || isDescendant(nodes, canvasId, dragSource.nodeId)) {
          return
        }
      }

      // Check canMoveIn on target canvas
      const canvasNode = nodes[canvasId]
      if (canvasNode) {
        const canvasDef = getDefinition(canvasNode.type)
        if (dragSource?.type === 'move') {
          const draggedNode = nodes[dragSource.nodeId]
          if (draggedNode && canvasDef?.rules?.canMoveIn && !canvasDef.rules.canMoveIn(draggedNode, canvasNode)) {
            return
          }
          // Check canMoveOut on current parent
          if (draggedNode?.parentId) {
            const parentNode = nodes[draggedNode.parentId]
            if (parentNode) {
              const parentDef = getDefinition(parentNode.type)
              if (parentDef?.rules?.canMoveOut && !parentDef.rules.canMoveOut(draggedNode, parentNode)) {
                return
              }
            }
          }
        } else if (dragSource?.type === 'new') {
          const tempDef = getDefinition(dragSource.componentType)
          if (tempDef && canvasDef?.rules?.canMoveIn) {
            const tempNode = { id: '__temp__', type: dragSource.componentType, parentId: null, childIds: [], linkedNodes: {}, props: {}, isCanvas: false, hidden: false }
            if (!canvasDef.rules.canMoveIn(tempNode, canvasNode)) {
              return
            }
          }
        }
      }

      // Exclude dragged node from dimension calculations (CraftJS Positioner pattern)
      const excludeIds = dragSource?.type === 'move'
        ? new Set([dragSource.nodeId])
        : undefined
      const childDims = getChildDims(canvasId, excludeIds)

      const pos = findDropPosition(canvasId, childDims, e.clientX, e.clientY)
      const index = dropPositionToIndex(pos)

      editorState.setDropIndicator({ parentId: canvasId, index })
    },
    [nodeId]
  )

  const handleDragEnter = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
    },
    []
  )

  // --- Drop handler ---
  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()

      const editorState = useAppEditorStore.getState()
      const { dragSource, dropIndicator } = editorState

      if (!dragSource || !dropIndicator) {
        editorState.clearDrag()
        return
      }

      const docStore = useAppDocumentStore.getState()

      if (dragSource.type === 'new') {
        const newId = docStore.addNode(
          dragSource.componentType,
          dropIndicator.parentId,
          dropIndicator.index
        )
        if (newId) {
          editorState.selectNode(newId)
        }
      } else if (dragSource.type === 'move') {
        docStore.moveNode(
          dragSource.nodeId,
          dropIndicator.parentId,
          dropIndicator.index
        )
      }

      editorState.clearDrag()
    },
    []
  )

  const handleDragLeave = useCallback(
    (e: React.DragEvent) => {
      const related = e.relatedTarget as HTMLElement | null
      if (wrapperRef.current && !wrapperRef.current.contains(related)) {
        const state = useAppEditorStore.getState()
        if (state.dropIndicator?.parentId === nodeId) {
          state.setDropIndicator(null)
        }
      }
    },
    [nodeId]
  )

  if (!node) return null

  // Design-time hidden (toggled in layers panel) — hide in both modes
  // but show a ghost in edit mode so user can still select it
  const isHiddenByDesign = node.hidden

  const def = getDefinition(node.type)
  if (!def) return null

  const resolvedProps = useResolvedProps(nodeId, node.props)
  const { onEvent } = useEventHandlers(nodeId)

  // Runtime visibility — __visible expression resolves to false
  // In edit mode: show ghosted (like design-time hidden) so user can still select/edit
  // In preview mode: fully hide
  const runtimeHidden = resolvedProps.__visible !== undefined && !resolvedProps.__visible

  const isRoot = node.parentId === null
  const hasVisibilityExpression = typeof node.props.__visible === 'string' && node.props.__visible.includes('{{')
  const hasExpressions = Object.values(node.props).some(
    (v) => typeof v === 'string' && v.includes('{{')
  )
  const stateStyles = node.stateStyles
  const Component = def.Component

  // Render children for containers
  let renderedChildren: React.ReactNode = null
  if (node.isCanvas) {
    const isListNode = node.type === 'List'
    const dataExpr = isListNode ? (node.props.data as string) : null

    if (isListNode && dataExpr) {
      // List component: resolve data and repeat children per item
      const repeatScope = useRepeatScope()
      const baseCtx = useRuntimeStateStore.getState().getContext()
      const ctx = repeatScope
        ? { ...baseCtx, item: repeatScope.item, index: repeatScope.index, items: repeatScope.items }
        : baseCtx
      const resolvedData = resolveExpression(dataExpr, ctx)
      const dataArray = Array.isArray(resolvedData) ? resolvedData : []

      renderedChildren = dataArray.map((item, index) => (
        <RepeatContext.Provider
          key={index}
          value={{ item, index, items: dataArray }}
        >
          {node.childIds.map((childId) => (
            <NodeWrapper key={`${childId}-${index}`} nodeId={childId} />
          ))}
        </RepeatContext.Provider>
      ))
    } else {
      // Normal container or List without data: render children once
      renderedChildren = node.childIds.map((childId) => (
        <NodeWrapper key={childId} nodeId={childId} />
      ))
    }
  }

  if (isRoot) {
    return (
      <div
        ref={wrapperRef}
        onDragOver={isEditMode ? handleDragOver : undefined}
        onDragEnter={isEditMode ? handleDragEnter : undefined}
        onDrop={isEditMode ? handleDrop : undefined}
        onDragLeave={isEditMode ? handleDragLeave : undefined}
        onClick={(e) => {
          if (isEditMode && (e.target === e.currentTarget || wrapperRef.current === e.target)) {
            useAppEditorStore.getState().clearSelection()
          }
        }}
        className="h-full w-full flex flex-col overflow-hidden"
      >
        <Component id={nodeId} props={resolvedProps} onEvent={onEvent}>
          {renderedChildren}
        </Component>
      </div>
    )
  }

  // Fully hidden in preview mode (runtime or design-time)
  if (!isEditMode && (isHiddenByDesign || runtimeHidden)) return null
  // In edit mode, runtime-hidden elements show as ghosted (same as design-time hidden)

  return (
    <div
      ref={wrapperRef}
      data-node-wrapper
      data-node-id={nodeId}
      data-selected={isEditMode && isSelected ? 'true' : undefined}
      data-hovered={isEditMode && isHovered && !isDropTarget ? 'true' : undefined}
      draggable={isEditMode}
      onDragStart={isEditMode ? handleDragStart : undefined}
      onDragEnd={isEditMode ? handleDragEnd : undefined}
      onDragOver={isEditMode ? handleDragOver : undefined}
      onDragEnter={isEditMode ? handleDragEnter : undefined}
      onDrop={isEditMode ? handleDrop : undefined}
      onDragLeave={isEditMode ? handleDragLeave : undefined}
      onClick={handleClick}
      onContextMenu={handleContextMenu}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      className="relative min-w-0"
      style={{
        alignSelf: 'auto',
        ...(resolvedProps.fillSpace === 'yes' ? { flex: 1, display: 'flex', flexDirection: 'column' as const, minHeight: 0 } : undefined),
        ...(resolvedProps.alignSelf && resolvedProps.alignSelf !== 'auto'
          ? { alignSelf: resolvedProps.alignSelf }
          : undefined),
        ...(isEditMode && (isHiddenByDesign || runtimeHidden) ? { opacity: 0.3 } : undefined),
        ...(def.meta.wrapperStyle ?? undefined),
      }}
    >
      {isEditMode && isSelected && (
        <FloatingToolbar nodeId={nodeId} variant="selected" />
      )}
      {isEditMode && isHovered && !isSelected && (
        <FloatingToolbar nodeId={nodeId} variant="hovered" />
      )}
      {/* Status badges */}
      {isEditMode && (isHiddenByDesign || hasVisibilityExpression || hasExpressions) && (
        <div className="absolute top-1 right-1 z-10 flex gap-0.5 pointer-events-none">
          {isHiddenByDesign && (
            <div className="w-4 h-4 rounded-full bg-muted-foreground/20 flex items-center justify-center" title="Hidden">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
                <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
                <line x1="1" y1="1" x2="23" y2="23" />
              </svg>
            </div>
          )}
          {hasVisibilityExpression && (
            <div className="w-4 h-4 rounded-full bg-amber-500/20 flex items-center justify-center" title="Conditional visibility">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="rgb(245,158,11)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
              </svg>
            </div>
          )}
        </div>
      )}
      {/* Inject CSS for element state styles (hover/focus/active) */}
      {stateStyles && Object.keys(stateStyles).length > 0 && (
        <style>{generateStateCSS(nodeId, stateStyles)}</style>
      )}
      <Component id={nodeId} props={resolvedProps} onEvent={onEvent}>
        {renderedChildren}
      </Component>
    </div>
  )
})
