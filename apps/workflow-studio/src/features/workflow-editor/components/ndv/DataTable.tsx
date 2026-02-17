import { useState, useMemo, memo } from 'react';
import { ChevronUp, ChevronDown, ChevronsUpDown, ChevronLeft, ChevronRight } from 'lucide-react';

interface DataTableProps {
  data: Record<string, unknown>[];
  pageSize?: number;
}

type SortDir = 'asc' | 'desc' | null;

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '\u2014';
  if (typeof value === 'object') {
    try {
      const s = JSON.stringify(value);
      return s.length > 120 ? s.slice(0, 117) + '\u2026' : s;
    } catch {
      return String(value);
    }
  }
  const s = String(value);
  return s.length > 120 ? s.slice(0, 117) + '\u2026' : s;
}

function compare(a: unknown, b: unknown): number {
  if (a === b) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b));
}

const DataTable = memo(function DataTable({ data, pageSize = 50 }: DataTableProps) {
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>(null);
  const [page, setPage] = useState(0);

  const columns = useMemo(() => {
    const keys = new Set<string>();
    for (const row of data) {
      for (const key of Object.keys(row)) keys.add(key);
    }
    return Array.from(keys);
  }, [data]);

  const sorted = useMemo(() => {
    if (!sortCol || !sortDir) return data;
    const col = sortCol;
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...data].sort((a, b) => dir * compare(a[col], b[col]));
  }, [data, sortCol, sortDir]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pageRows = sorted.slice(safePage * pageSize, (safePage + 1) * pageSize);

  const handleSort = (col: string) => {
    if (sortCol === col) {
      if (sortDir === 'asc') setSortDir('desc');
      else if (sortDir === 'desc') { setSortCol(null); setSortDir(null); }
      else setSortDir('asc');
    } else {
      setSortCol(col);
      setSortDir('asc');
    }
    setPage(0);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
          TABLE
        </span>
        <span className="text-xs text-muted-foreground">
          {data.length} row{data.length !== 1 ? 's' : ''} · {columns.length} column{columns.length !== 1 ? 's' : ''}
        </span>
      </div>

      <div className="overflow-auto rounded-md border border-border" style={{ maxHeight: 'calc(100vh - 320px)' }}>
        <table className="w-full border-collapse text-[12px]">
          <thead className="sticky top-0 z-10">
            <tr>
              {columns.map((col) => (
                <th
                  key={col}
                  onClick={() => handleSort(col)}
                  className="cursor-pointer select-none whitespace-nowrap border-b border-border bg-muted px-3 py-2 text-left font-semibold text-foreground hover:bg-accent transition-colors"
                  style={{ maxWidth: 300 }}
                >
                  <span className="inline-flex items-center gap-1">
                    {col}
                    {sortCol === col ? (
                      sortDir === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />
                    ) : (
                      <ChevronsUpDown size={10} className="opacity-30" />
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row, i) => (
              <tr key={safePage * pageSize + i} className="border-b border-border last:border-b-0 hover:bg-primary/5 transition-colors">
                {columns.map((col) => (
                  <td
                    key={col}
                    className="whitespace-nowrap px-3 py-1.5 font-mono text-foreground"
                    style={{ maxWidth: 300 }}
                    title={String(row[col] ?? '')}
                  >
                    <span className="block truncate">{formatCell(row[col])}</span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {safePage * pageSize + 1}\u2013{Math.min((safePage + 1) * pageSize, sorted.length)} of {sorted.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage(Math.max(0, safePage - 1))}
              disabled={safePage === 0}
              className="rounded p-1 hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ChevronLeft size={14} />
            </button>
            <span className="px-1">
              {safePage + 1} / {totalPages}
            </span>
            <button
              onClick={() => setPage(Math.min(totalPages - 1, safePage + 1))}
              disabled={safePage >= totalPages - 1}
              className="rounded p-1 hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
});

export default DataTable;
