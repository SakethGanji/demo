/**
 * Ship (design §4): dev → live with the live environment's release, and every secret as a lock chip with its
 * per-environment status. The release button names the blockers (secrets missing in live) or opens the
 * release sheet. Shown only when the project has a release environment or any secrets.
 */
import { Link, useNavigate } from '@tanstack/react-router'
import { LockKeyhole, Plus, Rocket } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { IconTile } from '@/shared/components/ui/surface'
import type { EffectiveVar, MissingSecret, ProjectSummary } from '@/shared/api/types'
import { cn } from '@/shared/lib/utils'
import { useProjectCtx } from '../../ProjectContext'
import { pluralize, timeAgo } from '../../lib/format'

type EnvState = 'set' | 'missing' | 'unset'

export function StoryShip({ summary, secrets, missing }: { summary: ProjectSummary; secrets: EffectiveVar[]; missing: MissingSecret[] }) {
  const { project, slug } = useProjectCtx()
  const navigate = useNavigate()
  const P = { project: slug }
  const hasRelease = project.environments.some((e) => e.kind === 'release')
  const keys = [...new Set([...secrets.filter((s) => s.type === 'secret').map((s) => s.key), ...missing.map((m) => m.key)])].sort()
  if (!hasRelease && !keys.length) return null

  const live = project.live_environment
  const draft = project.environments.find((e) => e.kind === 'draft')?.name ?? project.default_environment
  const rel = summary.active_releases?.[live] ?? null
  const envs = project.environments.map((e) => e.name)
  const state = (k: string, env: string): EnvState =>
    secrets.some((s) => s.key === k && s.environment === env) ? 'set' : missing.some((m) => m.key === k && m.missing_in.includes(env)) ? 'missing' : 'unset'
  const blockers = missing.filter((m) => m.missing_in.includes(live))

  return (
    <section className="story-ship tile flex flex-wrap items-center gap-3.5 px-[22px] py-4" aria-label={hasRelease ? 'Ship' : 'Secrets'} data-testid="story-ship" id="story-ship">
      <span className="flex items-center gap-2.5 text-[13.5px] font-semibold tracking-[-0.01em]">
        <IconTile hue={hasRelease ? 'release' : 'secrets'}>{hasRelease ? <Rocket /> : <LockKeyhole />}</IconTile>
        {hasRelease ? 'Ship' : 'Secrets'}
      </span>
      {hasRelease && (
        <>
          <div className="story-raised flex flex-col rounded-well px-3.5 py-2" data-testid="release-env-draft">
            <b className="text-[13.5px] font-semibold">{draft}</b>
            <span className="text-[11.5px] text-muted-foreground">your latest saved code</span>
          </div>
          <div className="story-beam" data-blocked={blockers.length > 0} aria-hidden />
          <div className="story-raised flex flex-col rounded-well px-3.5 py-2" data-blocked={blockers.length > 0 || undefined} data-testid="release-env-live">
            <b className="text-[13.5px] font-semibold">
              {live}
              {rel ? ` · #${rel.number}` : ''}
            </b>
            <span className="text-[11.5px] text-muted-foreground">{rel ? `released ${timeAgo(rel.created_at)}` : 'no release yet'}</span>
          </div>
        </>
      )}
      {keys.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Secrets">
          {keys.map((k) => {
            const states = envs.map((env) => [env, state(k, env)] as const)
            const firstMissing = states.find(([, s]) => s === 'missing')?.[0]
            return (
              <li key={k} className="story-raised inline-flex items-center gap-1.5 rounded-ctl px-2 py-1 text-xs" data-testid={`secret-summary-${k}`}>
                <LockKeyhole className="size-3 text-muted-foreground" aria-hidden />
                <span className="font-mono">{k}</span>
                {states.map(([env, s]) => (
                  <i
                    key={env}
                    className={cn(
                      'rounded-chip px-[5px] text-[10px] not-italic',
                      s === 'set' ? 'bg-success/10 text-success-text' : s === 'missing' ? 'bg-warning/15 text-warning-text' : 'bg-hairline text-muted-foreground',
                    )}
                    data-testid={`secret-env-${k}-${env}`}
                    data-state={s === 'unset' ? 'not-set' : s}
                  >
                    {env}
                    {s === 'missing' ? ' missing' : s === 'unset' ? ' not set' : ''}
                  </i>
                ))}
                {firstMissing && (
                  <Button variant="outline" size="xs" render={<Link to="/p/$project/secrets" params={P} search={{ create: k, env: firstMissing }} />} data-testid={`story-secret-set-${k}`}>
                    Set
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      )}
      <span className="flex-1" />
      {hasRelease ? (
        blockers.length ? (
          <Button variant="outline" size="sm" render={<Link to="/p/$project/secrets" params={P} search={{ create: blockers[0].key, env: live }} />} data-testid="releases-create">
            <LockKeyhole /> Fix {pluralize(blockers.length, 'blocker')} to release
          </Button>
        ) : (
          <Button size="sm" onClick={() => navigate({ to: '/p/$project', params: P, search: { sheet: 'release' } })} data-testid="releases-create">
            <Rocket /> Create release
          </Button>
        )
      ) : (
        <Button variant="outline" size="sm" render={<Link to="/p/$project/secrets" params={P} />} data-testid="secrets-add">
          <Plus /> Secret
        </Button>
      )}
    </section>
  )
}
