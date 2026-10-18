import { useEffect } from 'react';
import { useWorkflowStore } from '../stores/workflowStore';
import { useTestStore } from '../stores/testStore';
import { runTests, testFingerprint } from '../lib/testActions';

export const LIVE_TESTS_DEBOUNCE_MS = 1500;

/**
 * Runs the tests against the unsaved draft (CONTRACTS-STAGE3A §8).
 *
 * Trigger: when the workflow has an id and at least one test, on load and 1500 ms after the last change to the
 * nodes, edges, tests or test defaults. Identical fingerprints (positions excluded) are not re-sent.
 * A change drops the scorecard (it described the old draft). Failures only console.warn.
 */
export function useLiveTests(): void {
  const workflowId = useWorkflowStore((s) => s.workflowId);

  useEffect(() => {
    useTestStore.getState().clear();
    if (!workflowId) return;

    let lastSent: string | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const run = () => {
      timer = null;
      const s = useWorkflowStore.getState();
      if (s.workflowId !== workflowId) return;
      if (s.tests.length === 0) {
        lastSent = null;
        useTestStore.getState().setReport(null);
        useTestStore.getState().setScorecard(null);
        return;
      }
      const fp = testFingerprint();
      if (fp === lastSent) return;
      lastSent = fp;
      useTestStore.getState().setScorecard(null);
      void runTests({ silent: true }).then((r) => {
        if (!r) lastSent = null; // allow a retry on the next change
      });
    };

    const schedule = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(run, LIVE_TESTS_DEBOUNCE_MS);
    };

    let { nodes, edges, tests, testDefaults } = useWorkflowStore.getState();
    const unsub = useWorkflowStore.subscribe((s) => {
      if (s.nodes === nodes && s.edges === edges && s.tests === tests && s.testDefaults === testDefaults) return;
      nodes = s.nodes;
      edges = s.edges;
      tests = s.tests;
      testDefaults = s.testDefaults;
      schedule();
    });
    schedule();

    return () => {
      unsub();
      if (timer) clearTimeout(timer);
    };
  }, [workflowId]);
}
