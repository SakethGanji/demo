import { useEffect, useRef } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { CheckCircle2, FastForward, FlaskConical, Link2, Loader2, Minus, PenLine, Plus, ShieldCheck, XCircle } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Callout } from '@/shared/components/ui/surface';
import { cn } from '@/shared/lib/utils';
import { dur, ease, spring } from '@/shared/motion/tokens';
import { motionOff } from '@/shared/motion/testFlag';
import { roundsFromEvents, useAiProposalStore, type RoundProgress } from '../aiProposalStore';
import { buildLog, type LogRow, type LogRound } from '../lib/buildLog';
import { snapshotFromDefinition } from '../stage/buildOps';
import { useStageStore, type BuildHead, type StageLog } from '../stage/stageStore';
import type { AiEvent } from '../types';
import { Elapsed } from './Elapsed';

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function roundLine(r: RoundProgress): string {
  const bits: string[] = [];
  if (r.applied !== null) bits.push(`${plural(r.applied, 'change')} applied`);
  if (r.autoFixed) bits.push(`${plural(r.autoFixed, 'small fix', 'small fixes')} made automatically`);
  if (r.rejected) bits.push(`${plural(r.rejected, 'change')} refused`);
  if (r.errors !== null) bits.push(r.errors === 0 ? 'no mistakes found' : `${plural(r.errors, 'mistake')} found`);
  if (r.passed !== null) bits.push(`${r.passed} ${r.passed === 1 ? 'test' : 'tests'} passing${r.failed ? `, ${r.failed} failing` : ''}`);
  return bits.length ? bits.join(' · ') : 'Starting…';
}

function stageOf(r: RoundProgress): string {
  if (r.done) return r.errors === 0 && !r.failed ? 'Everything checks out' : 'Looking at what is left to fix';
  if (r.errors !== null) return 'Running the tests';
  if (r.applied !== null) return 'Checking for mistakes';
  return 'Building the steps';
}

const ICON = { add: Plus, change: PenLine, remove: Minus, link: Link2, check: ShieldCheck, test: FlaskConical } as const;
const TONE = { neutral: 'text-muted-foreground', ok: 'text-success-text', bad: 'text-destructive', warn: 'text-warning-text' } as const;

/**
 * While the canvas plays a build, the log shows only what the canvas has built (PLAN-MOTION-V2 §3.9). The gate is on from the
 * moment the build starts (the canvas has not begun its first round yet, so nothing is shown) until playback is done.
 */
const useGate = () => useStageStore((s) => s.active && s.mode !== 'review' && s.playback !== 'done' && !motionOff());

const HEAD_KINDS: Record<LogRow['kind'], readonly BuildHead['kind'][]> = {
  add: ['construct'], change: ['morph'], remove: ['deconstruct'], test: ['test', 'fail'], link: [], check: [],
};

/** The rows of `round` the canvas has reached: nodes at their lock, links once linked, tests as their probes start. */
function visibleRows(rows: LogRow[], round: number, log: StageLog): LogRow[] {
  if (round < log.round) return rows;
  if (round > log.round) return [];
  let tests = 0;
  return rows.filter((row) => {
    if (row.kind === 'test') return tests++ < log.tests;
    if (row.kind === 'link' || row.kind === 'check') return log.linked;
    return !!row.node && !!log.built[row.node];
  });
}

