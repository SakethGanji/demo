import { useState, useMemo, useEffect } from 'react'
import { ExternalLink, Lock, Monitor, RotateCw, Smartphone, Sparkles, Tablet, Terminal } from 'lucide-react'
import { IframeSandbox, type BridgeCall } from '../sandbox/IframeSandbox'
import type { AppFile } from '../sandbox/esbuild-bundler'
import { FileTree, FileTreeFolder, FileTreeFile } from '@/components/ai-elements/file-tree'
import {
  CodeBlock,
  CodeBlockHeader,
  CodeBlockTitle,
  CodeBlockFilename,
  CodeBlockActions,
  CodeBlockCopyButton,
} from '@/components/ai-elements/code-block'
import type { BundledLanguage } from 'shiki'
import { Segmented } from '@/shared/components/ui/segmented'
import { Button } from '@/shared/components/ui/button'
import { Badge } from '@/shared/components/ui/badge'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { cn } from '@/shared/lib/utils'
import { BottomPanel } from '../panels'
import { useConsoleStore } from '../stores'
import { usePreviewSession } from '../stores/previewSessionStore'
import { formatMs } from '@/features/project/lib/format'
import { isClientPath } from '../lib/appModel'

export type BuilderMode = 'preview' | 'code'
type Device = 'desktop' | 'tablet' | 'phone'

interface AppPreviewPanelProps {
  workflowPaths: string[]
  projectSlug: string
  allowedRoutes: import('../sandbox/requestPolicy').PreviewRoute[]
  files: AppFile[] | null
  mode: BuilderMode
  /** the publish address shown in the URL pill */
  slug: string
  env: string
  publishedUrl: string | null
  showUrl: boolean
  getToken?: () => Promise<string | null>
  refreshToken?: () => Promise<string | null>
  onApiCall?: (c: BridgeCall) => void
  /** file to select when the Code view opens (e.g. api/client.ts from the Backend panel) */
  codeFocus?: string | null
}

// ── Code view helpers ───────────────────────────────────────────────────────

interface TreeNode {
  name: string
  path: string
  children?: TreeNode[]
  isFile: boolean
}

function buildFileTree(files: AppFile[]): TreeNode[] {
  const root: TreeNode[] = []
  for (const file of files) {
    const parts = file.path.split('/')
    let current = root
    for (let i = 0; i < parts.length; i++) {
      const name = parts[i]
      const path = parts.slice(0, i + 1).join('/')
      const isFile = i === parts.length - 1
      let existing = current.find((n) => n.name === name)
      if (!existing) {
        existing = { name, path, isFile, children: isFile ? undefined : [] }
        current.push(existing)
      }
      if (!isFile) current = existing.children!
    }
  }
  const sort = (nodes: TreeNode[]): TreeNode[] => {
    nodes.sort((a, b) => (a.isFile !== b.isFile ? (a.isFile ? 1 : -1) : a.name.localeCompare(b.name)))
    for (const n of nodes) if (n.children) sort(n.children)
    return nodes
  }
  return sort(root)
}

function getLanguageFromPath(path: string): BundledLanguage {
  const ext = path.split('.').pop()?.toLowerCase()
  switch (ext) {
    case 'tsx':
      return 'tsx'
    case 'ts':
      return 'typescript'
    case 'jsx':
      return 'jsx'
    case 'js':
      return 'javascript'
    case 'css':
      return 'css'
    case 'json':
      return 'json'
    case 'html':
      return 'html'
    case 'md':
      return 'markdown'
    default:
      return 'text' as BundledLanguage
  }
}

function TreeNodes({ nodes }: { nodes: TreeNode[] }) {
  return (
    <>
      {nodes.map((node) =>
        node.isFile ? (
          <FileTreeFile key={node.path} path={node.path} name={node.name} />
        ) : (
          <FileTreeFolder key={node.path} path={node.path} name={node.name}>
            {node.children && <TreeNodes nodes={node.children} />}
          </FileTreeFolder>
        ),
      )}
    </>
  )
}

