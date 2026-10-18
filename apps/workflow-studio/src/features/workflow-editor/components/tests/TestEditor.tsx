import { useMemo, useState } from 'react';
import { Lock, Plus, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useTestStore } from '../../stores/testStore';
import {
  ALL_CATEGORIES, ASSERTION_OPS, CATEGORY_LABELS,
  type Assertion, type AssertionOp, type Category, type Mock, type TestCase, type TestTarget,
} from '../../lib/tests';
import { approveTest, unlockTest } from '../../lib/testActions';
import { APPROVAL_PORTS, APPROVAL_PORT_LABELS, approvalItemsPatch, isApprovalType, type ApprovalPort } from '../../lib/approvalMocks';
import type { WorkflowNodeData } from '../../types/workflow';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/shared/components/ui/sheet';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select';
import { Button } from '@/shared/components/ui/button';
import { Input } from '@/shared/components/ui/input';
import { Textarea } from '@/shared/components/ui/textarea';
import { Field } from '@/shared/components/ui/field';
import { Callout } from '@/shared/components/ui/surface';

/** Node types that reach outside the workflow; each needs a mock in a test (engine `effects.py`). */
const OUTSIDE_TYPES = new Set([
  'HttpRequest', 'SendEmail', 'Poll', 'Postgres', 'MongoDB', 'Neo4j', 'DataTable', 'LlmChat', 'AiAgent', 'ExecuteWorkflow',
  'Approval', // effect `human`: the mock is the decision, as a port
]);

type MockKind = 'default' | 'items' | 'http' | 'error' | 'timeout' | 'custom';
const MOCK_KIND_LABELS: Record<MockKind, string> = {
  default: 'Use the default',
  items: 'Return items',
  http: 'HTTP reply',
  error: 'Raise an error',
  timeout: 'Time out',
  custom: 'Custom (JSON)',
};
const NO_VALUE_OPS = new Set<AssertionOp>(['exists', 'not_exists']);

interface MockUi {
  kind: MockKind;
  /** items JSON, http body JSON, error message, or custom mock JSON */
  text: string;
  /** http status */
  status: string;
  /** output port of an items mock (Approval: approved / rejected / timed_out) */
  port: string;
}

interface ExpectRow {
  a: Assertion;
  text: string;
}

function parseValue(text: string): unknown {
  const t = text.trim();
  if (t === '') return undefined;
  try {
    return JSON.parse(t);
  } catch {
    return text;
  }
}

function valueToText(v: unknown): string {
  if (v === undefined) return '';
  if (typeof v === 'string') {
    try {
      JSON.parse(v);
      return JSON.stringify(v); // would be read back as another type; keep the quotes
    } catch {
      return v;
    }
  }
  return JSON.stringify(v);
}

const pretty = (v: unknown) => JSON.stringify(v, null, 2);

function mockToUi(m: Mock | undefined): MockUi {
  if (!m) return { kind: 'default', text: '', status: '200', port: '' };
  if ('items' in m && !('rules' in m)) return { kind: 'items', text: pretty(m.items), status: '200', port: m.port ?? '' };
  if ('http' in m) return { kind: 'http', text: m.http.body === undefined ? '' : pretty(m.http.body), status: String(m.http.status), port: '' };
  if ('error' in m) return { kind: 'error', text: m.error.message, status: '200', port: '' };
  if ('timeout' in m) return { kind: 'timeout', text: '', status: '200', port: '' };
  return { kind: 'custom', text: pretty(m), status: '200', port: '' };
}

/** Returns the mock, `null` for "use the default", or an error string. */
function uiToMock(ui: MockUi, original: Mock | undefined): Mock | null | string {
  switch (ui.kind) {
    case 'default':
      return null;
    case 'timeout':
      return { timeout: true };
    case 'error':
      return { error: { message: ui.text || 'Test error' } };
    case 'items': {
      try {
        const v = JSON.parse(ui.text || '[]');
        if (!Array.isArray(v)) return 'Items must be a JSON array';
        const port = ui.port || (original && 'items' in original ? original.port : undefined);
        return { items: v, ...(port ? { port } : {}) };
      } catch {
        return 'Items are not valid JSON';
      }
    }
    case 'http': {
      const status = Number(ui.status);
      if (!Number.isInteger(status) || status < 100 || status > 599) return 'HTTP status must be 100 to 599';
      let body: unknown;
      if (ui.text.trim() !== '') {
        try {
          body = JSON.parse(ui.text);
        } catch {
          return 'HTTP body is not valid JSON';
        }
      }
      const headers = original && 'http' in original ? original.http.headers : undefined;
      return { http: { status, ...(body !== undefined ? { body } : {}), ...(headers ? { headers } : {}) } };
    }
    case 'custom':
      try {
        return JSON.parse(ui.text) as Mock;
      } catch {
        return 'Custom mock is not valid JSON';
      }
  }
}