function Row({ row }: { row: LogRow }) {
  const hover = useStageStore((s) => s.hoverNode);
  const gate = useGate();
  const head = useStageStore((s) => (s.buildHead && s.buildHead.node === row.node && HEAD_KINDS[row.kind].includes(s.buildHead.kind) ? s.buildHead.seq : 0));
  const { setHoverNode } = useStageStore.getState();
  const Icon = row.kind === 'test' ? (row.tone === 'ok' ? CheckCircle2 : XCircle) : ICON[row.kind];
  const headLit = gate && !!row.node && head > 0;
  const lit = !!row.node && (hover === row.node || headLit || !!row.nodes?.includes(hover ?? '\u0000') && row.kind === 'test');
  const target = row.node;
  return (
    <motion.li
      layout="position"
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: dur.base, ease: ease.out }}
      data-testid="ai-log-row"
      data-kind={row.kind}
      data-node={row.node ?? ''}
      data-lit={lit ? 'true' : 'false'}
      onMouseEnter={target ? () => setHoverNode(target) : undefined}
      onMouseLeave={target ? () => setHoverNode(null) : undefined}
      onClick={target ? () => useAiProposalStore.getState().setFocusNode(target) : undefined}
      className={cn(
        'flex min-w-0 items-center gap-2 rounded-chip px-1.5 py-0.5 text-[12.5px] leading-[18px] transition-colors duration-(--d-fast)',
        'relative', target && 'cursor-default hover:bg-(--hover-fill)', lit && 'bg-(--hover-fill)', headLit && 'bg-primary/8',
      )}
    >
      {headLit && (
        <motion.span
          layoutId="ai-log-head" data-testid="ai-log-head" transition={spring.firm}
          className="absolute bottom-1 left-0 top-1 w-0.5 rounded-full bg-primary"
        />
      )}
      <motion.span
        className={cn('flex shrink-0', TONE[row.tone])}
        initial={{ scale: 0.4 }} animate={{ scale: 1 }} transition={spring.snap}
      ><Icon className="size-3.5" /></motion.span>
      <span className="min-w-0 truncate text-foreground">{row.text}</span>
      {row.detail && <span className={cn('min-w-0 shrink truncate text-[12px]', row.kind === 'test' ? TONE[row.tone] : 'text-muted-foreground')}>{row.detail}</span>}
    </motion.li>
  );
}

/** Seconds a round took, from the server's own timestamps (no clock skew): first to last event of the round. */
function serverMs(events: AiEvent[], round: number): number | null {
  const mine = events.filter((e) => e.payload.round === round).map((e) => Date.parse(e.at)).filter(Number.isFinite);
  return mine.length > 1 ? Math.max(...mine) - Math.min(...mine) : null;
}

function RoundItem({ log, last, finished, events }: { log: LogRound; last: boolean; finished?: boolean; events: AiEvent[] }) {
  const r = log.progress;
  const live = last && !finished && !r.done;
  const settled = r.done || finished || !last;
  return (
    <motion.li
      layout="position"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: dur.slow, ease: ease.out }}
      data-testid={`ai-build-round-${r.round}`}
      data-errors={r.errors ?? ''}
      data-passed={r.passed ?? ''}
      data-failed={r.failed ?? ''}
      className="flex items-start gap-2.5"
    >
      <span className="mt-0.5">
        {settled
          ? <motion.span initial={{ scale: 0.4 }} animate={{ scale: 1 }} transition={spring.snap} className="flex"><CheckCircle2 className={cn('size-3.5', r.failed ? 'text-warning-text' : 'text-success')} /></motion.span>
          : <Loader2 className="size-3.5 animate-spin text-primary" />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 text-[13px] font-medium">
          <span className="truncate">{r.round === 0 ? 'First attempt' : `Fixing problems, attempt ${r.round + 1}`}</span>
          {live && <span className="truncate font-normal text-muted-foreground">{stageOf(r)}</span>}
          <Elapsed
            running={live} frozenMs={settled ? serverMs(events, r.round) : null} decimals={1}
            className="ml-auto shrink-0 font-mono text-[11.5px] font-normal tabular-nums text-muted-foreground"
            data-testid={`ai-build-round-${r.round}-time`}
          />
        </div>
        <div className="text-[12.5px] leading-5 text-muted-foreground">{roundLine(r)}</div>
        {log.rows.length > 0 && (
          <ul className="-mx-1.5 mt-1 flex flex-col" data-testid={`ai-log-${r.round}`}>
            <AnimatePresence initial={false}>
              {log.rows.map((row) => <Row key={row.id} row={row} />)}
            </AnimatePresence>
          </ul>
        )}
      </div>
    </motion.li>
  );
}

