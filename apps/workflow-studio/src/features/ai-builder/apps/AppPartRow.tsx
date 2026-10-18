import { useState } from 'react';
import { motion } from 'motion/react';
import { AlertTriangle, Check, CheckCheck, CircleDashed, Clock, FileText, GitBranch, LayoutPanelTop, Link2, Loader2, type LucideIcon } from 'lucide-react';
import { ConceptChip } from '@/features/knowledge/kit/ConceptChip';
import { Construct, type ConstructMode } from '@/features/knowledge/kit/Construct';
import { Roll } from '@/shared/components/ui/roll';
import { IconTile, type EntityHue } from '@/shared/components/ui/surface';
import { cn } from '@/shared/lib/utils';
import { spring } from '@/shared/motion/tokens';
import { dashKey } from '../lib/understanding';
import type { AppPlan, PartView } from './appTypes';
import { conceptLabel, partSubtitle, stageLabel, stageOf, testsText, type PartStage } from './lib/appState';

type Kind = { Icon: LucideIcon; hue: EntityHue };

/** The icon of a part: what starts it (form, cron, page) or that it is a shared sub-workflow or a reused one. */
function kindOf(p: PartView): Kind {
  if (p.status === 'reused' || p.reuse) return { Icon: Link2, hue: 'flow' };
  if (p.role === 'subworkflow') return { Icon: GitBranch, hue: 'flow' };
  if (p.trigger === 'cron') return { Icon: Clock, hue: 'runs' };
  if (p.interface?.kind === 'custom') return { Icon: LayoutPanelTop, hue: 'apps' };
  return { Icon: FileText, hue: 'apps' };
}

const STATE_ICON: Record<PartStage, LucideIcon> = {
  waiting: CircleDashed, spec: CircleDashed, approved: Check, building: Loader2, built: CheckCheck, reused: Link2, failed: AlertTriangle, kept: CheckCheck,
};

function StatusText({ part }: { part: PartView }) {
  const stage = stageOf(part);
  const Icon = STATE_ICON[stage];
  const tests = testsText(part);
  return (
    <span
      className={cn(
        'flex shrink-0 items-center gap-1 text-[12px] tabular-nums',
        stage === 'built' || stage === 'kept' || stage === 'approved' ? 'text-success-text' : stage === 'failed' ? 'text-destructive' : stage === 'building' ? 'text-foreground' : 'text-muted-foreground',
      )}
      data-testid={`ai-app-part-status-${part.key}`}
    >
      <Icon className={cn('size-3.5', stage === 'building' && 'animate-spin text-primary')} aria-hidden />
      {(stage === 'built' || stage === 'kept') && tests ? (
        <span>{stage === 'kept' ? 'kept' : 'built'} · <Roll value={tests} /></span>
      ) : (
        <span>{stageLabel(part)}</span>
      )}
    </span>
  );
}

/**
 * One part of the app. It constructs in when the plan arrives, shows the accent bar while it is the part on the canvas being built
 * (`layoutId="ai-app-head"`, the build log's head style), and locks in success when it turns built. A click puts it on the canvas.
 */
export function AppPartRow({ part, plan, conceptLabels, selected, building, enter, delay, lockDelay, onSelect }: {
  part: PartView;
  plan: AppPlan | null;
  /** kept for callers; reused parts no longer link to a project page */
  projectSlug?: string;
  conceptLabels: Record<string, string>;
  selected: boolean;
  building: boolean;
  /** play the entrance (the first time this plan is shown) */
  enter: boolean;
  /** seconds before the entrance starts */
  delay: number;
  /** seconds before the success lock when the whole app is kept (rows flash one after another) */
  lockDelay: number;
  onSelect: (key: string) => void;
}) {
  const [mode, setMode] = useState<ConstructMode>(enter ? 'enter' : 'idle');
  const [prev, setPrev] = useState(part.status);
  if (prev !== part.status) {
    setPrev(part.status);
    if (part.status === 'ready' || part.status === 'kept') setMode('lock');
  }
  const { Icon, hue } = kindOf(part);
  const stage = stageOf(part);
  const shown = part.concepts.slice(0, 3);
  const reuseKey = part.reuse?.capability;
  return (
    <li
      className="relative" data-testid={`ai-app-part-${part.key}`} data-status={part.status} data-stage={stage}
      data-selected={selected ? 'true' : undefined} data-building={building ? 'true' : undefined}
    >
      {building && (
        <motion.span
          layoutId="ai-app-head" data-testid="ai-app-head" transition={spring.firm}
          className="absolute bottom-1 -left-2 top-1 w-0.5 rounded-full bg-primary"
        />
      )}
      <Construct mode={mode} scale={0.6} delay={mode === 'enter' ? delay : part.status === 'kept' ? lockDelay : 0} tone={mode === 'lock' ? 'success' : 'primary'} radius={8} onDone={() => setMode('idle')}>
        <div className={cn('flex items-center gap-1 rounded-ctl bg-recess', selected && 'bg-(--hover-fill)')}>
          <button
            type="button" onClick={() => onSelect(part.key)} aria-pressed={selected}
            className="flex min-w-0 flex-1 items-center gap-2.5 rounded-ctl px-2.5 py-2 text-left outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
            data-testid={`ai-app-part-open-${part.key}`}
          >
            <IconTile hue={hue} className="size-7"><Icon className="size-3.5" aria-hidden /></IconTile>
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="truncate text-[13px] font-medium text-foreground">{part.title}</span>
              <span className="flex min-w-0 flex-wrap items-center gap-1 text-[12px] leading-4 text-muted-foreground">
                <span className="truncate">{partSubtitle(part, plan)}</span>
                {part.role === 'entry' && shown.map((c) => (
                  <ConceptChip key={c} conceptKey={c} label={conceptLabel(c, conceptLabels)} tone="suggested" data-testid={`ai-app-part-${part.key}-chip-${dashKey(c)}`} />
                ))}
              </span>
            </span>
            <StatusText part={part} />
          </button>
          {reuseKey && (
            <span
              className="mr-2 flex shrink-0 items-center rounded-chip px-1 text-[12px] text-ai-text"
              data-testid={`ai-app-part-reused-${part.key}`}
              title={part.reuse?.label ? `Reuses ${part.reuse.label}` : undefined}
            >
              reused
            </span>
          )}
        </div>
      </Construct>
      {part.error && stage === 'failed' && (
        <p className="mt-1 px-2.5 text-[12px] leading-4 text-destructive" role="alert">{part.error}</p>
      )}
      {part.accepted_failing && <p className="mt-1 px-2.5 text-[12px] leading-4 text-warning-text">Kept with a failing test</p>}
    </li>
  );
}
