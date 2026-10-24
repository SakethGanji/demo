/** The inspector's Visible when section: always, a viewer role, a true/false state, or once data has loaded. */
import { useMemo, useState } from 'react'
import type { ElementView } from '../editor'
import { AskFix } from '../AskFix'
import { Note, Pick, Section } from './controls'
import { appRoles, buildVisible, dataConds, readVisible, type Cond } from './visibility'
import { stateVars, type Built, type Ctx } from './scope'

type Kind = Cond['kind']

export function VisibleSection({ ctx, view, disabled, run }: { ctx: Ctx; view: ElementView; disabled: boolean; run: (b: Built) => void }) {
  const data = useMemo(() => dataConds(ctx), [ctx])
  const read = useMemo(() => readVisible(ctx, data), [ctx, data])
  const roles = useMemo(() => appRoles(ctx.files), [ctx.files])
  const bools = useMemo(() => stateVars(ctx).filter((v) => v.boolean), [ctx])
  const cur = read.ok ? read.current : null
  const [kind, setKind] = useState<Kind>(cur && cur.kind !== 'custom' ? cur.kind : 'always')

  if (!read.ok) {
    return (
      <Section title="Visible when" testId="vbind-visible">
        <Note testId="vbind-visible-reason">{read.reason}</Note>
      </Section>
    )
  }
  const apply = (c: Cond) => run(buildVisible(ctx, read, c))
  const kinds: Record<string, string> = { always: 'Always', role: 'The viewer has a role' }
  if (bools.length) kinds.state = 'A switch in this screen is on / off'
  if (data.length) kinds.data = 'Data has loaded'
  const stateItems = Object.fromEntries(bools.flatMap((b) => [[b.name, `${b.name} is on`], [`!${b.name}`, `${b.name} is off`]]))

  return (
    <Section title="Visible when" hint={cur?.kind === 'custom' ? 'custom condition' : undefined} testId="vbind-visible">
      {cur?.kind === 'custom' ? (
        <>
          <Note testId="vbind-custom-note">Shown when <span className="font-mono">{cur.src}</span>; change it with AI.</Note>
          <AskFix view={view} about="when it is shown" />
        </>
      ) : (
        <>
          <Pick
            value={kind}
            items={kinds}
            onChange={(k) => {
              setKind(k as Kind)
              if (k === 'always') apply({ kind: 'always' })
            }}
            disabled={disabled}
            testId="vbind-visible-kind"
          />
          {kind === 'role' && (
            <Pick value={cur?.kind === 'role' ? cur.role : null} placeholder="Which role…" items={Object.fromEntries(roles.map((r) => [r, r]))} onChange={(r) => apply({ kind: 'role', role: r })} disabled={disabled} testId="vbind-visible-role" />
          )}
          {kind === 'state' && (
            <Pick
              value={cur?.kind === 'state' ? `${cur.negate ? '!' : ''}${cur.name}` : null}
              placeholder="Which switch…"
              items={stateItems}
              onChange={(v) => apply({ kind: 'state', name: v.replace(/^!/, ''), negate: v.startsWith('!') })}
              disabled={disabled}
              testId="vbind-visible-state"
            />
          )}
          {kind === 'data' && (
            <Pick value={cur?.kind === 'data' ? cur.expr : null} placeholder="Which data…" items={Object.fromEntries(data.map((d) => [d.expr, d.label]))} onChange={(e) => apply({ kind: 'data', expr: e })} disabled={disabled} testId="vbind-visible-data" />
          )}
        </>
      )}
    </Section>
  )
}
