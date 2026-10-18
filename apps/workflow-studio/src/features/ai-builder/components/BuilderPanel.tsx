import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Play, Plus } from 'lucide-react';
import { motionOff } from '@/shared/motion/testFlag';
import { Button } from '@/shared/components/ui/button';
import { Callout, SectionHeader } from '@/shared/components/ui/surface';
import { cn } from '@/shared/lib/utils';
import { dur, ease, spring } from '@/shared/motion/tokens';
import { aiBuilderApi } from '../api';
import { buildFinished, specIsDirty, useAiProposalStore } from '../aiProposalStore';
import { changesExistingWorkflow } from '../lib/proposalScope';
import { useAiBuilder } from '../useAiBuilder';
import { blockOf, buildStateOf, phaseOfState, phasesFor, type Phase } from '../lib/buildState';
import { answeredAnswers, openQuestions } from '../lib/understanding';
import { hoverNodeFromTestIds } from '../lib/hoverTarget';
import { endStage } from '../lib/stageBridge';
import { useStageStore } from '../stage/stageStore';
import { useStageDriver } from '../useStageDriver';
import type { Proposal, ProposalSummary } from '../types';
import { useActorName } from '../useActorName';
import { AppPlanPanel } from '../apps/AppPlanPanel';
import { isAppProposal } from '../apps/lib/appState';
import { ActorPrompt } from './ActorPrompt';
import { AiError } from './AiError';
import { BuildProgress } from './BuildProgress';
import { ClarifyQuestions, type AnswerDraft } from './ClarifyQuestions';
import { ProposalHistory } from './ProposalHistory';
import { RequestBox } from './RequestBox';
import { ThinkingBlock } from './ThinkingBlock';
import { UnderstandingCard } from './UnderstandingCard';
import { useStaggerIn } from './useStaggerIn';
import { ReviewPanel } from './ReviewPanel';
import { SpecEditor } from './SpecEditor';

