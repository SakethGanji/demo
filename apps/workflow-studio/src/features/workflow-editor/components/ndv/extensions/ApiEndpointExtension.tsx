import { useMemo, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { Plug } from 'lucide-react';
import type { Node } from '@xyflow/react';
import type { WorkflowNodeData } from '../../../types/workflow';
import { useWorkflowStore } from '../../../stores/workflowStore';
import { useEditorProjectStore } from '../../../stores/editorProjectStore';
import { successMock, type ApiOperation } from '../../../lib/tests';
import { apiSpecsApi } from '@/shared/lib/api';
import { qk } from '@/shared/api/queryKeys';
import { useApiSpecs } from '@/features/api-catalog/useApiSpecs';
import { Segmented } from '@/shared/components/ui/segmented';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { Input } from '@/shared/components/ui/input';
import { Field } from '@/shared/components/ui/field';
import { MethodBadge } from '@/shared/components/api/MethodBadge';

type Mode = 'custom' | 'api';
interface ApiRef {
  spec_id: string;
  operation_id: string;
}

const SEP = '\u0000';

/**
 * HttpRequest: "API endpoint" mode (CONTRACTS-STAGE3A §6). Picking an endpoint writes `api_ref` and `path_params`,
 * and seeds `test_defaults.mocks[node]` with the operation's success reply unless one is already there.
 */
export default function ApiEndpointExtension({ node }: { node: Node<WorkflowNodeData> }) {
  const pid = useEditorProjectStore((s) => s.projectId);
  const slug = useEditorProjectStore((s) => s.slug);
  const params = (node.data.parameters ?? {}) as Record<string, unknown>;
  const apiRef = (params.api_ref as ApiRef | null | undefined) ?? null;
  const pathParams = (params.path_params as Record<string, string> | undefined) ?? {};
  const [mode, setMode] = useState<Mode>(apiRef ? 'api' : 'custom');

  const specs = useApiSpecs(pid);
  const details = useQueries({
    queries: (specs.data ?? []).map((s) => ({
      queryKey: qk.apiSpec(pid, s.id),
      queryFn: () => apiSpecsApi.get(s.id),
      staleTime: 30_000,
    })),
  });

  const groups = useMemo(
    () =>
      details
        .map((d) => d.data)
        .filter((d): d is NonNullable<typeof d> => !!d)
        .map((d) => ({ id: d.id, name: d.name, operations: d.operations })),
    [details],
  );

  const current: ApiOperation | undefined = apiRef
    ? groups.find((g) => g.id === apiRef.spec_id)?.operations.find((o) => o.operation_id === apiRef.operation_id)
    : undefined;

  const items = useMemo(() => {
    const m: Record<string, string> = {};
    for (const g of groups) for (const o of g.operations) m[`${g.id}${SEP}${o.operation_id}`] = `${g.name}: ${o.method} ${o.path}`;
    return m;
  }, [groups]);

  const write = (patch: Record<string, unknown>) => {
    const store = useWorkflowStore.getState();
    const cur = store.nodes.find((n) => n.id === node.id);
    if (!cur) return;
    store.updateNodeData(node.id, { parameters: { ...(cur.data as WorkflowNodeData).parameters, ...patch } });
  };

  const setModeAndClear = (m: Mode) => {
    setMode(m);
    if (m === 'custom' && apiRef) write({ api_ref: null, path_params: {} });
  };

  const pick = (value: string | null) => {
    if (!value) return;
    const [spec_id, operation_id] = value.split(SEP);
    const op = groups.find((g) => g.id === spec_id)?.operations.find((o) => o.operation_id === operation_id);
    const next: Record<string, string> = {};
    for (const n of op?.path_params ?? []) next[n] = pathParams[n] ?? '';
    write({ api_ref: { spec_id, operation_id }, path_params: next });
    // seed the node's default test mock with the 2xx reply, unless the user already has one
    const nodeName = node.data.name;
    if (!useWorkflowStore.getState().testDefaults.mocks[nodeName]) {
      apiSpecsApi
        .mocks(spec_id, operation_id)
        .then((opts) => {
          const mock = successMock(opts);
          if (mock && !useWorkflowStore.getState().testDefaults.mocks[nodeName]) {
            useWorkflowStore.getState().setTestDefaultMock(nodeName, mock);
          }
        })
        .catch((e) => console.warn('[api endpoint] could not load the success mock', e));
    }
  };

  return (
    <div className="tile" data-testid="http-api-picker">
      <div className="flex flex-col gap-2.5 px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Plug size={14} className="text-muted-foreground" />
            <span className="text-sm font-medium text-foreground">Request</span>
          </div>
          <Segmented
            size="xs"
            value={mode}
            onChange={setModeAndClear}
            aria-label="Request mode"
            options={[
              { key: 'custom', label: 'Custom URL', testId: 'http-mode-custom' },
              { key: 'api', label: 'API endpoint', testId: 'http-mode-api' },
            ]}
          />
        </div>

        {mode === 'api' && (
          <>
            {specs.isSuccess && (specs.data ?? []).length === 0 ? (
              <p className="text-xs text-muted-foreground" data-testid="http-api-empty">
                No APIs yet.{' '}
                <Link to="/p/$project/apis" params={{ project: slug }} className="text-primary hover:underline">
                  Add one in the API catalog
                </Link>
                .
              </p>
            ) : (
              <Field label="Endpoint" help="The method and URL come from the endpoint.">
                <Select
                  value={apiRef ? `${apiRef.spec_id}${SEP}${apiRef.operation_id}` : null}
                  onValueChange={pick}
                  items={items}
                >
                  <SelectTrigger className="w-full" data-testid="http-api-endpoint">
                    <SelectValue placeholder="Pick an endpoint" />
                  </SelectTrigger>
                  <SelectContent>
                    {groups.map((g) => (
                      <SelectGroup key={g.id}>
                        <SelectLabel>{g.name}</SelectLabel>
                        {g.operations.map((o) => (
                          <SelectItem key={o.operation_id} value={`${g.id}${SEP}${o.operation_id}`} data-testid="http-api-option" data-operation-id={o.operation_id}>
                            <span className="inline-flex items-center gap-2">
                              <MethodBadge method={o.method} size="sm" />
                              <span className="font-mono text-[12px]">{o.path}</span>
                            </span>
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}

            {current && current.path_params.length > 0 && (
              <div className="flex flex-col gap-2" data-testid="http-path-params">
                {current.path_params.map((name) => (
                  <Field key={name} label={name} htmlFor={`path-param-${name}`}>
                    <Input
                      id={`path-param-${name}`}
                      value={pathParams[name] ?? ''}
                      onChange={(e) => write({ path_params: { ...pathParams, [name]: e.target.value } })}
                      placeholder={`{{ $json.${name} }}`}
                      className="font-mono text-[12px]"
                      data-testid={`http-path-param-${name}`}
                    />
                  </Field>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
