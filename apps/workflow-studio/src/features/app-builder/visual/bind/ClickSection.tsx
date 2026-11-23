/** The inspector's On click section: what a button does now, and a small set of actions to give it without code. */
import { useMemo, useState } from 'react'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import type { ElementView } from '../editor'
import { AskFix } from '../AskFix'
import { Note, Pick, Section } from './controls'
import { appScreens, buildClick, callableOps, readClick, type ClickChoice, type ClickStep } from './actions'
import { refetches, stateVars, type ApiOp, type Built, type Ctx } from './scope'

type Kind = 'none' | 'api' | 'open' | 'navigate' | 'toast' | 'refetch'

const NEW = '__new__'
const NO_REFETCH = '__none__'

function kindOf(steps: ClickStep[]): Kind {
  const s = steps.find((x) => x.kind !== 'refetch') ?? steps[0]
  return s?.kind ?? 'none'
}

export function ClickSection({ ctx, view, ops, disabled, run }: { ctx: Ctx; view: ElementView; ops: ApiOp[]; disabled: boolean; run: (b: Built) => void }) {
  const screens = useMemo(() => appScreens(ctx.files), [ctx.files])
  const read = useMemo(() => readClick(ctx, screens), [ctx, screens])
  const reloads = useMemo(() => refetches(ctx), [ctx])
  const bools = useMemo(() => stateVars(ctx).filter((v) => v.boolean), [ctx])
  const steps = read.kind === 'ours' ? read.steps : []
  const [kind, setKind] = useState<Kind>(kindOf(steps))
  const firstApi = steps.find((s): s is Extract<ClickStep, { kind: 'api' }> => s.kind === 'api')
  const firstRefetch = steps.find((s): s is Extract<ClickStep, { kind: 'refetch' }> => s.kind === 'refetch')
  const [op, setOp] = useState<string | null>(firstApi?.op ?? null)
  const [refetch, setRefetch] = useState<string>(firstRefetch?.call ?? NO_REFETCH)
  const toastStep = steps.find((s): s is Extract<ClickStep, { kind: 'toast' }> => s.kind === 'toast')
  const [message, setMessage] = useState(toastStep?.message ?? 'Done')
  const apply = (c: ClickChoice) => run(buildClick(ctx, read, c, ops))

  const kinds: Record<string, string> = { none: 'Do nothing', api: 'Call an API', open: 'Open a drawer', toast: 'Show a message' }
  if (screens.length) kinds.navigate = 'Go to a screen'
  if (reloads.length) kinds.refetch = 'Reload data'
  const reloadItems = Object.fromEntries(reloads.map((r) => [r.call, `Reload ${r.label}`]))
  const opItems = Object.fromEntries(callableOps(ctx, ops).filter((x) => x.ok).map(({ op: o }) => [o.name, `${o.method} ${o.name}`]))
  const current = steps.find((s) => s.kind === 'open' || s.kind === 'navigate')

  return (
    <Section title="On click" hint={read.kind === 'ours' ? read.summary : read.kind === 'none' ? 'nothing' : 'custom code'} testId="vbind-click">
      {read.kind === 'custom' ? (
        <>
          <Note testId="vbind-custom-note">Runs custom code. Change it with AI so nothing it does is lost.</Note>
          <AskFix view={view} about="what it does on click" />
        </>
      ) : (
        <>
          <Pick
            value={kind}
            items={kinds}
            onChange={(k) => {
              setKind(k as Kind)
              if (k === 'none') apply({ kind: 'none' })
            }}
            disabled={disabled}
            testId="vbind-click-kind"
          />
          {kind === 'api' && (
            <>
              {Object.keys(opItems).length ? (
                <Pick value={op} placeholder="Choose the API call…" items={opItems} onChange={setOp} disabled={disabled} testId="vbind-click-op" />
              ) : (
                <Note>No API calls this button can make here.</Note>
              )}
              <Pick value={refetch} items={{ [NO_REFETCH]: 'Then reload nothing', ...Object.fromEntries(Object.entries(reloadItems).map(([k, v]) => [k, `Then ${v.charAt(0).toLowerCase()}${v.slice(1)}`])) }} onChange={setRefetch} disabled={disabled} testId="vbind-click-refetch" />
              <Button size="xs" className="self-end" disabled={disabled || !op} onClick={() => op && apply({ kind: 'api', op, refetch: refetch === NO_REFETCH ? null : refetch })} data-testid="vbind-click-apply">
                Apply
              </Button>
            </>
          )}
          {kind === 'open' && (
            <Pick
              value={current?.kind === 'open' ? current.state : null}
              placeholder="Which drawer…"
              items={{ ...Object.fromEntries(bools.map((b) => [b.name, `Open ${b.name}`])), [NEW]: 'New drawer' }}
              onChange={(v) => apply(v === NEW ? { kind: 'openNew' } : { kind: 'open', state: v })}
              disabled={disabled}
              testId="vbind-click-state"
            />
          )}
          {kind === 'navigate' && (
            <Pick
              value={current?.kind === 'navigate' ? current.target : null}
              placeholder="Which screen…"
              items={Object.fromEntries(screens.map((s) => [s.id, s.label]))}
              onChange={(v) => apply({ kind: 'navigate', target: v })}
              disabled={disabled}
              testId="vbind-click-screen"
            />
          )}
          {kind === 'toast' && (
            <div className="flex items-center gap-1.5">
              <Input className="h-7 text-[12.5px]" value={message} onChange={(e) => setMessage(e.target.value)} disabled={disabled} data-testid="vbind-click-message" />
              <Button size="xs" disabled={disabled || !message.trim()} onClick={() => apply({ kind: 'toast', message })} data-testid="vbind-click-apply">
                Apply
              </Button>
            </div>
          )}
          {kind === 'refetch' && (
            <Pick value={steps.length === 1 && firstRefetch ? firstRefetch.call : null} placeholder="Which data…" items={reloadItems} onChange={(v) => apply({ kind: 'refetch', call: v })} disabled={disabled} testId="vbind-click-refetch" />
          )}
        </>
      )}
    </Section>
  )
}
