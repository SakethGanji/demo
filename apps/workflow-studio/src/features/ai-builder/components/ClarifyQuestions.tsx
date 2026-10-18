import { useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'motion/react';
import { Check, CircleHelp, Loader2, Plus, Sparkles } from 'lucide-react';
import { ConceptChip } from '@/features/knowledge/kit/ConceptChip';
import { Construct } from '@/features/knowledge/kit/Construct';
import { Button } from '@/shared/components/ui/button';
import { Callout, Tile } from '@/shared/components/ui/surface';
import { cn } from '@/shared/lib/utils';
import { motionOff } from '@/shared/motion/testFlag';
import { dur, ease, spring } from '@/shared/motion/tokens';
import type { Understanding, UnderstandingQuestion } from '../types';
import {
  answerPayload, answeredLine, hasAnswer, nextOpen, progressOf, roleHint, roleOptions, shownQuestions, slugOf,
} from '../lib/understanding';
import { QuestionControl } from './QuestionControl';
import { useStaggerIn } from './useStaggerIn';

/** The draft the panel keeps while the answers are sent, so a refused answer comes back to a form that is still filled in. */
export interface AnswerDraft { proposalId: string; values: Record<string, unknown> }
export interface AnswerError { qid: string; message: string }

const NUMBER_WORDS = ['Nothing', 'One thing', 'Two things', 'Three things', 'Four things'];
const SEAL_STAGGER_S = 0.04;
const SEAL_SEND_MS = 240;

/**
 * The questions the AI could not guess (PLAN-CAPGRAPH-UI §3.4). At most four blocking ones, one active at a time. Answering
 * a question locks its card, collapses it to one line and opens the next. Continue seals the cards and sends everything at once.
 */
export function ClarifyQuestions({ proposalId, understanding: u, busy, draft, error, onDraft, onSubmit, onStartOver }: {
  proposalId: string;
  understanding: Understanding;
  busy: boolean;
  draft: AnswerDraft | null;
  /** the engine refused one answer (422 answer_invalid) */
  error: AnswerError | null;
  onDraft: (values: Record<string, unknown>) => void;
  onSubmit: (answers: Record<string, unknown>) => void;
  onStartOver: () => void;
}) {
  const questions = useMemo(() => shownQuestions(u), [u]);
  const required = useMemo(() => questions.filter((q) => q.required), [questions]);
  const byId = useMemo(() => new Map(questions.map((q) => [q.id, q])), [questions]);

  const restored = draft?.proposalId === proposalId ? draft.values : {};
  const [values, setValues] = useState<Record<string, unknown>>(restored);
  const [settled, setSettled] = useState<Set<string>>(
    () => new Set(Object.keys(restored).filter((id) => byId.has(id) && hasAnswer(restored[id]) && id !== error?.qid)),
  );
  const [active, setActive] = useState<string | null>(() => error?.qid ?? nextOpen(required.map((q) => q.id), new Set(Object.keys(restored).filter((id) => hasAnswer(restored[id])))));
  const [locking, setLocking] = useState<string | null>(null);
  const [sealing, setSealing] = useState(false);
  const [err, setErr] = useState<AnswerError | null>(error);
  const root = useRef<HTMLDivElement>(null);
  const continueRef = useRef<HTMLButtonElement>(null);

  // a refused answer reopens its card
  const [seenError, setSeenError] = useState(error);
  if (error !== seenError) {
    setSeenError(error);
    if (error) {
      setErr(error);
      setSettled((s) => { const n = new Set(s); n.delete(error.qid); return n; });
      setActive(error.qid);
      setSealing(false);
    }
  }

  useStaggerIn(root, (el) => [...el.querySelectorAll('[data-slot="ai-question"]')], proposalId);

  const requiredIds = required.map((q) => q.id);
  const progress = progressOf(required, settled);
  const canContinue = progress.total > 0 ? progress.done === progress.total : false;

  const valuesRef = useRef(values);
  const setValue = (qid: string, v: unknown) => {
    const next = { ...valuesRef.current, [qid]: v };
    valuesRef.current = next;
    setValues(next);
    onDraft(next);
    if (err?.qid === qid) setErr(null);
  };

  const commit = (q: UnderstandingQuestion, v: unknown) => {
    const payload = answerPayload(q, v);
    if (!hasAnswer(payload)) return;
    setValue(q.id, payload);
    setLocking(q.id);
  };

  const settle = (qid: string) => {
    setLocking(null);
    const next = new Set(settled).add(qid);
    setSettled(next);
    setActive(nextOpen(requiredIds, next, qid));
  };

  // focus the active card's first control once it has settled in
  useEffect(() => {
    if (!active || sealing) return;
    const card = root.current?.querySelector<HTMLElement>(`[data-qid="${CSS.escape(active)}"]`);
    if (!card) return;
    const t = window.setTimeout(() => {
      card.querySelector<HTMLElement>('[data-autofocus]')?.focus({ preventScroll: true });
      card.scrollIntoView({ block: 'nearest', behavior: motionOff() ? 'auto' : 'smooth' });
    }, motionOff() ? 0 : 260);
    return () => window.clearTimeout(t);
  }, [active, sealing]);

  // the last answer in: the way forward is the button
  useEffect(() => {
    if (!canContinue) return;
    const t = window.setTimeout(() => continueRef.current?.focus({ preventScroll: true }), motionOff() ? 0 : 320);
    return () => window.clearTimeout(t);
  }, [canContinue]);

  const send = () => {
    const out: Record<string, unknown> = {};
    for (const q of questions) {
      const v = values[q.id];
      if (settled.has(q.id) && hasAnswer(v)) out[q.id] = v;
    }
    onSubmit(out);
  };

  const submit = () => {
    if (!canContinue || busy || sealing) return;
    if (motionOff()) { send(); return; }
    setSealing(true);
    window.setTimeout(send, SEAL_SEND_MS);
  };

  // the button gets one lock pulse the moment it becomes available
  const [pulse, setPulse] = useState(false);
  const [wasReady, setWasReady] = useState(canContinue);
  if (wasReady !== canContinue) {
    setWasReady(canContinue);
    if (canContinue) setPulse(true);
  }

  const conceptLabel = (key: string) => u.concepts.find((c) => c.key === key)?.label;
  const heading = progress.total > 0 ? `${NUMBER_WORDS[Math.min(progress.total, 4)]} to settle first` : 'A choice to make';
  const sub = u.assumptions.length > 0
    ? 'Everything else is assumed below. You can change it in the plan.'
    : 'You can change anything else in the plan.';

  return (
    <div ref={root} className="flex flex-col gap-3" data-testid="ai-clarify" data-open={progress.total - progress.done}>
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline gap-2">
          <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-foreground">{heading}</h3>
          <div className="flex-1" />
          {progress.total > 0 && (
            <span className="text-[12px] tabular-nums text-muted-foreground" data-testid="ai-clarify-count">{progress.done} of {progress.total} answered</span>
          )}
        </div>
        {progress.total > 0 && (
          <div
            className="h-1 w-full overflow-hidden rounded-full bg-recess"
            role="progressbar" aria-valuemin={0} aria-valuemax={progress.total} aria-valuenow={progress.done}
            data-testid="ai-clarify-progress" data-done={progress.done} data-total={progress.total}
          >
            <motion.div className="h-full w-full origin-left rounded-full bg-primary" initial={false} animate={{ scaleX: progress.ratio }} transition={spring.firm} />
          </div>
        )}
        <p className="text-[12.5px] text-muted-foreground">{sub}</p>
      </div>

      <div className="flex flex-col gap-2">
        {questions.map((q, i) => {
          const collapsed = settled.has(q.id) && locking !== q.id;
          const isActive = active === q.id && !collapsed;
          const value = values[q.id];
          const failed = err?.qid === q.id ? err : null;
          const label = conceptLabel(q.concept);
          const roles = q.kind === 'role' ? roleOptions(q, u) : [];
          const selectedRole = typeof value === 'string' ? value : null;
          const hint = q.kind === 'role' ? roleHint(selectedRole ?? roles[0]?.key, u) : null;
          const n = q.required ? requiredIds.indexOf(q.id) + 1 : null;
          return (
            <motion.div
              key={q.id}
              data-slot="ai-question"
              data-qid={q.id}
              animate={failed ? { x: [0, -4, 4, -2, 0] } : { x: 0 }}
              transition={failed ? { duration: dur.shake, ease: ease.out } : { duration: 0 }}
            >
              <Construct
                mode={sealing && q.required ? 'lock' : locking === q.id ? 'lock' : 'idle'}
                delay={sealing ? Math.max(0, requiredIds.indexOf(q.id)) * SEAL_STAGGER_S : 0}
                onDone={() => { if (locking === q.id) settle(q.id); }}
              >
                <Tile
                  className={cn('relative flex flex-col gap-2 p-3', collapsed && 'cursor-pointer py-2')}
                  data-testid={`ai-question-${q.id}`}
                  data-state={collapsed ? 'answered' : isActive ? 'active' : 'open'}
                  data-required={q.required ? 'true' : 'false'}
                  data-kind={q.kind}
                  onClick={collapsed ? () => { setSettled((s) => { const x = new Set(s); x.delete(q.id); return x; }); setActive(q.id); } : undefined}
                  onKeyDown={collapsed ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSettled((s) => { const x = new Set(s); x.delete(q.id); return x; }); setActive(q.id); } } : undefined}
                  role={collapsed ? 'button' : undefined}
                  tabIndex={collapsed ? 0 : undefined}
                  aria-label={collapsed ? `Change your answer: ${answeredLine(q, value, label)}` : undefined}
                >
                  {/* the active card wears a primary frame; a refused one flashes the warning frame once */}
                  <motion.span
                    aria-hidden
                    className="pointer-events-none absolute inset-0 rounded-panel shadow-[inset_0_0_0_1.5px_var(--primary)]"
                    initial={false}
                    animate={{ opacity: isActive && !failed ? 1 : 0 }}
                    transition={{ duration: dur.base, ease: ease.out }}
                  />
                  {failed && (
                    <motion.span
                      key={failed.message}
                      aria-hidden
                      className="pointer-events-none absolute inset-0 rounded-panel shadow-[inset_0_0_0_1.5px_var(--warning)]"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: [0, 1, 0] }}
                      transition={{ duration: 0.5, ease: ease.io }}
                    />
                  )}

                  {collapsed ? (
                    <div className="flex min-w-0 items-center gap-2 text-[12.5px]">
                      <Check className="size-3.5 shrink-0 text-success-text" aria-hidden />
                      <span className="min-w-0 flex-1 truncate text-foreground" data-testid={`ai-question-answer-${q.id}`}>{answeredLine(q, value, label)}</span>
                      <span className="text-[12px] text-muted-foreground">Change</span>
                    </div>
                  ) : (
                    <>
                      <div className="flex min-w-0 items-center gap-1.5 text-[12px] text-muted-foreground">
                        {q.required ? <span className="tabular-nums">{n}</span> : <CircleHelp className="size-3" aria-hidden />}
                        <span aria-hidden>·</span>
                        {q.concept ? (
                          <ConceptChip conceptKey={q.concept} label={label ?? slugOf(q.concept)} tone="confirmed" size="xs" />
                        ) : (
                          <span>{q.required ? 'Question' : 'Optional'}</span>
                        )}
                        {!q.required && q.concept && <span>Optional</span>}
                      </div>
                      <p className="text-[13px] font-semibold leading-5 text-foreground" id={`ai-q-text-${i}`}>{q.text}</p>
                      <QuestionControl
                        q={q}
                        value={value}
                        onDraft={(v) => setValue(q.id, v)}
                        onCommit={(v) => commit(q, v)}
                        roles={roles}
                        invalid={!!failed}
                        hint={hint}
                      />
                      {failed && (
                        <div className="text-[12px] leading-4 text-destructive-text" role="alert" data-testid={`ai-question-error-${q.id}`}>{failed.message}</div>
                      )}
                    </>
                  )}
                  {collapsed && !q.required && value === 'app' && (
                    <Callout tone="info" icon={<Sparkles />} data-testid="ai-app-note-answer">
                      It will be built as an app: a set of parts you approve and build one by one, then keep together.
                    </Callout>
                  )}
                </Tile>
              </Construct>
            </motion.div>
          );
        })}
      </div>

      {u.assumptions.length > 0 && (
        <div className="flex flex-col gap-1" data-testid="ai-assumptions">
          <div className="text-[12.5px] font-medium text-foreground">Assumed</div>
          <ul className="flex flex-col gap-0.5 text-[12px] leading-4 text-muted-foreground">
            {u.assumptions.map((a) => <li key={a} className="flex gap-1.5"><span aria-hidden>·</span><span>{a}</span></li>)}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Construct mode={pulse ? 'lock' : 'idle'} onDone={() => setPulse(false)}>
          <Button ref={continueRef} variant="ai" onClick={submit} disabled={!canContinue || busy || sealing} data-testid="ai-clarify-continue">
            {busy || sealing ? <Loader2 className="animate-spin" /> : <Sparkles />}
            Continue to the plan
          </Button>
        </Construct>
        <span className="text-[12px] text-muted-foreground">{progress.done} of {progress.total} answered</span>
      </div>
      <div>
        <Button variant="ghost" size="sm" onClick={onStartOver} disabled={busy || sealing} data-testid="ai-clarify-start-over"><Plus /> Start a new request</Button>
      </div>
    </div>
  );
}
