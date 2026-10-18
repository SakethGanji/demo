import { Check, Plus, X } from 'lucide-react';
import { useTestStore } from '../../stores/testStore';
import { CATEGORY_LABELS, newTestCase, visibleCategories, type CellStatus, type Category } from '../../lib/tests';
import { useNodeLabels } from '../../hooks/useNodeLabels';
import { cn } from '@/shared/lib/utils';

const cellStyle: Record<CellStatus, string> = {
  pass: 'bg-success-soft text-success-text',
  fail: 'bg-destructive-soft text-destructive',
  none: 'bg-recess text-muted-foreground hover:bg-(--hover-fill) hover:text-foreground',
  na: 'text-faint',
};

const cellLabel: Record<CellStatus, string> = { pass: 'Covered, passing', fail: 'Covered, failing', none: 'No test yet', na: 'Does not apply' };

/** Nodes by input kind: which cases have a test, which pass, which are gaps. A gap opens a prefilled editor. */
export default function CoverageGrid() {
  const grid = useTestStore((s) => s.report?.grid);
  const openEditor = useTestStore((s) => s.openEditor);
  const labels = useNodeLabels();

  if (!grid || grid.rows.length === 0) {
    return (
      <div className="px-3 py-4 text-[12.5px] text-muted-foreground" data-testid="coverage-empty">
        Run the tests to see which nodes and input kinds they cover.
      </div>
    );
  }
  const cols = visibleCategories(grid);

  const open = (node: string, category: Category) => {
    const t = newTestCase(node, category);
    openEditor(t, true);
  };

  return (
    <div className="h-full overflow-auto px-2 pb-2" data-testid="coverage-grid">
      <table className="w-full border-separate border-spacing-y-0.5 text-[12px]">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="sticky left-0 py-1 pr-3 pl-2 font-medium">Node</th>
            {cols.map((c) => (
              <th key={c} className="px-0.5 py-1 text-center font-medium whitespace-nowrap">
                {CATEGORY_LABELS[c]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.rows.map((row) => (
            <tr key={row.node}>
              <td className="max-w-40 truncate py-0.5 pr-3 pl-2 text-foreground" title={row.node}>{labels[row.node] ?? row.node}</td>
              {cols.map((c) => {
                const cell = row.categories[c];
                const status: CellStatus = cell?.status ?? 'na';
                const interactive = status === 'none';
                return (
                  <td key={c} className="px-0.5 py-0.5 text-center">
                    <button
                      type="button"
                      data-testid="coverage-cell"
                      data-node={row.node}
                      data-category={c}
                      data-status={status}
                      disabled={!interactive}
                      title={`${labels[row.node] ?? row.node} · ${CATEGORY_LABELS[c]}: ${cellLabel[status]}${cell && cell.tests.length ? ` (${cell.tests.length})` : ''}`}
                      aria-label={`${labels[row.node] ?? row.node}, ${CATEGORY_LABELS[c]}: ${cellLabel[status]}`}
                      onClick={() => interactive && open(row.node, c)}
                      className={cn(
                        'inline-flex h-6 w-full min-w-14 items-center justify-center rounded-chip transition-colors disabled:cursor-default',
                        cellStyle[status],
                      )}
                    >
                      {status === 'pass' && <Check size={12} />}
                      {status === 'fail' && <X size={12} />}
                      {status === 'none' && <Plus size={12} />}
                      {status === 'na' && <span aria-hidden>–</span>}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
