/**
 * `/p/$project/approvals/$taskId` (DESIGN-STAGE5-UIS §6.4, §11.4): a task with its context, the approver's form and
 * Approve / Reject. Renders the interface's custom task screen when the task carries one, else the default layout.
 */
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeft, ArrowUpRight } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Dot, Tile, Page } from '@/shared/components/ui/surface'
import { isApiError } from '@/shared/api/http'
import { ScreenRenderer } from '@/features/interfaces/renderer/ScreenRenderer'
import { screenHas, type ScreenData } from '@/features/interfaces/lib/binding'
import { formatWhen, taskStatusLabel, taskTone } from '@/features/interfaces/lib/format'
import type { TaskDetail } from '@/features/interfaces/lib/types'
import { useTask } from './hooks/useTasks'
import { useTaskDecision } from './hooks/useTaskDecision'
import { TaskActions, TaskContext, TaskFormFields, TaskIdentity, TaskOutcome } from './TaskParts'
import { assigneesLabel, nodeDataFromBindings } from './lib/tasks'

function screenData(task: TaskDetail): ScreenData {
  return {
    input: task.input,
    node: nodeDataFromBindings(task.context?._bindings),
    task: { title: task.title, status: task.status, due_at: task.due_at, assignees: task.assignees, context: task.context, form: task.form },
    result: null,
    run: { id: task.execution_id, status: task.run_status },
  }
}

function TaskBody({ task }: { task: TaskDetail }) {
  const { pid, slug } = useProjectCtx()
  const d = useTaskDecision(task, pid)
  const screen = task.screen
  const hasForm = !!screen && screenHas(screen, 'form')
  const hasActions = !!screen && screenHas(screen, 'actions')

  return (
    <div className="flex flex-col gap-4" data-testid="task-detail" data-task-id={task.id} data-status={task.status}>
      <div className="flex flex-col gap-1.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <h1 className="min-w-0 truncate text-title leading-[1.1] font-[650] tracking-[-0.03em]" data-testid="task-title">{task.title}</h1>
          <Badge variant={taskTone(task.status)} data-testid="task-status">{taskStatusLabel(task.status)}</Badge>
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted-foreground">
          <span>{task.workflow_name}</span><Dot />
          <span>{task.node_name}</span><Dot />
          <span>Assigned: {assigneesLabel(task.assignees)}</span>
          {task.due_at && <><Dot /><span>Due {formatWhen(task.due_at)}</span></>}
          <Dot />
          <Link
            to="/p/$project/runs/$executionId"
            params={{ project: slug, executionId: task.execution_id }}
            className="inline-flex items-center gap-1 text-primary hover:underline"
            data-testid="task-run-link"
          >
            Open the run <ArrowUpRight className="size-3" />
          </Link>
        </div>
      </div>

      <TaskOutcome d={d} />

      {screen ? (
        <ScreenRenderer
          spec={screen}
          data={screenData(task)}
          slots={{
            form: () => <TaskFormFields task={task} d={d} />,
            actions: (c) => (
              <TaskActions
                d={d}
                approveLabel={typeof c.approve_label === 'string' ? c.approve_label : undefined}
                rejectLabel={typeof c.reject_label === 'string' ? c.reject_label : undefined}
              />
            ),
          }}
        />
      ) : (
        <Tile className="flex flex-col gap-3 p-4">
          <div className="text-[13.5px] font-semibold">Context</div>
          <TaskContext context={task.context} />
        </Tile>
      )}

      <Tile className="flex flex-col gap-4 p-4">
        {!hasForm && <TaskFormFields task={task} d={d} />}
        <TaskIdentity task={task} d={d} />
        {!hasActions && <TaskActions d={d} />}
        {hasActions && !d.closed && <div className="text-[12px] text-muted-foreground">Use the buttons on the screen above to decide.</div>}
      </Tile>
    </div>
  )
}

export function TaskDetailPage() {
  const { taskId } = useParams({ strict: false }) as { taskId: string }
  const { slug } = useProjectCtx()
  const q = useTask(taskId)
  return (
    <Page data-testid="task-page">
      <div className="flex max-w-[720px] flex-col gap-4">
        <div>
          <Button variant="ghost" size="sm" render={<Link to="/p/$project/approvals" params={{ project: slug }} />} data-testid="task-back">
            <ArrowLeft /> Approvals
          </Button>
        </div>
        {q.isPending && <Skeleton className="h-48 w-full" />}
        {q.isError && (
          <EmptyState title={isApiError(q.error, undefined, 404) ? 'Task not found' : 'Could not load the task'} data-testid="task-error-state">
            {isApiError(q.error, undefined, 404) ? 'It may belong to another project, or it was removed.' : 'Try again in a moment.'}
          </EmptyState>
        )}
        {q.data && <TaskBody key={q.data.id} task={q.data} />}
      </div>
    </Page>
  )
}
