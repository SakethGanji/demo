import { useEffect, useState } from 'react';
import { Roll } from '@/shared/components/ui/roll';
import { motionOff } from '@/shared/motion/testFlag';
import type { AppBuildView } from './appTypes';
import { AppPartRow } from './AppPartRow';
import { progressOf } from './lib/appState';

/** Plans whose rows have already played their entrance in this session (a re-open shows them at once). */
const entered = new Set<string>();

export const ROW_GAP_S = 0.3;

/** The parts of the app, in the plan's order, each a row the person can put on the canvas. */
export function AppPartList({ app, projectSlug, conceptLabels, selected, buildingKey, onSelect }: {
  app: AppBuildView;
  projectSlug: string;
  conceptLabels: Record<string, string>;
  selected: string | null;
  buildingKey: string | null;
  onSelect: (key: string) => void;
}) {
  // decided once, at the first render of this plan: later renders keep it, so a status change never replays the entrance
  const [first] = useState(() => !entered.has(app.id) && !motionOff());
  useEffect(() => { entered.add(app.id); }, [app.id]);
  const { built, total } = progressOf(app.parts);
  return (
    <section className="flex flex-col gap-1.5" aria-label="Parts" data-testid="ai-app-parts">
      <div className="flex items-baseline justify-between">
        <h3 className="text-[12px] font-medium text-muted-foreground">Parts</h3>
        {total > 0 && (
          <span className="text-[12px] text-muted-foreground tabular-nums" data-testid="ai-app-progress" data-built={built} data-total={total}>
            <Roll value={built} /> of {total} built
          </span>
        )}
      </div>
      <ul className="flex flex-col gap-1.5 pl-2">
        {app.parts.map((p, i) => (
          <AppPartRow
            key={p.key} part={p} plan={app.plan} projectSlug={projectSlug} conceptLabels={conceptLabels}
            selected={selected === p.key} building={buildingKey === p.key}
            enter={first} delay={i * ROW_GAP_S} lockDelay={i * 0.08} onSelect={onSelect}
          />
        ))}
      </ul>
    </section>
  );
}
