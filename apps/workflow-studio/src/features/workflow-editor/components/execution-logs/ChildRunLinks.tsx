/**
 * Links between a run and the sub-workflow runs it started (DESIGN-STAGE8-REUSE 12.5). The lineage comes from
 * `GET /executions/{id}` (`parent`, `children`); a link opens the child's workflow with that run shown.
 */
import { Link } from '@tanstack/react-router';
import { ArrowUpRight, CornerLeftUp } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import { useEditorProjectStore } from '../../stores/editorProjectStore';
import { isFailedStatus, versionText, type ExecutionChildRef, type ExecutionParentRef } from '../../lib/subworkflowsApi';

const STATUS_VARIANT: Record<string, 'success' | 'error' | 'warning' | 'neutral'> = {
  success: 'success',
  completed: 'success',
  failed: 'error',
  error: 'error',
  timeout: 'error',
  running: 'warning',
  waiting: 'warning',
};

function RunLink({ workflowId, executionId, className, testId, children }: {
  workflowId: string;
  executionId: string;
  className?: string;
  testId: string;
  children: React.ReactNode;
}) {
  const slug = useEditorProjectStore((s) => s.slug);
  return (
    <Link
      to="/p/$project/workflows/$workflowId"
      params={{ project: slug, workflowId }}
      search={{ execution: executionId }}
      className={className}
      data-testid={testId}
    >
      {children}
    </Link>
  );
}

/** Header breadcrumb of a child run: "Called by <parent> · <node>". */
export function ParentRunLink({ parent, testId = 'exec-parent-link' }: { parent: ExecutionParentRef; testId?: string }) {
  return (
    <RunLink
      workflowId={parent.workflow_id}
      executionId={parent.execution_id}
      testId={testId}
      className="flex items-center gap-1 text-[10.5px] text-primary underline-offset-2 hover:underline"
    >
      <CornerLeftUp size={10} aria-hidden />
      Called by {parent.workflow_name}{parent.node ? ` · ${parent.node}` : ''}
    </RunLink>
  );
}

/** Detail panel of an ExecuteWorkflow node: one "View sub-execution" link per child run (several in `each` mode). */
export function ChildRunList({ runs }: { runs: ExecutionChildRef[] }) {
  if (runs.length === 0) return null;
  return (
    <div className="space-y-1" data-testid="run-child-executions">
      <h4 className="text-[11.5px] font-medium text-muted-foreground">Sub-workflow runs</h4>
      <ul className="space-y-1">
        {runs.map((c) => (
          <li key={c.execution_id}>
            <RunLink
              workflowId={c.workflow_id}
              executionId={c.execution_id}
              testId="run-open-child-execution"
              className="well row-hover flex items-center gap-2 px-2.5 py-1.5 text-[12px]"
            >
              <span className="min-w-0 flex-1 truncate">
                View sub-execution <span className="text-muted-foreground">· {c.workflow_name} {versionText(c.version_number)}</span>
              </span>
              <Badge variant={STATUS_VARIANT[c.status] ?? 'neutral'}>{c.status}</Badge>
              {c.duration_ms != null && <span className="text-[10.5px] text-muted-foreground">{c.duration_ms} ms</span>}
              <ArrowUpRight size={12} className="shrink-0 text-muted-foreground" aria-hidden />
            </RunLink>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Nested under the node in the logs tree. */
export function ChildRunRow({ run }: { run: ExecutionChildRef }) {
  return (
    <RunLink
      workflowId={run.workflow_id}
      executionId={run.execution_id}
      testId={`exec-log-child-${run.execution_id}`}
      className="row-hover ml-5 flex items-center gap-1.5 rounded-ctl px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
    >
      <CornerLeftUp size={10} className="rotate-180" aria-hidden />
      <span className="min-w-0 flex-1 truncate">{run.workflow_name} {versionText(run.version_number)}</span>
      <span className={isFailedStatus(run.status) ? 'text-destructive' : undefined}>{run.status}</span>
    </RunLink>
  );
}

/** In the error panel of a node that failed with SUBWORKFLOW_FAILED: jump to the run that failed. */
export function ErrorChildLink({ run }: { run: ExecutionChildRef }) {
  return (
    <RunLink
      workflowId={run.workflow_id}
      executionId={run.execution_id}
      testId="run-error-child-link"
      className="mt-1 inline-flex items-center gap-1 text-[12px] text-destructive underline underline-offset-2"
    >
      Open the failed run in {run.workflow_name} <ArrowUpRight size={12} aria-hidden />
    </RunLink>
  );
}
