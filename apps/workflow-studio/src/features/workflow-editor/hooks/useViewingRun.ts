import { useCallback } from 'react';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { followRunOnCanvas } from '../lib/followRun';
import { useWorkflowStore } from '../stores/workflowStore';

/** Clears the shown run (data and `?execution=`) and returns the editor to live mode. */
export function useBackToLive() {
  const navigate = useNavigate();
  return () => {
    useWorkflowStore.getState().exitViewingExecution();
    navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, execution: undefined }), replace: true } as never);
  };
}

/** The run id the URL asks to show (`?execution=<id>`), if any. */
export function useExecutionParam(): string | undefined {
  const search = useSearch({ strict: false }) as { execution?: string };
  return typeof search.execution === 'string' && search.execution ? search.execution : undefined;
}

/**
 * Shows a run that started from the interface dock on the canvas, and keeps it updated while it runs
 * (`lib/followRun`). The URL gets `?execution=<id>`, so the run banner and "Back to live" work as for any saved run.
 */
export function useFollowRun() {
  const navigate = useNavigate();
  return useCallback((executionId: string) => {
    followRunOnCanvas(executionId);
    navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, execution: executionId }), replace: true } as never);
  }, [navigate]);
}