function PhaseBar({ phase, phases }: { phase: Phase; phases: Array<{ id: Phase; label: string }> }) {
  const PHASES = phases;
  const at = PHASES.findIndex((p) => p.id === phase);
  return (
    <ol className="flex items-center gap-0.5 text-[12px]" aria-label="Progress" data-testid="ai-phases" data-phase={phase}>
      {PHASES.map((p, i) => (
        <li key={p.id} className={cn('relative flex items-center gap-0.5', i > at ? 'text-faint' : i === at ? 'font-semibold text-foreground' : 'text-muted-foreground')}>
          {i > 0 && <span aria-hidden className="text-faint">/</span>}
          <span className="relative px-1.5 py-0.5" aria-current={i === at ? 'step' : undefined}>
            {i === at && (
              <motion.span layoutId="ai-phase-pill" className="absolute inset-0 rounded-chip bg-(--hover-fill)" transition={spring.soft} />
            )}
            <span className="relative">{p.label}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

const slide = {
  initial: { opacity: 0, x: 12 },
  animate: { opacity: 1, x: 0, transition: { duration: dur.base, ease: ease.out } },
  // no exit animation: the old block must be gone before the next paints, or the sidebar shows an empty or ghosted frame
  exit: { opacity: 0, transition: { duration: 0 } },
};

/**
 * The AI builder, start to finish (DESIGN-STAGE6 §9.2), in the editor's Build tab. It follows one proposal through
 * ask, thinking, plan, building, review and kept / discarded / undone (`data-build-state`), and drives the canvas stage.
 */
export function BuilderPanel({ projectId, projectSlug, workflowId, resolveWorkflowId, proposalId, onOpenProposal, beforeRequest, className }: {
  projectId: string;
  projectSlug: string;
  /** changing this workflow (modify) instead of building a new one */
  workflowId?: string | null;
  /** the workflow to change at the moment a request is sent (a new workflow may have been saved by `beforeRequest`) */
  resolveWorkflowId?: () => string | null | undefined;
  /** the proposal on screen; none = the request screen */
  proposalId?: string | null;
  onOpenProposal: (id: string) => void;
  /** runs before a request is sent; false cancels it (the editor saves its unsaved edits here) */
  beforeRequest?: () => Promise<boolean>;
  className?: string;
}) {
  const actor = useActorName();
  const flow = useAiBuilder(projectId);
  const proposal = useAiProposalStore((s) => s.proposal);
  const specDraft = useAiProposalStore((s) => s.specDraft);
  const dirty = useAiProposalStore(specIsDirty);
  const events = useAiProposalStore((s) => s.events);
  const busy = useAiProposalStore((s) => s.busy);
  const error = useAiProposalStore((s) => s.error);
  const focusNode = useAiProposalStore((s) => s.focusNode);
  const { editSpec, setFocusNode, reset } = useAiProposalStore.getState();
  const config = useQuery({ queryKey: ['ai-builder-config'], queryFn: aiBuilderApi.config, staleTime: 60_000 });
  const notConfigured = config.data && !config.data.configured;
  const pendingRequest = useAiProposalStore((s) => s.pendingRequest);

  const leaving = useRef<string | null>(null);

  // follow the proposal named by the page
  useEffect(() => {
    const cur = useAiProposalStore.getState().proposal;
    if (!proposalId) {
      leaving.current = null;
      if (cur) reset();
      return;
    }
    if (cur?.id === proposalId) return;
    reset();
    void flow.load(proposalId);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load/reset are stable; only a new proposal id restarts this
  }, [proposalId]);

  // a page reload during a build: keep watching until it ends
  const status = proposal?.status;
  const id = proposal?.id;
  useEffect(() => {
    if (status === 'building' && busy === 'idle' && id) flow.startPolling(id, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only a status change should (re)start watching
  }, [status, id]);

  // the server can finish long before the canvas has played the build back: stay in `building` until playback is done
  const canvasPlaying = useStageStore((s) => s.active && s.mode === 'building' && s.playback !== 'done') && !!proposal?.proposed_definition;
  const state = buildStateOf(proposal, busy, canvasPlaying);
  const block = blockOf(state);
  const phase = phaseOfState(state, status);
  const phases = phasesFor(proposal, state);
  // `leaving`: the proposal the person just walked away from (Start a new request) is not being loaded again while the URL catches up
  const loading = !proposal && !!proposalId && !error && proposalId !== leaving.current;

  // An application is many proposals, one per part. Once the request on screen is an app, the app panel owns the sidebar until the
  // person starts over; it puts the part being built (or opened) in the store, and the stage follows that part.
  const [appCtx, setAppCtx] = useState<{ hostId: string; request: string; appBuildId: string | null; answers: Record<string, unknown> } | null>(null);
  if (!appCtx && proposal && isAppProposal(proposal)) {
    setAppCtx({ hostId: proposal.id, request: proposal.request, appBuildId: proposal.app_build_id ?? null, answers: answeredAnswers(proposal.understanding) });
  }
  useEffect(() => {
    // another request opened (or none): the app panel is done
    if (appCtx && proposalId !== appCtx.hostId) setAppCtx(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only a change of the proposal named by the page ends the app
  }, [proposalId]);
  useStageDriver({ state, proposal, spec: specDraft, loading, appMode: !!appCtx });

  // waiting for answers: the AI marker on the canvas says so (the label is cleared the moment the state moves on)
  const open = openQuestions(proposal?.understanding).length;
  useEffect(() => {
    if (state !== 'clarify' || !open || !useStageStore.getState().active) return;
    useStageStore.getState().setPresence({ visible: true, label: `waiting for ${open} answer${open === 1 ? '' : 's'}` });
    return () => useStageStore.getState().setPresence({ label: null });
  }, [state, open]);

  // the answers typed so far live here, so a refused answer comes back to a form that is still filled in
  const answerDraft = useRef<AnswerDraft | null>(null);
  const startOverFromClarify = async () => {
    leaving.current = useAiProposalStore.getState().proposal?.id ?? null
    if (await flow.discard()) startOver();
  };
  const jumpToQuestions = () => {
    scroller.current?.querySelector('[data-testid="ai-clarify"]')?.scrollIntoView({ block: 'start', behavior: motionOff() ? 'auto' : 'smooth' });
  };

  // a new block starts at the top (see onExitComplete): the plan's scroll offset would otherwise leave a shorter block scrolled out of view, a blank sidebar
  const scroller = useRef<HTMLDivElement>(null);

  const planRef = useRef<HTMLDivElement>(null);
  useStaggerIn(planRef, (el) => [...el.querySelectorAll('[data-testid="ai-spec-panel"] > *')], block === 'plan' ? proposal?.id : null);
  const reviewRef = useRef<HTMLDivElement>(null);
  useStaggerIn(reviewRef, (el) => [...el.querySelectorAll('[data-testid="ai-review-panel"] > *, [data-testid="ai-review-diff-list"] li')], block === 'review' ? proposal?.id : null);

  // hovering a plan step or a changed step in the review lights it on the canvas
  const hoverFrom = (e: React.MouseEvent) => {
    const t = e.target as HTMLElement;
    const ids: string[] = [];
    for (let el: HTMLElement | null = t; el; el = el.parentElement) {
      const id = el.dataset.testid;
      if (id) ids.push(id);
    }
    const name = hoverNodeFromTestIds(ids, specDraft?.steps.map((st) => st.name) ?? []);
    // rows that light their own step (the build log) set the hover themselves: do not undo it
    if (!name && t.closest('[data-testid="ai-log-row"]')) return;
    useStageStore.getState().setHoverNode(name ?? null);
  };

  const submitRequest = async (text: string) => {
    if (beforeRequest && !(await beforeRequest())) return;
    const p = await flow.draftSpec(text, resolveWorkflowId ? resolveWorkflowId() : workflowId);
    if (p) onOpenProposal(p.id);
  };

  const keep = async (opts: { acceptFailing?: boolean; name?: string }) => {
    const r = await flow.keep(opts);
    if (!r) return;
    const pid = r.proposal.id;
    toast.success('Change kept', {
      description: proposal?.workflow_id == null ? 'The new workflow has been created.' : 'The workflow has been updated.',
      action: { label: 'Undo', onClick: () => void flow.undo(pid).then((p) => { if (p) toast.success('Change undone'); }) },
      duration: 12_000,
    });
  };

  const revise = async (instruction: string) => {
    const p = await flow.revise(instruction);
    if (p) onOpenProposal(p.id);
  };

  const openFromHistory = (p: ProposalSummary) => onOpenProposal(p.id);

  const startOver = () => {
    leaving.current ??= useAiProposalStore.getState().proposal?.id ?? null; // read from the store: a handler held by the animated block can be a render old
    endStage();
    reset();
    setAppCtx(null);
    onOpenProposal('new');
  };

  return (
    <div
      className={cn('flex min-h-0 flex-1 flex-col', className)}
      data-testid="ai-builder-panel"
      data-status={status ?? 'new'}
      data-build-state={state}
      data-shape={appCtx ? 'app' : undefined}
    >
      <div className="flex shrink-0 flex-col gap-2 px-4 pb-2 pt-3">
        {!appCtx && <PhaseBar phase={phase} phases={phases} />}
        {proposal && !appCtx && <div className="text-[12px] text-muted-foreground">Asked by {proposal.actor}{changesExistingWorkflow(proposal) ? ' · change to an existing workflow' : ' · new workflow'}</div>}
        {appCtx && <div className="text-[12px] text-muted-foreground">Asked by {actor} · new application</div>}
      </div>

      <div ref={scroller} className="relative flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4 pt-1" onMouseOver={hoverFrom} onMouseLeave={() => useStageStore.getState().setHoverNode(null)}>
        {!actor ? (
          <ActorPrompt />
        ) : (
          <>
            {notConfigured && (
              <Callout tone="warn" data-testid="ai-not-configured">AI is not set up on this system yet, so it cannot write plans. Ask an administrator to connect it.</Callout>
            )}
            <AiError error={error} />

            {appCtx && (
              <AppPlanPanel
                key={appCtx.hostId}
                projectId={projectId}
                projectSlug={projectSlug}
                host={{ id: appCtx.hostId, request: appCtx.request, appBuildId: appCtx.appBuildId, answers: appCtx.answers }}
                onStartOver={startOver}
              />
            )}

            {!appCtx && proposal?.understanding && (
              <UnderstandingCard
                proposalId={proposal.id}
                projectId={projectId}
                projectSlug={projectSlug}
                understanding={proposal.understanding}
                compact={!(block === 'clarify' || block === 'plan' || (block === 'thinking' && status === 'clarify'))}
                sealed={block === 'thinking' && status === 'clarify'}
                onJumpToQuestions={block === 'clarify' ? jumpToQuestions : undefined}
              />
            )}

            {!appCtx && <AnimatePresence mode="wait" initial={false} onExitComplete={() => scroller.current?.scrollTo({ top: 0 })}>
              {/* the build block paints at once (no fade-in): the canvas is already busy starting the stage, so an opacity-0 first frame reads as a blank sidebar */}
              <motion.div key={block} className="flex flex-col gap-4" {...slide} initial={block === 'building' ? false : slide.initial}>
                {block === 'ask' && (
                  <>
                    {!proposal && (!proposalId || proposalId === leaving.current) && (
                      <>
                        <RequestBox projectId={projectId} modify={!!workflowId} busy={false} disabled={!!notConfigured} initialText={pendingRequest ?? ''} onSubmit={submitRequest} />
                        <section className="flex flex-col gap-1">
                          <SectionHeader title="Earlier changes" sub={workflowId ? 'To this workflow' : 'In this project'} />
                          <ProposalHistory projectId={projectId} workflowId={workflowId} onOpen={openFromHistory} />
                        </section>
                      </>
                    )}
                    {loading && (
                      <div className="flex items-center gap-2 text-[12.5px] text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> Loading…</div>
                    )}
                    {!proposal && proposalId && error?.code === 'not_found' && (
                      <div><Button variant="outline" size="sm" onClick={startOver}><Plus /> Start a new request</Button></div>
                    )}
                  </>
                )}

                {block === 'thinking' && <ThinkingBlock request={pendingRequest ?? proposal?.request ?? null} revising={busy === 'revising'} />}

                {block === 'clarify' && proposal?.understanding && (
                  <ClarifyQuestions
                    proposalId={proposal.id}
                    understanding={proposal.understanding}
                    busy={busy === 'drafting'}
                    draft={answerDraft.current}
                    error={flow.answerError}
                    onDraft={(values) => { answerDraft.current = { proposalId: proposal.id, values }; }}
                    onSubmit={(answers) => void flow.answer(answers)}
                    onStartOver={() => void startOverFromClarify()}
                  />
                )}

                {block === 'plan' && proposal && specDraft && (
                  <div ref={planRef} className="flex flex-col gap-4">
                    <SpecEditor
                      spec={specDraft}
                      issues={(proposal.spec?.issues ?? [])}
                      warnings={proposal.spec?.warnings ?? []}
                      projectId={projectId}
                      edit={editSpec}
                      readOnly={status !== 'spec_draft'}
                      approved={status === 'spec_approved'}
                      saving={busy === 'saving' || dirty}
                      busy={busy === 'approving'}
                      onApprove={() => void flow.approveAndBuild()}
                    />
                    {status === 'spec_approved' && busy !== 'approving' && (
                      <div className="flex flex-col gap-2">
                        <Callout tone="info">The plan is approved and its tests are locked. It has not been built yet.</Callout>
                        <div><Button onClick={() => void flow.build()} data-testid="ai-build-start"><Play /> Build it now</Button></div>
                      </div>
                    )}
                  </div>
                )}

                {block === 'building' && proposal && <BuildProgress events={events} finished={buildFinished(events)} finishing={canvasPlaying} />}

                {block === 'review' && proposal && (
                  <div ref={reviewRef} className="flex flex-col gap-4">
                    <ReviewPanel
                      key={proposal.id}
                      proposal={proposal as Proposal}
                      projectSlug={projectSlug}
                      busy={busy}
                      selectedNode={focusNode}
                      onSelectNode={(n) => setFocusNode(n)}
                      onKeep={(o) => void keep(o)}
                      onDiscard={() => void flow.discard()}
                      onRevise={(t) => void revise(t)}
                      onUndo={() => void flow.undo()}
                    />
                    {['undone', 'discarded', 'failed', 'kept'].includes(proposal.status) && (
                      <div><Button variant="ghost" size="sm" onClick={startOver} data-testid="ai-start-over"><Plus /> Start a new request</Button></div>
                    )}
                  </div>
                )}
              </motion.div>
            </AnimatePresence>}
          </>
        )}
      </div>
    </div>
  );
}
