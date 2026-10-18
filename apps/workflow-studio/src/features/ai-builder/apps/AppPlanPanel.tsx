import { useMemo, useState } from 'react';
import { ArrowRight, Check, Loader2, Play, Plus } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Callout, Tile } from '@/shared/components/ui/surface';
import { cn } from '@/shared/lib/utils';
import { AiError } from '../components/AiError';
import { ClarifyQuestions, type AnswerDraft } from '../components/ClarifyQuestions';
import { ThinkingBlock } from '../components/ThinkingBlock';
import { UnderstandingCard } from '../components/UnderstandingCard';
import { AdaptedFromBanner } from './AdaptedFromBanner';
import { AppKeepBar } from './AppKeepBar';
import { AppMiniMap } from './AppMiniMap';
import { AppPartList, ROW_GAP_S } from './AppPartList';
import { approvableSpecs, progressOf, stepOf, summaryLine, type AppStep } from './lib/appState';
import { useAppBuild, type AppHost } from './useAppBuild';

const AFTER_PLAN: AppStep[] = ['specs', 'build', 'keep', 'done'];
const AFTER_SPECS: AppStep[] = ['build', 'keep', 'done'];
const AFTER_BUILD: AppStep[] = ['keep', 'done'];

/**
 * The app plan (PLAN-CAPGRAPH-UI §4), shown in the Build tab when the request is an application. One panel for the whole walk:
 * answers, the plan with its parts and map, approve the plan, approve each part's plan, build the parts one by one (the canvas
 * follows the part being built; click a part to see it), then Keep or Discard the whole app.
 */
export function AppPlanPanel({ projectId, projectSlug, host, onStartOver }: {
  projectId: string;
  projectSlug: string;
  host: AppHost;
  /** back to the request screen */
  onStartOver: () => void;
}) {
  const a = useAppBuild(projectId, host);
  const { app, busy } = a;
  const [draft, setDraft] = useState<AnswerDraft | null>(null);

  const conceptLabels = useMemo(
    () => Object.fromEntries((app?.understanding?.concepts ?? []).map((c) => [c.key, c.label])),
    [app?.understanding],
  );

  const step: AppStep | null = app ? stepOf(app) : null;
  const buildingKey = busy === 'building' ? (app?.parts.find((p) => p.status === 'building')?.key ?? null) : null;
  const working = busy !== 'idle';
  const { built, total } = app ? progressOf(app.parts) : { built: 0, total: 0 };
  const planErrors = (app?.plan_issues ?? []).filter((i) => (i.severity ?? 'error') === 'error');

  return (
    <div className="flex flex-col gap-4" data-testid="ai-app-plan" data-status={app?.status ?? 'loading'} data-step={step ?? 'loading'} data-busy={busy}>
      <AiError error={a.error} />

      {!app && !a.error && <ThinkingBlock request={host.request} />}

      {app?.understanding && (
        <UnderstandingCard
          proposalId={app.id} projectId={projectId} projectSlug={projectSlug} understanding={app.understanding}
          compact={app.status !== 'clarify'} sealed={busy === 'answering'}
        />
      )}

      {app && app.status === 'clarify' && app.understanding && (
        <ClarifyQuestions
          proposalId={app.id} understanding={app.understanding} busy={busy === 'answering'} draft={draft} error={a.answerError}
          onDraft={(values) => setDraft({ proposalId: app.id, values })}
          onSubmit={(answers) => void a.answer(answers)} onStartOver={() => void a.discard().then(onStartOver)}
        />
      )}

      {app && app.status !== 'clarify' && app.plan && (
        <>
          <Tile className="flex flex-col gap-3 p-3">
            <div className="flex flex-col gap-0.5">
              <h2 className="text-[15px] leading-[20px] font-semibold tracking-[-0.01em]" data-testid="ai-app-title">{app.plan.title}</h2>
              <p className="text-[12px] text-muted-foreground" data-testid="ai-app-summary">{summaryLine(app.plan)}</p>
            </div>
            <AdaptedFromBanner adapted={app.plan.adapted_from ?? app.adapted_from} />
            <AppMiniMap plan={app.plan} parts={app.parts} startAt={app.parts.length * ROW_GAP_S * 0.6} />
          </Tile>

          {planErrors.length > 0 && (
            <Callout tone="warn" data-testid="ai-app-plan-issues">
              {planErrors.slice(0, 3).map((i) => <p key={`${i.code}-${i.path ?? ''}`}>{i.message}</p>)}
            </Callout>
          )}

          <AppPartList app={app} projectSlug={projectSlug} conceptLabels={conceptLabels} selected={a.selected} buildingKey={buildingKey} onSelect={a.selectPart} />

          {(step === 'plan' || step === 'specs' || step === 'build' || step === 'keep' || step === 'done') && (
            <div className="flex flex-col gap-1.5">
              <div className="flex flex-wrap items-center gap-1.5" data-testid="ai-app-steps">
                <Button
                  variant={step === 'plan' ? 'default' : 'outline'} disabled={step !== 'plan' || working}
                  onClick={() => void a.approvePlan()} data-testid="ai-app-approve-plan" data-done={AFTER_PLAN.includes(step) ? 'true' : undefined}
                >
                  {busy === 'approving' ? <Loader2 className="animate-spin" /> : AFTER_PLAN.includes(step) ? <Check /> : null} Approve plan
                </Button>
                <ArrowRight className="size-3.5 text-faint" aria-hidden />
                <Button
                  variant={step === 'specs' ? 'default' : 'outline'} disabled={step !== 'specs' || working || approvableSpecs(app).length === 0}
                  onClick={() => void a.approveAllSpecs()} data-testid="ai-app-approve-all-specs" data-done={AFTER_SPECS.includes(step) ? 'true' : undefined}
                >
                  {busy === 'specs' ? <Loader2 className="animate-spin" /> : AFTER_SPECS.includes(step) ? <Check /> : null} Approve all part plans
                </Button>
                <ArrowRight className="size-3.5 text-faint" aria-hidden />
                <Button
                  variant={step === 'build' ? 'default' : 'outline'} disabled={step !== 'build' || working}
                  onClick={() => void a.buildAll()} data-testid="ai-app-build" data-done={AFTER_BUILD.includes(step) ? 'true' : undefined}
                >
                  {busy === 'building' ? <Loader2 className="animate-spin" /> : AFTER_BUILD.includes(step) ? <Check /> : <Play />}
                  {busy === 'building' ? `Building ${Math.min(total, built + 1)} of ${total}` : 'Build app'}
                </Button>
              </div>
              {busy === 'approving' && <p className="text-[12px] text-muted-foreground" role="status">Writing a plan for each part. This can take a minute.</p>}
              {step === 'specs' && busy === 'idle' && <p className="text-[12px] text-muted-foreground">Click a part to see its plan on the canvas, then approve them all.</p>}
            </div>
          )}

          <AppKeepBar app={app} projectSlug={projectSlug} busy={busy} onKeep={(accept) => void a.keep(accept)} onDiscard={() => void a.discard()} />

          {(app.status === 'kept' || app.status === 'discarded') && (
            <div>
              <Button variant="ghost" size="sm" onClick={onStartOver} className={cn('mt-0.5')} data-testid="ai-app-start-over"><Plus /> Start a new request</Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
