import { useMemo, useState } from 'react';
import { AlertCircle, AlertTriangle, ChevronRight, CheckCircle2 } from 'lucide-react';
import { useCheckStore } from '../../stores/checkStore';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useNDVStore } from '../../stores/ndvStore';
import type { Finding } from '../../lib/shapes';

function ProblemRow({ finding, onOpen }: { finding: Finding; onOpen: (node: string) => void }) {
  const isError = finding.severity === 'error';
  const Icon = isError ? AlertCircle : AlertTriangle;
  return (
    <button
      type="button"
      data-testid="problem-row"
      data-code={finding.code}
      data-severity={finding.severity}
      disabled={!finding.node}
      onClick={() => finding.node && onOpen(finding.node)}
      className="flex w-full items-start gap-2 rounded-ctl px-2 py-1.5 text-left text-[12.5px] transition-colors enabled:hover:bg-(--hover-fill) disabled:cursor-default"
    >
      <Icon size={13} className={`mt-0.5 shrink-0 ${isError ? 'text-destructive' : 'text-warning'}`} />
      <span className="shrink-0 font-mono text-[11.5px] text-muted-foreground">{finding.code}</span>
      <span className="min-w-0 flex-1 text-foreground">{finding.message}</span>
      {finding.node && <span className="shrink-0 truncate text-[11.5px] text-muted-foreground">{finding.node}</span>}
    </button>
  );
}

/** Errors and warnings from the last check; info findings fold into a "Coverage" line. */
export default function ProblemsPanel() {
  const findings = useCheckStore((s) => s.findings);
  const coverage = useCheckStore((s) => s.coverage);
  const [coverageOpen, setCoverageOpen] = useState(false);

  const { problems, infos } = useMemo(() => {
    const rank = { error: 0, warning: 1, info: 2 } as const;
    const sorted = [...findings].sort((a, b) => rank[a.severity] - rank[b.severity]);
    return { problems: sorted.filter((f) => f.severity !== 'info'), infos: sorted.filter((f) => f.severity === 'info') };
  }, [findings]);

  const openNode = (name: string) => {
    const node = useWorkflowStore.getState().nodes.find((n) => (n.data as { name?: string }).name === name);
    if (!node) return;
    useWorkflowStore.getState().setSelectedNode(node.id);
    useNDVStore.getState().openNDV(node.id);
  };

  return (
    <div className="h-full overflow-y-auto p-2" data-testid="problems-panel">
      {problems.length === 0 ? (
        <div className="flex items-center gap-2 px-2 py-3 text-[12.5px] text-muted-foreground" data-testid="problems-empty">
          <CheckCircle2 size={13} /> No problems found
        </div>
      ) : (
        problems.map((f, i) => <ProblemRow key={`${f.code}-${f.node}-${f.param}-${i}`} finding={f} onOpen={openNode} />)
      )}

      {(coverage || infos.length > 0) && (
        <div className="mt-1 px-2">
          <button
            type="button"
            onClick={() => setCoverageOpen((o) => !o)}
            aria-expanded={coverageOpen}
            data-testid="problems-coverage"
            className="flex items-center gap-1 py-1 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
          >
            <ChevronRight size={12} className={`transition-transform ${coverageOpen ? 'rotate-90' : ''}`} />
            Coverage
            {coverage && (
              <span data-testid="problems-coverage-count">
                {' '}· {coverage.refs_checked}/{coverage.refs_total} references checked
              </span>
            )}
          </button>
          {coverageOpen && (
            <ul className="space-y-0.5 pb-2 pl-4 text-[12px] text-muted-foreground" data-testid="problems-coverage-list">
              {infos.length === 0 && <li>Every reference was checked.</li>}
              {infos.map((f, i) => (
                <li key={i} data-code={f.code} data-testid="problem-info">
                  <span className="font-mono text-[11.5px]">{f.code}</span> {f.message}
                  {f.node && <span> · {f.node}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
