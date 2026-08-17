/**
 * The cockpit KPI strip — the 70px band under the app shell.
 *
 * This is the screen's first read: tenant-wide scope before you look at any
 * one dataset. Rule 4 in its purest form — large figures over recessive
 * eyebrows, `tabular-nums` throughout, grouped by SPACE with exactly one
 * vertical rule where the meaning changes (inventory on the left, governance
 * exposure on the right). Rule 8 forbids wrapping these in cards: a strip of
 * homogeneous counts is a strip, not six panels.
 *
 * Every figure here is derived from the catalog the rail already loaded, so
 * the band costs no extra request. Nothing is fabricated: the prototype shows
 * a tenant-wide "PII COLUMNS" count, which would need a per-dataset dictionary
 * fan-out, so this shows the masked-column count for the dataset ON SCREEN and
 * says so rather than implying a tenant sweep nobody ran.
 */

import { useMemo } from 'react';
import { Metric } from '@/shared/components/instrument/Typography';
import { Sparkline } from '@/shared/components/instrument/charts';
import { compact, formatSizeParts } from '@/shared/lib/format';
import type { DatasetInfo } from '../hooks/useDatasets';

interface CockpitStripProps {
  /** The datasets actually loaded — the first page, not necessarily the tenant. */
  datasets: DatasetInfo[];
  /**
   * What the server said the tenant holds. The catalog read is capped at
   * `MAX_PAGE_LIMIT`, so above that these figures cover the loaded page only —
   * and a strip that silently reports 200 as "Datasets" while the tenant has
   * 4,000 is precisely the kind of confident wrong number the rest of this
   * codebase refuses to print. When the two disagree, say so.
   */
  total: number | null;
  /** Masked columns on the dataset currently open, if any. */
  maskedHere: number;
  /** Name of the dataset those masked columns belong to. */
  scopeName: string | null;
  syncedAt: string;
}

export function CockpitStrip({
  datasets,
  total,
  maskedHere,
  scopeName,
  syncedAt,
}: CockpitStripProps) {
  const truncated = total != null && total > datasets.length;
  const stats = useMemo(() => {
    let rows = 0;
    let bytes = 0;
    let restricted = 0;
    let undocumented = 0;
    let drift = 0;
    for (const d of datasets) {
      rows += d.row_count ?? 0;
      bytes += d.size_bytes ?? 0;
      if (d.classification === 'restricted' || d.classification === 'confidential') restricted += 1;
      if (d.documentation === 'none') undocumented += 1;
      if (d.has_schema_drift) drift += 1;
    }
    return { rows, bytes, restricted, undocumented, drift };
  }, [datasets]);

  // Relative size of each dataset, largest first — a shape, not a time series,
  // and labelled as such. Two points minimum or Sparkline withholds it.
  const spark = useMemo(
    () =>
      datasets
        .map((d) => d.row_count ?? 0)
        .sort((a, b) => b - a)
        .slice(0, 12)
        .reverse(),
    [datasets],
  );

  const size = formatSizeParts(stats.bytes);

  return (
    <div className="flex h-[70px] shrink-0 items-center gap-7 bg-[image:var(--chrome-strip)] px-5 shadow-[var(--chrome-strip-shadow)]">
      <Metric
        label="Datasets"
        value={(total ?? datasets.length).toLocaleString()}
        size="hero"
        note={truncated ? `${datasets.length} loaded` : undefined}
      />

      <div className="flex items-end gap-2">
        <Metric
          label="Rows stored"
          value={compact(stats.rows)}
          size="hero"
          // Summed over what was loaded. Above the page cap that is a floor,
          // not the total, and it says so rather than implying a sweep.
          note={truncated ? `across ${datasets.length} loaded` : undefined}
        />
        <div className="pb-1.5">
          <Sparkline points={spark} />
        </div>
      </div>

      <Metric
        label="Storage"
        value={size.value}
        unit={size.unit}
        size="hero"
        note={truncated ? 'loaded page only' : undefined}
      />

      {/* The ONE rule in this band. Rule 3 allows a rule at a zone boundary,
       * and this is one: inventory on the left, exposure on the right. */}
      <span className="h-[26px] w-px shrink-0 bg-[var(--r3)]" />

      <Metric
        label="Masked here"
        value={String(maskedHere)}
        size="hero"
        note={scopeName ? `on ${scopeName}` : 'no dataset open'}
      />
      <Metric
        label="Classified"
        value={String(stats.restricted)}
        size="hero"
        note={truncated ? 'label only · loaded page' : 'label only — enforces nothing'}
      />
      <Metric
        label="Undocumented"
        value={String(stats.undocumented)}
        size="hero"
        note={stats.drift > 0 ? `${stats.drift} with drift` : 'no drift'}
      />

      {/* SIGNAL — liveness. The app shell spends one accent on the active
       * route; this is the second and last on the screen. */}
      <div className="ml-auto flex items-center gap-2 self-center">
        <span className="size-[7px] rounded-full bg-[var(--sig)] shadow-[0_0_8px_var(--sig)]" />
        <span className="font-mono text-micro text-muted-foreground">
          <span className="font-medium text-foreground">LIVE</span> · SYNC {syncedAt}
        </span>
      </div>
    </div>
  );
}
