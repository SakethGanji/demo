/**
 * The relationships lens: how this dataset joins to the others, and where it
 * came from.
 *
 * Two things here are easy to render dishonestly and are handled on purpose:
 *
 *  - An edge pointing at a dataset this seat cannot read is dropped from both
 *    `items` and `total`. So the count is "relationships you can see" — never
 *    labelled as the dataset's total.
 *  - `suggest` reports `skipped`, meaning the sweep was not exhaustive. A run
 *    that skipped pairs says so; silence would read as "nothing else exists".
 */

import { ArrowRight, Check, GitBranch, Sparkles, X } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import {
  useLineage,
  useRelationships,
  useReviewRelationship,
  useSeedRelationships,
  useSuggestRelationships,
  type Relationship,
} from '../../hooks/useAnalysis';
import { errorText } from '../../hooks/useDatasetActions';
import { LensEmpty, LensError, Section } from './primitives';

function statusVariant(status: string): 'success' | 'destructive' | 'glass' {
  if (status === 'confirmed') return 'success';
  if (status === 'rejected') return 'destructive';
  return 'glass';
}

function EdgeCard({
  edge,
  onReview,
  reviewing,
}: {
  edge: Relationship;
  onReview: (id: string, action: 'confirm' | 'reject') => void;
  reviewing: boolean;
}) {
  return (
    <div className="mb-1.5 rounded-md border border-border px-2 py-1.5" data-testid="relationship">
      <div className="flex items-center gap-1.5">
        <Badge variant={statusVariant(edge.status)}>{edge.status}</Badge>
        {edge.confidence != null && (
          <span className="text-[10px] text-muted-foreground tabular-nums">
            {(edge.confidence * 100).toFixed(0)}% confident
          </span>
        )}
        {edge.method && (
          <span className="ml-auto text-[9px] text-muted-foreground/70">{edge.method}</span>
        )}
      </div>

      <div className="mt-1 flex items-center gap-1 font-mono text-[10px]">
        <span className="truncate" title={`${edge.from_sheet}.${edge.from_column}`}>
          {edge.from_sheet}.{edge.from_column}
        </span>
        <ArrowRight className="size-2.5 shrink-0 text-muted-foreground" />
        <span className="truncate" title={`${edge.to_sheet}.${edge.to_column}`}>
          {edge.to_sheet}.{edge.to_column}
        </span>
      </div>

      {/* A suggestion is a claim awaiting a human; give it the two verbs. */}
      {edge.status === 'suggested' && (
        <div className="mt-1.5 flex gap-1">
          <Button
            size="xs"
            variant="outline"
            disabled={reviewing}
            onClick={() => onReview(edge.id, 'confirm')}
            data-testid="relationship-confirm"
          >
            <Check className="size-3" />
            Confirm
          </Button>
          <Button
            size="xs"
            variant="ghost"
            disabled={reviewing}
            onClick={() => onReview(edge.id, 'reject')}
            data-testid="relationship-reject"
          >
            <X className="size-3" />
            Reject
          </Button>
        </div>
      )}
    </div>
  );
}

export function RelationshipsLens({ datasetId }: { datasetId: string | null }) {
  const rels = useRelationships(datasetId);
  const lineage = useLineage(datasetId);
  const suggest = useSuggestRelationships(datasetId);
  const seed = useSeedRelationships(datasetId);
  const review = useReviewRelationship(datasetId);

  const edges = rels.data?.items ?? [];
  const parents = lineage.data?.parents ?? [];
  const children = lineage.data?.children ?? [];

  return (
    <>
      <Section
        title={`Relationships (${edges.length})`}
        action={
          <div className="flex gap-1">
            <Button
              size="xs"
              variant="ghost"
              disabled={seed.isPending || !datasetId}
              onClick={() => seed.mutate()}
              title="Create edges from this dataset's foreign_key quality rules"
              data-testid="relationship-seed"
            >
              <GitBranch className="size-3" />
              Seed
            </Button>
            <Button
              size="xs"
              variant="outline"
              disabled={suggest.isPending || !datasetId}
              onClick={() => suggest.mutate()}
              title="Statistically discover overlapping columns"
              data-testid="relationship-suggest"
            >
              <Sparkles className="size-3" />
              {suggest.isPending ? 'Scanning…' : 'Suggest'}
            </Button>
          </div>
        }
      >
        {rels.isLoading && <p className="text-[11px] text-muted-foreground">Loading…</p>}
        {rels.error && <LensError>{errorText(rels.error)}</LensError>}
        {!rels.isLoading && !rels.error && edges.length === 0 && (
          <LensEmpty>
            No relationships visible to this seat. Seed from foreign-key rules, or run a scan.
          </LensEmpty>
        )}
        {edges.map((e) => (
          <EdgeCard
            key={e.id}
            edge={e}
            reviewing={review.isPending}
            onReview={(id, action) => review.mutate({ id, action })}
          />
        ))}
        {edges.length > 0 && (
          <p className="mt-1 text-[10px] text-muted-foreground/70">
            Only edges whose other side is readable by this seat are listed.
          </p>
        )}
      </Section>

      <Section title="Lineage">
        {lineage.isLoading && <p className="text-[11px] text-muted-foreground">Loading…</p>}
        {!lineage.isLoading && parents.length === 0 && children.length === 0 && (
          <LensEmpty>This dataset was uploaded directly — no derived parents.</LensEmpty>
        )}

        {parents.length > 0 && (
          <div className="mb-2">
            <p className="mb-1 text-[10px] text-muted-foreground">Derived from</p>
            {parents.map((p) => (
              <div key={p.id} className="py-0.5 text-[11px]" data-testid="lineage-parent">
                {/* A hidden parent keeps its row: the edge is real even when the name isn't visible. */}
                {p.parent_visible === false ? (
                  <span className="text-muted-foreground italic">
                    A dataset in another team{p.relation ? ` (${p.relation})` : ''}
                  </span>
                ) : (
                  <span>
                    {p.parent_dataset_name ?? 'Unknown'}
                    {p.parent_version_number != null && (
                      <span className="text-muted-foreground"> v{p.parent_version_number}</span>
                    )}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}

        {children.length > 0 && (
          <div>
            <p className="mb-1 text-[10px] text-muted-foreground">Used to build</p>
            {children.map((c) => (
              <div key={c.id} className="py-0.5 text-[11px]" data-testid="lineage-child">
                {c.child_visible === false ? (
                  <span className="text-muted-foreground italic">A dataset in another team</span>
                ) : (
                  <span>
                    {c.child_dataset_name ?? 'Unknown'}
                    {c.child_version_number != null && (
                      <span className="text-muted-foreground"> v{c.child_version_number}</span>
                    )}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}
