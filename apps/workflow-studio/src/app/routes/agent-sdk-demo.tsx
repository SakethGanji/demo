/**
 * FEASIBILITY SPIKE — not a product route. Proves, against the real app (real React Flow instance,
 * real node-group CSS tokens, real component tree), that two things claimed by the
 * design-prototypes/terminal-agent-sdk*.html mockups are actually buildable:
 *
 *   1. the "card" node style (left color bar, TYPE·SUBTYPE header, mono param preview,
 *      line-number badge) — see WorkflowNodeCard.tsx, a NEW node type, additive only.
 *   2. line ↔ node provenance linking — hover a script line, its node(s) highlight;
 *      click a node, its line pins. Driven by plain React state (`activeKey`), no new
 *      backend concept required beyond storing a string on each node/line.
 *
 * Deliberately does NOT use the shared workflowStore/WorkflowCanvas — this is a standalone
 * ReactFlow instance seeded with static demo data, so it cannot affect the real editor.
 * Answers "can this be built" without touching anything the real editor depends on.
 */
import { useMemo, useState } from 'react';
import { createRoute } from '@tanstack/react-router';
import { ReactFlow, ReactFlowProvider, Background, BackgroundVariant, type Edge } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { rootRoute } from './__root';
import WorkflowNodeCard, { type CardNode, type CardNodeData } from '@/features/workflow-editor/components/canvas/nodes/WorkflowNodeCard';
import { getNodeStyles } from '@/features/workflow-editor/lib/nodeStyles';

export const agentSdkDemoRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: 'agent-sdk-demo',
  component: AgentSdkDemoPage,
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const nodeTypes: any = { cardNode: WorkflowNodeCard };

const BASE_NODES: Array<{ id: string; position: { x: number; y: number }; data: CardNodeData }> = [
  { id: 'cron', position: { x: 40, y: 20 }, data: {
    group: 'trigger', typeLabel: 'Trigger · Cron', name: 'Cron', preview: '0 7 * * 1 · Europe/London',
    sourceKey: 'l3', sourceLabel: 'line 3',
  } },
  { id: 'fetch', position: { x: 40, y: 140 }, data: {
    group: 'action', typeLabel: 'Action · HttpRequest', name: 'HttpRequest', preview: 'GET {{ $vars.EXPORT_URL }}',
    sourceKey: 'l4', sourceLabel: 'line 4', hasInput: true,
  } },
  { id: 'norm', position: { x: 40, y: 240 }, data: {
    group: 'transform', typeLabel: 'Transform · Code', name: 'Code', preview: 'python · NORMALISE',
    sourceKey: 'l5', sourceLabel: 'line 5', hasInput: true,
  } },
  { id: 'check', position: { x: 40, y: 340 }, data: {
    group: 'flow', typeLabel: 'Flow · If', name: 'If', preview: 'body isNotEmpty',
    sourceKey: 'l6', sourceLabel: 'line 6', hasInput: true, outPorts: ['true', 'false'],
  } },
  { id: 'loadUS', position: { x: 380, y: 280 }, data: {
    group: 'output', typeLabel: 'Output · Postgres', name: 'Load US', preview: 'upsert · finance.ledger',
    sourceKey: 'loop', sourceLabel: 'line 13 · US', hasInput: true,
  } },
  { id: 'loadEU', position: { x: 380, y: 380 }, data: {
    group: 'output', typeLabel: 'Output · Postgres', name: 'Load EU', preview: 'upsert · finance.ledger',
    sourceKey: 'loop', sourceLabel: 'line 13 · EU', hasInput: true,
  } },
  { id: 'loadAPAC', position: { x: 380, y: 480 }, data: {
    group: 'output', typeLabel: 'Output · Postgres', name: 'Load APAC', preview: 'upsert · finance.ledger',
    sourceKey: 'loop', sourceLabel: 'line 13 · APAC', hasInput: true,
  } },
  { id: 'stop', position: { x: 40, y: 460 }, data: {
    group: 'flow', typeLabel: 'Flow · StopAndError', name: 'StopAndError', preview: 'Export was empty',
    sourceKey: 'l21', sourceLabel: 'line 21', hasInput: true,
  } },
  { id: 'summary', position: { x: 700, y: 380 }, data: {
    group: 'action', typeLabel: 'Action · SendEmail', name: 'SendEmail', preview: 'to finance-ops@ · Close',
    sourceKey: 'l22', sourceLabel: 'line 22 · 24', hasInput: true,
  } },
];

