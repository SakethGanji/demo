/**
 * Hook to fetch and cache node type definitions from the API
 *
 * This replaces the hardcoded node definitions in nodeCreatorStore
 * and provides schema-driven UI generation.
 *
 * Node types use backend PascalCase names everywhere (Start, Set, HttpRequest, etc.)
 */

import { useQuery } from '@tanstack/react-query';
import { nodesApi } from '@/shared/lib/api';

/**
 * Fetch all available node types with their schemas
 */
export function useNodeTypes() {
  return useQuery({
    queryKey: ['nodes'],
    queryFn: nodesApi.list,
    staleTime: 1000 * 60 * 5, // Cache for 5 minutes
    refetchOnWindowFocus: false,
  });
}
