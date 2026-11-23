/**
 * Approvals inbox, `/p/$project/approvals` (DESIGN-STAGE5-UIS §11.4): tasks across the project's workflows with
 * keyset paging ("Load more"), a status filter (open / closed / all) and a workflow filter.
 */
import { useNavigate, useSearch, Link } from '@tanstack/react-router'
import { CheckCheck, Loader2 } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useProjectWorkflows } from '@/features/project/hooks/useProjectData'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Segmented } from '@/shared/components/ui/segmented'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { PageHeader, Tile, Page, Callout } from '@/shared/components/ui/surface'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { errorMessage } from '@/shared/api/http'
import { formatWhen, taskStatusLabel, taskTone } from '@/features/interfaces/lib/format'
import { useTaskInbox } from './hooks/useTasks'
import { assigneesLabel, type InboxStatus } from './lib/tasks'

const ALL = '__all'

export function ApprovalsPage() {
  const { pid, slug } = useProjectCtx()
  const search = useSearch({ strict: false }) as { status?: InboxStatus; workflow?: string }
  const navigate = useNavigate()
  const status: InboxStatus = search.status ?? 'open'
  const workflowId = search.workflow
  const workflows = useProjectWorkflows(pid)
  const inbox = useTaskInbox(pid, status, workflowId)

  const setSearch = (patch: { status?: InboxStatus; workflow?: string | undefined }) =>
    navigate({
      to: '.',
      search: ((s: Record<string, unknown>) => {
        const next: Record<string, unknown> = { ...s, ...patch }
        for (const k of Object.keys(next)) if (next[k] === undefined || next[k] === 'open') delete next[k]
        return next
      }) as never,
      replace: true,
    })

  const wfItems = { [ALL]: 'All workflows', ...Object.fromEntries((workflows.data ?? []).map((w) => [w.id, w.name])) }

  return (
    <Page data-testid="approvals-page">
      <div className="flex flex-col gap-5">
        <PageHeader
          title="Approvals"
          icon={<CheckCheck />}
          hue="flow"
          meta="Human steps waiting on a decision, across this project's workflows"
        />

        <div className="flex flex-wrap items-center gap-3">
          <Segmented<InboxStatus>
            value={status}
            onChange={(k) => setSearch({ status: k })}
            aria-label="Status"
            data-testid="approvals-filter-status"
            options={[
              { key: 'open', label: 'Open', testId: 'approvals-filter-status-open' },
              { key: 'closed', label: 'Closed', testId: 'approvals-filter-status-closed' },
              { key: 'all', label: 'All', testId: 'approvals-filter-status-all' },
            ]}
          />
          <Select value={workflowId ?? ALL} onValueChange={(v) => setSearch({ workflow: !v || v === ALL ? undefined : v })} items={wfItems}>
            <SelectTrigger className="w-56" data-testid="approvals-filter-workflow">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All workflows</SelectItem>
              {(workflows.data ?? []).map((w) => <SelectItem key={w.id} value={w.id}>{w.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>

        {inbox.isPending && <Skeleton className="h-40 w-full" />}
        {inbox.isError && (
          <Callout tone="error" role="alert" data-testid="approvals-error">
            {errorMessage(inbox.error)}
          </Callout>
        )}

        {inbox.data && inbox.tasks.length === 0 && (
          <Tile>
            <EmptyState icon={<CheckCheck />} title={status === 'open' ? 'Nothing is waiting' : 'No tasks here'} data-testid="approvals-empty">
              {status === 'open'
                ? 'When a workflow reaches an Approval step, the task shows up here.'
                : 'Tasks that have been decided, timed out or cancelled appear here.'}
            </EmptyState>
          </Tile>
        )}

        {inbox.tasks.length > 0 && (
          <Tile className="overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Task</TableHead>
                  <TableHead>Workflow</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Due</TableHead>
                  <TableHead>Assignees</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {inbox.tasks.map((t) => (
                  <TableRow
                    key={t.id}
                    className="cursor-pointer"
                    onClick={() => navigate({ to: '/p/$project/approvals/$taskId', params: { project: slug, taskId: t.id } })}
                    data-testid="approvals-row"
                    data-task-id={t.id}
                    data-status={t.status}
                  >
                    <TableCell className="max-w-[320px] truncate font-medium" data-testid="approvals-cell-title">
                      <Link to="/p/$project/approvals/$taskId" params={{ project: slug, taskId: t.id }} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                        {t.title}
                      </Link>
                    </TableCell>
                    <TableCell className="max-w-[200px] truncate text-muted-foreground" data-testid="approvals-cell-workflow">{t.workflow_name}</TableCell>
                    <TableCell><Badge variant={taskTone(t.status)}>{taskStatusLabel(t.status)}</Badge></TableCell>
                    <TableCell className="text-muted-foreground" data-testid="approvals-cell-due">{t.due_at ? formatWhen(t.due_at) : '—'}</TableCell>
                    <TableCell className="max-w-[200px] truncate text-muted-foreground" data-testid="approvals-cell-assignees">{assigneesLabel(t.assignees)}</TableCell>
                    <TableCell className="text-muted-foreground" data-testid="approvals-cell-created">{formatWhen(t.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Tile>
        )}

        {inbox.hasNextPage && (
          <div>
            <Button variant="outline" onClick={() => inbox.fetchNextPage()} disabled={inbox.isFetchingNextPage} data-testid="approvals-load-more">
              {inbox.isFetchingNextPage && <Loader2 className="animate-spin" />} Load more
            </Button>
          </div>
        )}
      </div>
    </Page>
  )
}