function approverName(): string {
  try {
    return localStorage.getItem('contract-approved-by') ?? '';
  } catch {
    return '';
  }
}

export default function TestEditor() {
  const editing = useTestStore((s) => s.editing);
  const close = useTestStore((s) => s.closeEditor);
  return (
    <Sheet open={!!editing} onOpenChange={(o) => !o && close()}>
      <SheetContent data-testid="test-editor" className="w-full gap-0 sm:max-w-[34rem]">
        {editing && <EditorBody key={editing.test.id} test={editing.test} isNew={editing.isNew} />}
      </SheetContent>
    </Sheet>
  );
}

function EditorBody({ test, isNew }: { test: TestCase; isNew: boolean }) {
  const nodes = useWorkflowStore((s) => s.nodes);
  const defaults = useWorkflowStore((s) => s.testDefaults);
  const close = useTestStore((s) => s.closeEditor);
  const locked = test.locked;

  const nodeInfo = useMemo(
    () =>
      nodes
        .filter((n) => n.type === 'workflowNode')
        .map((n) => ({ name: (n.data as WorkflowNodeData).name, type: (n.data as WorkflowNodeData).type })),
    [nodes],
  );
  const nodeNames = nodeInfo.map((n) => n.name);
  const typeOf = (name: string) => nodeInfo.find((n) => n.name === name)?.type;
  const mockNodes = useMemo(() => {
    const set = new Set<string>(nodeInfo.filter((n) => OUTSIDE_TYPES.has(n.type)).map((n) => n.name));
    Object.keys(test.mocks).forEach((k) => set.add(k));
    return [...set];
  }, [nodeInfo, test.mocks]);

  const [name, setName] = useState(test.name);
  const [category, setCategory] = useState<Category>(test.category);
  const [target, setTarget] = useState<TestTarget>(test.target);
  const [inputText, setInputText] = useState(() => pretty(test.input?.items ?? []));
  const [mocks, setMocks] = useState<Record<string, MockUi>>(() =>
    Object.fromEntries(mockNodes.map((n) => [n, mockToUi(test.mocks[n])])),
  );
  const [rows, setRows] = useState<ExpectRow[]>(() => (test.expect ?? []).map((a) => ({ a, text: valueToText(a.value) })));
  const [approvedBy, setApprovedBy] = useState(test.approved_by ?? approverName());
  const [busy, setBusy] = useState(false);

  let inputError: string | null = null;
  try {
    if (!Array.isArray(JSON.parse(inputText || '[]'))) inputError = 'Input must be a JSON array of items';
  } catch {
    inputError = 'Input is not valid JSON';
  }

  const targetValue = target.kind === 'node' ? `node:${target.node}` : target.kind;
  const targetItems: Record<string, string> = {
    workflow: 'Whole workflow',
    range: 'A range of nodes',
    ...Object.fromEntries(nodeNames.map((n) => [`node:${n}`, `Node: ${n}`])),
  };
  const categoryItems = Object.fromEntries(ALL_CATEGORIES.map((c) => [c, CATEGORY_LABELS[c]]));
  const nodeItems = Object.fromEntries(nodeNames.map((n) => [n, n]));
  const opItems = Object.fromEntries(ASSERTION_OPS.map((o) => [o, o]));

  const setTargetValue = (v: string | null) => {
    if (!v) return;
    if (v === 'workflow') setTarget({ kind: 'workflow' });
    else if (v === 'range') setTarget({ kind: 'range', from: nodeNames[0] ?? '', to: nodeNames[nodeNames.length - 1] ?? '' });
    else setTarget({ kind: 'node', node: v.slice(5) });
  };

  const patchRow = (i: number, patch: Partial<Assertion>, text?: string) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { a: { ...r.a, ...patch }, text: text ?? r.text } : r)));

  /** Builds the test from the form, or toasts the first problem and returns null. */
  const build = (): TestCase | null => {
    if (!name.trim()) {
      toast.error('Give the test a name');
      return null;
    }
    if (inputError) {
      toast.error(inputError);
      return null;
    }
    const outMocks: Record<string, Mock> = {};
    for (const n of Object.keys(mocks)) {
      const r = uiToMock(mocks[n], test.mocks[n]);
      if (typeof r === 'string') {
        toast.error(`${n}: ${r}`);
        return null;
      }
      if (r) outMocks[n] = r;
    }
    const expect: Assertion[] = rows.map(({ a, text }) => {
      const out: Assertion = { ...a };
      if (a.op === 'status') out.subject = 'node';
      else if (a.subject === 'node') delete out.subject;
      if (a.op === 'exists' || a.op === 'not_exists') delete out.value;
      else {
        const v = parseValue(text);
        if (v === undefined) delete out.value;
        else out.value = v;
      }
      if (!out.port) delete out.port;
      if (!out.path) delete out.path;
      return out;
    });
    const covers = test.covers && target.kind === 'node' ? [target.node] : test.covers;
    return {
      ...test,
      name: name.trim(),
      category,
      target,
      input: { items: JSON.parse(inputText || '[]') },
      mocks: outMocks,
      expect,
      covers,
    };
  };

  const save = () => {
    const t = build();
    if (!t) return false;
    useWorkflowStore.getState().upsertTest(t);
    useTestStore.getState().selectTest(t.id);
    return t;
  };

  const onSave = () => {
    if (save()) close();
  };

  const onApprove = async () => {
    if (!approvedBy.trim()) return;
    const t = save();
    if (!t) return;
    setBusy(true);
    const ok = await approveTest(t.id, approvedBy.trim());
    setBusy(false);
    if (ok) {
      try {
        localStorage.setItem('contract-approved-by', approvedBy.trim());
      } catch {
        /* ignore */
      }
      const fresh = useWorkflowStore.getState().tests.find((x) => x.id === t.id);
      if (fresh) useTestStore.getState().openEditor(fresh, false);
    }
  };

  const onUnlock = async () => {
    setBusy(true);
    const ok = await unlockTest(test.id, approverName() || test.approved_by || 'studio');
    setBusy(false);
    if (ok) {
      const fresh = useWorkflowStore.getState().tests.find((x) => x.id === test.id);
      if (fresh) useTestStore.getState().openEditor(fresh, false);
    }
  };

  const onDelete = () => {
    useWorkflowStore.getState().removeTest(test.id);
    close();
  };

  return (
    <>
      <SheetHeader className="pr-12">
        <SheetTitle>{isNew ? 'New test' : 'Test'}</SheetTitle>
        <SheetDescription className="text-[12px]">
          Feed items in, fake the outside systems, say what must come out.
        </SheetDescription>
      </SheetHeader>

      <fieldset disabled={locked} className="flex min-h-0 min-w-0 flex-1 flex-col gap-4 overflow-x-hidden overflow-y-auto px-4 pb-4 [&>*]:min-w-0">
        {locked && (
          <Callout tone="info" icon={<Lock />} data-testid="test-locked">
            Approved by {test.approved_by ?? 'someone'}
            {test.approved_at ? ` on ${new Date(test.approved_at).toLocaleDateString()}` : ''}. Locked: unlock it to change it.
          </Callout>
        )}

        <Field label="Name" htmlFor="test-name">
          <Input id="test-name" className="w-full min-w-0 truncate" value={name} onChange={(e) => setName(e.target.value)} disabled={locked} data-testid="test-name" />
        </Field>

        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3 [&>*]:min-w-0">
          <Field label="Category">
            <Select value={category} onValueChange={(v) => v && setCategory(v as Category)} items={categoryItems} disabled={locked}>
              <SelectTrigger className="w-full" data-testid="test-category">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ALL_CATEGORIES.map((c) => (
                  <SelectItem key={c} value={c}>{CATEGORY_LABELS[c]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Runs">
            <Select value={targetValue} onValueChange={setTargetValue} items={targetItems} disabled={locked}>
              <SelectTrigger className="w-full" data-testid="test-target">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="workflow">Whole workflow</SelectItem>
                <SelectItem value="range">A range of nodes</SelectItem>
                {nodeNames.map((n) => (
                  <SelectItem key={n} value={`node:${n}`}>{`Node: ${n}`}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>

        {target.kind === 'range' && (
          <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3 [&>*]:min-w-0">
            {(['from', 'to'] as const).map((k) => (
              <Field key={k} label={k === 'from' ? 'From' : 'To'}>
                <Select
                  value={target[k]}
                  onValueChange={(v) => v && setTarget({ ...target, [k]: v })}
                  items={nodeItems}
                  disabled={locked}
                >
                  <SelectTrigger className="w-full" data-testid={`test-range-${k}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {nodeNames.map((n) => (
                      <SelectItem key={n} value={n}>{n}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            ))}
          </div>
        )}

        <Field
          label="Input items (JSON)"
          htmlFor="test-input"
          error={inputError}
          help={target.kind === 'workflow' ? 'What the trigger emits.' : 'What flows into the node.'}
        >
          <Textarea
            id="test-input"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            rows={5}
            spellCheck={false}
            disabled={locked}
            aria-invalid={inputError ? true : undefined}
            className="field-sizing-fixed w-full min-w-0 font-mono text-[12px]"
            data-testid="test-input"
          />
        </Field>

        {mockNodes.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="text-[12.5px] font-medium text-foreground">Outside systems</div>
            {mockNodes.map((n) => {
              const ui = mocks[n] ?? mockToUi(undefined);
              const def = defaults.mocks[n];
              const set = (p: Partial<MockUi>) => setMocks((m) => ({ ...m, [n]: { ...ui, ...p } }));
              return (
                <div key={n} className="well flex min-w-0 flex-col gap-2 p-2.5 [&>*]:min-w-0" data-testid={`test-mock-${n}`} data-kind={ui.kind}>
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-foreground">{n}</span>
                    <Select
                      value={ui.kind}
                      onValueChange={(v) => {
                        if (!v) return;
                        const kind = v as MockKind;
                        set(kind === 'items' && isApprovalType(typeOf(n)) ? { kind, ...approvalItemsPatch(ui.text, ui.port) } : { kind });
                      }}
                      items={MOCK_KIND_LABELS}
                      disabled={locked}
                    >
                      <SelectTrigger size="sm" className="w-40" data-testid={`test-mock-${n}-source`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {(Object.keys(MOCK_KIND_LABELS) as MockKind[]).map((k) => (
                          <SelectItem key={k} value={k}>{MOCK_KIND_LABELS[k]}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  {ui.kind === 'default' && (
                    <div className="text-[12px] text-muted-foreground">
                      {def ? 'The workflow-wide mock for this node is used.' : 'No default mock yet. The test fails as "not mocked" until you add one.'}
                    </div>
                  )}
                  {ui.kind === 'http' && (
                    <div className="flex flex-col gap-2">
                      <Field label="Status code">
                        <Input
                          value={ui.status}
                          onChange={(e) => set({ status: e.target.value })}
                          inputMode="numeric"
                          placeholder="e.g. 200"
                          disabled={locked}
                          data-testid={`test-mock-${n}-status`}
                        />
                      </Field>
                      <Field label="Body">
                        <Textarea
                          value={ui.text}
                          onChange={(e) => set({ text: e.target.value })}
                          rows={3}
                          spellCheck={false}
                          placeholder="Reply body (JSON)"
                          disabled={locked}
                          className="field-sizing-fixed w-full min-w-0 font-mono text-[12px]"
                          data-testid={`test-mock-${n}-value`}
                        />
                      </Field>
                    </div>
                  )}
                  {ui.kind === 'items' && isApprovalType(typeOf(n)) && (
                    <Field label="Decision (output port)">
                      <Select
                        value={ui.port || 'approved'}
                        onValueChange={(v) => v && set({ port: v })}
                        items={APPROVAL_PORT_LABELS}
                        disabled={locked}
                      >
                        <SelectTrigger size="sm" className="w-full" data-testid={`test-mock-${n}-port`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {APPROVAL_PORTS.map((p: ApprovalPort) => (
                            <SelectItem key={p} value={p}>{APPROVAL_PORT_LABELS[p]}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </Field>
                  )}
                  {(ui.kind === 'items' || ui.kind === 'custom') && (
                    <Textarea
                      value={ui.text}
                      onChange={(e) => set({ text: e.target.value })}
                      rows={4}
                      spellCheck={false}
                      placeholder={ui.kind === 'items' ? '[ { "id": 1 } ]' : '{ "rules": [] }'}
                      disabled={locked}
                      className="field-sizing-fixed w-full min-w-0 font-mono text-[12px]"
                      data-testid={`test-mock-${n}-value`}
                    />
                  )}
                  {ui.kind === 'error' && (
                    <Input
                      value={ui.text}
                      onChange={(e) => set({ text: e.target.value })}
                      placeholder="Error message"
                      disabled={locked}
                      data-testid={`test-mock-${n}-value`}
                    />
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="flex flex-col gap-2">
          <div className="text-[12.5px] font-medium text-foreground">Must be true afterwards</div>
          {rows.length === 0 && <div className="text-[12px] text-muted-foreground">No checks yet. Add one below.</div>}
          {rows.map((r, i) => (
            <div key={i} className="well flex min-w-0 flex-col gap-2 p-2.5 [&>*]:min-w-0" data-testid="test-expect-row">
              <div className="flex items-center gap-2">
                <Select value={r.a.node} onValueChange={(v) => v && patchRow(i, { node: v })} items={nodeItems} disabled={locked}>
                  <SelectTrigger size="sm" className="min-w-0 flex-1" data-testid="test-expect-node">
                    <SelectValue placeholder="Node" />
                  </SelectTrigger>
                  <SelectContent>
                    {nodeNames.map((n) => (
                      <SelectItem key={n} value={n}>{n}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input
                  value={r.a.port ?? ''}
                  onChange={(e) => patchRow(i, { port: e.target.value })}
                  placeholder="Port"
                  disabled={locked}
                  className="h-7 w-20"
                  data-testid="test-expect-port"
                />
                <Select value={r.a.op} onValueChange={(v) => v && patchRow(i, { op: v as AssertionOp })} items={opItems} disabled={locked}>
                  <SelectTrigger size="sm" className="w-28" data-testid="test-expect-op">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ASSERTION_OPS.map((o) => (
                      <SelectItem key={o} value={o}>{o}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center gap-2">
                <Input
                  value={r.a.path ?? ''}
                  onChange={(e) => patchRow(i, { path: e.target.value })}
                  placeholder="Path, e.g. body.total"
                  disabled={locked}
                  className="h-7 flex-1"
                  data-testid="test-expect-path"
                />
                <Input
                  value={r.text}
                  onChange={(e) => patchRow(i, {}, e.target.value)}
                  placeholder="Value"
                  disabled={locked || NO_VALUE_OPS.has(r.a.op)}
                  className="h-7 flex-1"
                  data-testid="test-expect-value"
                />
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label="Remove check"
                  disabled={locked}
                  onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
                  data-testid="test-expect-remove"
                >
                  <Trash2 />
                </Button>
              </div>
            </div>
          ))}
          <div>
            <Button
              variant="outline"
              size="sm"
              disabled={locked}
              onClick={() =>
                setRows((rs) => [
                  ...rs,
                  { a: { node: (target.kind === 'node' ? target.node : nodeNames[nodeNames.length - 1]) ?? '', op: 'eq' }, text: '' },
                ])
              }
              data-testid="test-expect-add"
            >
              <Plus /> Add check
            </Button>
          </div>
        </div>
      </fieldset>

      <div className="flex shrink-0 flex-wrap items-center gap-2 px-4 py-3 shadow-[inset_0_1px_0_var(--hairline)]">
        <Input
          value={approvedBy}
          onChange={(e) => setApprovedBy(e.target.value)}
          placeholder="Approved by"
          disabled={locked}
          className="h-8 w-36"
          data-testid="test-approved-by"
        />
        {locked ? (
          <Button variant="outline" onClick={onUnlock} disabled={busy} data-testid="test-unlock">
            Unlock
          </Button>
        ) : (
          <Button variant="outline" onClick={onApprove} disabled={busy || !approvedBy.trim()} data-testid="test-approve">
            Approve and lock
          </Button>
        )}
        <span className="flex-1" />
        {!isNew && !locked && (
          <Button variant="ghost" size="icon" aria-label="Delete test" onClick={onDelete} data-testid="test-delete">
            <Trash2 />
          </Button>
        )}
        <Button onClick={onSave} disabled={locked} data-testid="test-save">
          Save
        </Button>
      </div>
    </>
  );
}
