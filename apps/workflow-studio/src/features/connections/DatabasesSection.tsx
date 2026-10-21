import { useState } from 'react'
import { Database, Plus, RefreshCw, Table2, Trash2, Zap } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { Callout, SectionHeader, Tile, Well } from '@/shared/components/ui/surface'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/shared/components/ui/alert-dialog'
import { errorMessage } from '@/shared/api/http'
import { timeAgo } from '@/features/project/lib/format'
import { ConnectDialog } from './ConnectDialog'
import { SchemaBrowser } from './SchemaBrowser'
import { hostLabel, statusTone } from './connectionsLogic'
import type { Connection } from './connectionsTypes'
import { useConnectionMutations, useConnections } from './useConnections'

function ConnectionRow({ pid, c, onBrowse, onRemove }: { pid: string; c: Connection; onBrowse: () => void; onRemove: () => void }) {
  const { test, introspect } = useConnectionMutations(pid)
  const busy = test.isPending || introspect.isPending
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-hairline px-3 py-2.5 last:border-0" data-testid={`connection-${c.key}`}>
      <StatusDot tone={statusTone(c.status)} data-testid={`connection-status-${c.key}`} data-status={c.status} />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-2 text-[13px] font-medium">
          <span className="truncate">{c.name}</span>
          <span className="font-mono text-[11.5px] font-normal text-muted-foreground">{c.key}</span>
          {c.readonly && <Badge variant="neutral">read-only</Badge>}
        </span>
        <span className="truncate text-[12px] text-muted-foreground">
          {hostLabel(c)} · {c.table_count} table{c.table_count === 1 ? '' : 's'} · {c.introspected_at ? `read ${timeAgo(c.introspected_at)}` : 'not read yet'}
        </span>
        {c.status === 'error' && c.error && <span className="truncate text-[12px] text-destructive" data-testid={`connection-error-${c.key}`}>{c.error}</span>}
      </div>
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="xs" onClick={onBrowse} data-testid={`connection-browse-${c.key}`}>
          <Table2 /> Tables
        </Button>
        <Button
          variant="ghost"
          size="xs"
          disabled={busy}
          onClick={() =>
            test.mutate(c.id, {
              onSuccess: (r) => (r.ok ? toast.success('Connection works', { description: r.server_version ?? undefined }) : toast.error('Connection failed', { description: r.error ?? undefined })),
              onError: (e) => toast.error('Test failed', { description: errorMessage(e) }),
            })
          }
          data-testid={`connection-test-${c.key}`}
        >
          <Zap /> {test.isPending ? 'Testing…' : 'Test'}
        </Button>
        <Button
          variant="ghost"
          size="xs"
          disabled={busy}
          onClick={() =>
            introspect.mutate(c.id, {
              onSuccess: (s) => toast.success(`Read ${s.tables?.length ?? 0} tables`),
              onError: (e) => toast.error('Schema not refreshed', { description: errorMessage(e) }),
            })
          }
          data-testid={`connection-refresh-${c.key}`}
        >
          <RefreshCw className={introspect.isPending ? 'animate-spin' : undefined} /> Refresh schema
        </Button>
        <Button variant="ghost" size="icon-xs" onClick={onRemove} aria-label={`Remove ${c.name}`} data-testid={`connection-remove-${c.key}`}>
          <Trash2 />
        </Button>
      </div>
    </div>
  )
}

/** The project's databases: connect, test, read the schema, browse, remove. */
export function DatabasesSection({ pid }: { pid: string }) {
  const q = useConnections(pid)
  const { remove } = useConnectionMutations(pid)
  const [connectOpen, setConnectOpen] = useState(false)
  const [browsing, setBrowsing] = useState<Connection | null>(null)
  const [removing, setRemoving] = useState<Connection | null>(null)
  const list = q.data ?? []

  return (
    <Tile as="section" className="flex flex-col gap-2 p-3" data-testid="databases-section">
      <SectionHeader
        icon={<Database />}
        hue="data"
        title="Databases"
        sub={list.length ? `${list.length} connected` : undefined}
        actions={
          <Button variant="outline" size="sm" onClick={() => setConnectOpen(true)} data-testid="connect-open">
            <Plus /> Connect a database
          </Button>
        }
      />
      <Well className="flex flex-col">
        {q.isPending ? (
          <div className="flex flex-col gap-2 p-3" data-testid="databases-loading">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : q.isError ? (
          <div className="p-3">
            <Callout tone="error" data-testid="databases-error">
              <span className="flex items-center gap-3">
                <span className="flex-1">Couldn't load databases · {errorMessage(q.error)}</span>
                <Button variant="outline" size="sm" onClick={() => q.refetch()}>
                  Retry
                </Button>
              </span>
            </Callout>
          </div>
        ) : list.length === 0 ? (
          <div className="px-4 py-5 text-[13px] text-muted-foreground" data-testid="databases-empty">
            No databases yet. Connect one, then ask for a plan from its tables in the app builder or a workflow.
          </div>
        ) : (
          list.map((c) => <ConnectionRow key={c.id} pid={pid} c={c} onBrowse={() => setBrowsing(c)} onRemove={() => setRemoving(c)} />)
        )}
      </Well>
      <ConnectDialog pid={pid} open={connectOpen} onClose={() => setConnectOpen(false)} />
      <SchemaBrowser pid={pid} connection={browsing} onClose={() => setBrowsing(null)} />
      <AlertDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {removing?.name}?</AlertDialogTitle>
            <AlertDialogDescription>The stored URL is deleted. Workflows and plans that use "{removing?.key}" will stop finding it. The database itself is untouched.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                const c = removing
                setRemoving(null)
                if (c) remove.mutate(c.id, { onError: (e) => toast.error('Not removed', { description: errorMessage(e) }) })
              }}
              data-testid="connection-remove-confirm"
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Tile>
  )
}
