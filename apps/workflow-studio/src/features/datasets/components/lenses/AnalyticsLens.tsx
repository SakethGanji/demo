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
import { errorText } from '@/shared/lib/analyticsClient';
import {
  DtypeChip,
  LensError,
  LensList,
  LensRestricted,
  Row,
  Section,
} from './primitives';
import { Histogram, Meter, MiniBars } from './analyticsMarks';
import { Status, type StatusKind } from '@/shared/components/instrument/Status';
import { Guard } from '@/shared/components/instrument/Guard';
import { Identifier } from '@/shared/components/instrument/Typography';
import { isIdentityLike, middleTruncate } from '@/shared/components/instrument/shape';
import { compact, num } from '@/shared/lib/format';

/**
 * The service's health vocabulary mapped onto the five Instrument states.
 * Anything unrecognised becomes `unknown` — a ring, which reads as "not
 * assessed" rather than silently borrowing the look of a pass.
 */
function healthKind(status: string): StatusKind {
  switch (status) {
    case 'ok':
      return 'good';
    case 'warn':
      return 'warning';
    case 'fail':
      return 'critical';
    default:
      return 'unknown';
  }
}

/** Nulls are the first thing anyone checks; make the threshold visible. */
function nullTone(pct: number): 'neutral' | 'warning' | 'critical' {
  if (pct >= 50) return 'critical';
  if (pct >= 5) return 'warning';
  return 'neutral';
}

function ColumnCard({ col }: { col: ColumnProfile }) {
  const nullPct = col.null_percent ?? 0;
  const tops = (col.top_values ?? []).filter((t) => t.value !== null && t.value !== undefined);
  // The shared rule, not a local re-derivation — the column page and this dock
  // must agree on what counts as identity-like.
  const identityLike = isIdentityLike({
    name: col.name,
    dtype: col.dtype,
    uniqueCount: col.unique_count ?? null,
    nonNullCount: col.non_null_count ?? null,
  });
  const samples = tops.slice(0, 5).map((t) => String(t.value));

  return (
    <div className="mb-2 rounded-md border border-border px-2 py-2" data-testid="profile-column">
      <div className="flex items-center gap-1.5">
        <span
          className="truncate text-body font-medium"
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

      <div className="mt-1 flex items-center justify-between text-micro text-muted-foreground tabular-nums">
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
          <div className="mt-1 flex justify-between text-micro text-muted-foreground tabular-nums">
            <span>min {num(col.min)}</span>
            <span>med {num(col.median)}</span>
            <span>max {num(col.max)}</span>
          </div>
        </div>
      )}

      {/* SHAPE-R4 — above 95% distinct a top-values chart says nothing: every
       * bar is one row tall, so it renders as a row of identical `1 · 0%`
       * stubs that look like data and carry none.
       *
       * Replace it with an identity panel, and — the part that actually
       * matters — SAY the distribution was suppressed. The reference cockpit
       * drew no card at all for such a column, and a column with no card reads
       * as a column with no problem. */}
      {col.dtype !== 'numeric' && identityLike && (
        <div className="mt-2" data-testid="profile-identity-panel">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-micro text-muted-foreground">distinctness</span>
            <span className="text-micro tabular-nums">
              {((col.unique_count ?? 0) / (col.non_null_count || 1) * 100).toFixed(1)}%
            </span>
          </div>
          {samples.length > 0 && (
            <div className="mt-1 flex flex-col gap-0.5">
              {samples.map((v, i) => (
                <Identifier
                  key={`${v}-${i}`}
                  className="truncate text-micro text-muted-foreground"
                  title={v}
                >
                  {middleTruncate(v, 34)}
                </Identifier>
              ))}
            </div>
          )}
          <Guard className="mt-1.5">
            Distribution suppressed, not omitted — above 95% distinct every bar would be one
            row. These are sampled values, not the most common ones.
          </Guard>
        </div>
      )}

      {/* Everything else: the categories that actually occur. */}
      {col.dtype !== 'numeric' && !identityLike && tops.length > 0 && (
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
        <LensList query={health} items={dims} empty="No health signals yet.">
          {([key, dim]) => (
            <div key={key} className="py-1" data-testid="health-dimension">
              <div className="flex items-baseline justify-between gap-2">
                <span className="min-w-0 truncate text-small capitalize">
                  {key.replace(/_/g, ' ')}
                </span>
                {/* The status WORD, not just a dot. This row used to render the
                 * hue alone, which is the failure rule 6 exists to prevent —
                 * and "unknown" in particular is unreadable as a grey dot. */}
                <Status kind={healthKind(dim.status)} className="text-micro">
                  {dim.status}
                </Status>
              </div>
              <div className="text-micro leading-tight text-muted-foreground">{dim.summary}</div>
            </div>
          )}
        </LensList>
      </Section>

      <Section title="Column profile">
        {profile.isLoading && <p className="text-small text-muted-foreground">Profiling…</p>}

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
