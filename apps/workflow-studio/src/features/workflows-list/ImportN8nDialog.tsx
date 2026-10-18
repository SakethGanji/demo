/**
 * "Import from n8n": paste (or choose) an n8n workflow JSON, preview what maps (engine does the
 * conversion, nothing is saved), then create it through the normal create endpoint and open it.
 */
import { useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { FileJson, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { projectsApi } from '@/shared/api/endpoints'
import { errorMessage } from '@/shared/api/http'
import { invalidate } from '@/shared/api/queryKeys'
import { workflowsApi } from '@/shared/lib/api'
import type { BackendWorkflow } from '@/shared/lib/backendTypes'
import type { ImportReport } from '@/features/workflow-editor/lib/shapes'
import { Button } from '@/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'
import { Textarea } from '@/shared/components/ui/textarea'
import { Callout, Well } from '@/shared/components/ui/surface'

interface Preview {
  workflow: BackendWorkflow
  report: ImportReport
}

function Section({ title, count, testid, children }: { title: string; count: number; testid: string; children?: React.ReactNode }) {
  return (
    <div data-testid={testid} data-count={count}>
      <div className="text-[12.5px] font-medium text-foreground">
        {title} <span className="text-muted-foreground">({count})</span>
      </div>
      {count > 0 && children && <div className="mt-1 space-y-0.5 text-[12px] text-muted-foreground">{children}</div>}
    </div>
  )
}

export function ImportN8nDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { pid, slug } = useProjectCtx()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const fileRef = useRef<HTMLInputElement>(null)
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState<'preview' | 'import' | null>(null)

  const reset = () => {
    setText('')
    setError(null)
    setPreview(null)
    setBusy(null)
  }

  const runPreview = async () => {
    setError(null)
    setPreview(null)
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch (e) {
      setError(`Not valid JSON: ${(e as Error).message}`)
      return
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setError('Expected an n8n workflow: a JSON object with "nodes" and "connections".')
      return
    }
    setBusy('preview')
    try {
      setPreview(await workflowsApi.importN8n({ n8n: parsed, project_id: pid }))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  const confirm = async () => {
    if (!preview) return
    setBusy('import')
    setError(null)
    try {
      const created = await projectsApi.createWorkflow(pid, preview.workflow)
      await invalidate.workflowsChanged(qc, pid)
      toast.success(`Imported ${created.name}`)
      onOpenChange(false)
      reset()
      void navigate({ to: '/p/$project/workflows/$workflowId', params: { project: slug, workflowId: created.id } })
    } catch (e) {
      setError(errorMessage(e))
      setBusy(null)
    }
  }

  const report = preview?.report
  const problems = report?.findings.filter((f) => f.severity !== 'info') ?? []

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o)
        if (!o) reset()
      }}
    >
      <DialogContent className="sm:max-w-[560px]" data-testid="import-n8n-dialog">
        <DialogHeader>
          <DialogTitle>Import from n8n</DialogTitle>
          <DialogDescription>Paste an exported n8n workflow. Nothing is saved until you import.</DialogDescription>
        </DialogHeader>

        <Textarea
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            setPreview(null)
            setError(null)
          }}
          placeholder='{"nodes": [...], "connections": {...}}'
          className="h-36 font-mono text-[12px]"
          data-testid="import-n8n-input"
        />
        <div className="flex items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            data-testid="import-n8n-file"
            onChange={async (e) => {
              const f = e.target.files?.[0]
              if (!f) return
              setText(await f.text())
              setPreview(null)
              setError(null)
              if (fileRef.current) fileRef.current.value = ''
            }}
          />
          <Button variant="ghost" size="sm" onClick={() => fileRef.current?.click()} data-testid="import-n8n-choose-file">
            <FileJson /> Choose a .json file
          </Button>
        </div>

        {error && (
          <Callout tone="error" role="alert" data-testid="import-n8n-error">
            {error}
          </Callout>
        )}

        {report && (
          <Well className="max-h-64 space-y-3 overflow-y-auto p-3" data-testid="import-n8n-report">
            <Section title="Mapped nodes" count={report.mapped.length} testid="import-n8n-mapped">
              {report.mapped.map((m, i) => (
                <div key={i}>
                  {m.n8n_name} <span className="text-faint">({m.n8n_type})</span> to {m.node_type}
                </div>
              ))}
            </Section>
            <Section title="Unmapped nodes" count={report.unmapped_nodes.length} testid="import-n8n-unmapped">
              {report.unmapped_nodes.map((u, i) => (
                <div key={i}>
                  {u.n8n_name} <span className="text-faint">({u.n8n_type})</span>: {u.reason}
                </div>
              ))}
            </Section>
            <Section title="Untranslated expressions" count={report.untranslated_expressions.length} testid="import-n8n-untranslated">
              {report.untranslated_expressions.map((x, i) => (
                <div key={i}>
                  {x.node} / {x.param}: <code className="font-mono">{x.expression}</code>
                </div>
              ))}
            </Section>
            <Section title="Code nodes switched off" count={report.disabled_code_nodes.length} testid="import-n8n-disabled-code">
              {report.disabled_code_nodes.map((n) => (
                <div key={n}>{n}</div>
              ))}
            </Section>
            <Section title="Problems found" count={problems.length} testid="import-n8n-findings">
              {problems.map((f, i) => (
                <div key={i} className={f.severity === 'error' ? 'text-destructive' : 'text-warning-text'}>
                  {f.code} {f.message}
                  {f.node ? ` (${f.node})` : ''}
                </div>
              ))}
            </Section>
          </Well>
        )}

        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="outline" size="sm" disabled={!text.trim() || busy !== null} onClick={runPreview} data-testid="import-n8n-preview">
            {busy === 'preview' && <Loader2 className="animate-spin" />} Preview
          </Button>
          <Button size="sm" disabled={!preview || busy !== null} onClick={confirm} data-testid="import-n8n-confirm">
            {busy === 'import' && <Loader2 className="animate-spin" />} Import
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
