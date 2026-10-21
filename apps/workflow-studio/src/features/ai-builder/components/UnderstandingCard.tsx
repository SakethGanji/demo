import { Button } from '@/shared/components/ui/button'
import { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { useQueryClient } from '@tanstack/react-query';
import { ChevronDown, CircleCheck, GitBranch, HelpCircle, LayoutTemplate, Sparkles } from 'lucide-react';
import { ConceptChip } from '@/features/knowledge/kit/ConceptChip';
import { Construct, type ConstructMode } from '@/features/knowledge/kit/Construct';
import { Callout, IconTile, Tile } from '@/shared/components/ui/surface';
import { Roll } from '@/shared/components/ui/roll';
import { cn } from '@/shared/lib/utils';
import { motionOff } from '@/shared/motion/testFlag';
import { dur, ease, spring } from '@/shared/motion/tokens';
import type { Understanding } from '../types';
import {
  chipTone, compactLine, dashKey, openQuestions, reusingItems, reusingOpenByDefault, reusingText, shownQuestions, slugOf, understoodChips,
  type ReusingItem,
} from '../lib/understanding';
import { TypedText } from './TypedText';

/** Proposals whose understanding has already played its entrance this session (a re-open just shows the card). */
const seen = new Set<string>();

const CHIP_GAP_MS = 35;
const CHIPS_AT_MS = 400;
const REUSING_AT_S = 0.56;
const COUNT_AT_MS = 600;

const KIND_ICON = { subworkflow: GitBranch, template: LayoutTemplate, pattern: Sparkles } as const;
const KIND_WORD = { subworkflow: 'sub-workflow', template: 'template', pattern: 'pattern' } as const;

/** The definition of a concept from the Knowledge page's cache, when it was loaded; else the evidence (gap G6). */
function useDefinitions(projectId: string): (key: string) => string | undefined {
  const qc = useQueryClient();
  return (key) => {
    const data = qc.getQueryData<{ nodes?: Array<{ key: string; props?: { definition?: unknown } }> }>(['kg', 'graph', projectId, false]);
    const d = data?.nodes?.find((n) => n.key === key)?.props?.definition;
    return typeof d === 'string' && d.trim() ? d.trim() : undefined;
  };
}

function ReusingRow({ item, lockDelay }: { item: ReusingItem; lockDelay: number | null }) {
  const Icon = KIND_ICON[item.kind];
  const [mode, setMode] = useState<ConstructMode>(lockDelay === null ? 'idle' : 'lock');
  return (
    <li>
      <Construct mode={mode} delay={lockDelay ?? 0} onDone={() => setMode('idle')} radius={6}>
        <div
          className="flex min-w-0 items-start gap-2 rounded-ctl bg-recess px-2.5 py-2"
          data-testid={`ai-reusing-item-${dashKey(item.key)}`}
          data-kind={item.kind}
          data-adapting={item.adapting ? 'true' : undefined}
        >
          <Icon className="mt-0.5 size-3.5 shrink-0 text-ai-text" aria-hidden />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="truncate text-[13px] font-medium text-foreground">{item.label}</span>
            <span className="text-[12px] leading-4 text-muted-foreground">
              {KIND_WORD[item.kind]} · {item.line}
              {item.adapting && <span className="text-ai-text"> · adapting this</span>}
            </span>
          </span>
        </div>
      </Construct>
    </li>
  );
}

/**
 * What the AI understood from the request (PLAN-CAPGRAPH-UI §3.3): the concepts it heard, what it will reuse, and how many
 * questions it has. It stays above the sidebar block from clarify to review, and shrinks to one line once the plan is past.
 * Nothing here shows a score or a confidence.
 */
export function UnderstandingCard({ proposalId, projectId, understanding: u, compact = false, sealed = false, onJumpToQuestions }: {
  proposalId: string;
  projectId: string;
  /** kept for callers; reused items no longer link to a project page */
  projectSlug?: string;
  understanding: Understanding;
  /** past the plan: one line, click to expand */
  compact?: boolean;
  /** the answers are on their way: the questions row already reads "Answered" */
  sealed?: boolean;
  onJumpToQuestions?: () => void;
}) {
  const definitionOf = useDefinitions(projectId);
  const [first] = useState(() => !seen.has(proposalId) && !motionOff());
  useEffect(() => { seen.add(proposalId); }, [proposalId]);

  const [construct, setConstruct] = useState<ConstructMode>(first ? 'enter' : 'idle');
  const chips = useMemo(() => understoodChips(u), [u]);
  const [shown, setShown] = useState(first ? 0 : chips.length);
  useEffect(() => {
    if (!first) { setShown(chips.length); return; }
    let i = 0;
    let timer: number | undefined;
    const tick = () => { i += 1; setShown(i); if (i < chips.length) timer = window.setTimeout(tick, CHIP_GAP_MS); };
    timer = window.setTimeout(tick, CHIPS_AT_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the entrance plays for the chips it first saw; later changes show at once
  }, [first]);
  const visibleChips = first ? chips.slice(0, shown) : chips;

  const items = useMemo(() => reusingItems(u), [u]);
  const [reusingOpen, setReusingOpen] = useState(() => reusingOpenByDefault(u));
  useEffect(() => { if (reusingOpenByDefault(u)) setReusingOpen(true); }, [u]);

  const required = shownQuestions(u).filter((q) => q.required);
  const open = openQuestions(u).length;
  const answered = sealed || (required.length > 0 && open === 0);
  const count = required.length;
  const [rolled, setRolled] = useState(first ? 0 : count);
  useEffect(() => {
    if (!first) { setRolled(count); return; }
    const t = window.setTimeout(() => setRolled(count), COUNT_AT_MS);
    return () => window.clearTimeout(t);
  }, [first, count]);
  const shownCount = answered ? count : first ? rolled : open;

  const [expanded, setExpanded] = useState(false);
  const isCompact = compact && !expanded;
  useEffect(() => { if (!compact) setExpanded(false); }, [compact]);

  const hintOf = (key: string) => definitionOf(key) ?? u.concepts.find((c) => c.key === key)?.evidence;
  const toneOf = (key: string) => {
    const c = u.concepts.find((x) => x.key === key);
    return c ? chipTone(c) : 'confirmed';
  };
  const collapse = { initial: { height: 0, opacity: 0 }, animate: { height: 'auto', opacity: 1 }, exit: { height: 0, opacity: 0 } } as const;

  return (
    <Construct mode={construct} onDone={() => setConstruct('idle')}>
      <Tile
        className="flex flex-col gap-2.5 p-3"
        data-testid="ai-understanding"
        data-compact={isCompact ? 'true' : 'false'}
        data-shape={u.shape}
      >
        <AnimatePresence initial={false} mode="popLayout">
          {isCompact ? (
            <motion.button
              key="compact"
              type="button"
              {...collapse}
              transition={{ duration: dur.slow, ease: ease.out }}
              onClick={() => setExpanded(true)}
              className="flex min-w-0 items-center gap-2 overflow-hidden rounded-ctl text-left text-[12.5px] text-muted-foreground outline-none hover:text-foreground focus-visible:shadow-focus"
              aria-expanded={false}
              aria-label="Show what the AI understood"
              data-testid="ai-understanding-toggle"
            >
              <Sparkles className="size-3.5 shrink-0 text-ai-text" aria-hidden />
              <span className="min-w-0 flex-1 truncate">{compactLine(u)}</span>
              <ChevronDown className="size-3.5 shrink-0" aria-hidden />
            </motion.button>
          ) : (
            <motion.div
              key="full"
              {...collapse}
              transition={{ duration: dur.slow, ease: ease.out }}
              className="flex flex-col gap-2.5 overflow-hidden"
            >
              <div className="flex items-center gap-2">
                <IconTile hue="ai" className="size-6"><Sparkles aria-hidden /></IconTile>
                <span className="text-[13px] font-semibold text-foreground">Understood · {chips.length}</span>
                <div className="flex-1" />
                {compact && (
                  <Button type="button" variant="ghost" size="icon-xs" onClick={() => setExpanded(false)} aria-label="Show less">
                    <ChevronDown className="size-3.5 rotate-180" aria-hidden />
                  </Button>
                )}
              </div>

              <div className="flex flex-wrap gap-1" data-testid="ai-understood">
                <AnimatePresence initial={false}>
                  {visibleChips.map((c) => (
                    <ConceptChip
                      key={c.key}
                      conceptKey={c.key}
                      label={c.label}
                      tone={toneOf(c.key)}
                      hint={hintOf(c.key)}
                      data-testid={`ai-understood-chip-${slugOf(c.key)}`}
                    />
                  ))}
                </AnimatePresence>
                {chips.length === 0 && <span className="text-[12.5px] text-muted-foreground">Nothing specific recognised. The plan is written from your words alone.</span>}
              </div>

              {u.shape === 'app' && (
                <Callout tone="info" icon={<Sparkles />} data-testid="ai-app-note">
                  This sounds like an application. It will be planned as a set of parts that you approve and build one by one, then keep together.
                </Callout>
              )}

              <div className="flex flex-col gap-1.5 border-t border-hairline pt-2" data-testid="ai-reusing" data-count={items.length}>
                <div className="flex items-center gap-2 text-[12.5px]">
                  <GitBranch className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="text-muted-foreground">Reusing</span>
                  <TypedText text={reusingText(u.summary.reusing)} active={first} delay={REUSING_AT_S} className="font-medium text-foreground" data-testid="ai-reusing-text" />
                  <div className="flex-1" />
                  {items.length > 0 && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      onClick={() => setReusingOpen((v) => !v)}
                      aria-expanded={reusingOpen}
                      className="text-muted-foreground"
                      data-testid="ai-reusing-toggle"
                    >
                      {reusingOpen ? 'Hide' : 'Show'}
                      <ChevronDown className={cn('size-3 transition-transform duration-(--d-fast)', reusingOpen && 'rotate-180')} aria-hidden />
                    </Button>
                  )}
                </div>
                <AnimatePresence initial={false}>
                  {reusingOpen && items.length > 0 && (
                    <motion.ul
                      {...collapse}
                      transition={{ duration: dur.base, ease: ease.out }}
                      className="flex flex-col gap-1 overflow-hidden"
                    >
                      {items.map((it) => (
                        <ReusingRow key={it.key} item={it} lockDelay={first && it.adapting ? REUSING_AT_S + 0.12 : null} />
                      ))}
                    </motion.ul>
                  )}
                </AnimatePresence>
              </div>

              {count > 0 && (
                <button
                  type="button"
                  onClick={onJumpToQuestions}
                  disabled={!onJumpToQuestions || answered}
                  className="flex items-center gap-2 border-t border-hairline pt-2 text-left text-[12.5px] outline-none enabled:hover:text-foreground focus-visible:shadow-focus disabled:cursor-default"
                  data-testid="ai-questions-row"
                  data-answered={answered ? 'true' : 'false'}
                >
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.span key={answered ? 'done' : 'open'} initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1, transition: spring.snap }} exit={{ opacity: 0, transition: { duration: dur.instant } }} className="inline-flex">
                      {answered ? <CircleCheck className="size-3.5 text-success-text" aria-hidden /> : <HelpCircle className="size-3.5 text-ai-text" aria-hidden />}
                    </motion.span>
                  </AnimatePresence>
                  <span className={answered ? 'text-success-text' : 'font-medium text-foreground'}>{answered ? 'Answered' : 'Questions'}</span>
                  <Roll value={shownCount} className="font-semibold tabular-nums" data-testid="ai-questions-count" />
                  <div className="flex-1" />
                  {!answered && <span className="text-muted-foreground">answer below</span>}
                </button>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </Tile>
    </Construct>
  );
}
