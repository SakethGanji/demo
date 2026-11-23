import { Link } from '@tanstack/react-router'
import { KeyRound, LockKeyhole, Plus } from 'lucide-react'
import { useProjectCtx } from '../ProjectContext'
import { useMissingSecrets, useSecrets } from '../hooks/useProjectData'
import { useSecretReferences } from '@/features/secrets/useSecretReferences'
import { span, TileHead } from './tiles'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { StatusDot } from '@/shared/components/ui/status-dot'
import { Tile, Well } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'

const MAX = 2

export function SecretsTile() {
  const { project, pid, slug } = useProjectCtx()
  const secrets = useSecrets(pid)
  const missing = useMissingSecrets(pid)
  const { refs } = useSecretReferences(pid, missing.data)
  const keys = [...new Set([...(secrets.data ?? []).map((s) => s.key), ...(missing.data ?? []).map((m) => m.key)])].sort()
  const envs = project.environments.map((e) => e.name)
  const shown = keys.slice(0, MAX)
  return (
    <Tile as="section" className={cn('flex flex-col', span.half)} data-testid="secrets-card">
      <TileHead
        icon={<KeyRound />}
        hue="secrets"
        title="Secrets"
        actions={
          <Button variant="ghost" size="sm" render={<Link to="/p/$project/secrets" params={{ project: slug }} />} data-testid="secrets-add">
            <Plus /> Add
          </Button>
        }
      />
      <Well className="mx-2 mb-2 flex flex-1 flex-col">
        {keys.length === 0 ? (
          <div className="px-4 py-5 text-xs text-muted-foreground">No secrets. Add API keys your backend calls with.</div>
        ) : (
          <>
            {shown.map((k) => {
              const used = [...new Set((refs.get(k) ?? []).map((r) => (r.kind === 'module' ? `${r.name}.py` : r.name)))]
              return (
                <div key={k} data-testid={`secret-summary-${k}`}>
                  <div className="flex items-center gap-2 h-9 px-3 text-xs">
                    <LockKeyhole className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate font-mono">{k}</span>
                    {used.length > 0 && <span className="truncate text-muted-foreground">· used in {used.slice(0, 2).join(', ')}</span>}
                  </div>
                  {envs.map((env) => {
                    const set = (secrets.data ?? []).some((s) => s.key === k && s.environment === env)
                    const isMissing = (missing.data ?? []).some((m) => m.key === k && m.missing_in.includes(env))
                    return (
                      <div key={env} className="flex h-[34px] items-center gap-2 border-t border-hairline px-3 text-[12.5px]" data-testid={`secret-env-${k}-${env}`}>
                        <span className="w-10 font-mono text-muted-foreground">{env}</span>
                        {set ? (
                          <Badge variant="success" className="ml-auto" data-state="set">
                            <StatusDot tone="ok" flat /> set
                          </Badge>
                        ) : isMissing ? (
                          <Tip label="Missing: your code uses it and it isn't set here. Not set: unused there.">
                            <span className="ml-auto cursor-help">
                              <Badge variant="warning" data-state="missing">
                                <StatusDot tone="warn" /> missing
                              </Badge>
                            </span>
                          </Tip>
                        ) : (
                          <Badge variant="neutral" className="ml-auto" data-state="not-set">
                            not set
                          </Badge>
                        )}
                      </div>
                    )
                  })}
                </div>
              )
            })}
            {keys.length > MAX && (
              <Link
                to="/p/$project/secrets"
                params={{ project: slug }}
                className="border-t border-hairline px-3 py-2 text-xs text-muted-foreground hover:text-foreground"
                data-testid="secrets-more"
              >
                +{keys.length - MAX} more
              </Link>
            )}
          </>
        )}
      </Well>
    </Tile>
  )
}
