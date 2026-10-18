import { useState } from 'react';
import { Link } from '@tanstack/react-router';
import { AlertTriangle, ArrowUpRight, CheckCircle2, ChevronDown, Loader2, Send, Undo2, XCircle } from 'lucide-react';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog';
import { Button } from '@/shared/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/components/ui/collapsible';
import { Field } from '@/shared/components/ui/field';
import { Input } from '@/shared/components/ui/input';
import { Callout, SectionHeader } from '@/shared/components/ui/surface';
import { Textarea } from '@/shared/components/ui/textarea';
import { failedBuildMessage } from '../api';
import type { BuilderBusy } from '../aiProposalStore';
import { reviewButtons } from '../lib/reviewActions';
import { diffSummaryText } from '../proposalMarks';
import type { Proposal, StoppedBecause } from '../types';
import { ReviewDiffList } from './ReviewDiffList';
import { ReviewFlags } from './ReviewFlags';
import { ReviewTests } from './ReviewTests';
import { TestChangeRequests } from './TestChangeRequests';
import { UsageBadge } from './UsageBadge';

const STOPPED: Record<StoppedBecause, string> = {
  passed: '',
  rounds_exhausted: 'The AI used all of its repair attempts.',
  deadline: 'The AI ran out of time.',
  call_budget: 'The AI used up its allowed number of attempts.',
  no_progress: 'The AI stopped because it kept making the same mistake.',
  ai_failed: 'The AI stopped part-way.',
};

function testCounts(p: Proposal) {
  const s = p.report?.tests?.summary;
  const total = s?.total ?? p.spec?.tests.length ?? 0;
  const failed = s ? s.failed + s.errored : 0;
  return { total, failed, passed: total - failed };
}

function SummaryLine({ proposal }: { proposal: Proposal }) {
  const r = proposal.report;
  const { total, failed } = testCounts(proposal);
  const passed = !!r?.passed;
  const attempts = r?.rounds.length ?? 0;
  const lead = passed
    ? `All ${total} ${total === 1 ? 'test passes' : 'tests pass'}.`
    : failed > 0
      ? `${failed} of ${total} ${total === 1 ? 'test is' : 'tests are'} failing.`
      : 'The result could not be fully tested.';
  const why = r && !passed ? STOPPED[r.stopped_because] : '';
  return (
    <Callout
      tone={passed ? 'ok' : 'warn'}
      icon={passed ? <CheckCircle2 /> : <AlertTriangle />}
      className="items-start"
      data-testid="ai-review-summary"
      data-passed={passed ? 'true' : 'false'}
    >
      <div className="font-medium">{lead} {diffSummaryText(proposal.diff)}.</div>
      <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5">
        <span>{attempts} {attempts === 1 ? 'attempt' : 'attempts'}</span>
        {why && <span>{why}</span>}
        <UsageBadge proposal={proposal} />
      </div>
    </Callout>
  );
}

