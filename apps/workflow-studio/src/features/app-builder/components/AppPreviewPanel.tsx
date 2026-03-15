import { useState, useMemo, useEffect, useCallback } from 'react'
import { Eye, Code2, Sparkles } from 'lucide-react'
import { IframeSandbox } from '../sandbox/IframeSandbox'
import type { AppFile } from '../sandbox/esbuild-bundler'
import {
  FileTree,
  FileTreeFolder,
  FileTreeFile,
} from '@/components/ai-elements/file-tree'
import {
  CodeBlock,
  CodeBlockHeader,
  CodeBlockTitle,
  CodeBlockFilename,
  CodeBlockActions,
  CodeBlockCopyButton,
} from '@/components/ai-elements/code-block'
import type { BundledLanguage } from 'shiki'

type Tab = 'preview' | 'code'

interface AppPreviewPanelProps {
  files: AppFile[] | null
  onError: (err: { message: string; stack?: string }) => void
}

// ── Helpers ──────────────────────────────────────────────────────────────────

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
      if (!isFile) {
        current = existing.children!
      }
    }
  }

  // Sort: folders first, then files, alphabetical within each
  const sort = (nodes: TreeNode[]): TreeNode[] => {
    nodes.sort((a, b) => {
      if (a.isFile !== b.isFile) return a.isFile ? 1 : -1
      return a.name.localeCompare(b.name)
    })
    for (const n of nodes) {
      if (n.children) sort(n.children)
    }
    return nodes
  }

  return sort(root)
}

function getLanguageFromPath(path: string): BundledLanguage {
  const ext = path.split('.').pop()?.toLowerCase()
  switch (ext) {
    case 'tsx': return 'tsx'
    case 'ts': return 'typescript'
    case 'jsx': return 'jsx'
    case 'js': return 'javascript'
    case 'css': return 'css'
    case 'json': return 'json'
    case 'html': return 'html'
    case 'md': return 'markdown'
    default: return 'text' as BundledLanguage
  }
}

// ── Recursive tree renderer ──────────────────────────────────────────────────

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
        )
      )}
    </>
  )
}

// ── Component ────────────────────────────────────────────────────────────────

export function AppPreviewPanel({ files, onError }: AppPreviewPanelProps) {
  const [activeTab, setActiveTab] = useState<Tab>('preview')

  const [selectedFile, setSelectedFile] = useState<string | null>(null)

  // Auto-select first file when files change
  useEffect(() => {
    if (files?.length) {
      setSelectedFile((prev) => {
        if (prev && files.some((f) => f.path === prev)) return prev
        return files[0].path
      })
    } else {
      setSelectedFile(null)
    }
  }, [files])

  const tree = useMemo(() => (files ? buildFileTree(files) : []), [files])

  const selectedContent = useMemo(() => {
    if (!selectedFile || !files) return null
    return files.find((f) => f.path === selectedFile) ?? null
  }, [selectedFile, files])

  // Collect all folder paths for default expanded
  const defaultExpanded = useMemo(() => {
    const paths = new Set<string>()
    if (files) {
      for (const f of files) {
        const parts = f.path.split('/')
        for (let i = 1; i < parts.length; i++) {
          paths.add(parts.slice(0, i).join('/'))
        }
      }
    }
    return paths
  }, [files])

  const tabBtn = (tab: Tab, icon: React.ReactNode, label: string) => (
    <button
      onClick={() => setActiveTab(tab)}
      className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
        activeTab === tab
          ? 'bg-accent text-foreground'
          : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
      }`}
    >
      {icon}
      {label}
    </button>
  )

  return (
    <div className="h-full w-full flex flex-col overflow-hidden">
      {/* Tab bar */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-border/30 shrink-0">
        {tabBtn('preview', <Eye size={13} />, 'Preview')}
        {tabBtn('code', <Code2 size={13} />, 'Code')}
      </div>

      {/* Tab content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === 'preview' ? (
          <IframeSandbox files={files} onError={onError} />
        ) : (
          <div className="h-full flex overflow-hidden">
            {/* File tree sidebar */}
            {files?.length ? (
              <>
                <div className="w-[180px] shrink-0 border-r border-border/30 overflow-y-auto">
                  <FileTree
                    className="border-0 rounded-none bg-transparent"
                    selectedPath={selectedFile ?? undefined}
                    onSelect={setSelectedFile}
                    defaultExpanded={defaultExpanded}
                  >
                    <TreeNodes nodes={tree} />
                  </FileTree>
                </div>

                {/* Code viewer */}
                <div className="flex-1 min-w-0 overflow-auto h-full">
                  {selectedContent ? (
                    <CodeBlock
                      code={selectedContent.content}
                      language={getLanguageFromPath(selectedContent.path)}
                      showLineNumbers
                      className="border-0 rounded-none min-h-full [&_pre]:overflow-visible"
                    >
                      <CodeBlockHeader>
                        <CodeBlockTitle>
                          <CodeBlockFilename>{selectedContent.path}</CodeBlockFilename>
                        </CodeBlockTitle>
                        <CodeBlockActions>
                          <CodeBlockCopyButton />
                        </CodeBlockActions>
                      </CodeBlockHeader>
                    </CodeBlock>
                  ) : (
                    <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
                      Select a file to view its source.
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="h-full w-full flex items-center justify-center">
                <div className="text-center space-y-2 px-6" style={{ maxWidth: 280 }}>
                  <div className="w-10 h-10 rounded-full bg-muted flex items-center justify-center mx-auto">
                    <Sparkles size={20} className="text-muted-foreground" />
                  </div>
                  <p className="text-sm text-muted-foreground">
                    No files yet. Start a conversation to generate your app.
                  </p>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
