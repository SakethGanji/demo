/**
 * DatasetSelectorField - picks one of the current project's datasets (Profile / Sample / Aggregate nodes).
 *
 * The stored value is the project dataset's link `id`, so a rename never breaks the workflow; the trigger shows its
 * current name. Legacy values (a raw analytics UUID or a name) still show their dataset; a value that matches nothing
 * is kept and marked missing, never silently cleared.
 * Expression values never reach here (DynamicNodeForm renders them as a normal expression field).
 */
import { useId } from 'react';
import { ExternalLink } from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { useDatasets } from '@/features/datasets/useDatasets';
import { datasetsPageUrl, isUuid, missingDatasetMessage, resolveDatasetValue, shapeLabel } from '@/features/datasets/lib/datasets';
import { useEditorProjectStore } from '../../stores/editorProjectStore';

interface NodeProperty {
  displayName: string;
  name: string;
  required?: boolean;
  description?: string;
}

interface DatasetSelectorFieldProps {
  property: NodeProperty;
  value: string | undefined;
  onChange: (value: string) => void;
}

export function DatasetSelectorField({ property, value, onChange }: DatasetSelectorFieldProps) {
  const fieldId = useId();
  const projectId = useEditorProjectStore((s) => s.projectId);
  const slug = useEditorProjectStore((s) => s.slug);
  const { data, isLoading, error } = useDatasets(projectId);
  const items = data ?? [];
  const { item, unknown } = resolveDatasetValue(value, items);
  const selected = item?.id ?? unknown ?? '';

  const labels: Record<string, string> = {
    '': isLoading ? 'Loading datasets…' : error ? 'Error loading datasets' : items.length ? 'Select a dataset…' : 'No datasets in this project yet',
    ...Object.fromEntries(items.map((d) => [d.id, d.name])),
  };
  if (unknown && !isLoading) labels[unknown] = isUuid(unknown) ? 'Deleted dataset' : `${unknown} (not found)`;

  const openDatasets = () => window.open(datasetsPageUrl(slug), '_blank', 'noopener');

  return (
    <div>
      <label htmlFor={fieldId} className="mb-1 block text-[12.5px] font-medium text-foreground">
        {property.displayName}
        {property.required && <span className="text-destructive ml-0.5">*</span>}
      </label>
      <Select value={selected} onValueChange={(v) => onChange((v as string) || '')} disabled={isLoading} items={labels}>
        <SelectTrigger id={fieldId} className="w-full" data-testid="dataset-selector">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((d) => (
            <SelectItem key={d.id} value={d.id} data-testid={`dataset-option-${d.name}`}>
              <span className="min-w-0 truncate">{d.name}</span>
              <span className="ml-auto pl-3 text-[11.5px] tabular-nums text-muted-foreground">{shapeLabel(d)}</span>
            </SelectItem>
          ))}
          {items.length === 0 && !isLoading && (
            <div className="px-2 py-1.5 text-[12px] text-muted-foreground" data-testid="dataset-selector-empty">
              No datasets in this project yet
            </div>
          )}
        </SelectContent>
      </Select>
      <div className="mt-1 flex items-start gap-2">
        {property.description && <p className="min-w-0 flex-1 text-[12px] leading-4 text-muted-foreground">{property.description}</p>}
        <button
          type="button"
          onClick={openDatasets}
          className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-chip text-[12px] leading-4 text-primary outline-none hover:underline focus-visible:shadow-focus [&_svg]:size-3"
          data-testid="dataset-selector-import"
        >
          Import dataset… <ExternalLink />
        </button>
      </div>
      {unknown && !isLoading && (
        <p className="mt-1 text-[12px] text-warning-text" data-testid="dataset-selector-missing">
          {missingDatasetMessage(unknown)}
        </p>
      )}
      {error && <p className="mt-1 text-[12px] text-destructive">Failed to load datasets. Please try again.</p>}
    </div>
  );
}
