/**
 * The compact system diagram in a project card's well: route chips -> the table they serve -> the app,
 * plus the missing prod secret as an amber pill. Drawn from the project's real routes, first table, first
 * app and first missing secret. It is `SystemDiagram` at card scale: that component's 540px canvas would
 * shrink its text below 6px in a 300px well, so this one is laid out on a 300x110 canvas (same line
 * language: neutral wires, the table is the one focal node).
 */
import { useId } from 'react'

export interface CardRoute {
  key: string
  method: string
  path: string
  hot?: boolean
}

const METHOD_VAR: Record<string, string> = {
  GET: 'var(--method-get)',
  POST: 'var(--method-post)',
  PUT: 'var(--method-put)',
  PATCH: 'var(--method-patch)',
  DELETE: 'var(--method-delete)',
}

const CYS: Record<number, number[]> = { 1: [49], 2: [32, 66], 3: [15, 49, 83] }

export function CardDiagram({
  module,
  routes,
  table,
  app,
  secret,
}: {
  module?: string
  routes: CardRoute[]
  table?: { name: string } | null
  app?: { name: string } | null
  secret?: { key: string } | null
}) {
  const uid = useId().replace(/:/g, '')
  const shown = routes.slice(0, 3)
  const more = routes.length - shown.length
  const n = Math.max(1, shown.length)
  const cy = CYS[n]
  const lastBottom = cy[n - 1] + 11
  const label = `${routes.length} route${routes.length === 1 ? '' : 's'}${module ? ` in ${module}` : ''}${table ? ` use the ${table.name} table` : ''}${app ? `, which feeds ${app.name}` : ''}.${secret ? ` ${secret.key} is missing.` : ''}`
  const pillW = Math.max(104, 30 + (secret?.key.length ?? 0) * 5.4)
  const cyl = 'M152 30 v32 a20 6 0 0 0 40 0 v-32'

  return (
    <svg viewBox="0 0 300 110" className="art block h-auto w-full" role="img" aria-label={label} data-art="project-card">
      <defs>
        <linearGradient id={`${uid}-brand`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" style={{ stopColor: 'var(--brand-1)' }} /><stop offset="1" style={{ stopColor: 'var(--brand-1)' }} /></linearGradient>
        <pattern id={`${uid}-fade`} width="6" height="6" patternUnits="userSpaceOnUse"><path d="M0 0 L6 6 M6 0 L0 6" fill="none" strokeWidth=".6" style={{ stroke: 'var(--brand-1)', strokeOpacity: 0.35 }} /></pattern>
      </defs>

      {shown.map((r, i) => (
        <path
          key={`w-${r.key}`}
          d={`M122 ${cy[i]} C 138 ${cy[i]}, 138 ${n === 1 ? 46 : 42 + i * 7}, 152 ${n === 1 ? 46 : 42 + i * 7}`}
          className={`a-wire${r.hot ? ' a-hot' : ''}`}
        />
      ))}
      {table && app && <path d="M192 48 C 204 48, 206 46, 220 46" className="a-wire a-hot" />}
      {secret && <path d={`M96 ${lastBottom} C 96 ${lastBottom + 8}, 108 102, 124 102`} className="a-wire a-warn" data-wire="secret" />}

      {shown.map((r, i) => (
        <g key={r.key} transform={`translate(2 ${cy[i] - 11})`}>
          <rect width="120" height="22" rx="6" className="a-card" />
          <text x="8" y="14.5" className="a-mono a-strong" style={{ fontSize: 9.5, fill: METHOD_VAR[r.method.toUpperCase()] ?? 'var(--muted-foreground)' }}>
            {r.method.toUpperCase()}
          </text>
          <text x={r.method.length > 3 ? 38 : 32} y="14.5" className="a-mono" style={{ fontSize: 9.5 }}>
            {r.path.length > 14 ? `${r.path.slice(0, 13)}…` : r.path}
          </text>
        </g>
      ))}
      {routes.length === 0 && (
        <g transform="translate(2 38)">
          <rect width="120" height="22" rx="6" className="a-slot" />
          <text x="8" y="14.5" className="a-label" style={{ fontSize: 9.5 }}>
            no routes yet
          </text>
        </g>
      )}
      {more > 0 && (
        <text x="2" y="108" className="a-label" style={{ fontSize: 9 }}>
          +{more} more
        </text>
      )}

      {table ? (
        <g className="a-glow">
          <path d={cyl} className="a-solid a-stroke-brand" />
          <path d={cyl} fill={`url(#${uid}-fade)`} />
          <path d="M152 46 a20 6 0 0 0 40 0" className="a-stroke-brand" fill="none" opacity=".5" />
          <ellipse cx="172" cy="30" rx="20" ry="6" className="a-solid a-stroke-brand" />
          <ellipse cx="172" cy="30" rx="20" ry="6" fill={`url(#${uid}-fade)`} />
          <text x="172" y="82" textAnchor="middle" className="a-mono a-strong" style={{ fontSize: 9.5 }}>
            {table.name.length > 14 ? `${table.name.slice(0, 13)}…` : table.name}
          </text>
        </g>
      ) : (
        <g>
          <path d={cyl} className="a-slot" />
          <ellipse cx="172" cy="30" rx="20" ry="6" className="a-slot" />
          <text x="172" y="82" textAnchor="middle" className="a-label" style={{ fontSize: 9 }}>
            no table
          </text>
        </g>
      )}

      {app ? (
        <g transform="translate(220 14)">
          <rect width="76" height="64" rx="7" className="a-card" />
          <path d="M0 7 a7 7 0 0 1 7 -7 h62 a7 7 0 0 1 7 7 v5 h-76z" className="a-tile" />
          <circle cx="7" cy="6" r="1.6" className="a-dot" />
          <circle cx="12" cy="6" r="1.6" className="a-dot" />
          <circle cx="17" cy="6" r="1.6" className="a-dot" />
          <rect x="7" y="18" width="18" height="10" rx="2.5" className="a-tile" />
          <rect x="29" y="18" width="18" height="10" rx="2.5" className="a-tile" />
          <rect x="51" y="18" width="18" height="10" rx="2.5" className="a-tile" />
          <rect x="10" y="22" width="9" height="2.4" rx="1.2" style={{ fill: `url(#${uid}-brand)` }} />
          <path d="M7 57 L17 50 L26 52 L36 44 L46 47 L56 39 L69 42 L69 59 L7 59Z" fill={`url(#${uid}-fade)`} />
          <path d="M7 57 L17 50 L26 52 L36 44 L46 47 L56 39 L69 42" fill="none" strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" style={{ stroke: `url(#${uid}-brand)` }} />
          <text x="38" y="77" textAnchor="middle" className="a-label" style={{ fontSize: 9 }}>
            {app.name.length > 18 ? `${app.name.slice(0, 17)}…` : app.name}
          </text>
        </g>
      ) : (
        <g transform="translate(220 14)">
          <rect width="76" height="64" rx="7" className="a-slot" />
          <text x="38" y="35" textAnchor="middle" className="a-label" style={{ fontSize: 9 }}>
            no app yet
          </text>
        </g>
      )}

      {secret && (
        <g data-secret-pill transform="translate(124 94)">
          <rect width={pillW} height="16" rx="8" className="a-warn-pill" />
          <circle cx="9" cy="8" r="2.5" style={{ fill: 'var(--warning)' }} />
          <text x="16" y="11.2" className="a-mono" style={{ fontSize: 8.5, fill: 'var(--warning-text)' }}>
            {secret.key.length > 18 ? `${secret.key.slice(0, 17)}…` : secret.key}
          </text>
        </g>
      )}
    </svg>
  )
}
