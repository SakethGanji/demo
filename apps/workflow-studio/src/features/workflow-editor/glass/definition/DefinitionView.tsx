/**
 * The Definition view: the workflow's JSON, read-only. Each node's object is a span linked to its step: hover
 * highlights it in the other pane (Side by side), click selects it. Download saves the JSON; Import replaces the
 * draft from a file. A plain <pre> (not a code editor) so node spans can carry links.
 */
import { useMemo, useRef } from 'react'
import { cx } from '@/shared/glass/cx'
import { useImportWorkflow } from '../../hooks/useWorkflowApi'
import { useGlassStore } from '../state/glassStore'

export interface DefinitionDoc {
  id?: string
  name: string
  nodes: Array<{ name: string } & Record<string, unknown>>
  connections: Array<Record<string, unknown>>
}

const indent = (s: string, n: number) => s.replace(/\n/g, `\n${' '.repeat(n)}`)

export function downloadJson(doc: DefinitionDoc) {
  const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${doc.name.replace(/\s+/g, '_') || 'workflow'}.json`
  a.click()
  URL.revokeObjectURL(url)
}

export function DefinitionView({ doc, version, compact, readOnly }: { doc: DefinitionDoc; version?: number | null; compact?: boolean; readOnly?: boolean }) {
  const hover = useGlassStore((s) => s.hoverNode)
  const setHover = useGlassStore((s) => s.setHoverNode)
  const selected = useGlassStore((s) => s.selectedNode)
  const select = useGlassStore((s) => s.select)
  const { importWorkflow } = useImportWorkflow()
  const fileRef = useRef<HTMLInputElement>(null)

  const rest = useMemo(() => {
    const { nodes: _n, connections: _c, ...head } = doc
    return head
  }, [doc])

  const onImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    const r = new FileReader()
    r.onload = () => importWorkflow(String(r.result))
    r.readAsText(f)
    e.target.value = ''
  }

  const headLines = Object.entries(rest)
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => `  ${JSON.stringify(k)}: ${indent(JSON.stringify(v, null, 2), 2)},`)

  return (
    <div className={cx('gp-def', compact && 'is-compact')} data-testid="glass-definition">
      <div className="gp-def-head">
        <span className="gp-mono" data-testid="glass-definition-title">
          {doc.name.replace(/\s+/g, '_') || 'workflow'}.json{version ? ` · v${version}` : ''} · {doc.nodes.length} nodes · {doc.connections.length} connections · read-only
        </span>
        <span className="gp-flex" />
        <button type="button" className="gl-btn" data-size="sm" onClick={() => downloadJson(doc)} data-testid="glass-definition-download">
          Download
        </button>
        {!readOnly && (
          <>
            <button type="button" className="gl-btn" data-variant="ghost" data-size="sm" onClick={() => fileRef.current?.click()} data-testid="glass-definition-import">
              Import…
            </button>
            <input ref={fileRef} type="file" accept=".json,application/json" className="hidden" onChange={onImport} />
          </>
        )}
      </div>
      <pre className="gp-def-pre" onMouseLeave={() => hover && setHover(null)}>
        {'{\n'}
        {headLines.join('\n')}
        {headLines.length ? '\n' : ''}
        {'  "nodes": [\n'}
        {doc.nodes.map((n, i) => (
          <span key={n.name}>
            {'    '}
            <span
              className={cx('gp-def-node', (hover === n.name || selected === n.name) && 'is-on')}
              data-node={n.name}
              data-testid="glass-definition-node"
              onMouseEnter={() => setHover(n.name)}
              onClick={() => select(n.name)}
            >
              {indent(JSON.stringify(n, null, 2), 4)}
            </span>
            {i < doc.nodes.length - 1 ? ',\n' : '\n'}
          </span>
        ))}
        {'  ],\n  "connections": [\n'}
        {doc.connections.map((c, i) => {
          const on = hover !== null && (c.source_node === hover || c.target_node === hover)
          return (
            <span key={i} className={cx('gp-def-conn', on && 'is-on')}>
              {'    '}
              {JSON.stringify(c)}
              {i < doc.connections.length - 1 ? ',\n' : '\n'}
            </span>
          )
        })}
        {'  ]\n}'}
      </pre>
    </div>
  )
}
