/**
 * Settings · Environments (UI-DESIGN §3.7): list with kind, default/live selectors, add/remove.
 * Every edit is validated by the project PUT dry run (review D12: it refuses exactly what the
 * save refuses, e.g. `409 environment_in_use`), then saved with the real PUT.
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { projectsApi } from '@/shared/api/endpoints'
import { errorMessage, isApiError } from '@/shared/api/http'
import type { EnvKind, ProjectEnvironment } from '@/shared/api/types'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Segmented } from '@/shared/components/ui/segmented'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Spinner } from '@/shared/components/ui/spinner'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/components/ui/tooltip'
import { InlineError, SectionHeader, useProjectUpdate } from './common'

const sameEnvs = (a: ProjectEnvironment[], b: ProjectEnvironment[]) =>
  a.length === b.length && a.every((e, i) => e.name === b[i].name && e.kind === b[i].kind)

export function EnvironmentsSection({ open }: { open: boolean }) {
  const { project, pid } = useProjectCtx()
  const [envs, setEnvs] = useState<ProjectEnvironment[]>(project.environments)
  const [def, setDef] = useState(project.default_environment)
  const [live, setLive] = useState(project.live_environment)
  const [saveError, setSaveError] = useState<string | null>(null)
  const update = useProjectUpdate()

  useEffect(() => {
    if (!open) return
    setEnvs(project.environments)
    setDef(project.default_environment)
    setLive(project.live_environment)
    setSaveError(null)
  }, [open, project.environments, project.default_environment, project.live_environment])

  const dirty = !sameEnvs(envs, project.environments) || def !== project.default_environment || live !== project.live_environment
  const body = { environments: envs, default_environment: def, live_environment: live }
  const named = envs.every((e) => e.name.trim())
  const dry = useQuery({
    queryKey: ['project', pid, 'env-preview', JSON.stringify(body)],
    queryFn: () => projectsApi.previewUpdate(pid, body),
    enabled: open && dirty && named,
    retry: false,
  })
  const dryError = dirty && dry.isError ? dry.error : null
  const names = envs.map((e) => e.name).filter(Boolean)
  const isDefaultProject = project.id === 'proj_default'

  const setEnv = (i: number, patch: Partial<ProjectEnvironment>) =>
    setEnvs((prev) => prev.map((e, j) => (j === i ? { ...e, ...patch } : e)))

  return (
    <div className="flex flex-col gap-3" data-testid="settings-environments">
      <SectionHeader
        title="Environments"
        sub="secrets and releases are per environment; data is shared"
        actions={
          <Button
            variant="outline"
            size="xs"
            onClick={() => setEnvs((prev) => [...prev, { name: '', kind: 'draft' }])}
            data-testid="env-add"
          >
            <Plus /> Add
          </Button>
        }
      />
      <div className="overflow-hidden rounded-[10px] border border-border">
        {envs.map((e, i) => {
          const existing = project.environments.some((x) => x.name === e.name) && i < project.environments.length
          return (
            <div
              key={i}
              className="flex h-11 items-center gap-2.5 border-b border-border/65 px-3 last:border-0"
              data-testid={`env-row-${e.name || i}`}
            >
              {existing ? (
                <span className="w-32 truncate font-mono text-[12.5px]">{e.name}</span>
              ) : (
                <Input
                  value={e.name}
                  autoFocus
                  placeholder="staging"
                  onChange={(ev) => setEnv(i, { name: ev.target.value.replace(/[^A-Za-z0-9_-]/g, '') })}
                  className="h-7 w-32 font-mono text-[12.5px]"
                  data-testid="env-new-name"
                />
              )}
              <Segmented<EnvKind>
                size="xs"
                value={e.kind}
                onChange={(k) => setEnv(i, { kind: k })}
                options={[
                  { key: 'draft', label: 'draft', testId: `env-kind-${e.name || i}-draft` },
                  { key: 'release', label: 'release', testId: `env-kind-${e.name || i}-release` },
                ]}
              />
              <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                {e.kind === 'draft' ? 'serves the latest saved code' : 'serves its active release'}
              </span>
              {e.name === def && <Badge variant="neutral">default</Badge>}
              {e.name === live && <Badge variant="primary">live</Badge>}
              <Tooltip>
                <TooltipTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`Remove ${e.name || 'environment'}`}
                      disabled={envs.length <= 1}
                      onClick={() => {
                        const rest = envs.filter((_, j) => j !== i)
                        setEnvs(rest)
                        // Default/live must name a remaining environment.
                        const fallback = rest.find((x) => x.name)?.name
                        if (fallback && def === e.name) setDef(fallback)
                        if (fallback && live === e.name) setLive(fallback)
                      }}
                      data-testid={`env-remove-${e.name || i}`}
                    />
                  }
                >
                  <Trash2 />
                </TooltipTrigger>
                <TooltipContent>Remove</TooltipContent>
              </Tooltip>
            </div>
          )
        })}
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <EnvSelect label="Default" help="route tests and previews run here" value={def} onChange={setDef} names={names} testId="env-default" />
        <EnvSelect label="Live" help="published apps call this" value={live} onChange={setLive} names={names} testId="env-live" />
      </div>
      {isDefaultProject && (
        <div className="text-[11.5px] text-muted-foreground">The Default project keeps its single environment.</div>
      )}
      {(dryError || saveError) && (
        <InlineError testId={isApiError(dryError, 'environment_in_use') ? 'env-in-use' : 'env-error'}>
          {saveError ?? errorMessage(dryError)}
          {isApiError(dryError, 'environment_in_use') && ' — delete its secrets and revoke its API keys first.'}
        </InlineError>
      )}
      {dirty && (
        <div className="flex items-center justify-end gap-2">
          {dry.isFetching && <Spinner className="size-3.5 text-muted-foreground" />}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setEnvs(project.environments)
              setDef(project.default_environment)
              setLive(project.live_environment)
              setSaveError(null)
            }}
          >
            Discard
          </Button>
          <Button
            size="sm"
            disabled={!named || !!dryError || dry.isFetching || update.isPending}
            onClick={() => {
              setSaveError(null)
              update.mutate(body, { onError: (e) => setSaveError(errorMessage(e)) })
            }}
            data-testid="env-save"
          >
            Save environments
          </Button>
        </div>
      )}
    </div>
  )
}

function EnvSelect({
  label,
  help,
  value,
  onChange,
  names,
  testId,
}: {
  label: string
  help: string
  value: string
  onChange: (v: string) => void
  names: string[]
  testId: string
}) {
  return (
    <label className="flex items-center gap-2 text-[13px]">
      <span className="font-medium">{label}</span>
      <Select value={value} onValueChange={(v) => v && onChange(v as string)}>
        <SelectTrigger size="sm" className="min-w-24 font-mono text-[12.5px]" data-testid={testId}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {names.map((n) => (
            <SelectItem key={n} value={n} className="font-mono text-[12.5px]">
              {n}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <span className="text-xs text-muted-foreground">{help}</span>
    </label>
  )
}
