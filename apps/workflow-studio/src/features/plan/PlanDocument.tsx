/**
 * The plan as a requirements document a business owner can read and sign off, ahead of the technical plan:
 *
 *   RequirementsDoc   title and summary, then 1 What it must do (each route's purpose as a statement, grouped by area,
 *                     with the decisions from the question rounds and what is out of scope), 2 Acceptance criteria as a
 *                     checklist, 3 Assumptions (each marked, editable and confirmable on a draft), 4 Roles
 *   WhatWeBuild       in plain language: the screens of each app and what they show, the integrations, the secrets
 *   IssueSummary      every validation issue with where it is, above the document
 *
 * Validation issues also show next to the statement, screen, integration or secret they are about.
 */
import type { ReactNode } from 'react'
import { AlertTriangle, AppWindow, Check, CircleCheck, KeyRound, Plug, Trash2, Users } from 'lucide-react'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Textarea } from '@/shared/components/ui/textarea'
import { Callout, Dot, IconTile, SectionHeader, Tile, WellList, WellRow, type EntityHue } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { IssueList } from './PlanSections'
import { coversLabel, humanize, issuesAt, issueWhere, requirementGroups } from './planLogic'
import type { PlanBody, PlanIssue } from './planTypes'

type Edit = (fn: (b: PlanBody) => void) => void

const Muted = ({ children, className }: { children: ReactNode; className?: string }) => (
  <span className={cn('text-[12.5px] text-muted-foreground', className)}>{children}</span>
)

function DocHeading({ n, title, sub }: { n: number; title: string; sub?: string }) {
  return (
    <div className="flex items-baseline gap-2.5">
      <span className="inline-flex size-5 shrink-0 items-center justify-center rounded-full glass-orb-clear text-[11px] font-semibold text-primary tabular-nums">{n}</span>
      <h3 className="text-[15px] font-semibold tracking-[-0.01em]">{title}</h3>
      {sub ? <Muted className="min-w-0 truncate">{sub}</Muted> : null}
    </div>
  )
}

export function IssueSummary({ issues }: { issues: PlanIssue[] }) {
  if (!issues.length) return null
  const errors = issues.filter((i) => i.severity === 'error').length
  const warnings = issues.length - errors
  return (
    <Tile className="p-4" data-testid="plan-issues-summary">
      <SectionHeader
        title="Before you approve"
        sub={[errors ? `${errors} to fix` : '', warnings ? `${warnings} to look at` : ''].filter(Boolean).join(' · ')}
        icon={<AlertTriangle />}
        hue={errors ? 'runs' : 'release'}
      />
      <div className="mt-3 flex flex-col gap-1">
        {issues.map((i, n) => (
          <Callout key={`${i.code}-${i.path}-${n}`} tone={i.severity === 'error' ? 'error' : 'warn'} icon={<AlertTriangle />}>
            <span className="font-medium">{issueWhere(i.path)}</span>: {i.message}
          </Callout>
        ))}
      </div>
    </Tile>
  )
}