function CodeView({ files, focus }: { files: AppFile[] | null; focus?: string | null }) {
  const [selected, setSelected] = useState<string | null>(null)
  useEffect(() => {
    if (focus && files?.some((f) => f.path === focus)) setSelected(focus)
  }, [focus, files])
  useEffect(() => {
    setSelected((prev) => {
      if (!files?.length) return null
      if (prev && files.some((f) => f.path === prev)) return prev
      return (files.find((f) => /^(src\/)?App\.tsx$/.test(f.path)) ?? files[0]).path
    })
  }, [files])
  const tree = useMemo(() => (files ? buildFileTree(files) : []), [files])
  const content = files?.find((f) => f.path === selected) ?? null
  const expanded = useMemo(() => {
    const s = new Set<string>()
    for (const f of files ?? []) {
      const parts = f.path.split('/')
      for (let i = 1; i < parts.length; i++) s.add(parts.slice(0, i).join('/'))
    }
    return s
  }, [files])
  if (!files?.length) {
    return (
      <EmptyState icon={<Sparkles />} title="No files yet" className="h-full">
        Describe the UI in the chat to generate it.
      </EmptyState>
    )
  }
  return (
    <div className="flex h-full overflow-hidden" data-testid="builder-code-view">
      <div className="w-[190px] shrink-0 overflow-y-auto border-r border-border">
        <FileTree
          className="rounded-none border-0 bg-transparent"
          selectedPath={selected ?? undefined}
          onSelect={setSelected}
          defaultExpanded={expanded}
        >
          <TreeNodes nodes={tree} />
        </FileTree>
      </div>
      <div className="h-full min-w-0 flex-1 overflow-auto" data-scroll-x="allowed">
        {content && (
          <CodeBlock
            code={content.content}
            language={getLanguageFromPath(content.path)}
            showLineNumbers
            className="min-h-full rounded-none border-0 [&_pre]:overflow-visible"
          >
            <CodeBlockHeader>
              <CodeBlockTitle>
                <CodeBlockFilename>
                  {content.path}
                  {isClientPath(content.path) && <span className="ml-2 text-muted-foreground">generated · read-only</span>}
                </CodeBlockFilename>
              </CodeBlockTitle>
              <CodeBlockActions>
                <CodeBlockCopyButton />
              </CodeBlockActions>
            </CodeBlockHeader>
          </CodeBlock>
        )}
      </div>
    </div>
  )
}

// ── Console strip ───────────────────────────────────────────────────────────

