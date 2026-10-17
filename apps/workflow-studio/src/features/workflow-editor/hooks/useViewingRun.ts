import { useNavigate, useSearch } from '@tanstack/react-router';
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
