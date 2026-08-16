/**
 * The analytics lens: what is actually *in* each column.
 *
 * This is the lens most affected by the seat. `POST /profile` is refused
 * outright — not masked — for a viewer or editor on any dataset that declares a
 * sensitive column, because a profile is a computation over raw values and
 * `top_values` would leak them verbatim. So the whole panel has two legitimate
 * shapes, and both are rendered deliberately.
 */

import { AlertTriangle } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import {
  useHealth,
  useProfile,
  isRestricted,
  type ColumnProfile,
} from '../../hooks/useAnalysis';
import { errorText } from '../../hooks/useDatasetActions';
import {
  DtypeChip,
  Histogram,
  LensEmpty,
  LensError,
  LensRestricted,
  Meter,
  MiniBars,
  Row,
  Section,
  StatusDot,
} from './primitives';
import { compact, num } from './format';

/** Nulls are the first thing anyone checks; make the threshold visible. */
function nullTone(pct: number): 'neutral' | 'warning' | 'critical' {
  if (pct >= 50) return 'critical';
  if (pct >= 5) return 'warning';
  return 'neutral';
}

function ColumnCard({ col }: { col: ColumnProfile }) {
  const nullPct = col.null_percent ?? 0;
  const tops = (col.top_values ?? []).filter((t) => t.value !== null && t.value !== undefined);

  return (
    <div className="mb-2 rounded-md border border-border px-2 py-2" data-testid="profile-column">
      <div className="flex items-center gap-1.5">
        <span
          className="truncate text-[12px] font-medium"
          title={col.name}
          data-testid="profile-column-name"
        >
          {col.name}
        </span>
        <DtypeChip dtype={col.dtype} className="ml-auto" />
      </div>

      <div className="mt-1.5">
        <Meter value={nullPct} label="null" tone={nullTone(nullPct)} />
      </div>

      <div className="mt-1 flex items-center justify-between text-[10px] text-muted-foreground tabular-nums">
        <span>{compact(col.unique_count)} distinct</span>
        <span>{compact(col.non_null_count)} filled</span>
      </div>

      {/* Numeric: distribution plus the five-number summary the shape can't show. */}
      {col.dtype === 'numeric' && (col.histogram?.length ?? 0) > 0 && (
        <div className="mt-2">
          <Histogram
            bins={(col.histogram ?? []).map((b) => ({
              start: b.bin_start,
              end: b.bin_end,
              count: b.count,
            }))}
            testid="profile-histogram"
          />
          <div className="mt-1 flex justify-between text-[10px] text-muted-foreground tabular-nums">
            <span>min {num(col.min)}</span>
            <span>med {num(col.median)}</span>
            <span>max {num(col.max)}</span>
          </div>
        </div>
      )}

      {/* Everything else: the categories that actually occur. */}
      {col.dtype !== 'numeric' && tops.length > 0 && (
        <div className="mt-2">
          <MiniBars
            testid="profile-top-values"
            data={tops.map((t) => ({
              label: String(t.value),
              value: t.count,
              hint: `${t.percent.toFixed(0)}%`,
            }))}
          />
        </div>
      )}
    </div>
  );
}

interface AnalyticsLensProps {
  datasetId: string | null;
  version: number | null;
  sheet: string | null;
}

export function AnalyticsLens({ datasetId, version, sheet }: AnalyticsLensProps) {
  const profile = useProfile(datasetId, version, sheet);
  const health = useHealth(datasetId);

  const dims = Object.entries(health.data?.dimensions ?? {});

  return (
    <>
      <Section title="Health">
        {health.isLoading && <p className="text-[11px] text-muted-foreground">Loading…</p>}
        {dims.length === 0 && !health.isLoading && <LensEmpty>No health signals yet.</LensEmpty>}
        {dims.map(([key, dim]) => (
          <div key={key} className="flex items-start gap-1.5 py-1" data-testid="health-dimension">
            <span className="mt-1.5">
              <StatusDot status={dim.status} />
            </span>
            <div className="min-w-0">
              <div className="text-[11px] capitalize">{key.replace(/_/g, ' ')}</div>
              <div className="text-[10px] leading-tight text-muted-foreground">{dim.summary}</div>
            </div>
          </div>
        ))}
      </Section>

      <Section title="Column profile">
        {profile.isLoading && <p className="text-[11px] text-muted-foreground">Profiling…</p>}

        {/* The refusal is a first-class state, not an error. */}
        {isRestricted(profile.error) && <LensRestricted what="Profiling" />}

        {profile.error && !isRestricted(profile.error) && (
          <LensError>{errorText(profile.error)}</LensError>
        )}

        {profile.data && (
          <>
            <div className="mb-2 flex items-center gap-1.5">
              <Badge variant="glass">{compact(profile.data.row_count)} rows</Badge>
              <Badge variant="glass">{profile.data.column_count} cols</Badge>
              {(profile.data.duplicate_row_count ?? 0) > 0 && (
                <Badge variant="destructive" className="gap-1">
                  <AlertTriangle className="size-2.5" />
                  {compact(profile.data.duplicate_row_count)} dupes
                </Badge>
              )}
            </div>
            <Row label="Sheet" value={sheet ?? '—'} />
            <div className="mt-2">
              {profile.data.columns.map((c) => (
                <ColumnCard key={c.name} col={c} />
              ))}
            </div>
          </>
        )}
      </Section>
    </>
  );
}
