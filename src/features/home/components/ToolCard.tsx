import { useState } from 'react'
import { PluckString } from '@/shared/primitives/PluckString'
import { ToolGlyph } from '@/shared/primitives/ToolGlyph'
import type { Tool } from '@/shared/lib/tools'

export function ToolCard({ tool, pinned, onPin, onOpen }: { tool: Tool; pinned: boolean; onPin: () => void; onOpen: () => void }) {
  const [pluck, setPluck] = useState(0)
  return (
    <div className="tool" onPointerEnter={() => setPluck((n) => n + 1)}>
      <button className="tool-main" onClick={onOpen}>
        <span className="tool-glyph"><ToolGlyph id={tool.id} /></span>
        <span className="tool-text">
          <span className="micro-label">{tool.verb}</span>
          <b className="card-title">{tool.name}</b>
          <span className="helper">{tool.desc}</span>
        </span>
      </button>
      <button className={`pin ${pinned ? 'on' : ''}`} aria-pressed={pinned} title={pinned ? 'Remove from toolbelt' : 'Add to toolbelt'} onClick={onPin}>{pinned ? '● On Belt' : '○ Pin'}</button>
      <div className="tool-foot"><span className="mono">{tool.meta}</span><span className="open">Open →</span></div>
      <div className="tool-string"><PluckString pluck={pluck} at={0.3} height={12} /></div>
    </div>
  )
}
