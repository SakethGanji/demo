/**
 * First-run guide (UI-DESIGN §3.3, `project-first-run.png`). Steps 1–2 in any order; 3–4 are
 * gated on a route existing.
 */
import { useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { BarChart3, Check, Code2, Eye, EyeOff, Globe, Lock, Sparkles, Table2, WandSparkles, Workflow } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '../ProjectContext'
import type { SetupState } from '../hooks/useSetup'
import { useRoutes, useModules } from '../hooks/useProjectData'
import { moduleNameOf } from '../lib/routes'
import { useCreateSecrets } from '@/features/secrets/useSecretMutations'
import { Badge } from '@/shared/components/ui/badge'
import { Tile, Well, Page } from '@/shared/components/ui/surface'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { ChipsInput } from '@/shared/components/ui/chips-input'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { Kbd } from '@/shared/components/ui/kbd'
import { errorMessage } from '@/shared/api/http'
import { cn } from '@/shared/lib/utils'

export const STARTERS = [
  {
    key: 'crud',
    label: 'Table CRUD',
    icon: Table2,
    prompt: 'Store items in a table. POST /items creates one, GET /items lists them, DELETE /items/{id} removes one.',
  },
  {
    key: 'wrap',
    label: 'Wrap an API',
    icon: Globe,
    prompt: 'Call an external HTTP API with a secret token and return a trimmed response from GET /status.',
  },
  {
    key: 'summary',
    label: 'Summary',
    icon: BarChart3,
    prompt: 'GET /summary returns totals grouped by category from a table.',
  },
]

export function SetupGuide({ setup }: { setup: SetupState }) {
  const { project, slug } = useProjectCtx()
  return (
    <Page data-testid="setup-guide">
      <div className="mx-auto max-w-[760px]">
        <div className="flex items-center justify-between text-[12.5px] text-muted-foreground">
          <span>New project · {slug}</span>
          <span className="flex items-center gap-2" data-testid="setup-progress">
            {setup.done} of 4
            <span className="flex gap-1">
              {[0, 1, 2, 3].map((i) => (
                <span key={i} className={cn('h-1 w-6 rounded-full transition-colors duration-(--d-base)', i < setup.done ? 'bg-primary' : 'bg-foreground/12')} />
              ))}
            </span>
          </span>
        </div>
        <h1 className="mt-3 text-title leading-[1.1] font-[650] tracking-[-0.03em]">Set up {project.name}</h1>
        <p className="mt-1.5 text-[13px] text-muted-foreground">
          Four steps from nothing to a live app. Steps 1–2 can be done in any order; 3–4 unlock once a route exists.
        </p>

        <Tile className="mt-6 p-2">
          <Well className="flex flex-col p-1.5">
            <SecretStep setup={setup} />
            <GenerateStep done={setup.steps.module} />
            <TestStep setup={setup} />
            <UiStep setup={setup} />
          </Well>
        </Tile>
        <p className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
          <Workflow className="size-3.5 shrink-0 text-hue-flow" />
          <span>
            Prefer a visual canvas? Routes can also be handled by a{' '}
            <Link to="/p/$project/workflows" params={{ project: slug }} className="text-primary hover:underline">
              workflow
            </Link>
            .
          </span>
        </p>
      </div>
    </Page>
  )
}

function StepHead({
  n,
  done,
  current,
  title,
  extra,
  right,
  children,
}: {
  n: number
  done: boolean
  /** the step to do next */
  current?: boolean
  title: string
  extra?: React.ReactNode
  right?: React.ReactNode
  children?: React.ReactNode
}) {
  return (
    <div className="flex gap-3">
      <span
        className={cn(
          'mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold [&_svg]:size-3 [&_svg]:stroke-[2.5]',
          done
            ? 'bg-success text-primary-foreground'
            : current
              ? 'bg-primary text-primary-foreground shadow-[0_0_0_4px_var(--brand-soft)]'
              : 'bg-recess text-muted-foreground shadow-[inset_0_0_0_1px_var(--input)]',
        )}
        data-testid={`setup-step-${n}-state`}
        data-done={done ? 'true' : 'false'}
      >
        {done ? <Check /> : n}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[13.5px] font-semibold tracking-[-0.01em]">{title}</span>
          {extra}
          <div className="flex-1" />
          {right}
        </div>
        {children}
      </div>
    </div>
  )
}

function SecretStep({ setup }: { setup: SetupState }) {
  const { project, pid } = useProjectCtx()
  const envs = project.environments.map((e) => e.name)
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [show, setShow] = useState(false)
  const [hosts, setHosts] = useState<string[]>([])
  const [checked, setChecked] = useState<string[]>(envs)
  const [error, setError] = useState<string | null>(null)
  const create = useCreateSecrets(pid)
  const done = setup.steps.secret
  return (
    <section className="rounded-ctl px-3 py-3.5" data-testid="setup-step-1">
      <StepHead
        n={1}
        done={done}
        current={!done}
        title="Add a secret"
        extra={<Badge variant="secondary">optional</Badge>}
        right={
          !done && (
            <button type="button" className="text-xs text-muted-foreground hover:text-foreground" onClick={setup.skipSecret} data-testid="setup-skip-secret">
              I don't need one
            </button>
          )
        }
      >
        <p className="mt-1 text-[13px] text-muted-foreground">
          API keys your backend calls with. Stored encrypted, write-only — Python sees <code>studio.secret("NAME")</code>, never the
          value.
        </p>
        {!done && (
          <form
            className="mt-3 grid grid-cols-2 gap-x-3 gap-y-3"
            onSubmit={(e) => {
              e.preventDefault()
              setError(null)
              create.mutate(
                { key: name.trim(), value, environments: checked, allowed_hosts: hosts },
                {
                  onSuccess: () => {
                    toast.success(`${name.trim()} saved`, { description: `Set in ${checked.join(' and ')}` })
                    setValue('')
                  },
                  onError: (err) => setError(errorMessage(err)),
                },
              )
            }}
          >
            <Field label="Name" error={error} errorTestId="setup-secret-error">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_'))}
                placeholder="PAYMENTS_TOKEN"
                className="font-mono"
                data-testid="setup-secret-name"
              />
            </Field>
            <Field label="Value">
              <div className="relative">
                <Input
                  type={show ? 'text' : 'password'}
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  autoComplete="off"
                  className="pr-8"
                  data-testid="setup-secret-value"
                />
                <button
                  type="button"
                  aria-label={show ? 'Hide value' : 'Show value'}
                  onClick={() => setShow((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {show ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                </button>
              </div>
            </Field>
            <Field label="Allowed hosts" help="Calls carrying this secret may only go to these hosts.">
              <ChipsInput value={hosts} onChange={setHosts} testId="setup-secret-hosts" />
            </Field>
            <Field label="Environments" help={`Set a different ${envs[envs.length - 1]} value later in Secrets.`}>
              <div className="flex h-8 items-center gap-4">
                {envs.map((env) => (
                  <label key={env} className="inline-flex items-center gap-2 text-[13px]">
                    <Checkbox
                      checked={checked.includes(env)}
                      onCheckedChange={(c) => setChecked((prev) => (c ? [...new Set([...prev, env])] : prev.filter((x) => x !== env)))}
                      data-testid={`setup-secret-env-${env}`}
                    />
                    {env}
                  </label>
                ))}
                {checked.length > 1 && <span className="text-xs text-muted-foreground">· same value</span>}
              </div>
            </Field>
            <div className="col-span-2 flex items-center gap-2">
              <Button type="submit" disabled={!name.trim() || !value || checked.length === 0 || create.isPending} data-testid="setup-secret-save">
                Save secret
              </Button>
              <span className="text-xs text-muted-foreground">then generate your backend</span>
            </div>
          </form>
        )}
      </StepHead>
    </section>
  )
}

function GenerateStep({ done }: { done: boolean }) {
  const { slug } = useProjectCtx()
  const navigate = useNavigate()
  const [prompt, setPrompt] = useState('')
  const go = () =>
    navigate({
      to: '/p/$project/backend/$module',
      params: { project: slug, module: '~new' },
      search: { prompt: prompt.trim(), generate: 1 },
    })
  return (
    <section className="rounded-ctl px-3 py-3.5" data-testid="setup-step-2">
      <StepHead
        n={2}
        done={done}
        current={!done}
        title="Generate your backend"
        extra={
          <Badge variant="ai">
            <Sparkles /> AI
          </Badge>
        }
      >
        <p className="mt-1 text-[13px] text-muted-foreground">
          Describe the API. You get a Python module using node functions — routes appear when you save.
        </p>
        {!done && (
          <div className="mt-3 rounded-ctl bg-raise p-3 shadow-raise-sm transition-shadow duration-(--d-fast) focus-within:shadow-focus">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && prompt.trim()) {
                  e.preventDefault()
                  go()
                }
              }}
              rows={2}
              placeholder='Describe your API… e.g. "Store refunds in a table. POST /refunds creates one, GET /refunds/stats totals them by reason."'
              className="w-full resize-none bg-transparent text-[13px] outline-none placeholder:text-muted-foreground/80"
              data-testid="setup-prompt"
            />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {STARTERS.map((s) => (
                <Button key={s.key} variant="outline" size="sm" onClick={() => setPrompt(s.prompt)} data-testid={`starter-${s.key}`}>
                  <s.icon /> {s.label}
                </Button>
              ))}
              <div className="flex-1" />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigate({ to: '.', search: (x: Record<string, unknown>) => ({ ...x, dialog: 'new-module' }) } as never)}
                data-testid="setup-write-myself"
              >
                <Code2 /> Write it myself
              </Button>
              <Button variant="ai" size="sm" disabled={!prompt.trim()} onClick={go} data-testid="setup-generate">
                <WandSparkles /> Generate <Kbd>⏎</Kbd>
              </Button>
            </div>
          </div>
        )}
      </StepHead>
    </section>
  )
}

function Gate() {
  return (
    <Badge variant="neutral" data-testid="needs-route">
      <Lock /> needs a route
    </Badge>
  )
}

function TestStep({ setup }: { setup: SetupState }) {
  const { pid, slug } = useProjectCtx()
  const routes = useRoutes(pid)
  const modules = useModules(pid)
  const navigate = useNavigate()
  const first = routes.data?.[0]
  const m = first ? moduleNameOf(first, modules.data) : null
  return (
    <section className={cn('rounded-ctl px-3 py-3.5', !setup.hasRoute && 'opacity-70')} data-testid="setup-step-3">
      <StepHead
        n={3}
        done={setup.steps.test}
        current={setup.hasRoute && !setup.steps.test}
        title="Test a route"
        right={
          !setup.hasRoute ? (
            <Gate />
          ) : (
            !setup.steps.test &&
            first && (
              <Button
                variant="outline"
                size="sm"
                data-testid="setup-test-route"
                onClick={() =>
                  m
                    ? navigate({ to: '/p/$project/backend/$module', params: { project: slug, module: m }, search: { route: first.name } })
                    : navigate({ to: '/p/$project/runs', params: { project: slug } })
                }
              >
                Test {first.method} {first.path}
              </Button>
            )
          )
        }
      >
        <p className="mt-1 text-[13px] text-muted-foreground">Send a request and see the response plus every node call it made.</p>
      </StepHead>
    </section>
  )
}

function UiStep({ setup }: { setup: SetupState }) {
  const { slug } = useProjectCtx()
  const navigate = useNavigate()
  return (
    <section className={cn('rounded-ctl px-3 py-3.5', !setup.hasRoute && 'opacity-70')} data-testid="setup-step-4">
      <StepHead
        n={4}
        done={setup.steps.app}
        current={setup.hasRoute && !setup.steps.app}
        title="Add an interface on the backend"
        right={
          !setup.hasRoute ? (
            <Gate />
          ) : (
            !setup.steps.app && (
              <Button
                size="sm"
                data-testid="setup-create-ui"
                onClick={() => navigate({ to: '/p/$project', params: { project: slug }, search: { dialog: 'create-ui' } })}
              >
                New interface
              </Button>
            )
          )
        }
      >
        <p className="mt-1 text-[13px] text-muted-foreground">
          The interface builder gets your routes and a typed client; publish to a link that calls your Python.
        </p>
      </StepHead>
    </section>
  )
}
