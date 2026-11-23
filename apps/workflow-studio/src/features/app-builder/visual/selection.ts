/**
 * Preview-side selection: from a clicked DOM node to the source elements that rendered it. Elements written in app files
 * carry `data-vsrc="file:line:col"` (the jsxDEV shim in the bundler); kit components don't forward it, so we walk React's
 * fibers up from the node to every ancestor whose props carry a tag. Kit list parts (a column header, a tab, a nav item)
 * carry `data-vpart="<prop>.<index>"`, which refines the selection to an item of the owning element.
 *
 * These functions run INSIDE the preview iframe: they are stringified into its runtime, so they must stay self-contained
 * (no imports, no outer references, plain ES2019).
 */

export interface PickedSource {
  vsrc: string
  /** component or tag name of the fiber */
  name: string
}

export interface Picked {
  /** innermost first */
  chain: PickedSource[]
  /** a kit part under the click, with the tag of the element that owns it */
  part: { owner: string; vpart: string } | null
}

type FiberLike = { return: FiberLike | null; child: FiberLike | null; sibling: FiberLike | null; memoizedProps: unknown; type: unknown; stateNode: unknown }

/** Resolve a clicked element to its tagged source chain (and kit part). */
export function pickTarget(el: Element): Picked {
  function fiberOf(n: Element): FiberLike | null {
    for (const k in n) if (k.indexOf('__reactFiber$') === 0) return (n as unknown as Record<string, FiberLike>)[k]
    return null
  }
  function tagOf(f: FiberLike): string | null {
    const p = f.memoizedProps as Record<string, unknown> | null
    return p && typeof p === 'object' && typeof p['data-vsrc'] === 'string' ? (p['data-vsrc'] as string) : null
  }
  function nameOf(f: FiberLike): string {
    const ty = f.type as { displayName?: string; name?: string } | string | null
    if (typeof ty === 'string') return ty
    return (ty && (ty.displayName || ty.name)) || '?'
  }
  function owner(n: Element | null): FiberLike | null {
    let f: FiberLike | null = null
    for (let x = n; x && !f; x = x.parentElement) f = fiberOf(x)
    for (; f; f = f.return) if (tagOf(f)) return f
    return null
  }
  const chain: PickedSource[] = []
  for (let f = owner(el); f; f = f.return) {
    const tag = tagOf(f)
    if (!tag) continue
    // a kit component that spreads its props hands the tag on to its host node: name the element after the component
    if (chain.length && chain[chain.length - 1].vsrc === tag) {
      if (typeof f.type !== 'string') chain[chain.length - 1].name = nameOf(f)
    } else chain.push({ vsrc: tag, name: nameOf(f) })
  }
  let part: Picked['part'] = null
  const partEl = el.closest ? el.closest('[data-vpart]') : null
  if (partEl) {
    const o = owner(partEl)
    const tag = o && tagOf(o)
    if (tag) part = { owner: tag, vpart: partEl.getAttribute('data-vpart') || '' }
  }
  return { chain, part }
}

/**
 * The DOM nodes rendered by the element tagged `vsrc` (its top-level host nodes), found by walking the fiber tree from the
 * React root container. With `vpart`, the matching part node inside it instead.
 */
export function findRendered(container: Element, vsrc: string, vpart: string | null): Element[] {
  function fiberRoot(c: Element): FiberLike | null {
    for (const k in c) if (k.indexOf('__reactContainer$') === 0) return (c as unknown as Record<string, FiberLike>)[k]
    return null
  }
  function hosts(f: FiberLike, out: Element[]) {
    for (let c = f.child; c; c = c.sibling) {
      if (typeof c.type === 'string' && c.stateNode && (c.stateNode as Element).nodeType === 1) out.push(c.stateNode as Element)
      else hosts(c, out)
    }
  }
  const root = fiberRoot(container)
  const found: Element[] = []
  const stack: FiberLike[] = root ? [root] : []
  while (stack.length) {
    const f = stack.pop() as FiberLike
    const p = f.memoizedProps as Record<string, unknown> | null
    if (p && typeof p === 'object' && p['data-vsrc'] === vsrc) {
      const out: Element[] = []
      if (typeof f.type === 'string' && f.stateNode) out.push(f.stateNode as Element)
      else hosts(f, out)
      if (vpart) {
        for (const n of out) {
          const hit = n.getAttribute('data-vpart') === vpart ? n : n.querySelector('[data-vpart="' + vpart + '"]')
          if (hit) return [hit]
        }
      }
      found.push(...out)
      if (found.length) return found
    }
    if (f.sibling) stack.push(f.sibling)
    if (f.child) stack.push(f.child)
  }
  return found
}
