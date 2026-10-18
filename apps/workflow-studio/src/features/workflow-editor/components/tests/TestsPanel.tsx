import { useMemo } from 'react';
import {
  CheckCircle2, CircleDashed, FlaskConical, Lock, Loader2, Plus, Play, Sparkles, Target, X, XCircle, AlertTriangle, LayoutGrid, List, Check,
} from 'lucide-react';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useNodeLabels } from '../../hooks/useNodeLabels';
import { useTestStore } from '../../stores/testStore';
import {
  CATEGORY_LABELS, barText, failureDetail, failureMessage, newTestCase, resultFor, rowStatus, type Failure, type TestCase, type TestRowStatus,
} from '../../lib/tests';
import {
  acceptSuggestion, fixUnmocked, openNodeByName, runTests, scoreWorkflow, suggestTests,
} from '../../lib/testActions';
import ScorecardView from './ScorecardView';
import CoverageGrid from './CoverageGrid';
import TestEditor from './TestEditor';
import { Button } from '@/shared/components/ui/button';
import { Badge } from '@/shared/components/ui/badge';
import { Segmented } from '@/shared/components/ui/segmented';
import { cn } from '@/shared/lib/utils';

function targetText(t: TestCase): string {
  if (t.target.kind === 'node') return t.target.node;
  if (t.target.kind === 'range') return `${t.target.from} to ${t.target.to}`;
  return 'Whole workflow';
}

function StatusIcon({ status }: { status: TestRowStatus }) {
  if (status === 'passed') return <CheckCircle2 size={14} className="shrink-0 text-success" />;
  if (status === 'failed') return <XCircle size={14} className="shrink-0 text-destructive" />;
  if (status === 'error') return <AlertTriangle size={14} className="shrink-0 text-destructive" />;
  return <CircleDashed size={14} className="shrink-0 text-muted-foreground" />;
}

function FailureRow({ failure }: { failure: Failure }) {
  const unmocked = failure.kind === 'unmocked' && !!failure.node;
  const labels = useNodeLabels();
  const detail = failureDetail(failure);
  return (
    <div
      role="button"
      tabIndex={0}
      data-testid="test-failure"
      data-node={failure.node ?? ''}
      data-kind={failure.kind}
      onClick={() => failure.node && openNodeByName(failure.node)}
      onKeyDown={(e) => {
        if ((e.key === 'Enter' || e.key === ' ') && failure.node) {
          e.preventDefault();
          openNodeByName(failure.node);
        }
      }}
      className="flex items-start gap-2 rounded-ctl py-1 pr-2 pl-7 text-[12px] transition-colors hover:bg-(--hover-fill)"
    >
      <span className="min-w-0 flex-1 text-muted-foreground">
        {failure.node && <span className="font-medium text-foreground" title={failure.node}>{labels[failure.node] ?? failure.node}</span>}
        {failure.node && <span className="text-faint"> · </span>}
        {failureMessage(failure)}
        {detail && <span className="text-faint"> ({detail})</span>}
      </span>
      {unmocked && (
        <span className="flex shrink-0 gap-1" onClick={(e) => e.stopPropagation()}>
          <Button variant="outline" size="xs" data-testid="fix-generate-mock" onClick={() => void fixUnmocked(failure.node!, 'generate')}>
            Generate a mock
          </Button>
          <Button variant="outline" size="xs" data-testid="fix-last-real-reply" onClick={() => void fixUnmocked(failure.node!, 'last_real')}>
            Use last real reply
          </Button>
        </span>
      )}
    </div>
  );
}

function TestRow({ test }: { test: TestCase }) {
  const report = useTestStore((s) => s.report);
  const selected = useTestStore((s) => s.selectedTestId === test.id);
  const openEditor = useTestStore((s) => s.openEditor);
  const status = rowStatus(test.id, report);
  const result = resultFor(test.id, report);
  const failures = result?.failures ?? [];

  return (
    <div data-testid="test-row" data-test-id={test.id} data-status={status} data-selected={selected || undefined}>
      <button
        type="button"
        onClick={() => openEditor(test, false)}
        className={cn(
          'flex w-full items-center gap-2 rounded-ctl px-2 py-1.5 text-left text-[12.5px] transition-colors hover:bg-(--hover-fill)',
          selected && 'bg-(--hover-fill)',
        )}
      >
        <StatusIcon status={status} />
        <span className="min-w-0 flex-1 truncate font-medium text-foreground">{test.name}</span>
        {test.locked && <Lock size={11} className="shrink-0 text-muted-foreground" aria-label="Approved and locked" />}
        <span className="hidden max-w-40 shrink-0 truncate text-[11.5px] text-muted-foreground sm:inline">{targetText(test)}</span>
        <Badge variant="neutral" className="shrink-0">{CATEGORY_LABELS[test.category]}</Badge>
        {test.status === 'approved' ? (
          <Badge variant="success" className="shrink-0">Approved</Badge>
        ) : (
          <Badge variant="outline" className="shrink-0">Draft</Badge>
        )}
      </button>
      {failures.map((f, i) => (
        <FailureRow key={`${f.kind}-${f.node}-${i}`} failure={f} />
      ))}
    </div>
  );
}

