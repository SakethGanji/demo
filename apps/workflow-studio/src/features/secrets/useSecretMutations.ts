import { useMutation, useQueryClient } from '@tanstack/react-query'
import { secretsApi } from '@/shared/api/endpoints'
import { invalidate } from '@/shared/api/queryKeys'
import type { SecretUpdate, VarType } from '@/shared/api/types'

export interface CreateSecretsInput {
  key: string
  value: string
  environments: string[]
  type?: VarType
  allowed_hosts?: string[]
  description?: string
}

/** One `POST …/secrets` per environment (UI-DESIGN §3.5). Stops at the first error. */
export function useCreateSecrets(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (input: CreateSecretsInput) => {
      const created = []
      for (const environment of input.environments) {
        created.push(
          await secretsApi.create(pid, {
            key: input.key,
            value: input.value,
            environment,
            type: input.type ?? 'secret',
            allowed_hosts: input.allowed_hosts && input.allowed_hosts.length ? input.allowed_hosts : undefined,
            description: input.description,
          }),
        )
      }
      return created
    },
    onSettled: () => invalidate.secretsChanged(qc, pid),
  })
}

export function useUpdateSecret(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: SecretUpdate }) => secretsApi.update(pid, id, body),
    onSettled: () => invalidate.secretsChanged(qc, pid),
  })
}

export function useDeleteSecret(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => secretsApi.remove(pid, id),
    onSettled: () => invalidate.secretsChanged(qc, pid),
  })
}
