import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { backends } from '@/shared/lib/config';

export interface VariableListItem {
  id: number;
  key: string;
  environment: string;
  value: string | null; // null for secrets — backend masks them
  type: 'string' | 'secret' | 'number';
  description: string | null;
}

interface CreateVariableInput {
  key: string;
  value: string;
  type: 'string' | 'secret' | 'number';
  environment: string;
  description?: string | null;
  team_id?: string;
}

interface UpdateVariableInput {
  id: number;
  value: string;
}

const VARS_KEY = ['variables'] as const;
const ENVS_KEY = ['variable-environments'] as const;

async function jsonOrThrow(res: Response) {
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}${text ? ` — ${text}` : ''}`);
  }
  return res.json();
}

export function useVariables(environment: string) {
  return useQuery({
    queryKey: [...VARS_KEY, environment],
    queryFn: async () => {
      const url = new URL(`${backends.workflow}/api/variables`);
      url.searchParams.set('environment', environment);
      const res = await fetch(url.toString());
      return jsonOrThrow(res) as Promise<VariableListItem[]>;
    },
  });
}

export function useVariableEnvironments() {
  return useQuery({
    queryKey: ENVS_KEY,
    queryFn: async () => {
      const res = await fetch(`${backends.workflow}/api/variables/environments`);
      return jsonOrThrow(res) as Promise<string[]>;
    },
  });
}

export function useCreateVariable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: CreateVariableInput) => {
      const res = await fetch(`${backends.workflow}/api/variables`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ team_id: 'default', ...input }),
      });
      return jsonOrThrow(res);
    },
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: [...VARS_KEY, vars.environment] });
      qc.invalidateQueries({ queryKey: ENVS_KEY });
    },
  });
}

export function useUpdateVariable(environment: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, value }: UpdateVariableInput) => {
      // PUT requires `description` because of how the schema is currently
      // defined upstream — send null to leave it unchanged.
      const res = await fetch(`${backends.workflow}/api/variables/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value, description: null }),
      });
      return jsonOrThrow(res);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...VARS_KEY, environment] });
    },
  });
}

export function useDeleteVariable(environment: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      const res = await fetch(`${backends.workflow}/api/variables/${id}`, {
        method: 'DELETE',
      });
      return jsonOrThrow(res);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...VARS_KEY, environment] });
      qc.invalidateQueries({ queryKey: ENVS_KEY });
    },
  });
}
