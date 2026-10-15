/** Settings · Egress allowlist (UI-DESIGN §3.7): host chips in `settings.egress_allowlist`. */
import { useEffect, useState } from 'react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { errorMessage } from '@/shared/api/http'
import { Button } from '@/shared/components/ui/button'
import { ChipsInput } from '@/shared/components/ui/chips-input'
import { InlineError, SectionHeader, useProjectUpdate } from './common'

const HOST_RE = /^(\*\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(:\d+)?$/

export function EgressSection({ open }: { open: boolean }) {
  const { project } = useProjectCtx()
  const saved = (project.settings?.egress_allowlist as string[] | undefined) ?? []
  const [hosts, setHosts] = useState<string[]>(saved)
  const [error, setError] = useState<string | null>(null)
  const update = useProjectUpdate()
  const savedKey = saved.join(',')
  useEffect(() => {
    if (open) setHosts(savedKey ? savedKey.split(',') : [])
  }, [open, savedKey])
  const dirty = hosts.join(',') !== savedKey

  return (
    <div className="flex flex-col gap-2.5" data-testid="settings-egress">
      <SectionHeader title="Egress allowlist" sub="hosts Python routes and workflows may call" />
      <ChipsInput
        value={hosts}
        onChange={setHosts}
        placeholder={hosts.length ? 'add host…' : 'api.stripe.com, *.internal…'}
        validate={(h) => HOST_RE.test(h)}
        testId="egress-hosts"
      />
      <div className="text-[11.5px] leading-4 text-muted-foreground">
        Empty means no project-wide restriction; per-secret allowed hosts still apply. Use <span className="font-mono">*.example.com</span> for
        subdomains.
      </div>
      {error && <InlineError testId="egress-error">{error}</InlineError>}
      {dirty && (
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => setHosts(saved)}>
            Discard
          </Button>
          <Button
            size="sm"
            disabled={update.isPending}
            onClick={() => {
              setError(null)
              update.mutate({ settings: { egress_allowlist: hosts } }, { onError: (e) => setError(errorMessage(e)) })
            }}
            data-testid="egress-save"
          >
            Save allowlist
          </Button>
        </div>
      )}
    </div>
  )
}
