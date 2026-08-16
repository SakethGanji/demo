/**
 * The library lens: the derived outputs this dataset has produced.
 *
 * Artifacts are listed from the `artifacts` table, never a bucket scan — the
 * row is the only thing that maps a filename to a storage key, so a blob with
 * no row is unreachable by everyone, superusers included. That is also why the
 * download link goes through `/samples/{filename}` rather than composing a key.
 *
 * Retention is shown because it is real and surprising: a query output is gone
 * in 7 days, a published source never. Someone deciding whether to re-run or
 * rely on an artifact needs that in front of them.
 */

import { Download, FileBox } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import { ANALYTICS_BASE } from '@/shared/lib/analyticsClient';
import { useArtifacts, useSavedAnalytics } from '../../hooks/useAnalysis';
import { errorText } from '../../hooks/useDatasetActions';
import { LensEmpty, LensError, Section } from './primitives';

/** Server-side retention policy, surfaced so "it vanished" is never a surprise. */
const RETENTION_DAYS: Record<string, number | null> = {
  published_source: null,
  validation_failures: 90,
  transform_output: 30,
  join_output: 30,
  sample_output: 30,
  aggregation_output: 30,
  pivot_output: 30,
  diff_output: 14,
  export: 7,
  query_output: 7,
};

function retentionLabel(kind?: string | null): string {
  if (!kind) return '';
  if (!(kind in RETENTION_DAYS)) return 'kept 30d';
  const days = RETENTION_DAYS[kind];
  return days === null ? 'kept forever' : `kept ${days}d`;
}

function formatBytes(n?: number | null): string {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function LibraryLens({ datasetId }: { datasetId: string | null }) {
  const artifacts = useArtifacts(datasetId);
  const saved = useSavedAnalytics(datasetId);

  const items = artifacts.data?.items ?? [];
  const defs = saved.data?.items ?? [];

  return (
    <>
      <Section title={`Artifacts (${items.length})`}>
        {artifacts.isLoading && <p className="text-[11px] text-muted-foreground">Loading…</p>}
        {artifacts.error && <LensError>{errorText(artifacts.error)}</LensError>}
        {!artifacts.isLoading && !artifacts.error && items.length === 0 && (
          <LensEmpty>
            Nothing derived yet. Query results, exports and transform outputs land here.
          </LensEmpty>
        )}
        {items.map((a) => (
          <div key={a.key} className="mb-1 rounded-md border border-border px-2 py-1.5" data-testid="artifact">
            <div className="flex items-center gap-1.5">
              <FileBox className="size-3 shrink-0 text-muted-foreground" />
              <span className="truncate font-mono text-[10px]" title={a.filename}>
                {a.filename}
              </span>
              {/* Download is a plain link: the browser handles Content-Disposition,
                  and the identity header is not needed for a same-seat GET here
                  only because the link opens in the app's own context. */}
              <a
                href={`${ANALYTICS_BASE}/samples/${encodeURIComponent(a.filename)}`}
                target="_blank"
                rel="noreferrer"
                className="ml-auto text-muted-foreground transition-colors hover:text-foreground"
                title="Download"
                data-testid="artifact-download"
              >
                <Download className="size-3" />
              </a>
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
              {a.file_type && (
                <Badge variant="glass" className="h-4 px-1 text-[9px]">
                  {a.file_type}
                </Badge>
              )}
              <span className="tabular-nums">{formatBytes(a.size_bytes)}</span>
              <span className="ml-auto text-muted-foreground/70">
                {retentionLabel(a.file_type)}
              </span>
            </div>
          </div>
        ))}
      </Section>

      <Section title={`Saved analyses (${defs.length})`}>
        {saved.isLoading && <p className="text-[11px] text-muted-foreground">Loading…</p>}
        {saved.error && <LensError>{errorText(saved.error)}</LensError>}
        {!saved.isLoading && !saved.error && defs.length === 0 && (
          <LensEmpty>No saved analyses.</LensEmpty>
        )}
        {defs.map((d) => (
          <div key={d.id} className="mb-1 rounded-md border border-border px-2 py-1.5" data-testid="saved-analysis">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-[12px]">{d.name}</span>
              <Badge variant="glass" className="ml-auto">
                {d.kind}
              </Badge>
            </div>
            {d.description && (
              <p className="mt-0.5 text-[10px] leading-tight text-muted-foreground">
                {d.description}
              </p>
            )}
          </div>
        ))}
      </Section>

      {items.length > 0 && (
        <p className="text-[10px] text-muted-foreground/70">
          Derived outputs expire on the schedule shown. Publish anything you need permanently.
        </p>
      )}
    </>
  );
}
