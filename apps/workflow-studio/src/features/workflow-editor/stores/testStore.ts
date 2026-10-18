import { create } from 'zustand';
import type { Scorecard, TestCase, TestRunReport } from '../lib/tests';
import type { AiRun, FuzzResponse } from '../lib/testsAi';

export type TestsView = 'list' | 'grid' | 'score' | 'suggest' | 'fuzz';

interface TestState {
  report: TestRunReport | null;
  scorecard: Scorecard | null;
  running: boolean;
  scoring: boolean;
  suggesting: boolean;
  /** "Fill gaps with AI" is waiting on the model. */
  aiBusy: boolean;
  /** Dry-run result of each AI draft, by test id. */
  aiRuns: Record<string, AiRun>;
  fuzzRunning: boolean;
  fuzzResult: FuzzResponse | null;
  /** Finding ids already saved as tests. */
  savedFindings: string[];
  setAiBusy: (v: boolean) => void;
  addAiRuns: (runs: Record<string, AiRun>) => void;
  setFuzzRunning: (v: boolean) => void;
  setFuzzResult: (r: FuzzResponse | null) => void;
  markFindingSaved: (id: string) => void;
  selectedTestId: string | null;
  view: TestsView;
  /** The test open in the editor drawer (a copy; `isNew` until it is saved into the workflow). */
  editing: { test: TestCase; isNew: boolean } | null;
  /** Drafts proposed by "Suggest tests", waiting for the user to accept each. */
  suggestions: TestCase[];
  /** The latest live run's execution id (from the run stream), for "Save as test". */
  lastExecutionId: string | null;
  savingFromRun: boolean;
  setLastExecutionId: (id: string | null) => void;
  setSavingFromRun: (v: boolean) => void;
  setReport: (r: TestRunReport | null) => void;
  setScorecard: (s: Scorecard | null) => void;
  setRunning: (v: boolean) => void;
  setScoring: (v: boolean) => void;
  setSuggesting: (v: boolean) => void;
  selectTest: (id: string | null) => void;
  setView: (v: TestsView) => void;
  openEditor: (test: TestCase, isNew: boolean) => void;
  closeEditor: () => void;
  setSuggestions: (s: TestCase[]) => void;
  dismissSuggestion: (id: string) => void;
  clear: () => void;
}

export const useTestStore = create<TestState>((set, get) => ({
  report: null,
  scorecard: null,
  running: false,
  scoring: false,
  suggesting: false,
  aiBusy: false,
  aiRuns: {},
  fuzzRunning: false,
  fuzzResult: null,
  savedFindings: [],
  setAiBusy: (aiBusy) => set({ aiBusy }),
  addAiRuns: (runs) => set({ aiRuns: { ...get().aiRuns, ...runs } }),
  setFuzzRunning: (fuzzRunning) => set({ fuzzRunning }),
  setFuzzResult: (fuzzResult) => set({ fuzzResult, savedFindings: [] }),
  markFindingSaved: (id) => set({ savedFindings: [...get().savedFindings, id] }),
  selectedTestId: null,
  view: 'list',
  editing: null,
  suggestions: [],
  lastExecutionId: null,
  savingFromRun: false,
  setLastExecutionId: (lastExecutionId) => set({ lastExecutionId }),
  setSavingFromRun: (savingFromRun) => set({ savingFromRun }),
  setReport: (report) => set({ report }),
  setScorecard: (scorecard) => set({ scorecard, ...(!scorecard && get().view === 'score' ? { view: 'list' as const } : {}) }),
  setRunning: (running) => set({ running }),
  setScoring: (scoring) => set({ scoring }),
  setSuggesting: (suggesting) => set({ suggesting }),
  selectTest: (selectedTestId) => set({ selectedTestId }),
  setView: (view) => set({ view }),
  openEditor: (test, isNew) => set({ editing: { test, isNew }, selectedTestId: test.id }),
  closeEditor: () => set({ editing: null }),
  setSuggestions: (suggestions) =>
    set({ suggestions, ...(suggestions.length > 0 ? { view: 'suggest' as const } : get().view === 'suggest' ? { view: 'list' as const } : {}) }),
  dismissSuggestion: (id) => {
    const suggestions = get().suggestions.filter((t) => t.id !== id);
    set({ suggestions, ...(suggestions.length === 0 && get().view === 'suggest' ? { view: 'list' as const } : {}) });
  },
  clear: () =>
    set({ report: null, scorecard: null, running: false, scoring: false, suggesting: false, selectedTestId: null, editing: null, suggestions: [], lastExecutionId: null, savingFromRun: false, aiBusy: false, aiRuns: {}, fuzzRunning: false, fuzzResult: null, savedFindings: [] }),
}));
