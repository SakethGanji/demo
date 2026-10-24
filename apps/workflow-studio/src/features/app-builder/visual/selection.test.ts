import { describe, expect, it } from 'vitest'
import { findRendered, pickTarget } from './selection'

// A tiny fake of what React leaves on the DOM: host nodes carry `__reactFiber$x`, fibers link up through `return`.
type Fiber = { type: unknown; memoizedProps: Record<string, unknown> | null; return: Fiber | null; child: Fiber | null; sibling: Fiber | null; stateNode: unknown }

class FakeEl {
  parentElement: FakeEl | null = null
  children: FakeEl[] = []
  nodeType = 1
  constructor(public tag: string, public attrs: Record<string, string> = {}) {}
  getAttribute(k: string) {
    return this.attrs[k] ?? null
  }
  closest(sel: string): FakeEl | null {
    const attr = /^\[([\w-]+)\]$/.exec(sel)![1]
    if (attr in this.attrs) return this
    return this.parentElement ? this.parentElement.closest(sel) : null
  }
  querySelector(sel: string): FakeEl | null {
    const m = /^\[([\w-]+)="([^"]+)"\]$/.exec(sel)!
    for (const c of this.children) {
      if (c.attrs[m[1]] === m[2]) return c
      const d = c.querySelector(sel)
      if (d) return d
    }
    return null
  }
}

function fiber(type: unknown, props: Record<string, unknown> | null, parent: Fiber | null, stateNode: unknown = null): Fiber {
  const f: Fiber = { type, memoizedProps: props, return: parent, child: null, sibling: null, stateNode }
  if (parent) {
    if (!parent.child) parent.child = f
    else {
      let s = parent.child
      while (s.sibling) s = s.sibling
      s.sibling = f
    }
  }
  return f
}

function host(tag: string, attrs: Record<string, string>, parentEl: FakeEl | null, parentFiber: Fiber, props: Record<string, unknown> = {}) {
  const el = new FakeEl(tag, attrs)
  if (parentEl) {
    el.parentElement = parentEl
    parentEl.children.push(el)
  }
  const f = fiber(tag, { ...props, ...attrs }, parentFiber, el)
  ;(el as unknown as Record<string, unknown>)['__reactFiber$abc'] = f
  return { el, f }
}

/** <Page data-vsrc=App:3:5> → (kit) <section> → <DataTable data-vsrc=App:4:7> → (kit) <table><th data-vpart=columns.1><span> */
function tree() {
  const container = new FakeEl('div')
  const rootFiber = fiber(null, null, null)
  ;(container as unknown as Record<string, unknown>)['__reactContainer$abc'] = rootFiber
  function Page() {}
  function DataTable() {}
  const page = fiber(Page, { 'data-vsrc': 'App.tsx:3:5', title: 'X' }, rootFiber)
  const section = host('section', {}, container, page)
  const dt = fiber(DataTable, { 'data-vsrc': 'App.tsx:4:7' }, section.f)
  const table = host('table', {}, section.el, dt)
  const th0 = host('th', { 'data-vpart': 'columns.0' }, table.el, table.f)
  const th1 = host('th', { 'data-vpart': 'columns.1' }, table.el, table.f)
  const span = host('span', {}, th1.el, th1.f)
  const own = host('b', { 'data-vsrc': 'App.tsx:9:20' }, th0.el, th0.f) // an app element inside a column render
  return { container, span, own, table, section }
}

describe('pickTarget', () => {
  it('walks fibers to every tagged ancestor and keeps the kit part', () => {
    const { span } = tree()
    const p = pickTarget(span.el as unknown as Element)
    expect(p.chain).toEqual([{ vsrc: 'App.tsx:4:7', name: 'DataTable' }, { vsrc: 'App.tsx:3:5', name: 'Page' }])
    expect(p.part).toEqual({ owner: 'App.tsx:4:7', vpart: 'columns.1' })
  })
  it('selects an app element rendered inside a kit part, remembering the part for its owner', () => {
    const { own } = tree()
    const p = pickTarget(own.el as unknown as Element)
    expect(p.chain[0]).toEqual({ vsrc: 'App.tsx:9:20', name: 'b' })
    expect(p.chain.map((c) => c.name)).toEqual(['b', 'DataTable', 'Page'])
    expect(p.part).toEqual({ owner: 'App.tsx:4:7', vpart: 'columns.0' })
  })
  it('names an element after the kit component when the component spreads the tag onto its host node', () => {
    const { section } = tree()
    function Button() {}
    const btn = fiber(Button, { 'data-vsrc': 'App.tsx:7:9', variant: 'primary' }, section.f)
    const el = host('button', { 'data-vsrc': 'App.tsx:7:9' }, section.el, btn)
    const p = pickTarget(el.el as unknown as Element)
    expect(p.chain).toEqual([{ vsrc: 'App.tsx:7:9', name: 'Button' }, { vsrc: 'App.tsx:3:5', name: 'Page' }])
  })
  it('works stringified, as the iframe runs it', () => {
    const { span } = tree()
    const fn = new Function(`return (${pickTarget.toString()})`)() as typeof pickTarget
    expect(fn(span.el as unknown as Element).chain[0].vsrc).toBe('App.tsx:4:7')
  })
})

describe('findRendered', () => {
  it('finds the host nodes of a tagged component, or its part', () => {
    const { container, table, section } = tree()
    expect(findRendered(container as unknown as Element, 'App.tsx:4:7', null)).toEqual([table.el])
    expect(findRendered(container as unknown as Element, 'App.tsx:3:5', null)).toEqual([section.el])
    const part = findRendered(container as unknown as Element, 'App.tsx:4:7', 'columns.1')
    expect((part[0] as unknown as FakeEl).attrs['data-vpart']).toBe('columns.1')
    expect(findRendered(container as unknown as Element, 'App.tsx:99:1', null)).toEqual([])
    const fn = new Function(`return (${findRendered.toString()})`)() as typeof findRendered
    expect(fn(container as unknown as Element, 'App.tsx:4:7', null)).toEqual([table.el])
  })
})
