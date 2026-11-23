import { useState } from 'react'
import { ChevronRight, Play } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Textarea } from '@/shared/components/ui/textarea'
import { Callout } from '@/shared/components/ui/surface'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { errorMessage } from '@/shared/api/http'
import { cn } from '@/shared/lib/utils'
import { connectionsApi } from './connectionsApi'
import { cellText, rowEstimateLabel, tableId } from './connectionsLogic'
import type { Connection, SchemaTable } from './connectionsTypes'
import { useConnectionSchema } from './useConnections'

function TableRowItem({ t }: { t: SchemaTable }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-b border-hairline last:border-0" data-testid={`schema-table-${t.name}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex h-9 w-full items-center gap-2 px-3 text-left text-[13px] hover:bg-foreground/4"
      >
        <ChevronRight className={cn('size-3.5 text-muted-foreground transition-transform', open && 'rotate-90')} />
        <span className="truncate font-mono">{tableId(t)}</span>
        {t.kind !== 'table' && <Badge variant="neutral">{t.kind}</Badge>}
        <span className="ml-auto text-[12px] tabular-nums text-muted-foreground">{rowEstimateLabel(t.row_estimate)}</span>
      </button>
      {open && (
        <div className="flex flex-col gap-1 px-3 pb-2.5 pl-9">
          {t.columns.map((c) => (
            <div key={c.name} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12.5px]" data-testid={`schema-col-${t.name}-${c.name}`}>
              <span className="font-mono">{c.name}</span>
              <span className="font-mono text-muted-foreground">{c.type}{c.nullable ? '' : ' not null'}</span>
              {c.pk && <Badge variant="primary">pk</Badge>}
              {c.fk && <Badge variant="outline">fk {c.fk.table}.{c.fk.column}</Badge>}
              {c.samples.length > 0 && (
                <span className="min-w-0 truncate text-muted-foreground">e.g. {c.samples.slice(0, 3).map(cellText).join(', ')}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function QueryBox({ pid, id }: { pid: string; id: string }) {
  const [sql, setSql] = useState('select 1')
  const run = useMutation({ mutationFn: () => connectionsApi.query(pid, id, sql, 100) })
  const r = run.data
  return (
    <div className="flex flex-col gap-2" data-testid="schema-query">
      <Textarea value={sql} onChange={(e) => setSql(e.target.value)} rows={3} className="font-mono text-[12.5px]" spellCheck={false} aria-label="SQL" data-testid="query-sql" />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={() => run.mutate()} disabled={!sql.trim() || run.isPending} data-testid="query-run">
          <Play /> {run.isPending ? 'Running…' : 'Run'}
        </Button>
        <span className="text-[12px] text-muted-foreground">Read-only.</span>
        {r && <span className="ml-auto text-[12px] tabular-nums text-muted-foreground">{r.rows.length} row{r.rows.length === 1 ? '' : 's'} · {Math.round(r.ms)} ms</span>}
      </div>
      {run.isError && <Callout tone="error" data-testid="query-error">{errorMessage(run.error)}</Callout>}
      {r && (
        <div className="max-h-64 overflow-auto rounded-ctl bg-recess" data-testid="query-result">
          <Table dense>
            <TableHeader>
              <TableRow>{r.columns.map((c) => <TableHead key={c} className="font-mono">{c}</TableHead>)}</TableRow>
            </TableHeader>
            <TableBody>
              {r.rows.map((row, i) => (
                <TableRow key={i}>
                  {r.columns.map((c, j) => (
                    <TableCell key={c + j} className={cn('max-w-[240px] truncate font-mono text-[12.5px]', row[j] == null && 'text-muted-foreground')}>
                      {cellText(row[j])}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {r.truncated && <div className="px-3 py-2 text-[12px] text-muted-foreground" data-testid="query-truncated">Showing the first {r.rows.length} rows; more were not returned.</div>}
        </div>
      )}
    </div>
  )
}

export function SchemaBrowser({ pid, connection, onClose }: { pid: string; connection: Connection | null; onClose: () => void }) {
  const schema = useConnectionSchema(pid, connection?.id)
  const tables = schema.data?.tables ?? []
  return (
    <Dialog open={!!connection} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[85vh] gap-3 overflow-y-auto sm:max-w-[720px]" data-testid="schema-browser">
        <DialogHeader>
          <DialogTitle>{connection?.name}</DialogTitle>
          <DialogDescription>{tables.length ? `${tables.length} tables` : 'Tables, columns and a read-only query box.'}</DialogDescription>
        </DialogHeader>
        {schema.isPending ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : schema.isError ? (
          <Callout tone="error" data-testid="schema-error">
            Couldn't load the schema · {errorMessage(schema.error)}
          </Callout>
        ) : tables.length === 0 ? (
          <div className="rounded-ctl bg-recess px-3 py-5 text-[13px] text-muted-foreground" data-testid="schema-empty">
            No tables yet. Use Refresh schema on the connection to read them.
          </div>
        ) : (
          <div className="max-h-[320px] overflow-y-auto rounded-ctl bg-recess" data-testid="schema-tables">
            {tables.map((t) => <TableRowItem key={`${t.schema}.${t.name}`} t={t} />)}
          </div>
        )}
        {connection && <QueryBox pid={pid} id={connection.id} />}
      </DialogContent>
    </Dialog>
  )
}
