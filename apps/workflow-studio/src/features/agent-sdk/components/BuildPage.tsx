/**
 * /build — Script → Workflow.
 *
 * Drives POST /api/workflow-sdk/execute: the same sandboxed core the agent's
 * build_workflow tool calls, so what a human sees here is exactly what the
 * model experiences — same errors, same validation, same graph. Left: the
 * script. Right: the graph it built (even a partial one — "line 22 raised,
 * lines 1–21 are real"). Every node carries the source line that created it.
 */
import { useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import CodeEditor from '@/shared/components/ui/code-editor';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import WorkflowSVG from '@/features/workflow-editor/components/WorkflowSVG';
import { definitionToPreviewData } from '@/features/workflow-editor/lib/workflowTransform';
import { sdkApi, type SdkExecuteResponse } from '@/shared/lib/api';

const EXAMPLE_SCRIPT = `# Weekly export: fetch, branch on emptiness, load three regions
cron = Cron(mode="cron", cronExpression="0 7 * * 1")
fetch = HttpRequest(method="GET", url="https://example.com/export", responseType="json")
check = If(field="body", operation="isNotEmpty")
cron >> fetch >> check

# one loader per region - a for-loop, not three hand-written blocks
for region in ("US", "EU", "APAC"):
    node = Postgres(name=f"Load {region}", operation="query", query="INSERT INTO finance.ledger VALUES ($1)")
    check.true >> node

check.false >> StopAndError(errorType="error", message="Export was empty")
validate()
`;

export function BuildPage() {
  const [script, setScript] = useState(EXAMPLE_SCRIPT);
  const [name, setName] = useState('');
  const [result, setResult] = useState<SdkExecuteResponse | null>(null);

  const execute = useMutation({
    mutationFn: (persist: boolean) =>
      sdkApi.execute({ script, name: name.trim() || undefined, persist }),
    onSuccess: (data) => setResult(data),
  });

  const preview = useMemo(() => {
    if (!result || result.workflow.nodes.length === 0) return null;
    const definition = {
      nodes: result.workflow.nodes.map((n) => ({
        name: n.name,
        type: n.type,
        parameters: n.parameters,
        position: n.position ?? undefined,
        group: result.node_meta[n.name]?.group
          ? [result.node_meta[n.name].group as string]
          : undefined,
      })),
      connections: result.workflow.connections.map((c) => ({
        sourceNode: c.source_node,
        targetNode: c.target_node,
        sourceOutput: c.source_output,
        targetInput: c.target_input,
      })),
    };
    return definitionToPreviewData(
      definition as unknown as Parameters<typeof definitionToPreviewData>[0],
    );
  }, [result]);

  const nodeCount = result?.workflow.nodes.length ?? 0;
  const connectionCount = result?.workflow.connections.length ?? 0;

  return (
    <div className="flex h-[calc(100vh-92px)] min-h-0" data-testid="build-page">
      {/* Script pane */}
      <div className="flex w-[42%] min-w-[420px] flex-col border-r border-border">
        <div className="flex items-center gap-2 border-b border-border px-4 py-2">
          <span className="text-label font-medium">Script → Workflow</span>
          <span className="text-footnote text-muted-foreground">
            runs sandboxed · validated even if the script forgets to
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              data-testid="build-example"
              onClick={() => setScript(EXAMPLE_SCRIPT)}
            >
              Load example
            </Button>
            <Button
              size="sm"
              data-testid="build-run"
              disabled={execute.isPending || !script.trim()}
              onClick={() => execute.mutate(false)}
            >
              {execute.isPending ? 'Running…' : 'Run'}
            </Button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto" data-testid="build-script">
          <CodeEditor
            value={script}
            onChange={setScript}
            language="javascript"
            minHeight="100%"
          />
        </div>

        {/* Result strip */}
        <div
          className="max-h-56 overflow-auto border-t border-border px-4 py-3 text-small"
          data-testid="build-result"
        >
          {!result && !execute.isError && (
            <span className="text-muted-foreground">
              Run the script to build the graph. Nothing is saved until you save it.
            </span>
          )}
          {execute.isError && (
            <span className="text-destructive">
              request failed: {(execute.error as Error).message}
            </span>
          )}
          {result?.error && (
            <div className="font-mono text-destructive" data-testid="build-error">
              {result.error}
              {result.workflow.nodes.length > 0 && (
                <div className="mt-1 text-muted-foreground">
                  partial — {nodeCount} node{nodeCount === 1 ? '' : 's'} built before it raised
                </div>
              )}
            </div>
          )}
          {result && !result.error && result.problems.length > 0 && (
            <ul className="space-y-1 text-destructive" data-testid="build-problems">
              {result.problems.map((p) => (
                <li key={p} className="font-mono">
                  {p}
                </li>
              ))}
            </ul>
          )}
          {result?.ok && (
            <div className="flex flex-wrap items-center gap-3" data-testid="build-ok">
              <span>
                valid — {nodeCount} node{nodeCount === 1 ? '' : 's'} ·{' '}
                {connectionCount} connection{connectionCount === 1 ? '' : 's'}
              </span>
              {result.persisted && result.workflow_id ? (
                <a
                  className="underline underline-offset-2"
                  href={`/editor?workflowId=${result.workflow_id}`}
                  data-testid="build-open-editor"
                >
                  saved — open in editor →
                </a>
              ) : (
                <span className="flex items-center gap-2">
                  <Input
                    className="h-7 w-52 text-small"
                    placeholder="workflow name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    data-testid="build-name"
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    data-testid="build-save"
                    disabled={execute.isPending}
                    onClick={() => execute.mutate(true)}
                  >
                    Save workflow
                  </Button>
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Canvas pane */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1" data-testid="build-canvas">
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
              The graph appears here — including partial graphs when a script fails mid-way.
            </div>
          )}
        </div>
        {result && result.workflow.nodes.length > 0 && (
          <div
            className="flex flex-wrap gap-2 border-t border-border px-4 py-2"
            data-testid="build-provenance"
          >
            {result.workflow.nodes.map((n) => (
              <span
                key={n.name}
                className="rounded border border-border px-2 py-0.5 font-mono text-footnote text-muted-foreground"
              >
                {n.name}
                {result.node_meta[n.name]?.sourceLine != null &&
                  ` · line ${result.node_meta[n.name].sourceLine}`}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
