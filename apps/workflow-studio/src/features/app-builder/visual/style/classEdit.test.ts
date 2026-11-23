import { describe, expect, it } from 'vitest'
import type * as t from '@babel/types'
import { parseSource, walk } from '../source'
import { changeList, clearGroup, conflicts, currentOf, DYNAMIC, editClasses, readClasses, setClass } from './classEdit'
import { ALLOWED, controlById, CONTROLS, STYLE_PROPS } from './tokens'

const wrap = (jsx: string) => `import { cn } from './kit'\nexport function A({ on }: { on: boolean }) {\n  return ${jsx}\n}\n`

function firstEl(src: string): t.JSXElement {
  let el: t.JSXElement | null = null
  walk(parseSource(src).program, (n) => {
    if (!el && n.type === 'JSXElement') el = n
  })
  return el!
}

function edit(jsx: string, change: Parameters<typeof editClasses>[2]) {
  const src = wrap(jsx)
  const r = editClasses(src, firstEl(src), change)
  return r.ok ? { ok: true as const, jsx: r.after.slice(src.indexOf(jsx), r.after.length - (src.length - src.indexOf(jsx) - jsx.length)), classes: r.classes } : r
}

describe('class list semantics', () => {
  it('replaces the conflicting utility in place and keeps the order', () => {
    expect(setClass(['flex', 'p-2', 'text-sm', 'bg-card'], 'p-4')).toEqual(['flex', 'p-4', 'text-sm', 'bg-card'])
    expect(setClass(['text-sm', 'text-muted-foreground'], 'text-lg')).toEqual(['text-lg', 'text-muted-foreground'])
    expect(setClass(['text-sm', 'text-muted-foreground'], 'text-danger')).toEqual(['text-sm', 'text-danger'])
  })
  it('a shorthand replaces its longhands (first one in place, the rest go)', () => {
    expect(setClass(['px-2', 'flex', 'py-1'], 'p-4')).toEqual(['p-4', 'flex'])
  })
  it('a longhand after a shorthand is appended (tailwind-merge keeps both)', () => {
    expect(setClass(['p-4', 'flex'], 'px-2')).toEqual(['p-4', 'flex', 'px-2'])
  })
  it('token colours conflict within their utility (bg-card vs bg-primary-soft) and replace palette classes', () => {
    expect(setClass(['bg-white', 'rounded-xl'], 'bg-primary-soft')).toEqual(['bg-primary-soft', 'rounded-xl'])
    expect(setClass(['text-slate-500'], 'text-muted-foreground')).toEqual(['text-muted-foreground'])
    expect(conflicts('border-border', 'border-2')).toBe(false)
  })
  it('leaves variant classes alone', () => {
    expect(setClass(['bg-card', 'hover:bg-muted'], 'bg-subtle')).toEqual(['bg-subtle', 'hover:bg-muted'])
  })
  it('adds when nothing conflicts; re-setting the same class is a no-op', () => {
    expect(setClass(['flex'], 'gap-2')).toEqual(['flex', 'gap-2'])
    expect(setClass(['flex', 'gap-2'], 'gap-2')).toEqual(['flex', 'gap-2'])
  })
  it('clears a group without touching the rest', () => {
    expect(clearGroup(['p-4', 'text-sm', 'px-2', 'rounded'], controlById('p')!.options.map((o) => o.cls))).toEqual(['text-sm', 'rounded'])
    expect(clearGroup(['text-sm', 'text-danger'], controlById('text-color')!.options.map((o) => o.cls))).toEqual(['text-sm'])
  })
  it('reads the current value of a control', () => {
    const size = controlById('text-size')!.options.map((o) => o.cls)
    expect(currentOf(['text-base', 'font-semibold'], size)).toEqual({ cls: 'text-base', known: true })
    expect(currentOf(['text-[13px]'], size)).toEqual({ cls: 'text-[13px]', known: false })
    expect(currentOf(['font-semibold'], size)).toBeNull()
  })
  it('refuses values outside the token tables', () => {
    expect(changeList(['p-2'], { set: 'p-[13px]' })).toMatch(/not a theme token/)
    expect(changeList(['bg-card'], { set: 'bg-[#ff0000]' })).toMatch(/not a theme token/)
    expect(changeList(['bg-card'], { set: 'bg-red-500' })).toMatch(/not a theme token/)
  })
})

