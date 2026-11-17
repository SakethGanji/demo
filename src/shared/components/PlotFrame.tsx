import type { ReactNode } from 'react'
import type { Series } from './LineChart'

/** Every chart sits in a PlotFrame: header with sample context, plot, caption defining the quantity. */
export function PlotFrame({ title, chips, caption, legend, children, right }: { title: string; chips?: ReactNode; caption: ReactNode; legend?: Series[]; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="card plot-frame">
      <div className="card-head">
        <div className="left"><h3 className="card-title">{title}</h3>{right}</div>
        <div className="row">{chips}</div>
      </div>
      <div className="plot">{children}</div>
      <div className="caption">
        {legend?.map((s) => (
          <span key={s.name} className="legend">
            <svg width="22" height="8"><line x1="1" y1="4" x2="21" y2="4" stroke={s.color} strokeWidth="2" strokeDasharray={s.dash} /></svg>
            {s.name}
          </span>
        ))}
        <span style={{ marginLeft: legend?.length ? 'auto' : undefined }}>{caption}</span>
      </div>
    </div>
  )
}
