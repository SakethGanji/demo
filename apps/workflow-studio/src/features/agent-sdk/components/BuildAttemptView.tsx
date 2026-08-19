/**
 * One build_workflow attempt, shown INSIDE the agent flow: the script the
 * agent wrote on the left, the graph it produced on the right — the design's
 * "the script and the canvas are the same event, twice".
 *
 * The script comes from the run's own event stream (tool_call arguments); the
 * graph comes from REPLAYING it through the sandbox (persist=false), which is
 * deterministic and free of token overhead on the agent loop — so even a
 * failed attempt renders its partial graph and its error line.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/shared/components/ui/button';
import WorkflowSVG from '@/features/workflow-editor/components/WorkflowSVG';
import { definitionToPreviewData } from '@/features/workflow-editor/lib/workflowTransform';
import { sdkApi } from '@/shared/lib/api';

export interface BuildAttempt {
  seq: number;
  iteration: number;
  script: string;
  name?: string;
  /** Parsed tool_result payload, when the result event arrived. */
  result?: { ok?: boolean; persisted?: boolean; workflow_id?: string; error?: string } | null;
}

export function BuildAttemptView({
  attempt,
  onBack,
}: {
  attempt: BuildAttempt;
  onBack: () => void;
}) {
  const replay = useQuery({
    queryKey: ['sdk-replay', attempt.script],
    queryFn: () => sdkApi.execute({ script: attempt.script, persist: false }),
    staleTime: Infinity, // same script → same graph; replays are deterministic
  });

  const preview = useMemo(() => {
    const r = replay.data;
    if (!r || r.workflow.nodes.length === 0) return null;
    const definition = {
      nodes: r.workflow.nodes.map((n) => ({
        name: n.name,
        type: n.type,
        parameters: n.parameters,
        position: n.position ?? undefined,
        group: r.node_meta[n.name]?.group ? [r.node_meta[n.name].group as string] : undefined,
      })),
      connections: r.workflow.connections.map((c) => ({
        sourceNode: c.source_node,
        targetNode: c.target_node,
        sourceOutput: c.source_output,
        targetInput: c.target_input,
      })),
    };
    return definitionToPreviewData(
      definition as unknown as Parameters<typeof definitionToPreviewData>[0],
    );
  }, [replay.data]);

  const errorLine = useMemo(() => {
    const m = replay.data?.error?.match(/^line (\d+):/);
    return m ? Number(m[1]) : null;
  }, [replay.data]);

  const lines = useMemo(() => attempt.script.replace(/\n$/, '').split('\n'), [attempt.script]);

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="build-attempt">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <Button size="sm" variant="ghost" onClick={onBack} data-testid="attempt-back">
          ← runs
        </Button>
        <span className="text-label font-medium">build_workflow</span>
        <span className="font-mono text-footnote text-muted-foreground">
          iteration {attempt.iteration} · seq {attempt.seq}
          {attempt.name ? ` · ${attempt.name}` : ''}
        </span>
        <span className="ml-auto flex items-center gap-3 text-footnote">
          {replay.isPending && <span className="text-muted-foreground">replaying…</span>}
          {replay.data?.ok && (
            <span data-testid="attempt-ok">
              valid — {replay.data.workflow.nodes.length} nodes ·{' '}
              {replay.data.workflow.connections.length} connections
            </span>
          )}
          {replay.data?.error && (
            <span className="text-destructive" data-testid="attempt-error">
              {replay.data.error.slice(0, 120)}
            </span>
          )}
          {attempt.result?.persisted && attempt.result.workflow_id && (
            <a
              className="underline underline-offset-2"
              href={`/editor?workflowId=${attempt.result.workflow_id}`}
              data-testid="attempt-open-editor"
            >
              open in editor →
            </a>
          )}
        </span>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* The script the agent wrote, line-addressed like the canvas. */}
        <div className="w-[46%] min-w-[380px] overflow-auto border-r border-border p-3">
          <pre className="font-mono text-small leading-6" data-testid="attempt-script">
            {lines.map((line, i) => {
              const n = i + 1;
              const raised = errorLine === n;
              return (
                <div
                  key={n}
                  className={raised ? 'bg-secondary text-destructive' : undefined}
                >
                  <span className="mr-3 inline-block w-6 select-none text-right text-muted-foreground">
                    {n}
                  </span>
                  {line || ' '}
                </div>
              );
            })}
          </pre>
        </div>

        {/* The graph that script built — partial graphs included. */}
        <div className="min-w-0 flex-1" data-testid="attempt-canvas">
          {preview ? (
            <WorkflowSVG
              nodes={preview.nodes}
              edges={preview.edges}
              showDotGrid
              showIcons
              showLabels
              className="h-full w-full"
            />
          ) : (
            <div className="flex h-full items-center justify-center text-small text-muted-foreground">
              {replay.isPending ? 'Replaying the script…' : 'No nodes were built.'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
