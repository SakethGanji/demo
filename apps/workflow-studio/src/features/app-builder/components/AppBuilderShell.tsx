import { useEffect, useCallback, useState, useRef, useMemo } from 'react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import { Link } from '@tanstack/react-router'
import {
  Undo2,
  Redo2,
  Eye,
  Pencil,
  Save,
  Loader2,
  ChevronLeft,
  MoreHorizontal,
  Download,
  Upload,
  Trash2,
  Copy,
  Blocks,
  ScrollText,
  Paintbrush,
  Settings2,
  Zap,
  PanelLeftClose,
  PanelRightClose,
} from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import { ToolbarGroup, ToolbarSeparator } from '@/shared/components/ui/toolbar'
import { EditorTabList, EditorTab } from '@/shared/components/ui/editor-tabs'
import { cn } from '@/shared/lib/utils'
import { useAppDocumentStore, useAppEditorStore, useRuntimeStateStore, useThemeStore, useBreakpointStore } from '../stores'
import { ComponentPalette, ThemePanel, BottomPanel } from '../panels'
import { NodeWrapper, DropIndicator, Breadcrumbs, BreakpointBar, ContextMenu, QuickAddPalette } from '../canvas'
import { PropertyPanel, EventPanel } from '../inspector'
import { useAppSave, useAppLoad } from '../hooks'

// Ensure definitions are registered
import '../definitions'
import '../canvas-theme.css'

// ── Navbar button class (matches workflow editor) ────────────────────────────
const btnClass =
  'h-8 w-8 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors disabled:opacity-40 disabled:cursor-not-allowed'

// ── Status Bar ───────────────────────────────────────────────────────────────
function StatusBar() {
  const nodeCount = useAppDocumentStore(
    (s) => Object.keys(s.nodes).length
  )
  const storeCount = useAppDocumentStore((s) => s.storeDefinitions.length)
  const mode = useAppEditorStore((s) => s.mode)

  return (
    <div className="editor-chrome flex items-center h-6 px-3 bg-card border-t border-border text-[11px] text-muted-foreground shrink-0 select-none gap-3">
      <span>{nodeCount} components</span>
      <span className="text-border">·</span>
      <span>{storeCount} stores</span>
      <div className="flex-1" />
      <span className={mode === 'preview' ? 'text-[var(--success)]' : ''}>
        {mode === 'edit' ? 'Edit' : 'Preview'}
      </span>
    </div>
  )
}