function Findings({ proposal }: { proposal: Proposal }) {
  const r = proposal.report;
  if (!r) return null;
  const cov = r.spec_coverage ?? {};
  const lines: Array<{ tone: 'err' | 'warn'; text: string }> = [];
  for (const f of r.checker.findings) {
    if (f.severity === 'info') continue;
    lines.push({ tone: f.severity === 'error' ? 'err' : 'warn', text: f.node ? `${f.node}: ${f.message}` : f.message });
  }
  for (const g of r.grounding) lines.push({ tone: 'warn', text: g.node ? `${g.node}: ${g.message}` : g.message });
  for (const s of cov.steps_missing ?? []) lines.push({ tone: 'warn', text: `The plan listed "${s}" but it was not built.` });
  for (const s of cov.steps_wrong_type ?? []) lines.push({ tone: 'warn', text: `"${s}" was built as a different kind of step than planned.` });
  for (const id of cov.rules_untested ?? []) {
    const rule = proposal.spec?.rules.find((x) => x.id === id);
    lines.push({ tone: 'warn', text: `No test checks the rule "${rule?.text ?? id}".` });
  }
  const clean = lines.length === 0;
  return (
    <Collapsible defaultOpen={!clean} data-testid="ai-review-findings">
      <CollapsibleTrigger className="group/f flex w-full items-center gap-1.5 text-left text-[13px] font-semibold outline-none focus-visible:shadow-focus">
        <ChevronDown className="size-3.5 text-muted-foreground -rotate-90 transition-transform group-data-[panel-open]/f:rotate-0" />
        What was checked
        <span className="font-normal text-muted-foreground">
          {clean ? 'No mistakes or doubts found' : `${lines.length} ${lines.length === 1 ? 'thing' : 'things'} to look at`}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="mt-1.5 flex flex-col gap-1 pl-5 text-[12.5px] leading-[18px]">
          <li className="text-muted-foreground">The steps and their settings were checked for mistakes, then every test was run.</li>
          {lines.map((l, i) => (
            <li key={i} className={l.tone === 'err' ? 'text-destructive' : 'text-warning-text'}>{l.text}</li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function ReviewPanel({ proposal, projectSlug, busy, selectedNode, onSelectNode, onKeep, onDiscard, onRevise, onUndo, allowRevise = true }: {
  proposal: Proposal;
  projectSlug: string;
  busy: BuilderBusy;
  selectedNode: string | null;
  onSelectNode: (name: string) => void;
  onKeep: (opts: { acceptFailing?: boolean; name?: string }) => void;
  onDiscard: () => void;
  onRevise: (instruction: string) => void;
  onUndo: () => void;
  /** false for a one-node assist: there is no plan to revise, so only Keep or Discard are offered */
  allowRevise?: boolean;
}) {
  const [confirmRed, setConfirmRed] = useState(false);
  const [name, setName] = useState(proposal.spec?.title ?? '');
  const [revise, setRevise] = useState('');

  const status = proposal.status;
  const ready = status === 'ready';
  const passed = !!proposal.report?.passed;
  // a revision of a new workflow is a `modify` proposal with no workflow yet: Keep still creates one
  const isNew = proposal.workflow_id == null;
  const working = busy !== 'idle';
  const variants = reviewButtons(passed, allowRevise);
  const keepNow = () => (passed ? onKeep({ name: isNew ? name.trim() || undefined : undefined }) : setConfirmRed(true));

  return (
    <div className="flex flex-col gap-4" data-testid="ai-review-panel" data-status={status}>
      {status === 'failed' ? (
        <Callout tone="error" icon={<XCircle />} className="items-start" data-testid="ai-review-failed">
          <div className="font-medium">This build did not work out</div>
          <div>{failedBuildMessage(proposal.error_code)}</div>
          <div className="mt-1"><UsageBadge proposal={proposal} /></div>
        </Callout>
      ) : (
        <SummaryLine proposal={proposal} />
      )}

      {status === 'kept' && (
        <Callout tone="ok" icon={<CheckCircle2 />} data-testid="ai-kept">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">Kept{proposal.kept_by ? ` by ${proposal.kept_by}` : ''}.{proposal.accepted_failing ? ' Saved with failing tests, as you chose.' : ''}</span>
            {proposal.workflow_id && (
              <Link
                to="/p/$project/workflows/$workflowId" params={{ project: projectSlug, workflowId: proposal.workflow_id }}
                className="inline-flex items-center gap-1 text-primary hover:underline" data-testid="ai-open-workflow"
              >Open the workflow <ArrowUpRight className="size-3" /></Link>
            )}
            <Button variant="secondary" size="sm" className="ml-auto" disabled={working} onClick={onUndo} data-testid="ai-undo">
              {busy === 'undoing' ? <Loader2 className="animate-spin" /> : <Undo2 />} Undo
            </Button>
          </div>
        </Callout>
      )}
      {status === 'undone' && <Callout tone="info" icon={<Undo2 />} data-testid="ai-undone">This change was undone. The workflow is back to how it was.</Callout>}
      {status === 'discarded' && <Callout tone="info" data-testid="ai-discarded">This result was discarded. Nothing was saved.</Callout>}

      <section className="flex flex-col gap-1">
        <SectionHeader title="What changes" sub="Select a step to see it on the canvas" />
        <ReviewDiffList diff={proposal.diff} selected={selectedNode} onSelect={onSelectNode} />
      </section>

      <ReviewFlags proposal={proposal} />

      <section className="flex flex-col gap-1">
        <SectionHeader title="Tests" sub={proposal.spec?.tests.length ? 'You approved these' : undefined} />
        <ReviewTests proposal={proposal} />
      </section>

      <Findings proposal={proposal} />
      <TestChangeRequests proposal={proposal} onUseInRevise={setRevise} />

      {ready && (
        <div className="flex flex-col gap-3">
          {isNew && (
            <Field label="Name of the new workflow" htmlFor="ai-keep-name">
              <Input id="ai-keep-name" value={name} maxLength={255} onChange={(e) => setName(e.target.value)} data-testid="ai-keep-name" />
            </Field>
          )}
          <div className="flex items-center gap-2">
            {/* With a failing test the safe next step is to ask for a fix, so Keep steps back to secondary (the confirm dialog stays). */}
            <Button variant={variants.keep} onClick={keepNow} disabled={working} data-testid="ai-keep" data-primary={variants.keep === 'default' ? 'true' : 'false'}>
              {busy === 'keeping' ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}
              {isNew ? 'Keep and create the workflow' : 'Keep this change'}
            </Button>
            <Button variant="outline" onClick={onDiscard} disabled={working} data-testid="ai-discard">Discard</Button>
          </div>
          {!passed && <p className="text-[12px] leading-4 text-muted-foreground">Some tests are failing. {allowRevise ? 'Ask for a change below, or keep it anyway; you will be asked to confirm.' : 'You can still keep it, but you will be asked to confirm.'}</p>}

          {allowRevise && <Field label="Ask for a change" htmlFor="ai-revise" help="The AI will change this result and test it again. The approved tests stay as they are.">
            <Textarea
              id="ai-revise" rows={3} value={revise} disabled={working} onChange={(e) => setRevise(e.target.value)}
              placeholder="For example: also tell the finance team when a refund is rejected." data-testid="ai-revise-input"
            />
          </Field>}
          {allowRevise && <div>
            <Button
              variant={variants.revise} size="sm" disabled={!revise.trim() || working}
              onClick={() => onRevise(revise.trim())} data-testid="ai-revise-submit" data-primary={variants.revise === 'default' ? 'true' : 'false'}
            >
              {busy === 'revising' ? <Loader2 className="animate-spin" /> : <Send />}
              {busy === 'revising' ? 'Changing and testing…' : 'Ask for this change'}
            </Button>
          </div>}
        </div>
      )}

      {status === 'failed' && (
        <div><Button variant="outline" onClick={onDiscard} disabled={working} data-testid="ai-discard">Discard</Button></div>
      )}

      <AlertDialog open={confirmRed} onOpenChange={setConfirmRed}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Keep it with failing tests?</AlertDialogTitle>
            <AlertDialogDescription>
              Some of the tests you approved do not pass. If you keep this, it is saved anyway and the record shows that you accepted the failing tests.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Go back</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              data-testid="ai-keep-confirm-failing"
              onClick={() => onKeep({ acceptFailing: true, name: isNew ? name.trim() || undefined : undefined })}
            >Keep it anyway</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
