/**
 * Inline fixes for denied broker calls (UI-DESIGN §4.4), used under TraceWaterfall rows:
 * `missing_secret` → CreateSecretPopover; `secret_not_bound` → bind the key to the call's host
 * (`PUT …/secrets/{id} {allowed_hosts}`); `host_not_allowed` → project allowlist in Settings;
 * the rest are informational.
 */
import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useSecrets } from '@/features/project/hooks/useProjectData'
import { CreateSecretPopover } from '@/features/secrets/CreateSecretPopover'
import { useUpdateSecret } from '@/features/secrets/useSecretMutations'
import { Button } from '@/shared/components/ui/button'
import { errorMessage } from '@/shared/api/http'
import type { BrokerCall } from '@/shared/api/types'

const INFO: Record<string, string> = {
  read_only: 'The AI self-test runs read-only: writes, emails and non-GET requests are refused.',
  secret_expression_not_allowed: "Don't transform studio.secret(...) (no .upper(), slicing or hashing).",
  call_limit_exceeded: 'This run hit the broker call limit.',
  table_not_found: "The table doesn't exist yet. It's created when you save the module.",
}

function params(c: BrokerCall): Record<string, unknown> {
  return ((c.parameters ?? c.params_redacted ?? {}) as Record<string, unknown>) || {}
}

function secretKeyOf(c: BrokerCall): string | null {
  const extra = (c as Record<string, unknown>).key
  if (typeof extra === 'string') return extra
  return /secret '([^']+)'/.exec(c.error ?? '')?.[1] ?? null
}

function hostOf(c: BrokerCall): string | null {
  const m = /host '([^']+)'/.exec(c.error ?? '')
  if (m) return m[1]
  const url = params(c).url
  if (typeof url !== 'string') return null
  try {
    return new URL(url).hostname || null
  } catch {
    return /^https?:\/\/([^/:?#]+)/.exec(url)?.[1] ?? null
  }
}

export function BrokerFix({ call, environment }: { call: BrokerCall; environment?: string | null }) {
  const code = call.error_code ?? ''
  const env = environment ?? call.environment
  if (code === 'missing_secret') return <MissingSecretFix call={call} env={env} />
  if (code === 'secret_not_bound') return <BindHostFix call={call} env={env} />
  if (code === 'host_not_allowed') return <AllowHostFix call={call} />
  const info = INFO[code]
  return info ? (
    <div className="mt-1 text-muted-foreground" data-testid={`fix-${code}`}>
      {info}
    </div>
  ) : null
}

function MissingSecretFix({ call, env }: { call: BrokerCall; env: string }) {
  const key = secretKeyOf(call)
  const host = hostOf(call)
  if (!key) return null
  return (
    <div className="mt-1.5" data-testid="fix-missing_secret">
      <CreateSecretPopover
        secretKey={key}
        keyReadOnly
        environments={[env]}
        hosts={host ? [host] : []}
        testId="fix-create-secret"
        onCreated={() => toast.success(`${key} added to ${env}`)}
        trigger={
          <Button variant="outline" size="xs" data-testid="fix-add-secret">
            Add {key} to {env}
          </Button>
        }
      />
    </div>
  )
}

function BindHostFix({ call, env }: { call: BrokerCall; env: string }) {
  const { pid } = useProjectCtx()
  const secrets = useSecrets(pid)
  const update = useUpdateSecret(pid)
  const [done, setDone] = useState(false)
  const key = secretKeyOf(call)
  const host = hostOf(call)
  const row = secrets.data?.find((s) => s.key === key && s.environment === env && s.scope === 'project')
  if (!key || !host) return null
  if (done)
    return (
      <div className="mt-1 text-success-text" data-testid="fix-secret_not_bound-done">
        Bound {key} to {host}. Run it again.
      </div>
    )
  return (
    <div className="mt-1.5 flex items-center gap-2" data-testid="fix-secret_not_bound">
      <Button
        variant="outline"
        size="xs"
        disabled={!row || update.isPending}
        data-testid="fix-bind-host"
        onClick={() =>
          row &&
          update.mutate(
            { id: row.id, body: { allowed_hosts: [...new Set([...(row.allowed_hosts ?? []), host])] } },
            { onSuccess: () => setDone(true), onError: (e) => toast.error(errorMessage(e)) },
          )
        }
      >
        Bind {key} to {host}
      </Button>
      {!row && secrets.isSuccess && <span className="text-muted-foreground">{key} is a team value: edit it in Workflows → Variables</span>}
    </div>
  )
}

function AllowHostFix({ call }: { call: BrokerCall }) {
  const host = hostOf(call)
  return (
    <div className="mt-1.5" data-testid="fix-host_not_allowed">
      <Button
        variant="outline"
        size="xs"
        render={<Link to="." search={((s: Record<string, unknown>) => ({ ...s, sheet: 'settings', section: 'egress' })) as never} />}
        data-testid="fix-allow-host"
      >
        Add {host ?? 'host'} to the project allowlist
      </Button>
    </div>
  )
}
