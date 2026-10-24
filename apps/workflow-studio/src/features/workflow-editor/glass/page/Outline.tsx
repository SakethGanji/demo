/**
 * The Page view's left rail: the workflow's shape drawn for you (read only, laid out from the steps, the picked
 * run's path in green) and an outline of the top-level steps with status dots. Clicking either selects the step;
 * the Page view opens its folds and scrolls to it.
 */
import { useMemo } from 'react'
import { cx } from '@/shared/glass/cx'
import { buildShapeMap } from '../lib/shapeMap'
import type { PageModel } from '../lib/pageModel'
import { statusOf, type RunPath } from '../lib/runPath'
import { useGlassStore } from '../state/glassStore'

export function ShapeMapSvg({ model, path }: { model: PageModel; path: RunPath | null }) {
  const select = useGlassStore((s) => s.select)
  const selected = useGlassStore((s) => s.selectedNode)
  const map = useMemo(() => buildShapeMap(model, path), [model, path])
  const go = (node?: string, dom?: string) => {
    if (node) select(node)
    else if (dom) document.getElementById(dom)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
  return (
    <svg className="gp-map" viewBox={`0 0 ${map.width} ${map.height}`} role="img" aria-label="The shape of this workflow" data-testid="glass-map">
      {map.frames.map((f, i) => (
        <g key={`f${i}`} className={cx('frame', f.on && 'on')} onClick={() => go(f.node)} data-testid={f.node ? 'glass-map-frame' : undefined} data-node={f.node}>
          <rect x={f.x} y={f.y} width={f.w} height={f.h} rx={7} />
          <text x={f.textX} y={f.textY}>
            {f.text}
          </text>
        </g>
      ))}
      {map.edges.map((e, i) => (
        <path key={`e${i}`} className={cx('e', e.on && 'on')} d={e.d} />
      ))}
      {map.labels.map((l, i) => (
        <text key={`l${i}`} className={cx('lab', l.on && 'on')} x={l.x + l.w / 2} y={l.y + 7} textAnchor="middle">
          {l.text}
        </text>
      ))}
      {map.nodes.map((n, i) => {
        const cls = cx('n', n.kind, n.on && 'on', n.node && n.node === selected && 'sel')
        const shape =
          n.kind === 'diamond' ? (
            <path d={`M${n.x + n.w / 2} ${n.y} L${n.x + n.w} ${n.y + n.h / 2} L${n.x + n.w / 2} ${n.y + n.h} L${n.x} ${n.y + n.h / 2} Z`} />
          ) : n.kind === 'dot' ? (
            <circle cx={n.x + n.w / 2} cy={n.y + n.h / 2} r={n.w / 2} />
          ) : (
            <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={n.kind === 'stop' ? 2.5 : n.h / 2} />
          )
        return (
          <g key={`n${i}`} className={cls} onClick={() => go(n.node, n.dom)} data-testid={n.node ? 'glass-map-node' : undefined} data-node={n.node}>
            <title>{n.title}</title>
            {shape}
            {n.failpip && <circle className="failpip" cx={n.x + n.w + 3} cy={n.y + 2} r={2} />}
          </g>
        )
      })}
    </svg>
  )
}

export function Outline({ model, path }: { model: PageModel; path: RunPath | null }) {
  const select = useGlassStore((s) => s.select)
  const selected = useGlassStore((s) => s.selectedNode)
  return (
    <aside className="gp-rail glass" aria-label="Shape and outline" data-testid="glass-outline">
      <h3>Shape</h3>
      <div className="gp-rail-sub">Drawn for you. Click a step to jump to it.</div>
      <ShapeMapSvg model={model} path={path} />
      <nav className="gp-outl" aria-label="Outline">
        {model.outline.map((o, i) => {
          if (!o.node)
            return (
              <div key={i} className="gp-outl-h">
                {o.title}
              </div>
            )
          const st = path ? statusOf(path, o.node) : null
          return (
            <button
              key={i}
              type="button"
              className={cx('gp-toc-step', selected === o.node && 'is-on')}
              onClick={() => select(o.node!)}
              data-testid="glass-outline-step"
              data-status={st ?? undefined}
            >
              <span className="gp-outl-n">{o.num}</span>
              <span className="gp-outl-t">{o.title}</span>
              <span className={cx('gp-dot', (st === 'ok' || st === 'need') && 'on', st === 'fail' && 'stopd')} aria-hidden />
            </button>
          )
        })}
      </nav>
    </aside>
  )
}
