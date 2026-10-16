import type { CodeMap, Direction, Kind, MapEdge, MapFunction } from './types'

export function neighborhood(model: CodeMap, id: string, direction: Direction, hops: number) {
  const ids = new Set([id])
  let frontier = new Set([id])
  for (let i = 0; i < hops; i++) {
    const next = new Set<string>()
    model.edges.forEach((edge) => {
      if (direction !== 'callers' && frontier.has(edge.from) && !ids.has(edge.to)) next.add(edge.to)
      if (direction !== 'dependencies' && frontier.has(edge.to) && !ids.has(edge.from)) next.add(edge.from)
    })
    next.forEach((id) => ids.add(id))
    frontier = next
  }
  return ids
}
export function filterFunctions(model: CodeMap, scope: Set<string> | null, search: string, kind: Kind) {
  return model.functions.filter(
    (fn) =>
      (!scope || scope.has(fn.id)) &&
      fn.name.toLowerCase().includes(search.toLowerCase()) &&
      (kind === 'all' ||
        (kind === 'endpoint' && fn.routes.length > 0) ||
        (kind === 'secret' && fn.secret_references.length > 0) ||
        (kind === 'connector' && fn.calls.some((c) => c.kind === 'connector')) ||
        (kind === 'unresolved' && fn.calls.some((c) => c.kind === 'custom'))),
  )
}

/** Condense recursive components, then layer the DAG. No inferred execution order. */
export function arrange(functions: MapFunction[], edges: MapEdge[], heights: Map<string, number>, sorted: boolean) {
  const names = new Set(functions.map((fn) => fn.id))
  const links = edges.filter((e) => names.has(e.from) && names.has(e.to))
  let index = 0
  const indexes = new Map<string, number>(),
    low = new Map<string, number>(),
    stack: string[] = [],
    onStack = new Set<string>(),
    components: string[][] = []
  function visit(id: string) {
    indexes.set(id, index)
    low.set(id, index++)
    stack.push(id)
    onStack.add(id)
    links
      .filter((e) => e.from === id)
      .forEach((e) => {
        if (!indexes.has(e.to)) {
          visit(e.to)
          low.set(id, Math.min(low.get(id)!, low.get(e.to)!))
        } else if (onStack.has(e.to)) low.set(id, Math.min(low.get(id)!, indexes.get(e.to)!))
      })
    if (low.get(id) === indexes.get(id)) {
      const component: string[] = []
      let node: string
      do {
        node = stack.pop()!
        onStack.delete(node)
        component.push(node)
      } while (node !== id)
      components.push(component)
    }
  }
  functions.forEach((fn) => {
    if (!indexes.has(fn.id)) visit(fn.id)
  })
  const componentOf = new Map(components.flatMap((ids, i) => ids.map((id) => [id, i] as const)))
  const depth = new Map<number, number>()
  function rank(c: number): number {
    if (depth.has(c)) return depth.get(c)!
    const children = links.filter((e) => componentOf.get(e.from) === c && componentOf.get(e.to) !== c).map((e) => componentOf.get(e.to)!)
    const value = children.length ? 1 + Math.max(...children.map(rank)) : 0
    depth.set(c, value)
    return value
  }
  const max = Math.max(0, ...components.map((_, i) => rank(i)))
  const columns: MapFunction[][] = Array.from({ length: max + 1 }, () => [])
  functions.forEach((fn) => columns[max - rank(componentOf.get(fn.id)!)].push(fn))
  if (sorted)
    for (let sweep = 0; sweep < 6; sweep++) {
      const positions = new Map(columns.flatMap((column) => column.map((fn, i) => [fn.id, i] as const)))
      const center = (id: string) => {
        const neighbors = links.filter((e) => e.from === id || e.to === id).map((e) => positions.get(e.from === id ? e.to : e.from)!)
        return neighbors.length ? neighbors.reduce((a, b) => a + b, 0) / neighbors.length : positions.get(id)!
      }
      columns.forEach((column) => column.sort((a, b) => center(a.id) - center(b.id) || a.line - b.line))
    }
  const positions = new Map<string, { x: number; y: number }>()
  let bottom = 420
  columns.forEach((column, i) => {
    let y = 110
    column.forEach((fn) => {
      positions.set(fn.id, { x: 45 + i * 510, y })
      y += (heights.get(fn.id) ?? 110) + 45
    })
    bottom = Math.max(bottom, y + 20)
  })
  return { positions, width: 410 + max * 510, height: bottom }
}
