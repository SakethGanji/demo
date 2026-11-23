import { motion } from 'motion/react';
import { motionOff } from '@/shared/motion/testFlag';
import { dur, ease } from '@/shared/motion/tokens';
import { cn } from '@/shared/lib/utils';
import type { AppPlan, PartView } from './appTypes';
import { isDone, miniMap } from './lib/appState';

const NODE_W = 84;
const NODE_H = 20;
const clip = (s: string, n = 13) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/**
 * A static picture of the plan (PLAN-CAPGRAPH-UI §4): entries on the left, the sub-workflows they call on the right, tables along the
 * bottom. After the rows have locked, each edge draws and a comet runs along it once. A part's outline follows its status.
 */
export function AppMiniMap({ plan, parts, startAt = 0 }: { plan: AppPlan; parts: PartView[]; startAt?: number }) {
  const m = miniMap(plan);
  if (!m.nodes.length) return null;
  const off = motionOff();
  const at = (k: string) => m.nodes.find((n) => n.key === k);
  const status = (k: string) => parts.find((p) => p.key === k);
  return (
    <svg
      viewBox={`0 0 ${m.w} ${m.h}`} className="h-auto w-full max-w-[320px] self-center overflow-visible" role="img"
      aria-label={`How the ${plan.parts.length} parts connect`} data-testid="ai-app-map"
    >
      {m.links.map((l) => {
        const a = at(l.from);
        const t = m.tables.find((x) => x.key === l.table);
        if (!a || !t) return null;
        return <line key={`${l.from}-${l.table}`} x1={a.x} y1={a.y} x2={t.x} y2={t.y} className="stroke-faint" strokeWidth={1} strokeDasharray="3 3" />;
      })}
      {m.edges.map((e, i) => {
        const a = at(e.from);
        const b = at(e.to);
        if (!a || !b) return null;
        const delay = startAt + i * 0.08;
        return (
          <g key={`${e.from}-${e.to}`}>
            <motion.line
              x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="stroke-primary/50" strokeWidth={1.25}
              initial={off ? false : { pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: dur.draw, delay, ease: ease.out }}
            />
            {!off && (
              <motion.circle
                r={2.5} className="fill-primary" initial={{ cx: a.x, cy: a.y, opacity: 0 }}
                animate={{ cx: b.x, cy: b.y, opacity: [0, 1, 1, 0] }} transition={{ duration: dur.spark + 0.1, delay: delay + dur.draw * 0.5, ease: 'linear' }}
              />
            )}
          </g>
        );
      })}
      {m.nodes.map((n) => {
        const p = status(n.key);
        const state = !p ? 'waiting' : isDone(p) ? 'done' : p.status === 'building' ? 'active' : p.status === 'failed' ? 'failed' : 'waiting';
        return (
          <g key={n.key} data-node={n.key} data-state={state}>
            <rect
              x={n.x - NODE_W / 2} y={n.y - NODE_H / 2} width={NODE_W} height={NODE_H} rx={6}
              className={cn('fill-recess', state === 'done' ? 'stroke-success' : state === 'active' ? 'stroke-primary' : state === 'failed' ? 'stroke-destructive' : 'stroke-hairline')}
              strokeWidth={1.25}
            />
            <text x={n.x} y={n.y + 3.5} textAnchor="middle" className="fill-foreground" fontSize={10}>{clip(n.title)}</text>
          </g>
        );
      })}
      {m.tables.map((t) => (
        <g key={t.key} data-table={t.key}>
          <rect x={t.x - NODE_W / 2} y={t.y - NODE_H / 2} width={NODE_W} height={NODE_H} rx={4} className="fill-recess stroke-faint" strokeWidth={1} />
          <text x={t.x} y={t.y + 3.5} textAnchor="middle" className="fill-muted-foreground" fontSize={10}>{clip(t.name, 14)}</text>
        </g>
      ))}
    </svg>
  );
}
