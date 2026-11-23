/**
 * ExecuteWorkflowTrigger node details (DESIGN-STAGE8-REUSE 12.2): how this workflow declares its inputs, and who
 * calls it. The declared fields are the contract the callers' mapping tables are built from.
 */
import { useMemo } from 'react';
import type { Node } from '@xyflow/react';
import { Plus, Trash2 } from 'lucide-react';
import { Tile } from '@/shared/components/ui/surface';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { Input } from '@/shared/components/ui/input';
import { Checkbox } from '@/shared/components/ui/checkbox';
import { Button } from '@/shared/components/ui/button';
import { Field } from '@/shared/components/ui/field';
import type { WorkflowNodeData } from '../../../types/workflow';
import { useWorkflowStore } from '../../../stores/workflowStore';
import { useUsedBy } from '../../../hooks/useSubworkflows';
import { UsedByList } from './UsedByList';
import { useNodeParamsPatch } from './useNodeParams';
import { FIELD_TYPES, invalidFieldNames, readTriggerFields, type FieldType, type TriggerField } from './subworkflowParams';

const SOURCE_LABEL = {
  fields: 'Define using fields below',
  jsonSchema: 'Define using JSON schema',
  passthrough: 'Accept all data',
} as const;
type Source = keyof typeof SOURCE_LABEL;

function readSource(p: Record<string, unknown>): Source {
  const s = p.inputSource;
  if (s === 'fields' || s === 'jsonSchema' || s === 'passthrough') return s;
  const schema = p.inputSchema;
  const hasSchema = typeof schema === 'string' ? schema.trim() !== '' && schema.trim() !== '{}' : !!schema && Object.keys(schema as object).length > 0;
  return hasSchema ? 'jsonSchema' : 'fields';
}

export default function SubworkflowTriggerPanel({ node }: { node: Node<WorkflowNodeData> }) {
  const patch = useNodeParamsPatch(node.id);
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const raw = useMemo(() => (node.data.parameters as Record<string, unknown> | undefined) ?? {}, [node.data.parameters]);
  const source = readSource(raw);
  const fields = useMemo(() => readTriggerFields(raw.fields), [raw.fields]);
  const bad = invalidFieldNames(fields);
  const usedBy = useUsedBy(workflowId ?? undefined);

  const setFields = (next: TriggerField[]) => patch({ fields: next });
  const edit = (i: number, change: Partial<TriggerField>) => setFields(fields.map((f, j) => (j === i ? { ...f, ...change } : f)));

  return (
    <Tile className="overflow-hidden" data-testid="subwf-trigger-panel">
      <div className="space-y-3 px-3 py-3">
        <Field label="Input source" help="This is the contract callers see when they map inputs.">
          <Select value={source} onValueChange={(v) => patch({ inputSource: v })} items={SOURCE_LABEL}>
            <SelectTrigger size="sm" className="w-full" data-testid="subwf-trigger-source">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(SOURCE_LABEL) as Source[]).map((k) => (
                <SelectItem key={k} value={k} data-testid={`subwf-trigger-source-${k}`}>
                  {SOURCE_LABEL[k]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        {source === 'fields' && (
          <div className="space-y-2" data-testid="subwf-trigger-fields">
            {fields.length === 0 && <p className="text-[12px] text-muted-foreground">No inputs declared. Callers can send anything until you add fields.</p>}
            {fields.map((f, i) => (
              <div key={i} className="space-y-1.5 rounded-well bg-recess p-2" data-testid={`subwf-trigger-field-${i}`}>
                <div className="flex items-center gap-1.5">
                  <Input
                    value={f.name}
                    onChange={(e) => edit(i, { name: e.target.value })}
                    placeholder="field_name"
                    aria-label="Field name"
                    aria-invalid={bad.has(i) || undefined}
                    className="h-7 flex-1 font-mono"
                    data-testid={`subwf-trigger-field-name-${i}`}
                  />
                  <Select value={f.type} onValueChange={(v) => edit(i, { type: v as FieldType })} items={Object.fromEntries(FIELD_TYPES.map((t) => [t, t]))}>
                    <SelectTrigger size="sm" className="w-28" aria-label="Field type" data-testid={`subwf-trigger-field-type-${i}`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {FIELD_TYPES.map((t) => (
                        <SelectItem key={t} value={t}>{t}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <label className="flex items-center gap-1 text-[12px] text-muted-foreground">
                    <Checkbox
                      checked={f.required}
                      onCheckedChange={(v) => edit(i, { required: v === true })}
                      data-testid={`subwf-trigger-field-required-${i}`}
                    />
                    required
                  </label>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Remove field"
                    onClick={() => setFields(fields.filter((_, j) => j !== i))}
                    data-testid={`subwf-trigger-field-remove-${i}`}
                  >
                    <Trash2 />
                  </Button>
                </div>
                <Input
                  value={f.description}
                  onChange={(e) => edit(i, { description: e.target.value })}
                  placeholder="Description (shown to the callers)"
                  aria-label="Field description"
                  className="h-7"
                  data-testid={`subwf-trigger-field-description-${i}`}
                />
                {bad.has(i) && (
                  <p className="text-[12px] text-destructive" role="alert">
                    {f.name.trim() ? 'Another field already has this name.' : 'Give the field a name.'}
                  </p>
                )}
              </div>
            ))}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setFields([...fields, { name: '', type: 'string', required: false, description: '' }])}
              data-testid="subwf-trigger-add-field"
            >
              <Plus /> Add field
            </Button>
          </div>
        )}

        <div className="space-y-1" data-testid="subwf-trigger-used-by">
          <span className="text-[12.5px] font-medium text-foreground">
            Used by{usedBy.data ? ` (${usedBy.data.length})` : ''}
          </span>
          <UsedByList workflowId={workflowId} />
        </div>
      </div>
    </Tile>
  );
}
