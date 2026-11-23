/**
 * Line art for the Projects home (DESIGN §6): the Default project's empty orbits, the New project
 * blueprint and the three template sketches. Same language as `SystemDiagram`: neutral `--art-line`
 * strokes, one focal node per drawing with the brand stroke.
 */
import { useId } from 'react'

const METHOD_VAR: Record<string, string> = {
  GET: 'var(--method-get)',
  POST: 'var(--method-post)',
  PUT: 'var(--method-put)',
  PATCH: 'var(--method-patch)',
  DELETE: 'var(--method-delete)',
  JOB: 'var(--method-patch)',
}

function Defs({ id }: { id: string }) {
  return (
    <defs>
      <linearGradient id={`${id}-brand`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" style={{ stopColor: 'var(--brand-1)' }} /><stop offset="1" style={{ stopColor: 'var(--brand-1)' }} /></linearGradient>
      <pattern id={`${id}-fade`} width="6" height="6" patternUnits="userSpaceOnUse"><path d="M0 0 L6 6 M6 0 L0 6" fill="none" strokeWidth=".6" style={{ stroke: 'var(--brand-1)', strokeOpacity: 0.35 }} /></pattern>
    </defs>
  )
}

/** Default project, nothing in it: dashed orbits around a workflow glyph. */
export function EmptyOrbitArt({ label = 'No workflows yet' }: { label?: string }) {
  const id = useId().replace(/:/g, '')
  const cx = 150
  const cy = 46
  const ring = (r: number, o: number) => (
    <circle key={r} cx={cx} cy={cy} r={r} fill="none" strokeWidth="1" strokeDasharray="2 4" style={{ stroke: 'var(--art-line)', opacity: o }} />
  )
  const slot = (r: number, deg: number) => {
    const t = (deg * Math.PI) / 180
    return (
      <circle key={`${r}-${deg}`} cx={(cx + r * Math.cos(t)).toFixed(1)} cy={(cy - r * Math.sin(t)).toFixed(1)} r="3.2" strokeWidth="1" strokeDasharray="1.6 1.6" style={{ fill: 'var(--card)', stroke: 'var(--art-line)' }} />
    )
  }
  return (
    <svg viewBox="0 0 300 112" className="art block h-auto w-full" role="img" aria-label="No workflows yet">
      <defs>
        <radialGradient id={`${id}-g`} gradientUnits="userSpaceOnUse" cx={cx} cy={cy} r="80">
          <stop offset=".45" stopColor="white" />
          <stop offset="1" stopColor="black" />
        </radialGradient>
        <mask id={`${id}-m`}>
          <rect width="300" height="112" fill={`url(#${id}-g)`} />
        </mask>
      </defs>
      <g mask={`url(#${id}-m)`}>
        {ring(30, 1)}
        {ring(44, 0.75)}
        {ring(58, 0.5)}
        {ring(72, 0.3)}
        <path d={`M${cx - 80} ${cy} H${cx + 80}`} strokeWidth="1" strokeDasharray="1 5" style={{ stroke: 'var(--art-faint)' }} />
      </g>
      {slot(30, 38)}
      {slot(44, 152)}
      {slot(44, -24)}
      <circle cx={cx} cy={cy} r="17" className="a-card" />
      <g transform={`translate(${cx - 8.4} ${cy - 8.4}) scale(.7)`} fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ stroke: 'var(--muted-foreground)' }}>
        <rect width="8" height="8" x="3" y="3" rx="2" />
        <path d="M7 11v4a2 2 0 0 0 2 2h4" />
        <rect width="8" height="8" x="13" y="13" rx="2" />
      </g>
      <text x={cx} y="106" textAnchor="middle" className="a-label">
        {label}
      </text>
    </svg>
  )
}

/** New project: a blueprint of what a project holds, fanned out from the +. */
export function BlueprintArt() {
  const id = useId().replace(/:/g, '')
  const d = { fill: 'none', strokeWidth: 1, strokeDasharray: '2.5 3', style: { stroke: 'var(--art-line)' } } as const
  const solid = { ...d, strokeDasharray: undefined }
  const tick = (x: number, y: number) => <path key={`${x}-${y}`} d={`M${x - 4} ${y} h8 M${x} ${y - 4} v8`} strokeWidth="1" style={{ stroke: 'var(--art-faint)' }} />
  return (
    <svg viewBox="0 0 208 100" className="art block h-auto w-full" role="img" aria-label="A new project holds a backend, data and apps.">
      <Defs id={id} />
      {tick(4, 4)}
      {tick(204, 4)}
      {tick(4, 96)}
      {tick(204, 96)}
      <path d="M104 36 C 104 48, 36 46, 36 60" {...d} />
      <path d="M104 36 V58" {...d} />
      <path d="M104 36 C 104 48, 172 46, 172 58" {...d} />
      <rect x="12" y="60" width="48" height="20" rx="5" {...d} />
      <path d="M19 70 h8 M31 70 h21" {...solid} strokeLinecap="round" opacity=".8" />
      <path d="M92 62 v14 a12 3.6 0 0 0 24 0 v-14" {...d} />
      <ellipse cx="104" cy="62" rx="12" ry="3.6" {...d} />
      <rect x="148" y="58" width="48" height="26" rx="5" {...d} />
      <path d="M148 65 h48" {...d} />
      <path d="M154 78 l7 -5 l6 2 l8 -6 l7 3 l8 -4" {...solid} strokeLinejoin="round" strokeLinecap="round" opacity=".8" />
      <text x="36" y="96" textAnchor="middle" className="a-label" style={{ fontSize: 9.5 }}>backend</text>
      <text x="104" y="96" textAnchor="middle" className="a-label" style={{ fontSize: 9.5 }}>data</text>
      <text x="172" y="96" textAnchor="middle" className="a-label" style={{ fontSize: 9.5 }}>apps</text>
      <g className="a-glow">
        <circle cx="104" cy="22" r="14" strokeWidth="1.4" style={{ fill: 'var(--card)', stroke: `url(#${id}-brand)` }} />
        <path d="M104 16 v12 M98 22 h12" strokeWidth="1.6" strokeLinecap="round" fill="none" style={{ stroke: `url(#${id}-brand)` }} />
      </g>
    </svg>
  )
}

