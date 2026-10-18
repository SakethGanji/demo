/**
 * ExecuteWorkflow node details (DESIGN-STAGE8-REUSE 12.1): child picker, version pin, input mapping built from the
 * child's declared inputs, run mode, timeout and the output the parent will see. Registered as a node extension, so it
 * sits above the generic parameter list, which skips the parameters this panel owns.
 */
import { useMemo } from 'react';
import type { Node } from '@xyflow/react';
import { AlertTriangle, ArrowUpRight, CircleCheck, CircleDashed, Info } from 'lucide-react';
import { Tile, Callout } from '@/shared/components/ui/surface';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { Switch } from '@/shared/components/ui/switch';
import { Input } from '@/shared/components/ui/input';
import { Badge } from '@/shared/components/ui/badge';
import { Button } from '@/shared/components/ui/button';
import { Spinner } from '@/shared/components/ui/spinner';
import { Field } from '@/shared/components/ui/field';
import { Segmented } from '@/shared/components/ui/segmented';
import ExpressionEditor from '../ExpressionEditor';
import type { WorkflowNodeData } from '../../../types/workflow';
import { useWorkflowStore } from '../../../stores/workflowStore';
import { useEditorProjectStore } from '../../../stores/editorProjectStore';
import { useAllNodeExecData, useUpstreamNodeId, useUpstreamSampleData } from '../../../hooks/useWorkflowSelectors';
import { useSubworkflowList, useWorkflowIO, type SubworkflowListItem } from '../../../hooks/useSubworkflows';
import { SubworkflowPicker } from './SubworkflowPicker';
import { useNodeParamsPatch } from './useNodeParams';
import { contractStatus, MAX_TIMEOUT, missingRequired, pruneInputs, readCallParams, versionLabel } from './subworkflowParams';

interface Props {
  node: Node<WorkflowNodeData>;
}

const MODE_LABEL = { once: 'Run once with all items', each: 'Run once for each item' } as const;
const INPUT_MODE_OPTIONS = [
  { key: 'map', label: 'Map inputs', testId: 'subwf-input-mode-map' },
  { key: 'passThrough', label: 'Pass through', testId: 'subwf-input-mode-passthrough' },
  { key: 'custom', label: 'Custom', testId: 'subwf-input-mode-custom' },
] as const;

