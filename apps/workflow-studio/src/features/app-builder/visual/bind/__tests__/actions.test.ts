import { describe, expect, it } from 'vitest'
import { appScreens, buildClick, clickable, readClick } from '../actions'
import { apiOps } from '../scope'
import { after, ctxFor, FILES } from './fixture'

const ops = apiOps(FILES)
const RUNS = 'views/Runs.tsx'

describe('readClick', () => {
  it('reads our shapes as steps', () => {
    expect(readClick(ctxFor(FILES, RUNS, 'Button', 'New'))).toMatchObject({ kind: 'ours', steps: [{ kind: 'open', state: 'open', value: true }] })
    const cancel = readClick(ctxFor(FILES, RUNS, 'Button', 'Cancel'))
    expect(cancel).toMatchObject({ kind: 'ours', steps: [{ kind: 'api', op: 'cancelRun' }, { kind: 'refetch', call: 'runs.refresh()' }] })
    expect(cancel.kind === 'ours' && cancel.summary).toBe('call cancelRun, then reload runs')
    expect(readClick(ctxFor(FILES, 'views/Expenses.tsx', 'button'))).toMatchObject({ kind: 'ours', steps: [{ kind: 'refetch', call: 'reload()' }] })
  })

  it('calls anything else custom, and no handler none', () => {
    expect(readClick(ctxFor(FILES, RUNS, 'Button', 'Custom')).kind).toBe('custom')
    expect(readClick(ctxFor(FILES, RUNS, 'Button', 'Plain')).kind).toBe('none')
  })

  it('applies to buttons and links only (or anything with onClick)', () => {
    expect(clickable(ctxFor(FILES, RUNS, 'Button', 'Plain'))).toBe(true)
    expect(clickable(ctxFor(FILES, RUNS, 'p', 'Hello'))).toBe(false)
  })
})

describe('buildClick', () => {
  const plain = () => ctxFor(FILES, RUNS, 'Button', 'Plain')

  it('never overwrites a custom handler', () => {
    const ctx = ctxFor(FILES, RUNS, 'Button', 'Custom')
    expect(buildClick(ctx, readClick(ctx), { kind: 'toast', message: 'Hi' }, ops)).toMatchObject({ ok: false })
  })

  it('calls an API op and reloads a query', () => {
    const ctx = plain()
    const out = after(buildClick(ctx, readClick(ctx), { kind: 'api', op: 'listRuns', refetch: 'runs.refresh()' }, ops))
    expect(out).toContain('<Button onClick={async () => { await api.listRuns(); runs.refresh() }}>Plain</Button>')
  })

  it('refuses an op that needs an id outside a row', () => {
    const ctx = plain()
    const r = buildClick(ctx, readClick(ctx), { kind: 'api', op: 'cancelRun', refetch: null }, ops)
    expect(r.ok === false && r.error).toMatch(/needs id/)
  })

  it('shows a message through the app shell when there is no Toaster', () => {
    const ctx = plain()
    const out = after(buildClick(ctx, readClick(ctx), { kind: 'toast', message: 'Saved' }, ops))
    expect(out).toContain(`<Button onClick={() => notify('Saved')}>Plain</Button>`)
    expect(out).toContain(`import { DataTable, Stat, Button, usePoll, notify } from '../kit'`)
  })

  it('navigates with the screen’s go, and refuses where there is none', () => {
    const ctx = plain()
    expect(after(buildClick(ctx, readClick(ctx), { kind: 'navigate', target: 'expenses' }, ops))).toContain(`onClick={() => go('expenses')}`)
    const exp = ctxFor(FILES, 'views/Expenses.tsx', 'button')
    expect(buildClick(exp, readClick(exp), { kind: 'navigate', target: 'runs' }, ops)).toMatchObject({ ok: false })
    expect(appScreens(FILES)).toEqual([{ id: 'runs', label: 'Runs' }, { id: 'expenses', label: 'Expenses' }])
  })

  it('opens an existing drawer state, or adds state plus a kit Drawer', () => {
    const ctx = plain()
    expect(after(buildClick(ctx, readClick(ctx), { kind: 'open', state: 'open' }, ops))).toContain('<Button onClick={() => setOpen(true)}>Plain</Button>')
    const out = after(buildClick(ctx, readClick(ctx), { kind: 'openNew' }, ops))
    expect(out).toContain('  const [open, setOpen] = useState(false)\n  const [drawerOpen, setDrawerOpen] = useState(false)\n')
    expect(out).toContain('<Button onClick={() => setDrawerOpen(true)}>Plain</Button>')
    expect(out).toContain(
      '      <p>Hello</p>\n      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Plain">\n        Add content here\n      </Drawer>\n    </div>',
    )
    expect(out).toContain(`import { DataTable, Stat, Button, usePoll, Drawer } from '../kit'`)
  })

  it('removes the handler for "do nothing"', () => {
    const ctx = ctxFor(FILES, RUNS, 'Button', 'New')
    expect(after(buildClick(ctx, readClick(ctx), { kind: 'none' }, ops))).toContain('      <Button>New</Button>')
  })
})