/**
 * Step 3: the live step log, read from the proposal's events every 750 ms. One row per step the AI adds, changes or
 * removes, then the check and each test; hovering a row lights its step on the canvas. There is no Cancel in v1.
 */
export function BuildProgress({ events, finished, finishing }: { events: AiEvent[]; finished?: boolean; /** built, but the canvas is still playing it back */ finishing?: boolean }) {
  const base = useAiProposalStore((s) => s.proposal?.base_definition ?? null);
  const playing = useStageStore((s) => s.playback === 'playing');
  const gate = useGate();
  const stageLog = useStageStore((s) => s.log);
  const all = buildLog(events, base ? snapshotFromDefinition(base) : null);
  // gated to the canvas: a round the canvas has not reached is not shown; in the current round, only what it has built
  const log = gate
    ? all.filter((l) => l.round <= stageLog.round).map((l) => ({ ...l, rows: visibleRows(l.rows, l.round, stageLog) }))
    : all;
  const rounds = roundsFromEvents(events);
  const total = rounds.length;
  const progress = useStageStore((s) => s.progress);
  // the canvas is on the engine's last round and has built everything in it: only the tests and the tail are left
  const lastRound = Math.max(-1, ...rounds.map((r) => r.round));
  const tail = stageLog.round >= lastRound && (!progress || progress.done >= progress.total) && stageLog.round >= 0;
  const status = finishing
    ? tail ? 'Finishing on the canvas…' : `Building on the canvas${progress && progress.done < progress.total ? ` · ${progress.done} of ${progress.total}` : '…'}`
    : finished ? 'The build has finished.' : 'The AI is building your workflow and testing it. This can take up to a couple of minutes.';
  const end = useRef<HTMLDivElement>(null);
  const rows = log.reduce((n, l) => n + l.rows.length, 0);
  useEffect(() => {
    // scroll the sidebar's own scroller only: scrollIntoView would also scroll every ancestor, the page shell included
    const el = end.current;
    const box = el?.closest<HTMLElement>('.overflow-y-auto');
    if (el && box) box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' });
  }, [rows, total]);

  return (
    <div className="flex flex-col gap-3" data-testid="ai-build-progress" data-finished={finished ? 'true' : 'false'}>
      <Callout tone="ai" icon={finished && !finishing ? <CheckCircle2 /> : <Loader2 className="animate-spin" />}>
        <div className="flex items-center gap-2">
          <span className="min-w-0 flex-1">{status}</span>
          <Elapsed running={!finished || !!finishing} className="shrink-0 font-mono text-[12px] tabular-nums" data-testid="ai-build-timer" />
        </div>
      </Callout>
      {playing && (
        <div>
          <Button variant="ghost" size="sm" onClick={() => useStageStore.getState().requestSkip()} data-testid="ai-log-skip"><FastForward /> Skip the animation</Button>
        </div>
      )}
      <ol className="flex flex-col gap-3">
        {log.length === 0 && !finished && (
          <li className="flex flex-col gap-2" data-testid="ai-build-starting">
            <div className="flex items-center gap-2.5 text-[13px] font-medium">
              <Loader2 className="size-3.5 animate-spin text-primary" />
              <span>Starting the build…</span>
            </div>
            <div className="flex flex-col gap-1.5 pl-6" aria-hidden>
              {[72, 56, 64].map((w) => (
                <div key={w} className="h-3 animate-pulse rounded-chip bg-recess" style={{ width: `${w}%` }} />
              ))}
            </div>
          </li>
        )}
        {log.map((l, i) => <RoundItem key={l.round} log={l} last={i === log.length - 1} finished={finished} events={events} />)}
      </ol>
      <div ref={end} aria-hidden />
    </div>
  );
}
