/**
 * Settings · Developer (UI-DESIGN §3.7): SDK version, Download studio SDK (.zip), and a dev broker
 * token (1h) for running modules locally, shown once.
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Download, KeyRound } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useSdkReference } from '@/features/project/hooks/useProjectData'
import { sdkApi } from '@/shared/api/endpoints'
import { errorMessage } from '@/shared/api/http'
import type { BrokerToken } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'
import { InlineError, OnceBox, SectionHeader } from './common'

export function DeveloperSection() {
  const { project, pid } = useProjectCtx()
  const ref = useSdkReference()
  const env = project.environments.find((e) => e.name === project.default_environment && e.kind === 'draft')
    ? project.default_environment
    : project.environments.find((e) => e.kind === 'draft')?.name
  const [token, setToken] = useState<BrokerToken | null>(null)
  const zip = useMutation({
    mutationFn: sdkApi.pythonZip,
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `studio-sdk-${ref.data?.version ?? 'latest'}.zip`
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
  })
  const mint = useMutation({
    mutationFn: () => sdkApi.mintToken(pid, env!),
    onSuccess: setToken,
  })

  return (
    <div className="flex flex-col gap-3" data-testid="settings-developer">
      <SectionHeader title="Developer" sub="run your modules locally against this project" />
      <div className="flex items-center gap-3 rounded-[10px] border border-border px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium">Studio SDK</div>
          <div className="text-xs text-muted-foreground">
            Python package <span className="font-mono">studio</span> · version{' '}
            <span className="font-mono text-foreground" data-testid="sdk-version" title={ref.data?.version}>
              {shortVersion(ref.data?.version) ?? (ref.isError ? 'unknown' : '…')}
            </span>
          </div>
        </div>
        <Button variant="outline" size="sm" disabled={zip.isPending} onClick={() => zip.mutate()} data-testid="sdk-download">
          <Download /> Download studio SDK (.zip)
        </Button>
      </div>
      {zip.isError && <InlineError>{errorMessage(zip.error)}</InlineError>}
      <div className="flex items-center gap-3 rounded-[10px] border border-border px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium">Dev broker token</div>
          <div className="text-xs text-muted-foreground">
            Lets the SDK call this project's tables and secrets in <span className="font-mono">{env ?? 'a draft environment'}</span> for one
            hour.
          </div>
        </div>
        <Button variant="outline" size="sm" disabled={!env || mint.isPending} onClick={() => mint.mutate()} data-testid="broker-mint">
          <KeyRound /> Mint {env ?? 'dev'} broker token (1h)
        </Button>
      </div>
      {mint.isError && <InlineError testId="broker-mint-error">{errorMessage(mint.error)}</InlineError>}
      {token && (
        <OnceBox
          testId="broker-token-once"
          icon={<KeyRound />}
          title={`Broker token (${env})`}
          value={token.token}
          footer={
            <>
              Expires {new Date(token.expires_at).toLocaleTimeString()} · set <span className="font-mono">STUDIO_BROKER_TOKEN</span> before running
              your module.
            </>
          }
        />
      )}
    </div>
  )
}

/** Long content hashes read as `f8d828894ea5`; semver strings stay as they are. */
function shortVersion(v: string | undefined): string | undefined {
  if (!v) return undefined
  return /^[0-9a-f]{20,}$/i.test(v) ? v.slice(0, 12) : v
}
