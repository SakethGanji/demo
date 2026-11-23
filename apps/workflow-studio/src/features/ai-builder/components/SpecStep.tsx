import { WellRow } from '@/shared/components/ui/surface';
import { Trash2 } from 'lucide-react';
import { Input } from '@/shared/components/ui/input';
import { Button } from '@/shared/components/ui/button';
import { Tip } from '@/shared/components/ui/tooltip';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import type { ApiRefOption } from '../lib/catalog';
import type { StepTypeOption } from '../useSpecCatalog';
import type { SpecIssue, SpecStep as Step } from '../types';
import { IssueList } from './SpecIssues';

const NO_API = '__none__';

export function SpecStep({ index, step, stepTypes, apiRefs, issues, readOnly, onChange, onRemove }: {
  index: number;
  step: Step;
  stepTypes: StepTypeOption[];
  apiRefs: ApiRefOption[];
  issues: SpecIssue[];
  readOnly?: boolean;
  onChange: (patch: Partial<Step>) => void;
  onRemove: () => void;
}) {
  const typeItems = Object.fromEntries(stepTypes.map((t) => [t.type, t.label]));
  // a type the catalog does not list (or a catalog still loading) must still show what the plan says
  if (step.node_type && !(step.node_type in typeItems)) typeItems[step.node_type] = step.node_type;
  const apiItems: Record<string, string> = { [NO_API]: 'No outside service' };
  for (const o of apiRefs) apiItems[o.ref] = o.label;
  if (step.api_ref && !(step.api_ref in apiItems)) apiItems[step.api_ref] = step.api_ref;
  const callsOutside = step.node_type === 'HttpRequest';

  return (
    <WellRow className="flex-col items-stretch gap-1.5 py-2.5" data-testid={`ai-spec-step-${index}`}>
      <div className="flex items-center gap-2">
        <span className="inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-recess text-[11px] font-medium tabular-nums text-muted-foreground">
          {index + 1}
        </span>
        <Input
          value={step.name}
          readOnly={readOnly}
          aria-label={`Name of step ${index + 1}`}
          className="min-w-0 flex-1"
          onChange={(e) => onChange({ name: e.target.value })}
        />
        {!readOnly && (
          <Tip label="Remove this step">
            <Button variant="ghost" size="icon-sm" onClick={onRemove} aria-label={`Remove step ${index + 1}`}><Trash2 /></Button>
          </Tip>
        )}
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2 pl-7 [&>*]:min-w-0">
        <Select
          value={step.node_type}
          items={typeItems}
          disabled={readOnly}
          onValueChange={(v) => v && onChange({ node_type: v, ...(v !== 'HttpRequest' ? { api_ref: undefined } : {}) })}
        >
          <SelectTrigger className="w-full" aria-label={`Kind of step ${index + 1}`} data-testid={`ai-spec-step-type-${index}`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(typeItems).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
          </SelectContent>
        </Select>
        {callsOutside && (
          <Select
            value={step.api_ref || NO_API}
            items={apiItems}
            disabled={readOnly}
            onValueChange={(v) => v && onChange({ api_ref: v === NO_API ? undefined : v })}
          >
            <SelectTrigger className="w-full" aria-label={`Service called by step ${index + 1}`} data-testid={`ai-spec-step-api-${index}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(apiItems).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
      </div>
      <div className="pl-7">
        <Input
          value={step.purpose}
          readOnly={readOnly}
          placeholder="What this step does"
          aria-label={`What step ${index + 1} does`}
          onChange={(e) => onChange({ purpose: e.target.value })}
        />
      </div>
      <div className="pl-7"><IssueList issues={issues} /></div>
    </WellRow>
  );
}
