/**
 * Lights the neighbourhood of the hovered node without a React render (PLAN-CAPGRAPH-UI §2.2): one <style> element's text is
 * rewritten and a class is toggled on the wrapper. Ids go through CSS.escape.
 */
import { useCallback, useEffect, useRef } from 'react'
import type { adjacency } from '../../lib/layout'

type Adj = ReturnType<typeof adjacency>

const q = (s: string) => `"${CSS.escape(s).replaceAll('"', '\\"')}"`

export function hoverCss(id: string, adj: Adj): string {
  const a = adj.get(id)
  if (!a) return ''
  const nodes = [...a.nodes].map((n) => `[data-id=${q(n)}]`).join(',')
  const edges = [...a.edges].map((e) => `[data-eid=${q(e)}]`).join(',')
  const notNodes = `.kg-hover .react-flow__node:not(${nodes}){opacity:.28}`
  const notEdges = edges ? `.kg-hover .kg-edge:not(${edges}){opacity:.12}` : '.kg-hover .kg-edge{opacity:.12}'
  const lit = edges ? `.kg-hover .kg-edge:is(${edges}){stroke:var(--primary)}` : ''
  return `${notNodes}${notEdges}${lit}`
}

export function useHoverStyle(wrapper: React.RefObject<HTMLElement | null>, adj: Adj) {
  const style = useRef<HTMLStyleElement | null>(null)
  const adjRef = useRef(adj)
  const current = useRef<string | null>(null)
  useEffect(() => {
    adjRef.current = adj
    // the view changed under a resting pointer: light the new neighbourhood, or clear when the node is gone
    if (current.current && style.current) {
      if (adj.has(current.current)) style.current.textContent = hoverCss(current.current, adj)
      else { style.current.textContent = ''; wrapper.current?.classList.remove('kg-hover'); current.current = null }
    }
  }, [adj, wrapper])
  useEffect(() => {
    const el = document.createElement('style')
    el.setAttribute('data-kg-hover', '')
    document.head.appendChild(el)
    style.current = el
    return () => { el.remove(); style.current = null }
  }, [])
  const enter = useCallback((id: string) => {
    if (!style.current) return
    current.current = id
    style.current.textContent = hoverCss(id, adjRef.current)
    wrapper.current?.classList.add('kg-hover')
  }, [wrapper])
  const leave = useCallback(() => {
    current.current = null
    if (style.current) style.current.textContent = ''
    wrapper.current?.classList.remove('kg-hover')
  }, [wrapper])
  return { enter, leave }
}
