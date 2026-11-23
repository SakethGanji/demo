/**
 * client.ts tab (UI-DESIGN §4.1): read-only generated client for the **viewing** environment,
 * an OpenAPI toggle, Copy usage and the spec revision.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Copy } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { specApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { errorMessage } from '@/shared/api/http'
import { Button } from '@/shared/components/ui/button'
import { Segmented } from '@/shared/components/ui/segmented'
import { Skeleton } from '@/shared/components/ui/skeleton'

export function ClientTsPanel() {
  const { pid, viewEnv } = useProjectCtx()
  const [mode, setMode] = useState<'client' | 'openapi'>('client')
  const client = useQuery({ queryKey: qk.clientTs(pid, viewEnv), queryFn: () => specApi.clientTs(pid, viewEnv), enabled: mode === 'client' })
  const openapi = useQuery({ queryKey: qk.openapi(pid, viewEnv), queryFn: () => specApi.openapi(pid, viewEnv), enabled: mode === 'openapi' })
  const q = mode === 'client' ? client : openapi
  const text = mode === 'client' ? (client.data ?? '') : JSON.stringify(openapi.data ?? {}, null, 2)
  const rev = /spec rev (\d+)/.exec(client.data ?? '')?.[1]
  const usage = [...(client.data ?? '').matchAll(/^\s+(\w+)\s*[:(]/gm)].map((m) => m[1]).find((n) => !['headers', 'method', 'body'].includes(n))

  return (
    <div className="flex h-full min-h-0 flex-col pb-2" data-testid="client-ts-panel">
      <div className="flex items-center gap-2 px-4 pt-1 pb-3">
        <Segmented
          value={mode}
          onChange={setMode}
          size="xs"
          options={[
            { key: 'client', label: 'client.ts', testId: 'client-mode-ts' },
            { key: 'openapi', label: 'OpenAPI', testId: 'client-mode-openapi' },
          ]}
        />
        <span className="text-xs text-muted-foreground" data-testid="client-env">
          {viewEnv}
          {rev ? ` · spec rev ${rev}` : ''}
        </span>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="xs"
          onClick={() => {
            const snippet =
              mode === 'client' ? `import { api } from './api/client'\n\nconst data = await api.${usage ?? 'yourOperation'}()` : text
            navigator.clipboard?.writeText(snippet).catch(() => undefined)
            toast.success(mode === 'client' ? 'Usage copied' : 'OpenAPI copied')
          }}
        >
          <Copy /> {mode === 'client' ? 'Copy usage' : 'Copy'}
        </Button>
      </div>
      {q.isLoading ? (
        <div className="px-2">
          <Skeleton className="h-40" />
        </div>
      ) : q.isError ? (
        <div className="px-5 text-xs text-destructive">{errorMessage(q.error)}</div>
      ) : (
        <pre
          className="well mx-2 min-h-0 flex-1 overflow-auto p-3 font-mono text-[11.5px] leading-[18px]"
          data-scroll-x="allowed"
          data-testid="client-ts-source"
        >
          {text}
        </pre>
      )}
    </div>
  )
}