function Chip({ x, y, m, p, w = 104 }: { x: number; y: number; m: string; p: string; w?: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect width={w} height="20" rx="6" className="a-card" />
      <text x="7" y="13.5" className="a-mono a-strong" style={{ fontSize: 8.5, fill: METHOD_VAR[m] ?? 'var(--muted-foreground)' }}>
        {m}
      </text>
      <text x={m.length > 4 ? 44 : m.length > 3 ? 36 : 30} y="13.5" className="a-mono" style={{ fontSize: 8.5 }}>
        {p}
      </text>
    </g>
  )
}

function TableNode({ x, y, name, id }: { x: number; y: number; name: string; id: string }) {
  const body = 'M0 6 v26 a18 5.5 0 0 0 36 0 v-26'
  return (
    <g transform={`translate(${x} ${y})`}>
      <path d={body} className="a-solid a-stroke-brand" />
      <path d={body} fill={`url(#${id}-fade)`} />
      <ellipse cx="18" cy="6" rx="18" ry="5.5" className="a-solid a-stroke-brand" />
      <ellipse cx="18" cy="6" rx="18" ry="5.5" fill={`url(#${id}-fade)`} />
      {name && (
        <text x="18" y="50" textAnchor="middle" className="a-mono a-strong" style={{ fontSize: 8.5 }}>
          {name}
        </text>
      )}
    </g>
  )
}

function IconNode({ x, y, id, label, children }: { x: number; y: number; id: string; label?: string; children: React.ReactNode }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <circle r="17" className="a-card" />
      <g fill="none" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ stroke: `url(#${id}-brand)` }}>
        {children}
      </g>
      {label && (
        <text y="32" textAnchor="middle" className="a-label" style={{ fontSize: 8.5 }}>
          {label}
        </text>
      )}
    </g>
  )
}

export type TemplateArtKind = 'webhook' | 'crud' | 'cron'

export function TemplateArt({ kind }: { kind: TemplateArtKind }) {
  const id = useId().replace(/:/g, '')
  if (kind === 'webhook')
    return (
      <svg viewBox="0 0 240 90" className="art relative block h-auto w-full" role="img" aria-label="An incoming webhook is received by a POST route and stored in an events table.">
        <Defs id={id} />
        <path d="M50 40 H70" className="a-wire a-hot" />
        <path d="M174 40 H192" className="a-wire a-hot" />
        <IconNode x={32} y={40} id={id} label="Stripe">
          <path d="M-5 4 a5 5 0 1 1 6 -6" />
          <path d="M5 4 h-6 l3 -8" />
          <circle cx="-5" cy="4" r="1.5" />
          <circle cx="5" cy="4" r="1.5" />
        </IconNode>
        <Chip x={70} y={30} m="POST" p="/hooks/stripe" />
        <TableNode x={192} y={18} name="events" id={id} />
      </svg>
    )
  if (kind === 'crud')
    return (
      <svg viewBox="0 0 240 90" className="art relative block h-auto w-full" role="img" aria-label="Routes read and write one table.">
        <Defs id={id} />
        {[0, 1, 2, 3].map((i) => (
          <path key={i} d={`M134 ${12 + i * 22} C 156 ${12 + i * 22}, 156 ${38 + i * 4}, 178 ${38 + i * 4}`} className={`a-wire${i < 2 ? ' a-hot' : ''}`} />
        ))}
        <Chip x={30} y={2} m="GET" p="/items" w={104} />
        <Chip x={30} y={24} m="POST" p="/items" w={104} />
        <Chip x={30} y={46} m="PUT" p="/items/:id" w={104} />
        <Chip x={30} y={68} m="DELETE" p="/items/:id" w={104} />
        <TableNode x={178} y={24} name="items" id={id} />
      </svg>
    )
  return (
    <svg viewBox="0 0 240 90" className="art relative block h-auto w-full" role="img" aria-label="A POST route calls an outside API and stores the results.">
      <Defs id={id} />
      <path d="M50 40 H70" className="a-wire a-hot" />
      <path d="M174 34 C 184 34, 184 22, 196 22" className="a-wire a-hot" />
      <path d="M174 46 C 184 46, 184 60, 196 60" className="a-wire" />
      <IconNode x={32} y={40} id={id} label="request">
        <circle r="7" />
        <path d="M0 -4 v4 l3 2" />
      </IconNode>
      <Chip x={70} y={30} m="POST" p="/sync-rates" />
      <g transform="translate(196 10)">
        <rect width="34" height="24" rx="6" className="a-card" />
        <path d="M8 15 a5 5 0 0 1 4 -8 a6 6 0 0 1 11 2 a4 4 0 0 1 -1 6z" fill="none" strokeWidth="1.2" style={{ stroke: 'var(--muted-foreground)' }} />
      </g>
      <TableNode x={196} y={50} name="" id={id} />
    </svg>
  )
}
