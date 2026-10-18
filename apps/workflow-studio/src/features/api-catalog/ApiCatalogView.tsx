/**
 * API catalog (CONTRACTS-STAGE3A §6, §8): the outside APIs a project's workflows call. A spec (OpenAPI 3,
 * pasted, fetched from a URL or uploaded, or inferred from one example) gives an HttpRequest node its endpoint
 * list, the path parameters, and the replies the tests fake.
 */
import { useRef, useState } from 'react'
import { ChevronRight, Plug, Plus, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { errorMessage } from '@/shared/api/http'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Dot, PageHeader, Tile, Page, Callout } from '@/shared/components/ui/surface'
import { Textarea } from '@/shared/components/ui/textarea'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog'
import type { ApiSpecSummary } from '@/features/workflow-editor/lib/tests'
import { useApiSpec, useApiSpecs, useCreateApiSpec, useCreateApiSpecFromExample, useDeleteApiSpec } from './useApiSpecs'

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
const SOURCE_LABEL: Record<string, string> = { paste: 'Pasted', upload: 'Uploaded', url: 'From URL', example: 'From an example' }

export function ApiCatalogView() {
  const { pid } = useProjectCtx()
  const specs = useApiSpecs(pid)
  const [addOpen, setAddOpen] = useState(false)
  const [toDelete, setToDelete] = useState<ApiSpecSummary | null>(null)
  const del = useDeleteApiSpec(pid)
  const list = specs.data ?? []

  return (
    <div className="h-full" data-testid="api-catalog">
      <Page>
        <PageHeader
          title="External APIs"
          icon={<Plug />}
          hue="data"
          meta={
            <>
              <span>
                {list.length} {list.length === 1 ? 'API' : 'APIs'}
              </span>
              <Dot />
              <span>Add a spec once. HTTP Request nodes pick an endpoint from it, and tests fake its replies without calling the real system.</span>
            </>
          }
          actions={
            <Button size="sm" onClick={() => setAddOpen(true)} data-testid="api-add">
              <Plus /> Add API
            </Button>
          }
        />

        <div className="mt-6 flex flex-col gap-3">
          {specs.isPending ? (
            <Skeleton className="h-20 w-full" />
          ) : specs.isError ? (
            <Tile>
              <EmptyState title="Couldn't load the APIs" data-testid="api-error">
                {errorMessage(specs.error)}
              </EmptyState>
            </Tile>
          ) : list.length === 0 ? (
            <Tile>
              <EmptyState icon={<Plug />} title="No APIs yet" data-testid="api-empty" actions={<Button size="sm" onClick={() => setAddOpen(true)}>Add API</Button>}>
                Paste an OpenAPI 3 spec, fetch one from a URL, or describe one call by pasting an example reply.
              </EmptyState>
            </Tile>
          ) : (
            list.map((s) => <SpecRow key={s.id} spec={s} pid={pid} onDelete={() => setToDelete(s)} />)
          )}
        </div>
      </Page>

      <AddApiDialog open={addOpen} onOpenChange={setAddOpen} pid={pid} />

      <AlertDialog open={!!toDelete} onOpenChange={(o) => !o && setToDelete(null)}>
        <AlertDialogContent data-testid="api-delete-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {toDelete?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              HTTP Request nodes that point at it will show a missing-endpoint problem until you pick another.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              data-testid="api-delete-confirm"
              onClick={() => {
                if (!toDelete) return
                del.mutate(toDelete.id, {
                  onSuccess: () => toast.success(`${toDelete.name} deleted`),
                  onError: (e) => toast.error("Couldn't delete the API", { description: errorMessage(e) }),
                })
                setToDelete(null)
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function SpecRow({ spec, pid, onDelete }: { spec: ApiSpecSummary; pid: string; onDelete: () => void }) {
  const [open, setOpen] = useState(true)
  const detail = useApiSpec(pid, open ? spec.id : undefined)
  return (
    <Tile data-testid="api-row" data-spec-id={spec.id} data-name={spec.name}>
      <div className="flex items-center gap-2 px-3 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={open ? 'Hide endpoints' : 'Show endpoints'}
          className="flex size-6 items-center justify-center rounded-chip text-muted-foreground transition-colors hover:bg-(--hover-fill)"
        >
          <ChevronRight size={14} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13.5px] font-semibold text-foreground">{spec.name}</div>
          <div className="truncate font-mono text-[11.5px] text-muted-foreground">{spec.base_url || 'no base URL'}</div>
        </div>
        <Badge variant="neutral">{SOURCE_LABEL[spec.source] ?? spec.source}</Badge>
        <span className="text-[12px] tabular-nums text-muted-foreground" data-testid="api-operation-count">
          {spec.operation_count} {spec.operation_count === 1 ? 'endpoint' : 'endpoints'}
        </span>
        <Button variant="ghost" size="icon-sm" aria-label={`Delete ${spec.name}`} onClick={onDelete} data-testid="api-delete">
          <Trash2 />
        </Button>
      </div>
      {open && (
        <div className="max-h-72 overflow-y-auto px-3 pb-2.5">
          {detail.isPending ? (
            <Skeleton className="h-8 w-full" />
          ) : detail.isError ? (
            <div className="px-2 py-1 text-[12.5px] text-destructive">{errorMessage(detail.error)}</div>
          ) : (
            detail.data.operations.map((op) => (
              <div
                key={op.operation_id}
                data-testid="api-operation-row"
                data-operation-id={op.operation_id}
                className="flex items-center gap-2.5 rounded-ctl px-2 py-1.5 text-[12.5px] hover:bg-(--hover-fill)"
              >
                <MethodBadge method={op.method} />
                <span className="min-w-0 truncate font-mono text-[12px] text-foreground">{op.path}</span>
                <span className="min-w-0 flex-1 truncate text-muted-foreground">{op.summary}</span>
                <span className="shrink-0 text-[11.5px] text-muted-foreground">
                  {Object.keys(op.responses ?? {}).join(' ')}
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </Tile>
  )
}

function AddApiDialog({ open, onOpenChange, pid }: { open: boolean; onOpenChange: (o: boolean) => void; pid: string }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl" data-testid="api-add-dialog">
        {open && <AddApiForm pid={pid} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  )
}

function AddApiForm({ pid, onDone }: { pid: string; onDone: () => void }) {
  const create = useCreateApiSpec(pid)
  const fromExample = useCreateApiSpecFromExample(pid)
  const [name, setName] = useState('')
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [fileName, setFileName] = useState('')
  const [exampleOpen, setExampleOpen] = useState(false)
  const [method, setMethod] = useState('GET')
  const [baseUrl, setBaseUrl] = useState('')
  const [path, setPath] = useState('')
  const [status, setStatus] = useState('200')
  const [example, setExample] = useState('')
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const busy = create.isPending || fromExample.isPending

  const onFile = async (f: File | undefined) => {
    if (!f) return
    setFileName(f.name)
    setText(await f.text())
    if (!name.trim()) setName(f.name.replace(/\.(json|ya?ml)$/i, ''))
  }

  const canSave = exampleOpen
    ? !!name.trim() && !!path.trim() && example.trim() !== ''
    : text.trim() !== '' || url.trim() !== ''

  const done = (n: string) => {
    toast.success(`${n} added`)
    onDone()
  }

  const save = () => {
    setError(null)
    if (exampleOpen) {
      let parsed: unknown
      try {
        parsed = JSON.parse(example)
      } catch {
        setError('The example is not valid JSON')
        return
      }
      const st = Number(status)
      fromExample.mutate(
        { name: name.trim(), method, base_url: baseUrl.trim(), path: path.trim(), status: Number.isInteger(st) ? st : 200, example: parsed },
        { onSuccess: (d) => done(d.name), onError: (e) => setError(errorMessage(e)) },
      )
      return
    }
    create.mutate(
      {
        ...(name.trim() ? { name: name.trim() } : {}),
        ...(text.trim() ? { spec_text: text } : { url: url.trim() }),
      },
      { onSuccess: (d) => done(d.name), onError: (e) => setError(errorMessage(e)) },
    )
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Add an API</DialogTitle>
        <DialogDescription>OpenAPI 3.0 or 3.1, as JSON or YAML.</DialogDescription>
      </DialogHeader>

      <div className="flex max-h-[60vh] flex-col gap-3 overflow-y-auto pr-1">
        <Field label="Name" help="Optional. Defaults to the spec's title.">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Payments API" data-testid="api-name" />
        </Field>

        {!exampleOpen && (
          <>
            <Field label="Paste the spec">
              <Textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={6}
                spellCheck={false}
                placeholder={'openapi: 3.0.0\ninfo: ...'}
                className="font-mono text-[12px]"
                data-testid="api-spec-input"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Or fetch it from a URL">
                <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://api.example.com/openapi.json" data-testid="api-spec-url" />
              </Field>
              <Field label="Or upload a file">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".json,.yaml,.yml,application/json"
                  className="sr-only"
                  onChange={(e) => void onFile(e.target.files?.[0])}
                  data-testid="api-spec-file"
                />
                <Button variant="outline" onClick={() => fileRef.current?.click()} className="justify-start">
                  <Upload /> <span className="truncate">{fileName || 'Choose a file'}</span>
                </Button>
              </Field>
            </div>
          </>
        )}

        {exampleOpen && (
          <div className="flex flex-col gap-3" data-testid="api-example">
            <div className="grid grid-cols-[7rem_1fr] gap-3">
              <Field label="Method">
                <Select value={method} onValueChange={(v) => v && setMethod(v)} items={Object.fromEntries(METHODS.map((m) => [m, m]))}>
                  <SelectTrigger className="w-full" data-testid="api-example-method">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {METHODS.map((m) => (
                      <SelectItem key={m} value={m}>{m}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Base URL">
                <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.example.com" data-testid="api-example-base-url" />
              </Field>
            </div>
            <div className="grid grid-cols-[1fr_6rem] gap-3">
              <Field label="Path">
                <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/orders/{order_id}" data-testid="api-example-path" />
              </Field>
              <Field label="Reply status">
                <Input value={status} onChange={(e) => setStatus(e.target.value)} inputMode="numeric" data-testid="api-example-status" />
              </Field>
            </div>
            <Field label="One example reply (JSON)" help="The reply's shape is inferred from it.">
              <Textarea
                value={example}
                onChange={(e) => setExample(e.target.value)}
                rows={6}
                spellCheck={false}
                placeholder={'{ "id": 42, "status": "paid" }'}
                className="font-mono text-[12px]"
                data-testid="api-example-input"
              />
            </Field>
          </div>
        )}

        <button
          type="button"
          onClick={() => {
            setExampleOpen((o) => !o)
            setError(null)
          }}
          className="self-start text-[12.5px] text-primary hover:underline"
          data-testid="api-example-toggle"
        >
          {exampleOpen ? 'Use a spec instead' : 'No spec? Paste one example reply'}
        </button>

        {error && (
          <Callout tone="error" role="alert" data-testid="api-save-error">
            {error}
          </Callout>
        )}
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={onDone}>Cancel</Button>
        <Button onClick={save} disabled={busy || !canSave} data-testid="api-save">
          {busy ? 'Adding…' : 'Add API'}
        </Button>
      </DialogFooter>
    </>
  )
}
