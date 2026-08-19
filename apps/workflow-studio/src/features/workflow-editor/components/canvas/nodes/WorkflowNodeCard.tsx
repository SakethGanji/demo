/**
 * Feasibility spike — the "card" node style from design-prototypes/terminal-agent-sdk*.html,
 * built as a real React Flow node against this app's actual node-group color tokens
 * (--node-trigger, --node-action, etc. from src/index.css), not a CSS mockup.
 *
 * Deliberately a NEW node type, not a change to WorkflowNode.tsx — the production editor's
 * circle-icon node is used elsewhere and this proves the card look is achievable without
 * touching it. If the card style is adopted, this is the file that would replace it.
 *
 * Two things the mockup only asserted, proven here as real component behavior:
 *  - a node can carry provenance (`sourceKey`) and light up when that key is active, driven by
 *    ordinary React state — no new subsystem, no new backend field beyond storing the string.
 *  - a `ghost` (placed-but-not-configured) visual state is a CSS variant of the same component,
 *    not a second node type.
 */
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { getNodeStyles, type NodeGroup } from '../../../lib/nodeStyles';

export interface CardNodeData extends Record<string, unknown> {
  group: NodeGroup;
  typeLabel: string; // "Trigger · Cron"
  name: string; // "Cron"
  preview: string; // "0 7 * * 1 · Europe/London"
  sourceKey: string; // ties this node back to the script line(s) that produced it
  sourceLabel: string; // "line 3" or "line 13 · APAC" — what the badge shows
  ghost?: boolean; // placed but not yet configured
  outPorts?: string[]; // named output ports, e.g. ["true", "false"]
  hasInput?: boolean;
}

export type CardNode = Node<CardNodeData, 'cardNode'>;

interface WorkflowNodeCardProps extends NodeProps<CardNode> {
  data: CardNodeData & { active?: boolean };
}

function WorkflowNodeCard({ data }: WorkflowNodeCardProps) {
  // Same var strings the production node resolves via getNodeStyles — this
  // card must not carry its own copy of the group→token mapping, or a token
  // rename in nodeStyles.ts silently stops propagating here.
  const accent = getNodeStyles(data.group).accentColor;
  const outPorts = data.outPorts && data.outPorts.length > 0 ? data.outPorts : ['out'];
  const isMultiOut = outPorts.length > 1;
  const outTop = (i: number) =>
    isMultiOut ? 30 + (i * 40) / Math.max(1, outPorts.length - 1) : 50;

  return (
    <div
      className="relative rounded-lg transition-shadow duration-150"
      style={{
        width: 190,
        padding: '8px 10px 9px 13px',
        background: data.ghost ? 'transparent' : 'var(--node-bg, var(--card))',
        // NOTE: cannot append an alpha suffix to a var() reference (e.g. `${accent}33`) — that
        // makes the whole box-shadow value invalid CSS, and an invalid entry drops the ENTIRE
        // comma-separated box-shadow list, not just that one shadow. Caught by actually rendering
        // this in a browser — the ring silently failed to appear even though nothing errored.
        boxShadow: data.active
          ? `0 0 0 2px ${accent}, 0 3px 14px -3px rgba(0,0,0,.4)`
          : data.ghost
            ? 'inset 0 0 0 1px var(--border)'
            : '0 1px 2px rgba(0,0,0,.08), 0 3px 10px -6px rgba(0,0,0,.25), inset 0 0 0 1px var(--border)',
        outline: data.ghost ? '1.5px dashed var(--border)' : 'none',
        outlineOffset: data.ghost ? '-1.5px' : undefined,
      }}
    >
      {/* left color bar — identity, never a fill */}
      {!data.ghost && (
        <div
          className="absolute rounded-full"
          style={{ left: 4, top: 8, bottom: 8, width: 2.5, background: accent }}
        />
      )}

      {/* provenance badge — the line that produced this node */}
      <div
        className="absolute -top-2 left-2.5 rounded px-1 font-mono text-[8.5px] leading-tight"
        style={{
          background: 'var(--background)',
          color: data.active ? accent : 'var(--muted-foreground)',
          fontWeight: data.active ? 700 : 400,
          boxShadow: '0 0 0 1px var(--border)',
        }}
      >
        {data.sourceLabel}
      </div>

      {data.hasInput && (
        <Handle
          type="target"
          position={Position.Left}
          style={{ top: '50%', background: accent, borderColor: accent, width: 6, height: 6 }}
        />
      )}

      <div
        className="text-[9px] font-medium uppercase tracking-wide"
        style={{ color: 'var(--muted-foreground)', letterSpacing: '.07em' }}
      >
        {data.typeLabel}
      </div>
      <div
        className="text-[11.5px] font-semibold leading-tight mt-0.5"
        style={{ color: data.ghost ? 'var(--muted-foreground)' : 'var(--foreground)' }}
      >
        {data.name}
      </div>
      {!data.ghost && (
        <div
          className="font-mono text-[9.5px] mt-1 truncate"
          style={{ color: 'var(--muted-foreground)' }}
        >
          {data.preview}
        </div>
      )}

      {outPorts.map((label, i) => {
        return (
          <div key={label} className="absolute" style={{ right: -2, top: `${outTop(i)}%`, transform: 'translate(50%, -50%)' }}>
            <Handle
              type="source"
              position={Position.Right}
              id={label}
              style={{ position: 'relative', background: accent, borderColor: accent, width: 6, height: 6 }}
            />
            {isMultiOut && (
              <span
                className="absolute font-mono text-[8.5px] whitespace-nowrap"
                style={{ left: 9, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted-foreground)' }}
              >
                {label}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default WorkflowNodeCard;
