/**
 * The overview lens: what this dataset is, and the dictionary that governs how
 * it is shown.
 *
 * The column dictionary is here rather than in a settings screen because
 * `sensitivity` is not documentation — it is the switch that turns masking on.
 * Setting it to `pii` or `confidential` immediately changes what a viewer or
 * editor sees in the grid beside it, and refuses profiling outright. Editing it
 * next to the "Masked for this seat" list is what makes that cause-and-effect
 * legible.
 */

import { useState } from 'react';
import { Pencil, Save, X } from 'lucide-react';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import {
  useSetColumnMetadata,
  useUpdateDataset,
  type DatasetPatch,
} from '../../hooks/useDatasetActions';
import type { DatasetInfo } from '../../hooks/useDatasets';
import { LensEmpty, Row, Section } from './primitives';
import { fieldClass } from '../fieldStyles';
import { Metric } from '@/shared/components/instrument/Typography';
import { compact } from '@/shared/lib/format';

const CLASSIFICATIONS = ['public', 'internal', 'confidential', 'restricted'] as const;

/** Matched case-insensitively server-side; these are the values that mask. */
const SENSITIVITIES = ['', 'pii', 'confidential', 'restricted', 'sensitive', 'secret', 'phi'];


interface OverviewLensProps {
  dataset: DatasetInfo;
  datasetId: string | null;
  sheetKey: string | null;
  columns: { name: string }[];
  maskedColumns: string[];
}

