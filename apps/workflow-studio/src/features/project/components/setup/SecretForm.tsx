/**
 * The setup guide's secret form: name, write-only value, allowed hosts, environments. Used by a
 * "Needs <KEY>" step, pre-filled with the key. It never blocks anything: the step can be deferred or dismissed.
 */
import { useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '../../ProjectContext'
import { useCreateSecrets } from '@/features/secrets/useSecretMutations'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { ChipsInput } from '@/shared/components/ui/chips-input'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { errorMessage } from '@/shared/api/http'

export function SecretForm({ initialName = '', hint }: { initialName?: string; hint?: string }) {
  const { project, pid } = useProjectCtx()
  const envs = project.environments.map((e) => e.name)
  const [name, setName] = useState(initialName)
  const [value, setValue] = useState('')
  const [show, setShow] = useState(false)
  const [hosts, setHosts] = useState<string[]>([])
  const [checked, setChecked] = useState<string[]>(envs)
  const [error, setError] = useState<string | null>(null)
  const create = useCreateSecrets(pid)
  return (
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
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>
    </form>
  )
}