const BASE_EDGES: Edge[] = [
  { id: 'e1', source: 'cron', target: 'fetch' },
  { id: 'e2', source: 'fetch', target: 'norm' },
  { id: 'e3', source: 'norm', target: 'check' },
  { id: 'e4', source: 'check', sourceHandle: 'true', target: 'loadUS' },
  { id: 'e5', source: 'check', sourceHandle: 'true', target: 'loadEU' },
  { id: 'e6', source: 'check', sourceHandle: 'true', target: 'loadAPAC' },
  { id: 'e7', source: 'check', sourceHandle: 'false', target: 'stop' },
  { id: 'e8', source: 'loadUS', target: 'summary' },
  { id: 'e9', source: 'loadEU', target: 'summary' },
  { id: 'e10', source: 'loadAPAC', target: 'summary' },
];

type Row = { ln: number; code: React.ReactNode; key?: string };

const ROWS: Row[] = [
  { ln: 1, code: <span className="text-neutral-500"># Monday 07:00 → fetch the export → branch on emptiness</span> },
  { ln: 2, code: '' },
  { ln: 3, code: <>cron  = <b className="text-neutral-100">Cron</b>(expression=<span className="text-emerald-400">"0 7 * * 1"</span>, timezone=<span className="text-emerald-400">"Europe/London"</span>)</>, key: 'l3' },
  { ln: 4, code: <>fetch = <b className="text-neutral-100">HttpRequest</b>(method=<span className="text-emerald-400">"GET"</span>, url=<span className="text-emerald-400">"{'{{ $vars.EXPORT_URL }}'}"</span>)</>, key: 'l4' },
  { ln: 5, code: <>norm  = <b className="text-neutral-100">Code</b>(language=<span className="text-emerald-400">"python"</span>, code=NORMALISE)</>, key: 'l5' },
  { ln: 6, code: <>check = <b className="text-neutral-100">If</b>(field=<span className="text-emerald-400">"body"</span>, operation=<span className="text-emerald-400">"isNotEmpty"</span>)</>, key: 'l6' },
  { ln: 7, code: '' },
  { ln: 8, code: <>cron <span className="text-sky-400">{'>>'}</span> fetch <span className="text-sky-400">{'>>'}</span> norm <span className="text-sky-400">{'>>'}</span> check</>, key: 'l3' },
  { ln: 9, code: '' },
  { ln: 10, code: <span className="text-neutral-500"># one loader per region — a for-loop, not twelve tool calls</span> },
  { ln: 11, code: 'loaders = []' },
  { ln: 12, code: <><span className="text-sky-400">for</span> region <span className="text-sky-400">in</span> ("US", "EU", "APAC"):</>, key: 'loop' },
  { ln: 13, code: <>    node = <b className="text-neutral-100">Postgres</b>(name=f"Load {'{region}'}", operation="upsert", table="finance.ledger")</>, key: 'loop' },
  { ln: 18, code: <>    check.true <span className="text-sky-400">{'>>'}</span> node</>, key: 'loop' },
  { ln: 19, code: '    loaders.append(node)', key: 'loop' },
  { ln: 20, code: '' },
  { ln: 21, code: <>check.false <span className="text-sky-400">{'>>'}</span> <b className="text-neutral-100">StopAndError</b>(message="Export was empty")</>, key: 'l21' },
  { ln: 22, code: <>summary = <b className="text-neutral-100">SendEmail</b>(toEmail="finance-ops@", subject="Close", body=SUMMARY)</>, key: 'l22' },
  { ln: 23, code: <><span className="text-sky-400">for</span> node <span className="text-sky-400">in</span> loaders:</>, key: 'l22' },
  { ln: 24, code: <>    node <span className="text-sky-400">{'>>'}</span> summary</>, key: 'l22' },
  { ln: 25, code: '' },
  { ln: 26, code: <><b className="text-neutral-100">validate</b>()</> },
  { ln: 27, code: <><b className="text-neutral-100">test_run</b>(input={'{'}"body": SAMPLE{'}'})</> },
  { ln: 28, code: '' },
];

function AgentSdkDemoPage() {
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const [pinnedKey, setPinnedKey] = useState<string | null>(null);
  const activeKey = pinnedKey ?? hoveredKey;

  const nodes: CardNode[] = useMemo(
    () =>
      BASE_NODES.map((n) => ({
        id: n.id,
        type: 'cardNode',
        position: n.position,
        draggable: false,
        selectable: false,
        data: { ...n.data, active: activeKey !== null && n.data.sourceKey === activeKey },
      })),
    [activeKey],
  );

  const edges: Edge[] = useMemo(
    () =>
      BASE_EDGES.map((e) => {
        const srcNode = BASE_NODES.find((n) => n.id === e.source);
        const isActive = activeKey !== null && srcNode?.data.sourceKey === activeKey;
        return {
          ...e,
          style: isActive
            ? { stroke: getNodeStyles(srcNode!.data.group).accentColor, strokeWidth: 2 }
            : { stroke: 'var(--border)', strokeWidth: 1.5 },
        };
      }),
    [activeKey],
  );

  return (
    <div className="h-screen w-screen flex bg-background text-foreground">
      {/* script pane */}
      <div className="w-[440px] min-w-[440px] border-r border-border flex flex-col">
        <div className="px-3 py-2 border-b border-border text-[11px] font-medium text-muted-foreground">
          build_workflow.py — hover or click a line
        </div>
        <div className="flex-1 overflow-auto font-mono text-[11px] leading-[1.7] py-2">
          {ROWS.map((r) => {
            const active = r.key && r.key === activeKey;
            const interactive = Boolean(r.key);
            return (
              <div
                key={r.ln}
                onMouseEnter={() => r.key && setHoveredKey(r.key)}
                onMouseLeave={() => setHoveredKey(null)}
                onClick={() => r.key && setPinnedKey((p) => (p === r.key ? null : r.key!))}
                className="flex px-3"
                style={{
                  cursor: interactive ? 'pointer' : 'default',
                  background: active ? 'color-mix(in srgb, var(--node-action) 14%, transparent)' : undefined,
                }}
              >
                <span className="w-6 text-right pr-2 select-none" style={{ color: active ? 'var(--node-action)' : 'var(--muted-foreground)', fontWeight: active ? 700 : 400 }}>
                  {r.ln}
                </span>
                <span className="text-neutral-300 whitespace-pre">{r.code}</span>
              </div>
            );
          })}
        </div>
        <div className="px-3 py-2 border-t border-border text-[10.5px] text-muted-foreground">
          {activeKey ? (
            <>pinned: <span className="font-mono">{activeKey}</span> — click the line again to release</>
          ) : (
            'nothing pinned — hover a line to preview, click to pin'
          )}
        </div>
      </div>

      {/* canvas */}
      <div className="flex-1 relative">
        <ReactFlowProvider>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            fitView
            fitViewOptions={{ padding: 0.15 }}
            proOptions={{ hideAttribution: true }}
            onNodeClick={(_, node) => {
              const key = (node.data as CardNodeData).sourceKey;
              setPinnedKey((p) => (p === key ? null : key));
            }}
            onNodeMouseEnter={(_, node) => setHoveredKey((node.data as CardNodeData).sourceKey)}
            onNodeMouseLeave={() => setHoveredKey(null)}
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
          </ReactFlow>
        </ReactFlowProvider>
      </div>
    </div>
  );
}
