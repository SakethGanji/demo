/**
 * Route settings for a saved code route (UI-DESIGN §11 "Route settings"): description,
 * response mode, validate_request and timeout. These are owned by the route, not the decorator,
 * and survive module saves (§9 C8). `PUT …/routes/{R}?dry_run=true` previews, then `PUT` saves.
 */
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { SlidersHorizontal } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useRoutes } from '@/features/project/hooks/useProjectData'
import { routesApi } from '@/shared/api/endpoints'
import { errorMessage } from '@/shared/api/http'
import { invalidate } from '@/shared/api/queryKeys'
import type { ResponseMode, Route } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { Segmented } from '@/shared/components/ui/segmented'
import { Switch } from '@/shared/components/ui/switch'
import { Textarea } from '@/shared/components/ui/textarea'

export function RouteSettingsButton({ routeId }: { routeId: string }) {
  const { pid } = useProjectCtx()
  const routes = useRoutes(pid)
  const route = routes.data?.find((r) => r.id === routeId)
  const [open, setOpen] = useState(false)
  if (!route) return null
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Route settings" data-testid="route-settings" />}>
        <SlidersHorizontal className="size-3.5" />
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[340px]" data-testid="route-settings-popover">
        {open && <RouteSettingsForm route={route} onDone={() => setOpen(false)} />}
      </PopoverContent>
    </Popover>
  )
}

function RouteSettingsForm({ route, onDone }: { route: Route; onDone: () => void }) {
  const { pid } = useProjectCtx()
  const qc = useQueryClient()
  const [description, setDescription] = useState(route.description ?? '')
  const [mode, setMode] = useState<ResponseMode>(route.response_mode)
  const [validate, setValidate] = useState(route.validate_request)
  const [timeout, setTimeoutS] = useState(String(route.timeout_s ?? 30))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  useEffect(() => setDescription(route.description ?? ''), [route.description])

  const save = async () => {
    setBusy(true)
    setError(null)
    const t = Number(timeout)
    const body: Partial<Route> = {
      description: description.trim() || null,
      response_mode: mode,
      validate_request: validate,
      ...(Number.isFinite(t) && t > 0 ? { timeout_s: t } : {}),
    }
    try {
      if (!confirm) {
        const dry = await routesApi.update(pid, route.id, body, true)
        if (dry.breaking_changes?.length) {
          setError(`Breaking for apps: ${dry.breaking_changes.map((c) => c.kind).join(', ')}. Save again to confirm.`)
          setConfirm(true)
          return
        }
      }
      await routesApi.update(pid, route.id, body)
      await invalidate.routesChanged(qc, pid)
      toast.success(`Saved settings for ${route.method} ${route.path}`)
      onDone()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      <div className="text-[13px] font-medium">
        {route.method} <span className="font-mono">{route.path}</span>
      </div>
      <Field label="Description" help="Shown in the API docs and given to the app builder AI.">
        <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} data-testid="route-description" />
      </Field>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium">Response</span>
        <Segmented<ResponseMode>
          value={mode}
          onChange={setMode}
          size="xs"
          options={[
            { key: 'auto', label: 'auto' },
            { key: 'first_item', label: 'first item' },
            { key: 'all_items', label: 'all items' },
          ]}
        />
      </div>
      <label className="flex items-center justify-between gap-2 text-xs font-medium">
        Validate requests against the schema
        <Switch checked={validate} onCheckedChange={setValidate} data-testid="route-validate" />
      </label>
      <Field label="Timeout (seconds)">
        <Input value={timeout} onChange={(e) => setTimeoutS(e.target.value)} inputMode="numeric" className="h-7 w-24" data-testid="route-timeout" />
      </Field>
      {error && <div className="text-xs text-destructive">{error}</div>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={busy} data-testid="route-settings-save">
          {confirm ? 'Save anyway' : 'Save'}
        </Button>
      </div>
    </form>
  )
}