export function OverviewLens({
  dataset,
  datasetId,
  sheetKey,
  columns,
  maskedColumns,
}: OverviewLensProps) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<DatasetPatch>({});
  const update = useUpdateDataset(datasetId);

  const [dictColumn, setDictColumn] = useState('');
  const [businessName, setBusinessName] = useState('');
  const [sensitivity, setSensitivity] = useState('');
  const setColumnMeta = useSetColumnMetadata(datasetId);

  const startEdit = () => {
    // Seed from the record so an untouched field round-trips its current value.
    setForm({
      name: dataset.name,
      description: dataset.description ?? '',
      classification: (dataset.classification ?? 'internal') as DatasetPatch['classification'],
      domain: dataset.domain ?? '',
      source_system: dataset.source_system ?? '',
      deprecated: dataset.deprecated ?? false,
    });
    setEditing(true);
  };

  const masked = new Set(maskedColumns);

  return (
    <>
      <Section
        title="Dataset"
        action={
          editing ? (
            <Button size="xs" variant="ghost" onClick={() => setEditing(false)}>
              <X className="size-3" />
            </Button>
          ) : (
            <Button size="xs" variant="ghost" onClick={startEdit} data-testid="metadata-edit">
              <Pencil className="size-3" />
              Edit
            </Button>
          )
        }
      >
        {editing ? (
          <div data-testid="metadata-form">
            <label className="text-micro text-muted-foreground">Name</label>
            <input
              value={form.name ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              aria-label="Dataset name"
              className={fieldClass}
              data-testid="metadata-name"
            />

            <label className="mt-1.5 block text-micro text-muted-foreground">Description</label>
            <input
              value={form.description ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              aria-label="Dataset description"
              className={fieldClass}
              data-testid="metadata-description"
            />

            <label className="mt-1.5 block text-micro text-muted-foreground">Classification</label>
            <select
              value={form.classification ?? 'internal'}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  classification: e.target.value as DatasetPatch['classification'],
                }))
              }
              aria-label="Dataset classification"
              className={fieldClass}
              data-testid="metadata-classification"
            >
              {CLASSIFICATIONS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>

            <label className="mt-1.5 block text-micro text-muted-foreground">Domain</label>
            <input
              value={form.domain ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, domain: e.target.value }))}
              aria-label="Dataset domain"
              className={fieldClass}
              data-testid="metadata-domain"
            />

            <label className="mt-1.5 flex items-center gap-1.5 text-small">
              <input
                type="checkbox"
                checked={form.deprecated ?? false}
                onChange={(e) => setForm((f) => ({ ...f, deprecated: e.target.checked }))}
                data-testid="metadata-deprecated"
              />
              Deprecated
            </label>

            <Button
              size="xs"
              className="mt-2 w-full"
              disabled={update.isPending}
              onClick={() => update.mutate(form, { onSuccess: () => setEditing(false) })}
              data-testid="metadata-save"
            >
              <Save className="size-3" />
              {update.isPending ? 'Saving…' : 'Save'}
            </Button>
          </div>
        ) : (
          <>
            {/* Rule 4 — the numbers that answer "what am I looking at" get to
             * be figures, not another label/value row. Grouped by space in a
             * 2x2; wrapping four homogeneous counts in cards is exactly what
             * rule 8 reserves a surface AGAINST. */}
            <div className="mb-3 grid grid-cols-2 gap-x-3 gap-y-2.5">
              <Metric
                label="Rows"
                value={compact(dataset.row_count)}
                size="figure"
                note={`as-of v${dataset.current_version ?? '—'}`}
              />
              <Metric
                label="Columns"
                value={String(columns.length)}
                size="figure"
                note={
                  maskedColumns.length > 0
                    ? `${maskedColumns.length} masked`
                    : 'none masked'
                }
              />
              <Metric
                label="Documentation"
                value={dataset.documentation ?? 'none'}
                size="figure"
                note="per-column dictionary"
              />
              <Metric
                label="Classification"
                value={dataset.classification ?? 'unclassified'}
                size="figure"
                note="label only"
              />
            </div>

            <Row label="Name" value={dataset.name} />
            <Row label="Description" value={dataset.description || '—'} />
            <Row label="Classification" value={dataset.classification} />
            <Row label="Domain" value={dataset.domain ?? '—'} />
            <Row label="Source" value={dataset.source_system ?? '—'} />
            <Row label="Rows" value={(dataset.row_count ?? 0).toLocaleString()} />
            <Row label="Current version" value={`v${dataset.current_version ?? '—'}`} />
            {dataset.deprecated && (
              <Row label="Status" value={<Badge variant="destructive">deprecated</Badge>} />
            )}
          </>
        )}
      </Section>

      <Section title="Health">
        <Row
          label="Validation"
          value={<Badge variant="glass">{dataset.validation_status ?? 'none'}</Badge>}
        />
        <Row
          label="Documentation"
          value={
            <Badge variant={dataset.documentation === 'full' ? 'success' : 'glass'}>
              {dataset.documentation ?? 'none'}
            </Badge>
          }
        />
        <Row
          label="Schema drift"
          value={
            dataset.has_schema_drift ? (
              <Badge variant="destructive">drift</Badge>
            ) : (
              <Badge variant="success">none</Badge>
            )
          }
        />
      </Section>

      {maskedColumns.length > 0 && (
        <Section title="Masked for this seat">
          <div className="flex flex-wrap gap-1">
            {maskedColumns.map((c) => (
              <Badge key={c} variant="glass" className="font-mono text-micro">
                {c}
              </Badge>
            ))}
          </div>
          <p className="mt-1.5 text-small text-muted-foreground/70">
            Masking applies to a viewer and an editor. Only admin, owner or superuser see raw
            values.
          </p>
        </Section>
      )}

      <Section title="Column dictionary">
        {columns.length === 0 ? (
          <LensEmpty>Select a sheet to document its columns.</LensEmpty>
        ) : (
          <>
            <select
              value={dictColumn}
              onChange={(e) => {
                setDictColumn(e.target.value);
                setBusinessName('');
                setSensitivity('');
              }}
              aria-label="Dictionary column"
              className={fieldClass}
              data-testid="dict-column"
            >
              <option value="">Choose a column…</option>
              {columns.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                  {masked.has(c.name) ? ' (masked)' : ''}
                </option>
              ))}
            </select>

            {dictColumn && (
              <>
                <label className="mt-1.5 block text-micro text-muted-foreground">
                  Business name
                </label>
                <input
                  value={businessName}
                  onChange={(e) => setBusinessName(e.target.value)}
                  aria-label="Column business name"
                  className={fieldClass}
                  data-testid="dict-business-name"
                />

                <label className="mt-1.5 block text-micro text-muted-foreground">
                  Sensitivity
                </label>
                <select
                  value={sensitivity}
                  onChange={(e) => setSensitivity(e.target.value)}
                  aria-label="Column sensitivity"
                  className={fieldClass}
                  data-testid="dict-sensitivity"
                >
                  {SENSITIVITIES.map((s) => (
                    <option key={s} value={s}>
                      {s || 'none'}
                    </option>
                  ))}
                </select>

                <Button
                  size="xs"
                  className="mt-2 w-full"
                  disabled={!sheetKey || setColumnMeta.isPending}
                  onClick={() =>
                    sheetKey &&
                    setColumnMeta.mutate({
                      sheetKey,
                      column: dictColumn,
                      body: {
                        business_name: businessName || null,
                        sensitivity: sensitivity || null,
                      },
                    })
                  }
                  data-testid="dict-save"
                >
                  <Save className="size-3" />
                  {setColumnMeta.isPending ? 'Saving…' : 'Save column'}
                </Button>
                {/* PUT replaces the record, so say what "empty" will do. */}
                <p className="mt-1 text-micro text-muted-foreground/70">
                  Replaces this column's entry — blank fields are cleared. Setting a sensitivity
                  masks the column for viewers and editors immediately.
                </p>
              </>
            )}
          </>
        )}
      </Section>
    </>
  );
}