export default function SubworkflowCallPanel({ node }: Props) {
  const patch = useNodeParamsPatch(node.id);
  const params = useMemo(() => readCallParams(node.data.parameters as Record<string, unknown> | undefined), [node.data.parameters]);
  const projectId = useEditorProjectStore((s) => s.projectId);
  const slug = useEditorProjectStore((s) => s.slug);
  const currentWorkflowId = useWorkflowStore((s) => s.workflowId);

  const list = useSubworkflowList(projectId);
  const child = list.data?.find((w) => w.id === params.workflowId);
  const io = useWorkflowIO(params.workflowId || undefined, params.version);
  const inputFields = io.data?.input_fields ?? child?.inputs ?? [];
  const outputFields = io.data?.output_fields ?? child?.output_fields ?? [];
  const status = contractStatus(io.data);

  const upstreamId = useUpstreamNodeId(node.id);
  const sampleData = useUpstreamSampleData(upstreamId);
  const allNodeData = useAllNodeExecData();

  const patchInput = (name: string, value: string) => {
    const latest = useWorkflowStore.getState().nodes.find((n) => n.id === node.id);
    const current = readCallParams(latest?.data.parameters as Record<string, unknown> | undefined).inputs;
    patch({ inputs: { ...current, [name]: value } });
  };

  const missing = new Set(params.inputMode === 'map' ? missingRequired(inputFields, params.inputs, params.includeItem) : []);

  const pick = (w: SubworkflowListItem) => {
    // a new child: its fields differ, so the old mapping goes; a never-published child can only be called as draft
    const sameChild = w.id === params.workflowId;
    patch({
      workflowId: w.id,
      version: params.legacy ? (w.published_version != null ? 'latest' : 'draft') : w.published_version == null && params.version === 'latest' ? 'draft' : params.version,
      ...(params.legacy ? { inputMode: 'map', outputMode: 'items' } : {}),
      ...(sameChild ? {} : { inputs: pruneInputs(params.inputs, w.inputs) }),
    });
  };

  const maxVersion = child?.published_version ?? (typeof params.version === 'number' ? params.version : 0);
  const versionItems: Record<string, string> = {
    latest: child && child.published_version == null ? 'Latest published (none yet)' : 'Latest published',
    draft: 'Draft',
    ...Object.fromEntries(Array.from({ length: maxVersion }, (_, i) => [String(maxVersion - i), `v${maxVersion - i}`])),
  };

  return (
    <Tile className="overflow-hidden" data-testid="subwf-panel">
      <div className="space-y-3 px-3 py-3">
        <Field label="Sub-workflow">
          <SubworkflowPicker
            value={params.workflowId}
            items={list.data}
            loading={list.isLoading}
            currentWorkflowId={currentWorkflowId}
            onPick={pick}
          />
        </Field>

        {params.workflowId && (
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-[12px]" data-testid="subwf-contract-status" data-status={status}>
              {status === 'typed' ? <CircleCheck size={13} className="text-success" aria-hidden /> : <CircleDashed size={13} className="text-muted-foreground" aria-hidden />}
              <span className="text-muted-foreground">
                {io.isLoading ? 'Reading the contract...' : status === 'typed' ? 'Typed contract' : 'Untyped: accepts and returns anything'}
              </span>
            </span>
            <a
              href={`/p/${slug}/workflows/${params.workflowId}`}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="subwf-open-child"
              className="flex items-center gap-1 text-[12px] text-primary underline-offset-2 hover:underline"
            >
              Open sub-workflow <ArrowUpRight size={12} aria-hidden />
            </a>
          </div>
        )}

        {params.legacy && params.workflowId && (
          <Callout tone="info" icon={<Info />} data-testid="subwf-legacy">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px]">This node predates versions: it calls the draft and returns one merged item.</span>
              <Button
                size="xs"
                variant="outline"
                data-testid="subwf-upgrade"
                disabled={!child || child.published_version == null}
                onClick={() => patch({ version: 'latest', inputMode: 'map', outputMode: 'items' })}
              >
                Upgrade
              </Button>
            </div>
          </Callout>
        )}

        <Field label="Version" help="Which version of the sub-workflow this node calls.">
          <Select
            value={String(params.version)}
            onValueChange={(v) => {
              const next = v === 'draft' || v === 'latest' ? v : parseInt(String(v), 10);
              patch({ version: next, ...(params.legacy ? { inputMode: params.inputMode, outputMode: 'merged' } : {}) });
            }}
            items={versionItems}
          >
            <SelectTrigger size="sm" className="w-full" data-testid="subwf-version">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(versionItems).map(([value, label]) => (
                <SelectItem key={value} value={value} data-testid={`subwf-version-${value}`}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        {params.version === 'draft' && params.workflowId && (
          <Callout tone="warn" icon={<AlertTriangle />} data-testid="subwf-draft-warning">
            <span className="text-[12px]">
              This calls the draft of the sub-workflow, so unreviewed edits run here. Publishing this workflow is refused until you pin the latest published version or a number.
            </span>
          </Callout>
        )}

        <Field label="Input">
          <Segmented
            size="xs"
            aria-label="Input mode"
            value={params.inputMode}
            onChange={(k) => patch({ inputMode: k })}
            options={INPUT_MODE_OPTIONS.map((o) => ({ ...o }))}
            data-testid="subwf-input-mode"
          />
        </Field>

        {params.inputMode === 'map' && (
          <div className="space-y-2" data-testid="subwf-inputs">
            {io.isLoading && params.workflowId && <Spinner className="size-4 text-muted-foreground" />}
            {!params.workflowId && <p className="text-[12px] text-muted-foreground">Pick a sub-workflow to map its inputs.</p>}
            {params.workflowId && !io.isLoading && inputFields.length === 0 && (
              <p className="text-[12px] text-muted-foreground" data-testid="subwf-inputs-empty">
                This sub-workflow declares no inputs. Switch to Pass through to hand it the incoming items.
              </p>
            )}
            {inputFields.map((f) => (
              <div key={f.name} className="space-y-1" data-testid={`subwf-input-${f.name}`}>
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-[12px] font-medium">{f.name}</span>
                  <Badge variant="neutral">{f.type}</Badge>
                  {f.required && <Badge variant="primary">required</Badge>}
                  {missing.has(f.name) && (
                    <span className="flex items-center gap-1 text-[11.5px] text-destructive" data-testid={`subwf-input-missing-${f.name}`}>
                      <AlertTriangle size={11} aria-hidden /> needs a value
                    </span>
                  )}
                </div>
                {f.description && <p className="text-[11.5px] text-muted-foreground">{f.description}</p>}
                <ExpressionEditor
                  value={params.inputs[f.name] ?? ''}
                  onChange={(v) => patchInput(f.name, v)}
                  placeholder={`{{ $json.${f.name} }}`}
                  sampleData={sampleData}
                  allNodeData={allNodeData}
                />
              </div>
            ))}
            <label className="flex items-start gap-2.5">
              <Switch
                checked={params.includeItem}
                onCheckedChange={(v) => patch({ includeItem: v })}
                className="mt-0.5"
                data-testid="subwf-include-item"
              />
              <div>
                <span className="text-[13px] text-foreground">Include incoming item</span>
                <p className="text-[12px] text-muted-foreground">Start from the incoming item and overlay the mapped fields.</p>
              </div>
            </label>
          </div>
        )}

        <Field label="Run mode">
          <Select value={params.mode} onValueChange={(v) => patch({ mode: v })} items={MODE_LABEL}>
            <SelectTrigger size="sm" className="w-full" data-testid="subwf-mode">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="once" data-testid="subwf-mode-once">{MODE_LABEL.once}</SelectItem>
              <SelectItem value="each" data-testid="subwf-mode-each">{MODE_LABEL.each}</SelectItem>
            </SelectContent>
          </Select>
        </Field>

        <Field label="Timeout (seconds)" help={`Up to ${MAX_TIMEOUT}; also capped by this workflow's own limit.`}>
          <Input
            type="number"
            min={1}
            max={MAX_TIMEOUT}
            value={params.timeoutSeconds}
            onChange={(e) => {
              const n = parseInt(e.target.value, 10);
              patch({ timeoutSeconds: Number.isFinite(n) ? Math.min(MAX_TIMEOUT, Math.max(1, n)) : 300 });
            }}
            className="w-28"
            data-testid="subwf-timeout"
          />
        </Field>

        {params.workflowId && (
          <div className="space-y-1" data-testid="subwf-output-fields">
            <span className="text-[12.5px] font-medium text-foreground">Output</span>
            {outputFields.length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {outputFields.map((f) => (
                  <Badge key={f} variant="outline" className="font-mono">{f}</Badge>
                ))}
              </div>
            ) : (
              <p className="text-[12px] text-muted-foreground">
                {io.isLoading ? 'Reading...' : `The items of ${child ? `"${child.name}"` : 'the sub-workflow'} (${versionLabel(params.version)}); fields are not known.`}
              </p>
            )}
          </div>
        )}
      </div>
    </Tile>
  );
}
