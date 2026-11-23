/**
 * Side by side: the Page on the left, its Definition on the right. Hovering a step or a node's JSON highlights
 * the other (shared `hoverNode`), and scrolls the JSON to the hovered step's node.
 */
import { useEffect, useRef, type ReactNode } from 'react'
import { useGlassStore } from '../state/glassStore'
import { DefinitionView, type DefinitionDoc } from './DefinitionView'

export function SideBySide({ page, doc, version }: { page: ReactNode; doc: DefinitionDoc; version?: number | null }) {
  const hover = useGlassStore((s) => s.hoverNode)
  const right = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!hover || !right.current) return
    const el = right.current.querySelector(`[data-node="${CSS.escape(hover)}"]`) as HTMLElement | null
    if (!el || el.matches(':hover')) return
    const box = right.current.getBoundingClientRect()
    const r = el.getBoundingClientRect()
    if (r.top < box.top || r.bottom > box.bottom) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [hover])
  return (
    <div className="gp-side" data-testid="glass-side-by-side">
      <div className="gp-side-pane gp-doc glass">{page}</div>
      <div className="gp-side-pane" ref={right}>
        <DefinitionView doc={doc} version={version} compact />
      </div>
    </div>
  )
}
