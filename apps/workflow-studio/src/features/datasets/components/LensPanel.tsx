/**
 * The contextual lens panel.
 *
 * This is the difference between this page and the reference UI's eight tabs:
 * choosing a lens never hides your data. The panel docks beside the table — the
 * same right-hand dock idiom the workflow editor already uses — and acts on
 * whatever version/sheet is currently on screen.
 *
 * Each lens owns its own data fetching, so switching lenses does not refetch the
 * grid and an expensive lens (profiling) costs nothing until it is opened.
 */

import {
  Info,
  ShieldCheck,
  History,
  BarChart3,
  Share2,
  Wand2,
  Library as LibraryIcon,
} from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import type { DatasetInfo, QualityRule, SheetColumn, VersionSummary } from '../hooks/useDatasets';
import { AnalyticsLens } from './lenses/AnalyticsLens';
import { LibraryLens } from './lenses/LibraryLens';
import { OverviewLens } from './lenses/OverviewLens';
import { QualityLens } from './lenses/QualityLens';
import { RelationshipsLens } from './lenses/RelationshipsLens';
import { TransformLens } from './lenses/TransformLens';
import { VersionsLens } from './lenses/VersionsLens';

export const LENSES = [
  { id: 'overview', label: 'Overview', icon: Info },
  { id: 'quality', label: 'Quality', icon: ShieldCheck },
  { id: 'versions', label: 'Versions', icon: History },
  { id: 'analytics', label: 'Analytics', icon: BarChart3 },
  { id: 'relationships', label: 'Relations', icon: Share2 },
  { id: 'transform', label: 'Transform', icon: Wand2 },
  { id: 'library', label: 'Library', icon: LibraryIcon },
] as const;

export type LensId = (typeof LENSES)[number]['id'];

interface LensPanelProps {
  lens: LensId;
  onLensChange: (l: LensId) => void;
  dataset: DatasetInfo | null;
  datasetId: string | null;
  versions: VersionSummary[];
  version: number | null;
  sheet: string | null;
  /**
   * The stable key for the current sheet. Distinct from `sheet` (the display
   * name): metadata routes address a sheet by `sheet_key`, and the two differ
   * after a confirmed rename.
   */
  sheetKey: string | null;
  columns: SheetColumn[];
  rules: QualityRule[];
  rulesLoading: boolean;
  maskedColumns: string[];
}

export function LensPanel({
  lens,
  onLensChange,
  dataset,
  datasetId,
  versions,
  version,
  sheet,
  sheetKey,
  columns,
  rules,
  rulesLoading,
  maskedColumns,
}: LensPanelProps) {
  return (
    <aside className="flex w-96 shrink-0 flex-col border-l border-border bg-[var(--surface)]/40">
      <div className="flex flex-wrap gap-0.5 border-b border-border p-1.5">
        {LENSES.map((l) => {
          const Icon = l.icon;
          const active = l.id === lens;
          return (
            <button
              key={l.id}
              onClick={() => onLensChange(l.id)}
              data-testid={`lens-${l.id}`}
              aria-pressed={active}
              className={cn(
                'flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] transition-colors',
                active
                  ? 'bg-primary/10 text-primary'
                  : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
              )}
            >
              <Icon className="size-3" />
              {l.label}
            </button>
          );
        })}
      </div>

      {/* The panel owns its own scroll. */}
      <div className="min-h-0 flex-1 overflow-y-auto p-3" data-testid="lens-body">
        {!dataset && <p className="text-[12px] text-muted-foreground">Select a dataset.</p>}

        {dataset && lens === 'overview' && (
          <OverviewLens
            dataset={dataset}
            datasetId={datasetId}
            sheetKey={sheetKey}
            columns={columns}
            maskedColumns={maskedColumns}
          />
        )}

        {dataset && lens === 'quality' && (
          <QualityLens
            datasetId={datasetId}
            version={version}
            sheet={sheet}
            columns={columns}
            rules={rules}
            rulesLoading={rulesLoading}
          />
        )}

        {dataset && lens === 'versions' && (
          <VersionsLens datasetId={datasetId} versions={versions} version={version} />
        )}

        {dataset && lens === 'analytics' && (
          <AnalyticsLens datasetId={datasetId} version={version} sheet={sheet} />
        )}

        {dataset && lens === 'relationships' && <RelationshipsLens datasetId={datasetId} />}

        {dataset && lens === 'transform' && (
          <TransformLens datasetId={datasetId} sheet={sheet} columns={columns} />
        )}

        {dataset && lens === 'library' && <LibraryLens datasetId={datasetId} />}
      </div>
    </aside>
  );
}