function Suggestions() {
  const suggestions = useTestStore((s) => s.suggestions);
  const setSuggestions = useTestStore((s) => s.setSuggestions);
  const dismiss = useTestStore((s) => s.dismissSuggestion);
  if (suggestions.length === 0) return null;
  return (
    <div className="p-1" data-testid="tests-suggestions">
      <div className="sticky top-0 z-10 flex items-center gap-2 rounded-ctl bg-ai-soft px-1.5 py-1 text-[12px] font-medium text-ai-text">
        <Sparkles size={12} />
        {suggestions.length} suggested {suggestions.length === 1 ? 'test' : 'tests'}
        <span className="flex-1" />
        <Button
          variant="ghost"
          size="xs"
          data-testid="suggest-accept-all"
          onClick={() => {
            suggestions.forEach(acceptSuggestion);
          }}
        >
          Add all
        </Button>
        <Button variant="ghost" size="xs" data-testid="suggest-dismiss-all" onClick={() => setSuggestions([])}>
          Dismiss
        </Button>
      </div>
      {suggestions.map((t) => (
        <div key={t.id} data-testid="suggestion-row" data-test-id={t.id} className="flex items-center gap-2 rounded-ctl px-1.5 py-1 text-[12.5px]">
          <span className="min-w-0 flex-1 truncate text-foreground">{t.name}</span>
          <span className="hidden max-w-40 shrink-0 truncate text-[11.5px] text-muted-foreground sm:inline">{targetText(t)}</span>
          <Badge variant="neutral" className="shrink-0">{CATEGORY_LABELS[t.category]}</Badge>
          <Button variant="outline" size="xs" data-testid="suggest-accept" onClick={() => acceptSuggestion(t)}>
            <Check /> Add as draft
          </Button>
          <Button variant="ghost" size="icon-xs" aria-label="Dismiss suggestion" data-testid="suggest-dismiss" onClick={() => dismiss(t.id)}>
            <X />
          </Button>
        </div>
      ))}
    </div>
  );
}

/** Bottom-panel tab: toolbar, suggested drafts, the test list or the coverage grid, and the editor drawer. */
export default function TestsPanel() {
  const tests = useWorkflowStore((s) => s.tests);
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const report = useTestStore((s) => s.report);
  const scorecard = useTestStore((s) => s.scorecard);
  const suggestions = useTestStore((s) => s.suggestions);
  const running = useTestStore((s) => s.running);
  const scoring = useTestStore((s) => s.scoring);
  const suggesting = useTestStore((s) => s.suggesting);
  const view = useTestStore((s) => s.view);
  const setView = useTestStore((s) => s.setView);
  const openEditor = useTestStore((s) => s.openEditor);

  const summary = useMemo(() => barText(report, scorecard), [report, scorecard]);

  return (
    <div className="flex h-full flex-col" data-testid="tests-panel">
      <div className="flex shrink-0 items-center gap-2 px-2 py-1.5">
        <span className="min-w-0 truncate text-[12px] text-muted-foreground" data-testid="tests-summary">
          {summary || (tests.length === 0 ? 'No tests yet' : `${tests.length} ${tests.length === 1 ? 'test' : 'tests'}, not run`)}
        </span>
        {running && <Loader2 size={12} className="shrink-0 animate-spin text-muted-foreground" aria-label="Running tests" />}
        <span className="flex-1" />
        <Segmented
          size="xs"
          value={view}
          onChange={setView}
          aria-label="Tests view"
          options={[
            { key: 'list', label: 'Tests', icon: <List />, testId: 'tests-view-list' },
            { key: 'grid', label: 'Coverage', icon: <LayoutGrid />, testId: 'tests-view-grid' },
            ...(scorecard ? [{ key: 'score' as const, label: 'Score', icon: <Target />, testId: 'tests-view-score' }] : []),
            ...(suggestions.length > 0
              ? [{ key: 'suggest' as const, label: `Suggestions (${suggestions.length})`, icon: <Sparkles />, testId: 'tests-view-suggest' }]
              : []),
          ]}
        />
        <Button variant="outline" size="sm" onClick={() => void runTests()} disabled={!workflowId || running} data-testid="tests-run-all">
          <Play /> Run all
        </Button>
        <Button variant="outline" size="sm" onClick={() => openEditor(newTestCase(null, 'normal'), true)} data-testid="tests-new">
          <Plus /> New test
        </Button>
        <Button variant="outline" size="sm" onClick={() => void suggestTests()} disabled={!workflowId || suggesting} data-testid="tests-suggest">
          {suggesting ? <Loader2 className="animate-spin" /> : <Sparkles />} Suggest
        </Button>
        <Button variant="outline" size="sm" onClick={() => void scoreWorkflow()} disabled={!workflowId || scoring || tests.length === 0} data-testid="tests-score">
          {scoring ? <Loader2 className="animate-spin" /> : <Target />} Score
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
        {view === 'grid' ? (
          <CoverageGrid />
        ) : view === 'score' && scorecard ? (
          <ScorecardView scorecard={scorecard} />
        ) : view === 'suggest' && suggestions.length > 0 ? (
          <Suggestions />
        ) : tests.length === 0 ? (
          <div className="flex flex-col items-center gap-1 px-3 py-6 text-center text-[12.5px] text-muted-foreground" data-testid="tests-empty">
            <FlaskConical size={16} />
            <span>No tests yet. Suggest some, or write one from a node.</span>
          </div>
        ) : (
          tests.map((t) => <TestRow key={t.id} test={t} />)
        )}
      </div>

      <TestEditor />
    </div>
  );
}
