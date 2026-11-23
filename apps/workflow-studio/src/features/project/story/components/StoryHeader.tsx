/**
 * The story (design §1): who the project is, a plain-language sentence about what it has and how its day went
 * (each count or name a button), how to call it when it has routes, and the Generate / New interface actions.
 */
import type { CSSProperties } from 'react'
import { Link } from '@tanstack/react-router'
import { Sparkles, WandSparkles } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Snippet } from '@/shared/components/ui/snippet'
import type { ProjectSummary, Route } from '@/shared/api/types'
import { cn } from '@/shared/lib/utils'
import { useProjectCtx } from '../../ProjectContext'
import { ProjectAvatar } from '../../components/ProjectTopBar'
import { HealthBadge } from '../../overview/HealthBadge'
import { sortRoutes } from '../../lib/routes'
import { LANE_HUE } from '../lib/model'
import type { Seg, TokTarget } from '../lib/sentence'
import { KindIcon } from './KindIcon'

/** toned tokens: the soft status fills; neutral ones are raised chips that take their kind's hue on hover */
const TOK_TONE = {
  ok: 'text-success-text bg-success-soft hover:shadow-[inset_0_0_0_1px_currentColor]',
  bad: 'text-destructive bg-destructive-soft hover:shadow-[inset_0_0_0_1px_currentColor]',
  warn: 'text-warning-text bg-warning-soft hover:shadow-[inset_0_0_0_1px_currentColor]',
} as const

export function StoryHeader({
  summary,
  sentence,
  routes,
  onToken,
  onCreateUi,
}: {
  summary: ProjectSummary
  sentence: Seg[]
  routes: Route[]
  onToken: (t: TokTarget) => void
  onCreateUi: () => void
}) {
  const { project, slug } = useProjectCtx()
  const origin = typeof window === 'undefined' ? '' : window.location.origin
  const base = `${origin}/api/p/${slug}`
  const sample = sortRoutes(routes).find((r) => r.method === 'GET' && !r.path.includes('{'))
  const sampleUrl = sample ? `${base}${sample.path}` : base
  const forms = [
    { key: 'url', label: 'URL', value: base },
    { key: 'curl', label: 'curl', lead: 'curl', value: `curl ${sampleUrl}` },
    { key: 'python', label: 'Python', value: `import requests\nrequests.get("${sampleUrl}")` },
  ]
  return (
    <section className="story-head" aria-label={project.name} data-testid="story">
      <ProjectAvatar name={project.name} size={46} className="[grid-area:av] rounded-panel shadow-[0_8px_24px_color-mix(in_srgb,var(--brand-1)_32%,transparent)]" />
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 self-center [grid-area:title]">
        <h1 className="min-w-0 truncate text-title leading-[1.15] font-[650] tracking-[-0.03em]" data-testid="project-name">
          {project.name}
        </h1>
        <HealthBadge summary={summary} />
      </div>
      <div className="min-w-0 [grid-area:body]">
        <p className="max-w-[860px] text-[16px] leading-[1.7] text-muted-foreground text-pretty" data-testid="story-sentence">
          {sentence.map((s, i) =>
            'tok' in s ? (
              <button
                key={i}
                type="button"
                onClick={() => onToken(s.target)}
                className={cn(
                  'story-tok rounded-ctl px-1.5 py-[2px] text-[14.5px] font-[560] whitespace-nowrap box-decoration-clone transition-[box-shadow,background-color] duration-(--d-fast) focus-visible:shadow-focus focus-visible:outline-none',
                  s.tone ? TOK_TONE[s.tone] : 'bg-raise text-foreground shadow-[inset_0_0_0_1px_var(--hairline)] hover:shadow-[inset_0_0_0_1px_var(--tok-hue)]',
                )}
                style={s.target.type === 'lane' ? ({ '--tok-hue': LANE_HUE[s.target.lane] } as CSSProperties) : undefined}
                data-tok={s.target.type}
              >
                {s.tone && <span className="mr-[5px] mb-px inline-block size-[7px] rounded-full bg-current align-middle" aria-hidden />}
                {s.target.type === 'lane' && (
                  <span className="mr-1 inline-block align-[-2px]" style={{ color: LANE_HUE[s.target.lane] }}>
                    <KindIcon lane={s.target.lane} className="size-3.5" />
                  </span>
                )}
                {s.tok}
              </button>
            ) : s.strong ? (
              <b key={i} className="font-[620] text-foreground">
                {s.text}
              </b>
            ) : (
              <span key={i}>{s.text}</span>
            ),
          )}
        </p>
        {routes.length > 0 && <Snippet testId="base-url" className="mt-3 max-w-[640px] gap-1.5" forms={forms} />}
      </div>
      <div className="flex flex-none items-center gap-2 [grid-area:act]">
        <Button variant="outline" size="sm" render={<Link to="/p/$project/backend/$module" params={{ project: slug, module: '~new' }} search={{ generate: 1 }} />} data-testid="hero-generate">
          <Sparkles className="text-ai" /> Generate
        </Button>
        <Button size="sm" onClick={onCreateUi} data-testid="hero-create-ui">
          <WandSparkles /> New interface
        </Button>
      </div>
    </section>
  )
}
