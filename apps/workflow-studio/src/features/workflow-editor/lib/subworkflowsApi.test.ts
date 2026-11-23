import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

// `shared/api/http` reads `window.location` when it loads (the node test environment has none)
let m: typeof import('./subworkflowsApi');
beforeAll(async () => {
  vi.stubGlobal('window', { location: { hostname: 'localhost' } });
  m = await import('./subworkflowsApi');
});
const REFUSAL_MESSAGES = new Proxy({} as Record<string, string>, { get: (_t, k: string) => m.REFUSAL_MESSAGES[k] });
const refusalMessage = (...a: Parameters<typeof m.refusalMessage>) => m.refusalMessage(...a);
const parsePublishImpact = (...a: Parameters<typeof m.parsePublishImpact>) => m.parsePublishImpact(...a);
const hasBreakingImpact = (...a: Parameters<typeof m.hasBreakingImpact>) => m.hasBreakingImpact(...a);
const versionText = (...a: Parameters<typeof m.versionText>) => m.versionText(...a);
const isFailedStatus = (...a: Parameters<typeof m.isFailedStatus>) => m.isFailedStatus(...a);
const subworkflowsApi = new Proxy({} as typeof m.subworkflowsApi, { get: (_t, k: string) => (m.subworkflowsApi as never)[k] });

describe('refusal messages', () => {
  it('has a readable sentence for CS01 to CS10', () => {
    for (let i = 1; i <= 10; i++) {
      const code = `CS${String(i).padStart(2, '0')}`;
      expect(REFUSAL_MESSAGES[code], code).toMatch(/\w{4}/);
    }
  });
  it('falls back to the engine message for an unknown code', () => {
    expect(refusalMessage('CS99', 'engine says no')).toBe('engine says no');
    expect(refusalMessage(null, 'x')).toBe('x');
    expect(refusalMessage('CS02', 'engine')).toBe(REFUSAL_MESSAGES.CS02);
  });
});

describe('publish impact', () => {
  it('reads the impact list and tolerates its absence', () => {
    expect(parsePublishImpact({ id: 'a' })).toEqual([]);
    expect(parsePublishImpact(null)).toEqual([]);
    const rows = [{ parent_workflow_id: 'p', parent_name: 'P', node: 'Call', version_spec: 'latest', in_release: false, breaking: [] }];
    expect(parsePublishImpact({ impact: rows })).toBe(rows);
  });
  it('is breaking only when a parent has findings', () => {
    const f = { code: 'WF024', severity: 'error' as const, message: 'm', node: 'Call', param: null, path: null };
    expect(hasBreakingImpact([])).toBe(false);
    expect(hasBreakingImpact([{ parent_workflow_id: 'p', parent_name: 'P', node: 'Call', version_spec: 'latest', in_release: false, breaking: [] }])).toBe(false);
    expect(hasBreakingImpact([{ parent_workflow_id: 'p', parent_name: 'P', node: 'Call', version_spec: 'latest', in_release: true, breaking: [f] }])).toBe(true);
  });
});

describe('run helpers', () => {
  it('formats a child version and recognises failures', () => {
    expect(versionText(null)).toBe('draft');
    expect(versionText(2)).toBe('v2');
    expect(isFailedStatus('failed')).toBe(true);
    expect(isFailedStatus('timeout')).toBe(true);
    expect(isFailedStatus('success')).toBe(false);
  });
});

describe('subworkflowsApi paths', () => {
  afterEach(() => vi.unstubAllGlobals());

  function stub(body: unknown = []) {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock as unknown as { mock: { calls: [string, RequestInit][] } };
  }

  it('calls the section 11 endpoints', async () => {
    const f = stub();
    await subworkflowsApi.list('proj_1', 'refund');
    await subworkflowsApi.io('wf_1', 3);
    await subworkflowsApi.io('wf_1');
    await subworkflowsApi.usedBy('wf_1');
    await subworkflowsApi.lineage('ex_1');
    const urls = f.mock.calls.map((c) => c[0]);
    expect(urls[0]).toMatch(/\/api\/projects\/proj_1\/subworkflows\?q=refund$/);
    expect(urls[1]).toMatch(/\/api\/workflows\/wf_1\/io\?version=3$/);
    expect(urls[2]).toMatch(/\/api\/workflows\/wf_1\/io$/);
    expect(urls[3]).toMatch(/\/api\/workflows\/wf_1\/used-by$/);
    expect(urls[4]).toMatch(/\/api\/executions\/ex_1$/);
  });

  it('sends dry_run true for a preview and false for the commit', async () => {
    const f = stub({});
    await subworkflowsApi.previewExtract('wf_1', ['A', 'B'], 'Child', { nodes: [] });
    await subworkflowsApi.commitExtract('wf_1', ['A', 'B'], 'Child');
    const [preview, commit] = f.mock.calls.map((c) => JSON.parse(String(c[1].body)));
    expect(preview).toMatchObject({ nodes: ['A', 'B'], name: 'Child', dry_run: true, definition: { nodes: [] } });
    expect(commit).toMatchObject({ dry_run: false });
    expect(commit.definition).toBeUndefined();
    expect(f.mock.calls[0][0]).toMatch(/\/workflows\/wf_1\/extract-subworkflow$/);
  });
});

describe('usedByChipLabel', () => {
  it('shows no chip when nothing calls the workflow', () => {
    expect(m.usedByChipLabel([])).toBeNull();
    expect(m.usedByChipLabel(undefined)).toBeNull();
  });
  it('counts distinct caller workflows, not reference rows', () => {
    const row = (id: string) => ({ parent_workflow_id: id });
    expect(m.usedByChipLabel([row('a'), row('b'), row('c')])).toBe('Used by 3');
    expect(m.usedByChipLabel([row('a'), row('a')])).toBe('Used by 1');
  });
});
