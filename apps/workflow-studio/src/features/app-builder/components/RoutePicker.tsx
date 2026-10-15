/**
 * Route picker (UI-DESIGN §5.2, review A2): the composer chip "N routes ⌃" / "Attach routes"
 * opens a checklist; Done writes `definition.studio.route_ids` (PUT /api/apps/{A}) and every
 * later chat body sends them. The same checklist is used by the App settings sheet (§6.4).
 */
import { useEffect, useState } from 'react'
import { ChevronUp, Plug } from 'lucide-react'
import type { Route } from '@/shared/api/types'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Button } from '@/shared/components/ui/button'
import { Badge } from '@/shared/components/ui/badge'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { sortRoutes } from '@/features/project/lib/routes'
import { errorMessage } from '@/shared/api/http'
import { cn } from '@/shared/lib/utils'

export function RouteChecklist({
  routes,
  value,
  onChange,
  testIdPrefix,
  className,
}: {
  routes: Route[]
  value: string[]
  onChange: (ids: string[]) => void
  testIdPrefix: string
  className?: string
}) {
  return (
    <div className={cn('flex flex-col', className)}>
      {sortRoutes(routes).map((r) => {
        const serverOnly = r.auth === 'api_key'
        const on = value.includes(r.id)
        return (
          <label
            key={r.id}
            className={cn(
              'flex h-[30px] items-center gap-2 rounded-md px-1.5 hover:bg-muted',
              serverOnly && 'cursor-not-allowed opacity-60 hover:bg-transparent',
            )}
            data-testid={`${testIdPrefix}-${r.name}`}
          >
            <Checkbox
              checked={on}
              disabled={serverOnly}
              onCheckedChange={(c) => onChange(c ? [...new Set([...value, r.id])] : value.filter((x) => x !== r.id))}
              aria-label={`${r.method} ${r.path}`}
            />
            <MethodBadge method={r.method} />
            <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">{r.path}</span>
            {r.handler.type === 'stub' && <Badge variant="stub">stub</Badge>}
            {serverOnly && <Badge variant="neutral">server-only</Badge>}
          </label>
        )
      })}
    </div>
  )
}

export function RoutePicker({
  routes,
  value,
  onSave,
  disabled,
}: {
  routes: Route[]
  value: string[]
  onSave: (ids: string[]) => Promise<unknown>
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<string[]>(value)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (open) {
      setDraft(value.filter((id) => routes.some((r) => r.id === id)))
      setError(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  const count = value.filter((id) => routes.some((r) => r.id === id)).length

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        disabled={disabled}
        data-testid="builder-routes-chip"
        className={cn(
          'inline-flex h-6 items-center gap-1 rounded-full border border-border bg-background px-2 text-[11.5px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50',
          open && 'border-ring text-foreground ring-3 ring-ring/30',
        )}
      >
        <Plug className="size-3" />
        {count > 0 ? `${count} ${count === 1 ? 'route' : 'routes'}` : 'Attach routes'}
        <ChevronUp className="size-3" />
      </PopoverTrigger>
      <PopoverContent side="top" align="start" sideOffset={8} className="w-[336px] p-0" data-testid="builder-route-picker">
        <div className="flex items-center gap-2 px-3 pb-1.5 pt-2.5">
          <span className="font-semibold">Routes for the next messages</span>
          <div className="flex-1" />
          <span className="text-[11.5px] text-muted-foreground">saved with the app</span>
        </div>
        <div className="max-h-[260px] overflow-y-auto px-1.5 pb-1.5">
          {routes.length === 0 ? (
            <div className="px-2 py-3 text-xs text-muted-foreground">This project has no routes yet.</div>
          ) : (
            <RouteChecklist routes={routes} value={draft} onChange={setDraft} testIdPrefix="route-picker-row" />
          )}
        </div>
        {error && (
          <div className="px-3 pb-2 text-[11.5px] text-destructive" role="alert" data-testid="route-picker-error">
            {error}
          </div>
        )}
        <div className="flex items-center gap-2 border-t border-border px-3 py-2">
          <span className="flex-1 text-[11.5px] leading-4 text-muted-foreground">
            The AI always sees every route; checked ones are its focus.
          </span>
          <Button
            size="xs"
            disabled={saving}
            data-testid="route-picker-done"
            onClick={async () => {
              setSaving(true)
              setError(null)
              try {
                await onSave(draft)
                setOpen(false)
              } catch (e) {
                setError(errorMessage(e))
              } finally {
                setSaving(false)
              }
            }}
          >
            Done
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
