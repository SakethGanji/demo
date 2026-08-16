/**
 * The versions lens: immutable history, and the moving pointers into it.
 *
 * Tags are the only mutable thing about a version, and there are two ways to
 * move one — which the panel keeps visibly distinct rather than collapsing into
 * a single "set" verb:
 *
 *   promote  — gated. The version must be `ready`, and if the dataset has any
 *              enabled rule the target needs a validation run with no error
 *              failures. This is the one you want in front of a release.
 *   set      — ungated. Skips both checks. Useful, but it is an escape hatch and
 *              is labelled as one.
 *
 * A `validation-required` / `validation-failed` refusal from promote is
 * therefore the system working, and the error text says so.
 */

import { useState } from 'react';
import { ArrowUpCircle, Tag as TagIcon, Undo2 } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { useTags } from '../../hooks/useAnalysis';
import { usePromoteTag, useRollbackTag, useSetTag } from '../../hooks/useDatasetActions';
import type { VersionSummary } from '../../hooks/useDatasets';
import { LensEmpty, Section } from './primitives';

const inputClass =
  'h-6 w-full rounded border border-border bg-background px-1.5 text-[11px] outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40';

interface VersionsLensProps {
  datasetId: string | null;
  versions: VersionSummary[];
  version: number | null;
}

export function VersionsLens({ datasetId, versions, version }: VersionsLensProps) {
  const tags = useTags(datasetId);
  const setTag = useSetTag(datasetId);
  const promote = usePromoteTag(datasetId);
  const rollback = useRollbackTag(datasetId);
  const [newTag, setNewTag] = useState('');

  const tagItems = tags.data?.items ?? [];

  return (
    <>
      <Section title={`History (${versions.length})`}>
        {versions.length === 0 && <LensEmpty>No versions yet.</LensEmpty>}
        {versions.map((v) => (
          <div
            key={v.version_number}
            className="mb-1 rounded-md border border-border px-2 py-1.5"
            data-testid="version-entry"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px] font-medium">
                v{v.version_number}
                {v.version_number === version && (
                  <span className="ml-1 text-[10px] text-primary">viewing</span>
                )}
              </span>
              <Badge variant={v.status === 'ready' ? 'success' : 'glass'}>
                {v.status ?? 'unknown'}
              </Badge>
            </div>
            <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground tabular-nums">
              <span>{(v.row_count ?? 0).toLocaleString()} rows</span>
              {/* Tags pinned to this version, so history and pointers read together. */}
              {tagItems
                .filter((t) => t.version_number === v.version_number)
                .map((t) => (
                  <Badge key={t.tag_name} variant="glass" className="h-4 gap-0.5 px-1 text-[9px]">
                    <TagIcon className="size-2" />
                    {t.tag_name}
                  </Badge>
                ))}
            </div>
          </div>
        ))}
        <p className="mt-1 text-[10px] text-muted-foreground/70">
          Versions are immutable; new data creates a new version.
        </p>
      </Section>

      <Section title={`Tags (${tagItems.length})`}>
        {tagItems.length === 0 && <LensEmpty>No tags yet.</LensEmpty>}

        {tagItems.map((t) => (
          <div key={t.tag_name} className="mb-1 rounded-md border border-border px-2 py-1.5" data-testid="tag-entry">
            <div className="flex items-center gap-1.5">
              <TagIcon className="size-3 shrink-0 text-muted-foreground" />
              <span className="truncate font-mono text-[11px]" data-testid="tag-name">
                {t.tag_name}
              </span>
              <span className="ml-auto text-[10px] text-muted-foreground tabular-nums">
                v{t.version_number}
              </span>
            </div>
            <div className="mt-1 flex gap-1">
              <Button
                size="xs"
                variant="outline"
                disabled={version == null || promote.isPending || t.version_number === version}
                onClick={() =>
                  version != null && promote.mutate({ tag: t.tag_name, version_number: version })
                }
                title="Promote to the version you are viewing (runs the quality gate)"
                data-testid="tag-promote"
              >
                <ArrowUpCircle className="size-3" />
                Promote
              </Button>
              <Button
                size="xs"
                variant="ghost"
                disabled={rollback.isPending}
                onClick={() => rollback.mutate({ tag: t.tag_name })}
                title="Move this tag to its previous version"
                data-testid="tag-rollback"
              >
                <Undo2 className="size-3" />
                Roll back
              </Button>
            </div>
          </div>
        ))}

        <div className="mt-2">
          <input
            value={newTag}
            onChange={(e) => setNewTag(e.target.value)}
            placeholder="new-tag-name"
            aria-label="New tag name"
            className={inputClass}
            data-testid="tag-new-name"
          />
          <Button
            size="xs"
            variant="outline"
            className="mt-1 w-full"
            disabled={!newTag.trim() || version == null || setTag.isPending}
            onClick={() =>
              version != null &&
              setTag.mutate(
                { tag_name: newTag.trim(), version_number: version },
                { onSuccess: () => setNewTag('') },
              )
            }
            data-testid="tag-set"
          >
            Set tag → v{version ?? '—'}
          </Button>
          {/* Name the escape hatch as an escape hatch. */}
          <p className="mt-1 text-[10px] text-muted-foreground/70">
            Sets the pointer directly, skipping the status and quality gates that Promote enforces.
          </p>
        </div>
      </Section>
    </>
  );
}
