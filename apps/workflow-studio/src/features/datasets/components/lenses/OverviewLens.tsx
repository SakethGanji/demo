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

const CLASSIFICATIONS = ['public', 'internal', 'confidential', 'restricted'] as const;

/** Matched case-insensitively server-side; these are the values that mask. */
const SENSITIVITIES = ['', 'pii', 'confidential', 'restricted', 'sensitive', 'secret', 'phi'];

const inputClass =
  'h-6 w-full rounded border border-border bg-background px-1.5 text-[11px] outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40';

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
            <label className="text-[10px] text-muted-foreground">Name</label>
            <input
              value={form.name ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              aria-label="Dataset name"
              className={inputClass}
              data-testid="metadata-name"
            />

            <label className="mt-1.5 block text-[10px] text-muted-foreground">Description</label>
            <input
              value={form.description ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              aria-label="Dataset description"
              className={inputClass}
              data-testid="metadata-description"
            />

            <label className="mt-1.5 block text-[10px] text-muted-foreground">Classification</label>
            <select
              value={form.classification ?? 'internal'}
              onChange={(e) =>
                setForm((f) => ({
                  ...f,
                  classification: e.target.value as DatasetPatch['classification'],
                }))
              }
              aria-label="Dataset classification"
              className={inputClass}
              data-testid="metadata-classification"
            >
              {CLASSIFICATIONS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>

            <label className="mt-1.5 block text-[10px] text-muted-foreground">Domain</label>
            <input
              value={form.domain ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, domain: e.target.value }))}
              aria-label="Dataset domain"
              className={inputClass}
              data-testid="metadata-domain"
            />

            <label className="mt-1.5 flex items-center gap-1.5 text-[11px]">
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
              <Badge key={c} variant="glass" className="font-mono text-[10px]">
                {c}
              </Badge>
            ))}
          </div>
          <p className="mt-1.5 text-[11px] text-muted-foreground/70">
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
              className={inputClass}
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
                <label className="mt-1.5 block text-[10px] text-muted-foreground">
                  Business name
                </label>
                <input
                  value={businessName}
                  onChange={(e) => setBusinessName(e.target.value)}
                  aria-label="Column business name"
                  className={inputClass}
                  data-testid="dict-business-name"
                />

                <label className="mt-1.5 block text-[10px] text-muted-foreground">
                  Sensitivity
                </label>
                <select
                  value={sensitivity}
                  onChange={(e) => setSensitivity(e.target.value)}
                  aria-label="Column sensitivity"
                  className={inputClass}
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
                <p className="mt-1 text-[10px] text-muted-foreground/70">
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
