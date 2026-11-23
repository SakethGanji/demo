/**
 * The project's documentation on the Overview, once its plan is approved and built: what the project is, what it
 * does, who uses it and what to know, read straight from the approved plan. Build progress is gone from here (the
 * app builder's Plan tab and "Plan & builds" below keep it); this is the page a newcomer reads first.
 */
import { useState, type ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import { AppWindow, BookOpen, CheckCircle2, ChevronDown, Info, Users } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Tile } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import type { PlanBody } from './planTypes'

const FEATURES = 5

function Block({ title, icon, children, className }: { title: string; icon: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn('well flex min-w-0 flex-col gap-2.5 rounded-well p-4', className)}>
      <span className="flex items-center gap-2 text-[12px] font-semibold tracking-[-0.005em] text-muted-foreground">
        <span className="icon-tile size-5 text-muted-foreground [&>svg]:size-3" aria-hidden>
          {icon}
        </span>
        {title}
      </span>
      {children}
    </div>
  )
}

export function ProjectDocs({
  slug,
  body,
  version,
  approvedBy,
  approvedAt,
  children,
}: {
  slug: string
  body: PlanBody
  version: number
  approvedBy: string | null
  approvedAt: string | null
  /** the full plan flow (build progress, revise, builds), shown under "Plan & builds" */
  children: ReactNode
}) {
  const [planOpen, setPlanOpen] = useState(false)
  const [allFeatures, setAllFeatures] = useState(false)
  const features = body.requirements.acceptance
  const shown = allFeatures ? features : features.slice(0, FEATURES)
  const notes = [...body.requirements.limitations, ...body.requirements.assumptions].slice(0, 4)
  const date = approvedAt ? new Date(approvedAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : null

  return (
    <div className="flex flex-col gap-4">
      <Tile as="section" className="@container/docs flex flex-col gap-4 p-5" data-testid="project-docs">
        <header className="flex flex-wrap items-start gap-x-4 gap-y-2">
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <span className="flex items-center gap-2 text-[12px] font-medium text-muted-foreground">
              <span className="icon-tile size-5 text-muted-foreground [&>svg]:size-3" aria-hidden>
                <BookOpen />
              </span>
              Project docs · plan v{version}
              {approvedBy ? ` · approved by ${approvedBy}` : ''}
              {date ? ` · ${date}` : ''}
            </span>
            <h2 className="text-[20px] leading-tight font-[650] tracking-[-0.025em]" data-testid="project-docs-title">
              {body.title || 'Untitled project'}
            </h2>
            {body.summary ? <p className="max-w-[78ch] text-[13.5px] leading-[21px] text-foreground/80">{body.summary}</p> : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button size="sm" variant="outline" render={<Link to="/p/$project/knowledge" params={{ project: slug }} />} data-testid="project-docs-knowledge">
              <BookOpen /> Knowledge
            </Button>
            <Button size="sm" variant="outline" aria-expanded={planOpen} onClick={() => setPlanOpen((o) => !o)} data-testid="project-docs-plan">
              Plan &amp; builds <ChevronDown className={cn('transition-transform', planOpen && 'rotate-180')} />
            </Button>
          </div>
        </header>

        <div className="grid gap-3 @[760px]/docs:grid-cols-3">
          <Block title="What it does" icon={<CheckCircle2 />} className="@[760px]/docs:col-span-2">
            {shown.length ? (
              <ul className="grid gap-x-6 gap-y-2 @[560px]/docs:grid-cols-2">
                {shown.map((a) => (
                  <li key={a.id} className="flex items-start gap-2 text-[12.5px] leading-[18px] text-foreground/85">
                    <CheckCircle2 className="mt-px size-3.5 shrink-0 text-success" aria-hidden />
                    <span>{a.text}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <span className="text-[12.5px] text-muted-foreground">The plan lists no acceptance checks.</span>
            )}
            {features.length > FEATURES && (
              <button type="button" className="self-start text-[12px] font-medium text-muted-foreground hover:text-foreground" onClick={() => setAllFeatures((o) => !o)}>
                {allFeatures ? 'Show fewer' : `+${features.length - FEATURES} more`}
              </button>
            )}
          </Block>

          <div className="flex min-w-0 flex-col gap-3">
            <Block title="Who uses it" icon={<Users />}>
              {body.apps.length ? (
                <ul className="flex flex-col gap-2">
                  {body.apps.map((a) => (
                    <li key={a.key} className="flex min-w-0 flex-col">
                      <span className="flex items-center gap-1.5 text-[12.5px] font-medium">
                        <AppWindow className="size-3.5 text-muted-foreground" aria-hidden />
                        <span className="truncate">{a.name}</span>
                        <span className="font-normal text-muted-foreground">· {a.screens.length} screens</span>
                      </span>
                      {a.users ? <span className="text-[12px] leading-[17px] text-muted-foreground">{a.users}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="text-[12.5px] text-muted-foreground">{body.roles_note || 'No apps in the plan.'}</span>
              )}
            </Block>
            {notes.length ? (
              <Block title="Good to know" icon={<Info />}>
                <ul className="flex flex-col gap-1.5">
                  {notes.map((n) => (
                    <li key={n} className="text-[12px] leading-[17px] text-foreground/75">
                      {n}
                    </li>
                  ))}
                </ul>
              </Block>
            ) : null}
          </div>
        </div>
      </Tile>
      {planOpen ? <div data-testid="project-docs-plan-body">{children}</div> : null}
    </div>
  )
}
