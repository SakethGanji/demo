/**
 * The transform lens: shape a pipeline and see what it would produce.
 *
 * The interesting decision here is what this panel deliberately does NOT do.
 *
 * `POST /transformations/compile` has two modes. Omit `rows` and it is a
 * schema-only compile: it needs `dataset:read`, touches no data, and answers
 * "what columns would come out". Set `rows` and it becomes a read of the
 * pipeline's *output*, gated behind raw access — because a `compute` step can
 * copy a sensitive column into a new name, so masking by source column name
 * would not hold.
 *
 * This lens only ever asks the first question. That makes it work identically
 * for every seat, which is the whole point: a viewer can design a pipeline and
 * see its shape without ever being handed a value they aren't cleared for.
 * Running it for real is a separate, gated action.
 */

import { useState } from 'react';
import { Eye, Play, Wand2 } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { cn } from '@/shared/lib/utils';
import { useCompilePreview, useTransformations } from '../../hooks/useAnalysis';
import { errorText } from '../../hooks/useDatasetActions';
import { DtypeChip, LensEmpty, LensError, Section } from './primitives';

interface TransformLensProps {
  datasetId: string | null;
  sheet: string | null;
  /** Columns of the sheet currently on screen; the pipeline's input schema. */
  columns: { name: string; dtype?: string | null }[];
}

export function TransformLens({ datasetId, sheet, columns }: TransformLensProps) {
  const saved = useTransformations(datasetId);
  const compile = useCompilePreview(datasetId);
  const [dropped, setDropped] = useState<string[]>([]);

  const toggle = (name: string) =>
    setDropped((d) => (d.includes(name) ? d.filter((c) => c !== name) : [...d, name]));

  const steps = dropped.length ? [{ type: 'drop', columns: dropped }] : [];
  const result = compile.data;

  return (
    <>
      <Section title={`Saved pipelines (${saved.data?.items.length ?? 0})`}>
        {saved.isLoading && <p className="text-[11px] text-muted-foreground">Loading…</p>}
        {saved.error && <LensError>{errorText(saved.error)}</LensError>}
        {!saved.isLoading && (saved.data?.items.length ?? 0) === 0 && (
          <LensEmpty>No saved transformations for this dataset.</LensEmpty>
        )}
        {(saved.data?.items ?? []).map((t) => (
          <div key={t.id} className="mb-1 rounded-md border border-border px-2 py-1.5" data-testid="transformation">
            <div className="flex items-center gap-1.5">
              <Wand2 className="size-3 shrink-0 text-muted-foreground" />
              <span className="truncate text-[12px]">{t.name}</span>
              {t.sheet && (
                <Badge variant="glass" className="ml-auto font-mono text-[9px]">
                  {t.sheet}
                </Badge>
              )}
            </div>
            {t.description && (
              <p className="mt-0.5 text-[10px] leading-tight text-muted-foreground">
                {t.description}
              </p>
            )}
          </div>
        ))}
      </Section>

      <Section title="Shape a pipeline">
        {columns.length === 0 ? (
          <LensEmpty>Select a sheet to see its columns.</LensEmpty>
        ) : (
          <>
            <p className="mb-1.5 text-[10px] text-muted-foreground">
              Tap a column to drop it, then compile to see the resulting schema.
            </p>
            <div className="flex flex-wrap gap-1">
              {columns.map((c) => {
                const off = dropped.includes(c.name);
                return (
                  <button
                    key={c.name}
                    onClick={() => toggle(c.name)}
                    data-testid="transform-column-toggle"
                    aria-pressed={off}
                    className={cn(
                      'rounded border px-1.5 py-0.5 font-mono text-[10px] transition-colors',
                      off
                        ? 'border-destructive/40 text-muted-foreground/60 line-through'
                        : 'border-border hover:border-primary/50',
                    )}
                  >
                    {c.name}
                  </button>
                );
              })}
            </div>

            <div className="mt-2 flex items-center gap-1.5">
              <Button
                size="xs"
                variant="outline"
                disabled={compile.isPending || !sheet}
                onClick={() => compile.mutate({ sheet, steps })}
                data-testid="transform-compile"
              >
                <Play className="size-3" />
                {compile.isPending ? 'Compiling…' : 'Compile schema'}
              </Button>
              {dropped.length > 0 && (
                <Button size="xs" variant="ghost" onClick={() => setDropped([])}>
                  Reset
                </Button>
              )}
            </div>

            {compile.error && (
              <div className="mt-2">
                <LensError>{errorText(compile.error)}</LensError>
              </div>
            )}

            {result && (
              <div className="mt-2" data-testid="transform-output-schema">
                <div className="mb-1 flex items-center gap-1.5">
                  <Eye className="size-3 text-muted-foreground" />
                  <span className="text-[10px] text-muted-foreground">
                    {result.output_schema.length} output column
                    {result.output_schema.length === 1 ? '' : 's'}
                  </span>
                </div>
                {result.output_schema.map((c) => (
                  <div
                    key={c.name}
                    className="flex items-center gap-1.5 py-0.5"
                    data-testid="transform-output-column"
                  >
                    <span
                      className="truncate font-mono text-[10px]"
                      data-testid="transform-output-name"
                    >
                      {c.name}
                    </span>
                    <DtypeChip dtype={c.dtype} className="ml-auto" />
                  </div>
                ))}
                {/* Say plainly that no values were fetched — it is the point. */}
                <p className="mt-1.5 text-[10px] text-muted-foreground/70">
                  Schema only — no rows were read, so this works the same for every seat.
                </p>
              </div>
            )}
          </>
        )}
      </Section>
    </>
  );
}
