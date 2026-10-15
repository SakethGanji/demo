/**
 * CreateSecretPopover (UI-DESIGN §3.5): shared by the code widget, release checks, publish
 * pre-flight, the Secrets view and setup step 1. One `POST …/secrets` per checked environment.
 */
import { useEffect, useState } from 'react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useCreateSecrets } from './useSecretMutations'
import { isApiError, errorMessage } from '@/shared/api/http'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { ChipsInput } from '@/shared/components/ui/chips-input'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { Segmented } from '@/shared/components/ui/segmented'
import type { EffectiveVar, VarType } from '@/shared/api/types'

export interface CreateSecretFormProps {
  /** prefilled key; read-only when it comes from a reference */
  secretKey?: string
  keyReadOnly?: boolean
  environments?: string[]
  hosts?: string[]
  onCreated?: (created: EffectiveVar[]) => void
  onCancel?: () => void
  testId?: string
}

export function CreateSecretForm({
  secretKey = '',
  keyReadOnly = false,
  environments,
  hosts = [],
  onCreated,
  onCancel,
  testId = 'create-secret',
}: CreateSecretFormProps) {
  const { project, pid } = useProjectCtx()
  const allEnvs = project.environments.map((e) => e.name)
  const [key, setKey] = useState(secretKey)
  const [value, setValue] = useState('')
  const [type, setType] = useState<VarType>('secret')
  const [envs, setEnvs] = useState<string[]>(environments ?? allEnvs)
  const [allowed, setAllowed] = useState<string[]>(hosts)
  const [error, setError] = useState<string | null>(null)
  const create = useCreateSecrets(pid)

  useEffect(() => setKey(secretKey), [secretKey])

  return (
    <form
      className="flex flex-col gap-3"
      data-testid={testId}
      onSubmit={(e) => {
        e.preventDefault()
        setError(null)
        create.mutate(
          { key: key.trim(), value, environments: envs, type, allowed_hosts: allowed },
          {
            onSuccess: (created) => {
              setValue('')
              onCreated?.(created)
            },
            onError: (err) => {
              if (isApiError(err, undefined, 409)) {
                const env = /environment '([^']+)'/.exec(err.message)?.[1] ?? envs.join(', ')
                setError(`Already set in ${env}: edit instead`)
              } else setError(errorMessage(err))
            },
          },
        )
      }}
    >
      <Field label="Key" error={error} errorTestId={`${testId}-error`}>
        <Input
          value={key}
          readOnly={keyReadOnly}
          onChange={(e) => setKey(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_'))}
          className="font-mono"
          placeholder="PAYMENTS_TOKEN"
          data-testid={`${testId}-key`}
        />
      </Field>
      <Field label="Value" help="Write-only: it is never shown again.">
        <Input
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus={keyReadOnly}
          data-testid={`${testId}-value`}
        />
      </Field>
      <div className="flex items-center gap-4">
        <span className="text-xs font-medium">Environments</span>
        {allEnvs.map((env) => (
          <label key={env} className="inline-flex items-center gap-1.5 text-[13px]">
            <Checkbox
              checked={envs.includes(env)}
              onCheckedChange={(c) => setEnvs((prev) => (c ? [...new Set([...prev, env])] : prev.filter((x) => x !== env)))}
              data-testid={`${testId}-env-${env}`}
            />
            {env}
          </label>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium">Type</span>
        <Segmented<VarType>
          value={type}
          onChange={setType}
          size="xs"
          options={[
            { key: 'secret', label: 'secret' },
            { key: 'string', label: 'string' },
            { key: 'number', label: 'number' },
          ]}
        />
      </div>
      <Field label="Allowed hosts" help="Python calls carrying this secret may only go to these hosts.">
        <ChipsInput value={allowed} onChange={setAllowed} testId={`${testId}-hosts`} />
      </Field>
      <div className="flex justify-end gap-2">
        {onCancel && (
          <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
            Cancel
          </Button>
        )}
        <Button type="submit" size="sm" disabled={!key.trim() || !value || envs.length === 0 || create.isPending} data-testid={`${testId}-save`}>
          {envs.length === 1 ? `Add to ${envs[0]}` : 'Add secret'}
        </Button>
      </div>
    </form>
  )
}

/** The popover wrapper; `trigger` is the element that opens it (rendered via Base UI `render`). */
export function CreateSecretPopover({
  trigger,
  open,
  onOpenChange,
  ...form
}: CreateSecretFormProps & {
  trigger: React.ReactElement
  open?: boolean
  onOpenChange?: (o: boolean) => void
}) {
  const [inner, setInner] = useState(false)
  const isOpen = open ?? inner
  const setOpen = onOpenChange ?? setInner
  return (
    <Popover open={isOpen} onOpenChange={setOpen}>
      <PopoverTrigger render={trigger} />
      <PopoverContent className="w-[340px]" align="start">
        {isOpen && (
          <CreateSecretForm
            {...form}
            onCreated={(c) => {
              setOpen(false)
              form.onCreated?.(c)
            }}
            onCancel={() => setOpen(false)}
          />
        )}
      </PopoverContent>
    </Popover>
  )
}
