import { useQuery } from '@tanstack/react-query';
import { useNodeTypes } from '@/features/workflow-editor/hooks/useNodeTypes';
import { aiBuilderApi } from './api';
import { apiRefOptions, type ApiRefOption } from './lib/catalog';

export interface StepTypeOption { type: string; label: string }

/** What a plan step can be (the node catalog) and which of the project's API operations a step can call (the engine's own list). */
export function useSpecCatalog(projectId: string): { stepTypes: StepTypeOption[]; apiRefs: ApiRefOption[] } {
  const types = useNodeTypes();
  const refs = useQuery({
    queryKey: ['ai-builder-api-refs', projectId],
    queryFn: () => aiBuilderApi.apiRefs(projectId),
    enabled: !!projectId,
    staleTime: 30_000,
  });
  return {
    stepTypes: (types.data ?? []).map((t) => ({ type: t.type, label: t.displayName || t.type })).sort((a, b) => a.label.localeCompare(b.label)),
    apiRefs: apiRefOptions(refs.data?.items ?? []),
  };
}