describe('editClasses splices', () => {
  it('edits a plain attribute string only', () => {
    const r = edit('<h1 className="text-base font-semibold">Hi</h1>', { set: 'text-2xl' })
    expect(r).toMatchObject({ ok: true, jsx: '<h1 className="text-2xl font-semibold">Hi</h1>' })
  })
  it('edits {"…"} and static template literals', () => {
    expect(edit("<div className={'p-2 flex'} />", { set: 'p-6' })).toMatchObject({ jsx: "<div className={'p-6 flex'} />" })
    expect(edit('<div className={`p-2 flex`} />', { set: 'gap-3' })).toMatchObject({ jsx: '<div className={`p-2 flex gap-3`} />' })
  })
  it('edits the first string-literal argument of cn(), leaving the conditional parts alone', () => {
    expect(edit("<div className={cn('p-2 rounded', on && 'bg-primary')} />", { set: 'rounded-lg' })).toMatchObject({
      jsx: "<div className={cn('p-2 rounded-lg', on && 'bg-primary')} />",
    })
    expect(edit("<div className={cn(on && 'ring', 'p-2')} />", { set: 'p-4' })).toMatchObject({ jsx: "<div className={cn(on && 'ring', 'p-4')} />" })
  })
  it('adds a className when there is none', () => {
    expect(edit('<section>x</section>', { set: 'p-4' })).toMatchObject({ jsx: '<section className="p-4">x</section>' })
    expect(edit('<Card title="T">x</Card>', { set: 'rounded-lg' })).toMatchObject({ jsx: '<Card title="T" className="rounded-lg">x</Card>' })
  })
  it('removes the attribute when a plain string empties', () => {
    expect(edit('<div className="shadow-md" id="a" />', { clear: controlById('shadow')!.options.map((o) => o.cls) })).toMatchObject({ jsx: '<div id="a" />' })
  })
  it('refuses dynamic classes loudly', () => {
    for (const jsx of [
      "<div className={on ? 'p-2' : 'p-4'} />",
      '<div className={`p-2 ${on ? "x" : ""}`} />',
      '<div className={klass} />',
      '<div className={tabClass(on)} />',
      '<div className={cn(on && "a")} />',
    ]) {
      const r = edit(jsx, { set: 'p-4' })
      expect(r.ok, jsx).toBe(false)
      expect((r as { error: string }).error).toContain(DYNAMIC)
    }
  })
  it('says when nothing changes', () => {
    expect(edit('<div className="p-4" />', { set: 'p-4' })).toEqual({ ok: false, error: 'Nothing to change' })
  })
  it('reads classes without editing', () => {
    const src = wrap("<div className={cn('a b', on && 'c')} />")
    expect(readClasses(src, firstEl(src))).toMatchObject({ ok: true, classes: ['a', 'b'], site: { kind: 'expr', via: 'cn' } })
  })
})

describe('token tables', () => {
  it('only offer token or scale values (no arbitrary values, no palette colours)', () => {
    for (const c of ALLOWED) {
      expect(c).not.toMatch(/\[|\]/)
      expect(c).not.toMatch(/-(slate|gray|zinc|red|blue|green|amber|yellow|emerald|indigo|white|black)(-|$)/)
    }
  })
  it('colour controls use theme tokens, each with its kit default as the swatch', () => {
    for (const c of CONTROLS.filter((x) => x.color)) for (const o of c.options) expect(o.swatch === 'transparent' || /^#[0-9a-f]{6}$/.test(o.swatch ?? ''), o.cls).toBe(true)
  })
  it('options within a control conflict with each other (one group per control)', () => {
    for (const c of CONTROLS) {
      const [a, ...rest] = c.options
      for (const o of rest) expect(conflicts(a.cls, o.cls), `${c.id}: ${a.cls} vs ${o.cls}`).toBe(true)
    }
  })
  it('controls are unique and style props point at real controls', () => {
    expect(new Set(CONTROLS.map((c) => c.id)).size).toBe(CONTROLS.length)
    for (const ids of Object.values(STYLE_PROPS)) for (const id of ids) expect(controlById(id), id).toBeTruthy()
  })
})