// ── Canvas Area (extracted for breakpoint width) ────────────────────────────
function CanvasArea({
  mode,
  themeMode,
  canvasStyles,
  rootNodeId,
}: {
  mode: 'edit' | 'preview'
  themeMode: string
  canvasStyles: Record<string, string>
  rootNodeId: string
}) {
  const canvasWidth = useBreakpointStore((s) => {
    const bp = s.activeBreakpoint
    if (bp === 'desktop') return null
    return s.getCanvasWidth()
  })

  return (
    <div className={cn(
      'h-full w-full overflow-hidden flex flex-col',
      mode === 'edit' && 'editor-chrome bg-muted/50'
    )}>
      {mode === 'edit' && <BreakpointBar />}
      {mode === 'edit' && <Breadcrumbs />}
      <div className={cn('flex-1 min-h-0 flex justify-center', mode === 'edit' && 'p-4')}>
        <div
          className={themeMode === 'dark' ? 'dark h-full' : 'h-full'}
          style={{
            width: canvasWidth ? `${canvasWidth}px` : '100%',
            maxWidth: '100%',
            transition: 'width 0.3s ease',
          }}
        >
          <div
            className={cn(
              'h-full w-full app-canvas bg-background text-foreground',
              mode === 'edit' && 'overflow-auto',
              canvasWidth && mode === 'edit' && 'border-x border-border/50'
            )}
            style={canvasStyles}
          >
            <NodeWrapper nodeId={rootNodeId} />
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Main Shell ───────────────────────────────────────────────────────────────
export function AppBuilderShell({ appId }: { appId?: string }) {
  const rootNodeId = useAppDocumentStore((s) => s.rootNodeId)
  const appName = useAppDocumentStore((s) => s.appName)
  const setAppName = useAppDocumentStore((s) => s.setAppName)
  const mode = useAppEditorStore((s) => s.mode)
  const setMode = useAppEditorStore((s) => s.setMode)
  const [leftTab, setLeftTab] = useState<'components' | 'theme'>('components')
  const [rightTab, setRightTab] = useState<'customize' | 'events'>('customize')
  const [leftPanelOpen, setLeftPanelOpen] = useState(true)
  const [rightPanelOpen, setRightPanelOpen] = useState(true)
  const [bottomPanelOpen, setBottomPanelOpen] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const themeMode = useThemeStore((s) => s.mode)
  const lightOverrides = useThemeStore((s) => s.lightOverrides)
  const darkOverrides = useThemeStore((s) => s.darkOverrides)
  const activeOverrides = themeMode === 'light' ? lightOverrides : darkOverrides
  const canvasStyles = useMemo(() => {
    const styles: Record<string, string> = {}
    for (const [key, value] of Object.entries(activeOverrides)) {
      if (value) styles[`--${key}`] = value
    }
    if (activeOverrides.background) styles.backgroundColor = 'var(--background)'
    if (activeOverrides.foreground) styles.color = 'var(--foreground)'
    if (activeOverrides['font-sans']) styles.fontFamily = 'var(--font-sans)'
    return styles
  }, [activeOverrides])

  // Inline name editing
  const [isEditingName, setIsEditingName] = useState(false)
  const [editedName, setEditedName] = useState(appName)
  const nameInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setEditedName(appName)
  }, [appName])

  useEffect(() => {
    if (isEditingName && nameInputRef.current) {
      nameInputRef.current.focus()
      nameInputRef.current.select()
    }
  }, [isEditingName])

  const handleNameSubmit = () => {
    if (editedName.trim()) {
      setAppName(editedName.trim())
    } else {
      setEditedName(appName)
    }
    setIsEditingName(false)
  }

  // Save/Load
  const saveFn = useAppSave()
  useAppLoad(appId)

  const save = useCallback(async () => {
    setIsSaving(true)
    try {
      await saveFn()
    } finally {
      setIsSaving(false)
    }
  }, [saveFn])

  // Runtime lifecycle
  const nodes = useAppDocumentStore((s) => s.nodes)
  const storeDefs = useAppDocumentStore((s) => s.storeDefinitions)
  const initializeRuntime = useRuntimeStateStore((s) => s.initialize)

  const loadedAppId = useAppDocumentStore((s) => s.appId)

  useEffect(() => {
    initializeRuntime(nodes, storeDefs)
  }, [mode, loadedAppId])

  const undo = useCallback(() => {
    useAppDocumentStore.temporal.getState().undo()
  }, [])

  const redo = useCallback(() => {
    useAppDocumentStore.temporal.getState().redo()
  }, [])

  // Export
  const handleExport = useCallback(() => {
    const state = useAppDocumentStore.getState()
    const definition = state.toDefinition()
    const exportData = { name: state.appName, definition }
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${state.appName.replace(/\s+/g, '-').toLowerCase()}.json`
    a.click()
    URL.revokeObjectURL(url)
  }, [])

  // Import
  const fileInputRef = useRef<HTMLInputElement>(null)
  const handleImport = useCallback(() => {
    fileInputRef.current?.click()
  }, [])
  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    console.log('[import] file input changed', e.target.files)
    const file = e.target.files?.[0]
    if (!file) { console.log('[import] no file selected'); return }
    console.log('[import] reading file:', file.name, file.size, 'bytes')
    const reader = new FileReader()
    reader.onload = () => {
      console.log('[import] file read complete, length:', (reader.result as string)?.length)
      try {
        const data = JSON.parse(reader.result as string)
        console.log('[import] parsed JSON, keys:', Object.keys(data))
        const def = data.definition ?? data
        console.log('[import] definition keys:', Object.keys(def))
        console.log('[import] node count:', Object.keys(def.nodes || {}).length)
        console.log('[import] stores:', def.storeDefinitions?.length, 'webhooks:', def.webhookDefinitions?.length)
        const store = useAppDocumentStore.getState()
        console.log('[import] calling loadDocument...')
        store.loadDocument({
          nodes: def.nodes,
          rootNodeId: def.rootNodeId ?? 'ROOT',
          appId: store.appId ?? '',
          appName: data.name ?? 'Imported App',
          storeDefinitions: def.storeDefinitions,
          webhookDefinitions: def.webhookDefinitions,
          styleClasses: def.styleClasses,
        })
        console.log('[import] loadDocument done, node count now:', Object.keys(useAppDocumentStore.getState().nodes).length)
        // Re-initialize runtime with the imported document
        const fresh = useAppDocumentStore.getState()
        useRuntimeStateStore.getState().initialize(fresh.nodes, fresh.storeDefinitions)
        console.log('[import] runtime re-initialized')
      } catch (err) {
        console.error('[import] FAILED:', err)
      }
    }
    reader.onerror = () => console.error('[import] FileReader error:', reader.error)
    reader.readAsText(file)
    e.target.value = ''
  }, [])

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      const isInput =
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.tagName === 'SELECT' ||
        target.isContentEditable

      if ((e.metaKey || e.ctrlKey) && e.key === 'z') {
        e.preventDefault()
        if (e.shiftKey) redo()
        else undo()
        return
      }

      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault()
        save()
        return
      }

      if (isInput) return

      const editorState = useAppEditorStore.getState()

      if (e.key === 'Escape') {
        // Close context menu first, then clear selection
        if (editorState.contextMenu) {
          editorState.closeContextMenu()
        } else {
          editorState.clearSelection()
        }
        return
      }

      if (e.key === 'Delete' || e.key === 'Backspace') {
        const { selectedNodeIds } = editorState
        if (selectedNodeIds.length > 0) {
          e.preventDefault()
          if (selectedNodeIds.length > 1) {
            useAppDocumentStore.getState().deleteNodes([...selectedNodeIds])
          } else {
            useAppDocumentStore.getState().deleteNode(selectedNodeIds[0])
          }
          editorState.clearSelection()
        }
        return
      }

      // Copy
      if ((e.metaKey || e.ctrlKey) && e.key === 'c') {
        if (editorState.selectedNodeIds.length > 0) {
          e.preventDefault()
          editorState.copySelection()
        }
        return
      }

      // Cut
      if ((e.metaKey || e.ctrlKey) && e.key === 'x') {
        if (editorState.selectedNodeIds.length > 0) {
          e.preventDefault()
          editorState.cutSelection()
        }
        return
      }

      // Paste
      if ((e.metaKey || e.ctrlKey) && e.key === 'v') {
        const { clipboard, selectedNodeIds } = editorState
        if (clipboard) {
          e.preventDefault()
          const docStore = useAppDocumentStore.getState()

          if (clipboard.mode === 'copy') {
            const newIds = docStore.duplicateNodes(clipboard.nodeIds)
            if (newIds.length > 0) editorState.selectNode(newIds[0])
          } else {
            // Cut: move nodes to selected container or sibling's parent
            const targetId = selectedNodeIds[0]
            const targetNode = targetId ? docStore.nodes[targetId] : null
            const parentId = targetNode?.isCanvas ? targetId : (targetNode?.parentId ?? docStore.rootNodeId)
            const parent = docStore.nodes[parentId]
            if (parent) {
              for (const id of clipboard.nodeIds) {
                docStore.moveNode(id, parentId, parent.childIds.length)
              }
            }
            editorState.clearClipboard()
          }
        }
        return
      }

      // Select all siblings
      if ((e.metaKey || e.ctrlKey) && e.key === 'a') {
        const selectedId = editorState.selectedNodeIds[0]
        if (selectedId) {
          e.preventDefault()
          const node = useAppDocumentStore.getState().nodes[selectedId]
          if (node?.parentId) {
            const parent = useAppDocumentStore.getState().nodes[node.parentId]
            if (parent) {
              editorState.selectRange(
                parent.childIds[0],
                parent.childIds[parent.childIds.length - 1],
                useAppDocumentStore.getState().nodes
              )
            }
          }
        }
        return
      }

      // Quick add palette
      if (e.key === '/') {
        e.preventDefault()
        editorState.openQuickAdd()
        return
      }

      if ((e.metaKey || e.ctrlKey) && e.key === 'd') {
        const { selectedNodeIds } = editorState
        if (selectedNodeIds.length > 0) {
          e.preventDefault()
          if (selectedNodeIds.length > 1) {
            const newIds = useAppDocumentStore.getState().duplicateNodes([...selectedNodeIds])
            if (newIds.length > 0) editorState.selectNode(newIds[0])
          } else {
            const newId = useAppDocumentStore.getState().duplicateNode(selectedNodeIds[0])
            if (newId) editorState.selectNode(newId)
          }
        }
        return
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [undo, redo, save])

  return (
    <div className="h-full w-full flex flex-col bg-background">
      {/* ── Navbar ──────────────────────────────────────────────────────────── */}
      <div className="editor-chrome flex items-center h-11 px-3 bg-card border-b border-border shrink-0 gap-1">
        {/* Left: Back + Name */}
        <Link
          to="/apps"
          className="h-8 px-1.5 flex items-center gap-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors text-xs"
          title="Back to apps"
        >
          <ChevronLeft size={14} />
          <span className="hidden sm:inline">Apps</span>
        </Link>

        <ToolbarSeparator />

        {/* App name (inline editable) */}
        {isEditingName ? (
          <input
            ref={nameInputRef}
            type="text"
            value={editedName}
            onChange={(e) => setEditedName(e.target.value)}
            onBlur={handleNameSubmit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleNameSubmit()
              if (e.key === 'Escape') {
                setEditedName(appName)
                setIsEditingName(false)
              }
            }}
            className="h-8 w-36 px-2 text-[13px] font-medium text-foreground bg-transparent outline-none border border-border rounded-md"
          />
        ) : (
          <button
            onClick={() => {
              setEditedName(appName)
              setIsEditingName(true)
            }}
            className="h-8 px-2 text-[13px] font-medium text-foreground hover:bg-accent transition-colors rounded-md truncate max-w-40"
            title="Click to rename"
          >
            {appName}
          </button>
        )}

        {/* Center */}
        <div className="flex-1" />

        {/* Undo/Redo */}
        <ToolbarGroup>
          <button onClick={undo} className={btnClass} title="Undo (Ctrl+Z)">
            <Undo2 size={14} />
          </button>
          <button onClick={redo} className={btnClass} title="Redo (Ctrl+Shift+Z)">
            <Redo2 size={14} />
          </button>
        </ToolbarGroup>

        <ToolbarSeparator />

        {/* Edit / Preview toggle */}
        <ToolbarGroup>
          <button
            onClick={() => setMode('edit')}
            className={btnClass + (mode === 'edit' ? ' !text-primary' : '')}
            title="Edit mode"
          >
            <Pencil size={14} />
          </button>
          <button
            onClick={() => setMode('preview')}
            className={btnClass + (mode === 'preview' ? ' !text-primary' : '')}
            title="Preview mode"
          >
            <Eye size={14} />
          </button>
        </ToolbarGroup>

        {/* Right section */}
        <div className="flex-1" />

        {/* Panel toggles */}
        <ToolbarGroup>
          {mode === 'edit' && (
            <button
              onClick={() => setLeftPanelOpen(!leftPanelOpen)}
              className={btnClass + (leftPanelOpen ? ' !text-primary' : '')}
              title="Toggle left panel"
            >
              <PanelLeftClose size={14} />
            </button>
          )}
          <button
            onClick={() => setBottomPanelOpen(!bottomPanelOpen)}
            className={btnClass + (bottomPanelOpen ? ' !text-primary' : '')}
            title="Toggle console"
          >
            <ScrollText size={14} />
          </button>
          {mode === 'edit' && (
            <button
              onClick={() => setRightPanelOpen(!rightPanelOpen)}
              className={btnClass + (rightPanelOpen ? ' !text-primary' : '')}
              title="Toggle right panel"
            >
              <PanelRightClose size={14} />
            </button>
          )}
        </ToolbarGroup>

        <ToolbarSeparator />

        {/* Save */}
        <button
          onClick={save}
          disabled={isSaving}
          className={btnClass}
          title="Save (Ctrl+S)"
        >
          {isSaving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
        </button>

        {/* More options */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className={btnClass}>
              <MoreHorizontal size={14} />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem>
              <Copy size={14} className="mr-2" />
              Duplicate
            </DropdownMenuItem>
            <DropdownMenuItem onClick={handleExport}>
              <Download size={14} className="mr-2" />
              Export JSON
            </DropdownMenuItem>
            <DropdownMenuItem onClick={handleImport}>
              <Upload size={14} className="mr-2" />
              Import JSON
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-destructive focus:text-destructive">
              <Trash2 size={14} className="mr-2" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Hidden file input for import (must be outside dropdown to persist after close) */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json"
        onChange={handleFileChange}
        className="hidden"
      />

      {/* ── Panels ─────────────────────────────────────────────────────────── */}
      <PanelGroup direction="horizontal" className="flex-1 min-h-0" id="app-builder-h">
        {/* Left — Components / Theme */}
        {mode === 'edit' && leftPanelOpen && (
          <>
            <Panel id="left-panel" order={1} defaultSize={15} minSize={12} maxSize={25}>
              <div className="editor-chrome h-full flex flex-col bg-card">
                <EditorTabList>
                  <EditorTab active={leftTab === 'components'} icon={Blocks} onClick={() => setLeftTab('components')}>
                    Components
                  </EditorTab>
                  <EditorTab active={leftTab === 'theme'} icon={Paintbrush} onClick={() => setLeftTab('theme')}>
                    Theme
                  </EditorTab>
                </EditorTabList>
                <div className="flex-1 min-h-0 overflow-hidden">
                  {leftTab === 'components' && <ComponentPalette />}
                  {leftTab === 'theme' && <ThemePanel />}
                </div>
              </div>
            </Panel>
            <PanelResizeHandle className="w-px bg-border hover:bg-primary/50 transition-colors data-[resize-handle-active]:bg-primary/50" />
          </>
        )}

        {/* Center — Canvas + Bottom Panel */}
        <Panel id="center-panel" order={2} defaultSize={mode === 'edit' ? 65 : 100} minSize={40}>
          <PanelGroup direction="vertical" id="app-builder-v">
            <Panel id="canvas-panel" order={1} defaultSize={bottomPanelOpen ? 70 : 100} minSize={30}>
              <CanvasArea
                mode={mode}
                themeMode={themeMode}
                canvasStyles={canvasStyles}
                rootNodeId={rootNodeId}
              />
            </Panel>

            {bottomPanelOpen && (
              <>
                <PanelResizeHandle className="h-px bg-border hover:bg-primary/50 transition-colors data-[resize-handle-active]:bg-primary/50" />
                <Panel id="bottom-panel" order={2} defaultSize={30} minSize={15} maxSize={60}>
                  <BottomPanel />
                </Panel>
              </>
            )}
          </PanelGroup>
        </Panel>

        {/* Right — Props / Events */}
        {mode === 'edit' && rightPanelOpen && (
          <>
            <PanelResizeHandle className="w-px bg-border hover:bg-primary/50 transition-colors data-[resize-handle-active]:bg-primary/50" />
            <Panel id="right-panel" order={3} defaultSize={20} minSize={15} maxSize={30}>
              <div className="editor-chrome h-full flex flex-col bg-card">
                <EditorTabList>
                  <EditorTab
                    active={rightTab === 'customize'}
                    icon={Settings2}
                    onClick={() => setRightTab('customize')}
                  >
                    Props
                  </EditorTab>
                  <EditorTab
                    active={rightTab === 'events'}
                    icon={Zap}
                    onClick={() => setRightTab('events')}
                  >
                    Events
                  </EditorTab>
                  <div className="flex-1" />
                </EditorTabList>
                <div className="flex-1 min-h-0 overflow-y-auto">
                  {rightTab === 'customize' ? (
                    <PropertyPanel />
                  ) : (
                    <EventPanel />
                  )}
                </div>
              </div>
            </Panel>
          </>
        )}
      </PanelGroup>

      {/* ── Status Bar ─────────────────────────────────────────────────────── */}
      <StatusBar />

      <DropIndicator />
      <ContextMenu />
      <QuickAddPalette />
      <div id="app-builder-overlay" className="pointer-events-none fixed inset-0 z-50" />
    </div>
  )
}