export function RequirementsDoc({
  body,
  issues,
  editable,
  onChange,
  confirmed,
  onConfirm,
  meta,
}: {
  body: PlanBody
  issues?: PlanIssue[]
  editable: boolean
  onChange: (b: PlanBody) => void
  confirmed?: ReadonlySet<string>
  onConfirm?: (assumption: string) => void
  meta?: ReactNode
}) {
  const edit: Edit = (fn) => {
    const next = structuredClone(body)
    fn(next)
    onChange(next)
  }
  const r = body.requirements
  const groups = requirementGroups(body)

  return (
    <Tile as="article" className="flex flex-col gap-7 p-5 @min-[640px]/plan:p-7" data-testid="plan-requirements">
      <header className="flex flex-col gap-1.5">
        {meta ? <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-muted-foreground">{meta}</div> : null}
        <h2 className="text-[24px] leading-tight font-[650] tracking-[-0.03em]" data-testid="plan-title">{body.title || 'Untitled plan'}</h2>
        {body.summary ? <p className="max-w-[68ch] text-[14px] leading-[22px] text-foreground/85">{body.summary}</p> : null}
      </header>

      <section className="flex flex-col gap-3" data-testid="plan-doc-requirements">
        <DocHeading n={1} title="What it must do" sub={`${groups.reduce((n, g) => n + g.items.length, 0)} requirements`} />
        {groups.length === 0 ? <Muted>No requirements yet.</Muted> : null}
        <div className="grid grid-cols-1 gap-x-8 gap-y-4 @min-[720px]/plan:grid-cols-2">
          {groups.map((g) => {
            const gi = g.items.flatMap((i) => issuesAt(issues, `routes.${i.route}`))
            return (
              <div key={g.key || '~other'} className="min-w-0" data-testid={`plan-req-${g.key || 'other'}`}>
                <div className="text-[13px] font-semibold">{g.title}</div>
                {g.purpose ? <Muted>{g.purpose}</Muted> : null}
                <ul className="mt-1.5 flex flex-col gap-1">
                  {g.items.map((i) => {
                    const bad = issuesAt(issues, `routes.${i.route}`).some((x) => x.severity === 'error')
                    return (
                      <li key={i.route} className="flex items-start gap-2 text-[13px] leading-5" title={i.route}>
                        <span aria-hidden className={cn('mt-[7px] size-1.5 shrink-0 rounded-full', bad ? 'bg-destructive' : 'bg-primary/60')} />
                        <span className="min-w-0">{i.text}</span>
                      </li>
                    )
                  })}
                  {!g.items.length ? <Muted>Nothing for users directly.</Muted> : null}
                </ul>
                <IssueList issues={gi} />
              </div>
            )
          })}
        </div>
        {r.answers.length ? (
          <div className="mt-1 flex flex-col gap-1.5" data-testid="plan-answers">
            <div className="text-[13px] font-semibold">Decided in the questions</div>
            <WellList>
              {r.answers.map((a) => (
                <WellRow key={a.id} className="flex-col items-stretch gap-0.5 py-2.5 @min-[640px]/plan:flex-row @min-[640px]/plan:items-baseline @min-[640px]/plan:gap-3">
                  <Muted className="@min-[640px]/plan:w-[42%] @min-[640px]/plan:shrink-0">{a.question}</Muted>
                  <span className="min-w-0 text-[13px]">{a.answer}</span>
                </WellRow>
              ))}
            </WellList>
          </div>
        ) : null}
        {r.limitations.length ? (
          <div className="flex flex-col gap-1" data-testid="plan-limitations">
            <div className="text-[13px] font-semibold">Out of scope</div>
            <ul className="flex flex-col gap-1">
              {r.limitations.map((l, n) => (
                <li key={n} className="flex items-start gap-2 text-[13px] leading-5 text-muted-foreground">
                  <span aria-hidden className="mt-[9px] h-px w-2 shrink-0 bg-muted-foreground" />
                  {l}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <section className="flex flex-col gap-3" data-testid="plan-doc-acceptance">
        <DocHeading n={2} title="Acceptance criteria" sub="how we know it's done" />
        {r.acceptance.length ? (
          <ul className="flex flex-col gap-2">
            {r.acceptance.map((a) => (
              <li key={a.id} className="flex items-start gap-2.5" data-testid={`plan-ac-${a.id}`}>
                <CircleCheck className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] leading-5">{a.text}</div>
                  {a.covers.length ? (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {a.covers.map((c) => (
                        <Badge key={c} variant="neutral">{coversLabel(c)}</Badge>
                      ))}
                    </div>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Muted>No acceptance criteria stated.</Muted>
        )}
      </section>

      <section className="flex flex-col gap-3" data-testid="plan-doc-assumptions">
        <DocHeading n={3} title="Assumptions" sub={editable ? 'confirm each, or correct it' : undefined} />
        {r.assumptions.length ? (
          <WellList>
            {r.assumptions.map((a, n) => {
              const ok = confirmed?.has(a) ?? false
              return (
                <WellRow key={n} className="flex-wrap gap-y-1.5 py-2" data-testid={`plan-assumption-${n}`}>
                  {ok ? <Badge variant="success"><Check /> confirmed</Badge> : <Badge variant="warning">assumed</Badge>}
                  {editable ? (
                    <Input
                      className="h-7 min-w-[12rem] flex-1 text-[13px]"
                      value={a}
                      aria-label={`Assumption ${n + 1}`}
                      onChange={(e) => edit((b) => { b.requirements.assumptions[n] = e.target.value })}
                    />
                  ) : (
                    <span className="min-w-0 flex-1 text-[13px]">{a}</span>
                  )}
                  {editable ? (
                    <span className="flex items-center gap-1">
                      {!ok && onConfirm ? (
                        <Button size="xs" variant="secondary" onClick={() => onConfirm(a)} data-testid={`plan-assumption-confirm-${n}`}>
                          <Check /> Confirm
                        </Button>
                      ) : null}
                      <Button
                        size="icon-xs"
                        variant="ghost"
                        aria-label={`Remove assumption ${n + 1}`}
                        onClick={() => edit((b) => { b.requirements.assumptions.splice(n, 1) })}
                      >
                        <Trash2 />
                      </Button>
                    </span>
                  ) : null}
                </WellRow>
              )
            })}
          </WellList>
        ) : (
          <Muted>None. Everything above was stated or answered.</Muted>
        )}
      </section>

      <section className="flex flex-col gap-3" data-testid="plan-doc-roles">
        <DocHeading n={4} title="Who does what" />
        {editable ? (
          <Textarea
            rows={2}
            value={body.roles_note}
            placeholder="Who uses it, and who may do what"
            aria-label="Roles"
            onChange={(e) => edit((b) => { b.roles_note = e.target.value })}
          />
        ) : (
          <p className="flex items-start gap-2 text-[13px] leading-5">
            <Users className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
            {body.roles_note || <Muted>Not stated.</Muted>}
          </p>
        )}
        <Muted>Sign-in and identity are outside this plan; an approver's name is typed, not verified.</Muted>
      </section>
    </Tile>
  )
}

function Group({ title, icon, hue, children, testId }: { title: string; icon: ReactNode; hue: EntityHue; children: ReactNode; testId: string }) {
  return (
    <section className="flex flex-col gap-2" data-testid={testId}>
      <div className="flex items-center gap-2">
        <IconTile hue={hue} className="size-6">{icon}</IconTile>
        <span className="text-[13px] font-semibold">{title}</span>
      </div>
      {children}
    </section>
  )
}

/** "What we'll build", in plain language. */
export function WhatWeBuild({ body, issues }: { body: PlanBody; issues?: PlanIssue[] }) {
  return (
    <Tile className="flex flex-col gap-5 p-5 @min-[640px]/plan:p-7" data-testid="plan-what-we-build">
      <div className="flex flex-col gap-0.5">
        <h3 className="text-[17px] font-semibold tracking-[-0.02em]">What we'll build</h3>
        <Muted>
          {body.apps.length} app{body.apps.length === 1 ? '' : 's'}
          {' · '}
          {body.integrations.length} integration{body.integrations.length === 1 ? '' : 's'}
          {' · '}
          {body.secrets.length} secret{body.secrets.length === 1 ? '' : 's'} needed
        </Muted>
      </div>

      {body.apps.map((a) => (
        <Group key={a.key} title={a.name || humanize(a.key)} icon={<AppWindow />} hue="apps" testId={`plan-build-app-${a.key}`}>
          {a.users ? <Muted>For {a.users}</Muted> : null}
          <WellList>
            {a.screens.map((s) => (
              <WellRow key={s.name} className="flex-col items-stretch gap-1 py-2.5" data-testid={`plan-doc-screen-${s.name}`}>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-[13px] font-medium">{s.name}</span>
                  {s.purpose ? <Muted>{s.purpose}</Muted> : null}
                </div>
                {s.journeys.length ? (
                  <ul className="flex flex-col gap-0.5">
                    {s.journeys.map((j, n) => (
                      <li key={n} className="text-[12.5px] text-foreground/80">→ {j}</li>
                    ))}
                  </ul>
                ) : null}
                {s.states.empty ? <Muted>When empty: {s.states.empty}</Muted> : null}
                <IssueList issues={issuesAt(issues, `apps.${a.key}.screens.${s.name}`)} />
              </WellRow>
            ))}
            {!a.screens.length ? <WellRow><Muted>No screens planned.</Muted></WellRow> : null}
          </WellList>
        </Group>
      ))}
      {!body.apps.length ? <Muted>No apps: this plan builds the backend only.</Muted> : null}

      {body.integrations.length ? (
        <Group title="Connects to" icon={<Plug />} hue="flow" testId="plan-doc-integrations">
          <WellList>
            {body.integrations.map((i) => (
              <WellRow key={i.key} className="flex-col items-stretch gap-0.5 py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[13px] font-medium">{humanize(i.key)}</span>
                  <Badge variant="neutral">{i.kind}</Badge>
                </div>
                <Muted>
                  {[
                    i.purpose,
                    i.job_types.length ? `runs ${i.job_types.join(', ')} jobs` : '',
                    i.secrets.length ? `signs in with ${i.secrets.join(', ')}` : '',
                    i.smoke ? 'proven by a smoke run after it is built' : '',
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </Muted>
                <IssueList issues={issuesAt(issues, `integrations.${i.key}`)} />
              </WellRow>
            ))}
          </WellList>
        </Group>
      ) : null}

      {body.secrets.length ? (
        <Group title="Secrets you'll need to provide" icon={<KeyRound />} hue="secrets" testId="plan-doc-secrets">
          <WellList>
            {body.secrets.map((s) => (
              <WellRow key={s.name} className="flex-col items-stretch gap-0.5 py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[12.5px]">{s.name}</span>
                  {s.purpose ? <Muted>{s.purpose}</Muted> : null}
                </div>
                <Muted>
                  {s.environments.length ? `in ${s.environments.join(', ')}` : 'every environment'}
                  {s.allowed_hosts.length ? (
                    <>
                      {' '}
                      <Dot /> only sent to {s.allowed_hosts.join(', ')}
                    </>
                  ) : null}
                </Muted>
                <IssueList issues={issuesAt(issues, `secrets.${s.name}`)} />
              </WellRow>
            ))}
          </WellList>
        </Group>
      ) : null}
    </Tile>
  )
}