function PreviewConsoleStrip({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const last = usePreviewSession((s) => s.lastCall)
  const errors = useConsoleStore((s) => s.entries.filter((e) => e.level === 'error').length)
  const ok = last && last.status >= 200 && last.status < 300
  return (
    <div
      className="flex h-[30px] shrink-0 items-center gap-3 border-t border-border bg-card px-3 font-mono text-[11px] text-muted-foreground"
      data-testid="preview-console-strip"
    >
      <span className="flex min-w-0 items-center gap-1.5 overflow-hidden whitespace-nowrap" data-testid="preview-last-call">
        {last ? (
          <>
            <StatusDot tone={ok ? 'ok' : last.status >= 400 ? 'err' : 'warn'} />
            <span className="truncate">
              {last.method} {last.path} → {last.status} · {formatMs(last.ms)}
            </span>
          </>
        ) : (
          <span className="truncate">No /api/p/ calls yet</span>
        )}
      </span>
      <div className="flex-1" />
      <span className={cn('whitespace-nowrap', errors > 0 && 'text-destructive')} data-testid="preview-error-count">
        {errors} {errors === 1 ? 'error' : 'errors'}
      </span>
      <Button
        variant="ghost"
        size="xs"
        className={cn('font-sans', open && 'bg-muted text-foreground')}
        onClick={onToggle}
        data-testid="preview-console-toggle"
      >
        <Terminal /> Console
      </Button>
    </div>
  )
}

// ── Panel ───────────────────────────────────────────────────────────────────

const DEVICE_WIDTH: Record<Device, string> = { desktop: '100%', tablet: '768px', phone: '390px' }

export function AppPreviewPanel({
  files,
  projectSlug,
  allowedRoutes,
  workflowPaths,
  mode,
  slug,
  env,
  publishedUrl,
  showUrl,
  getToken,
  refreshToken,
  onApiCall,
  codeFocus,
}: AppPreviewPanelProps) {
  const [device, setDevice] = useState<Device>('desktop')
  const [reloadKey, setReloadKey] = useState(0)
  const [consoleOpen, setConsoleOpen] = useState(false)
  const appFiles = files?.filter((f) => !isClientPath(f.path)) ?? []

  if (mode === 'code') return <CodeView files={files} focus={codeFocus} />

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-2.5">
        <Segmented<Device>
          value={device}
          onChange={setDevice}
          aria-label="Device"
          options={[
            { key: 'desktop', label: <span className="sr-only">Desktop</span>, icon: <Monitor className="size-3.5" />, testId: 'preview-device-desktop' },
            { key: 'tablet', label: <span className="sr-only">Tablet</span>, icon: <Tablet className="size-3.5" />, testId: 'preview-device-tablet' },
            { key: 'phone', label: <span className="sr-only">Phone</span>, icon: <Smartphone className="size-3.5" />, testId: 'preview-device-phone' },
          ]}
        />
        {showUrl ? (
          <div
            className="mx-auto flex h-7 min-w-0 max-w-[460px] flex-1 items-center gap-2 rounded-md bg-muted px-2.5 font-mono text-[11.5px] text-muted-foreground"
            data-testid="preview-url"
          >
            <Lock className="size-3 shrink-0" />
            <span className="truncate">preview · /a/{slug}</span>
            <div className="flex-1" />
            <Badge variant="primary" className="h-[18px] px-1.5 text-[10.5px]">
              {env}
            </Badge>
          </div>
        ) : (
          <div className="flex-1" />
        )}
        <Tooltip>
          <TooltipTrigger
            render={
              <Button variant="ghost" size="icon-sm" onClick={() => setReloadKey((k) => k + 1)} aria-label="Reload preview" data-testid="preview-reload" />
            }
          >
            <RotateCw />
          </TooltipTrigger>
          <TooltipContent>Reload</TooltipContent>
        </Tooltip>
        {publishedUrl ? (
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-sm"
                  render={<a href={publishedUrl} target="_blank" rel="noreferrer noopener" />}
                  aria-label="Open published app"
                  data-testid="preview-open-tab"
                />
              }
            >
              <ExternalLink />
            </TooltipTrigger>
            <TooltipContent>Open the published app</TooltipContent>
          </Tooltip>
        ) : (
          <Button
            variant="ghost"
            size="icon-sm"
            disabled
            title="Publish to open it in a tab"
            aria-label="Open published app"
            data-testid="preview-open-tab"
          >
            <ExternalLink />
          </Button>
        )}
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden bg-muted/40">
        {appFiles.length === 0 ? (
          <EmptyState icon={<Sparkles />} title="No app yet" className="h-full" data-testid="preview-empty">
            Describe the UI in the chat and it appears here, calling your backend.
          </EmptyState>
        ) : (
          <div className="mx-auto h-full transition-[width] duration-150" style={{ width: DEVICE_WIDTH[device], maxWidth: '100%' }}>
            <IframeSandbox key={reloadKey} projectSlug={projectSlug} workflowPaths={workflowPaths} allowedRoutes={allowedRoutes} files={files} getToken={getToken} refreshToken={refreshToken} onApiCall={onApiCall} />
          </div>
        )}
        {consoleOpen && (
          <div className="absolute inset-x-0 bottom-0 z-20 h-[min(220px,45%)] border-t border-border bg-card">
            <BottomPanel />
          </div>
        )}
      </div>
      <PreviewConsoleStrip open={consoleOpen} onToggle={() => setConsoleOpen((o) => !o)} />
    </div>
  )
}
