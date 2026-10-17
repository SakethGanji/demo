/**
 * The system diagram (DESIGN §6, Overview hero and Projects cards), drawn from the project's real data:
 * route chips (method + path) wired to the table they serve, the table wired to the app, and a dashed
 * amber wire to a missing secret. Lines are neutral (`--art-line`); the table is the one focal node
 * (brand stroke + glow). Wires are still until `pulse()` is called for a real event.
 *
 *   <SystemDiagram ref={handle} module="refunds.py" routes={[…]} table={{ name, rows }} app={{ name }} secret={{ key, env }} />
 *   handle.current?.pulse('route:refundStats') · pulse('app') · pulse('secret', { color: 'secret' })
 */
import { forwardRef, useId, useImperativeHandle, useRef } from 'react'
import { cn } from '@/shared/lib/utils'
import { pulseWire, type PulseColor } from './pulse'

export interface DiagramRoute {
  key: string
  method: string
  path: string
  /** carries traffic that reaches the app: drawn as a "hot" wire */
  hot?: boolean
}

export interface SystemDiagramHandle {
  pulse: (wire: string, opts?: { color?: PulseColor; delay?: number }) => void
}

const METHOD_VAR: Record<string, string> = {
  GET: 'var(--method-get)',
  POST: 'var(--method-post)',
  PUT: 'var(--method-put)',
  PATCH: 'var(--method-patch)',
  DELETE: 'var(--method-delete)',
}

const W = 540
const CHIP_W = 150
const CHIP_H = 30
const TABLE_X = 262
const APP_X = 384

export const SystemDiagram = forwardRef<
  SystemDiagramHandle,
  {
    module?: string
    routes: DiagramRoute[]
    table?: { name: string; rows?: number | null } | null
    app?: { name: string } | null
    secret?: { key: string; env: string } | null
    /** most routes drawn before "+N more" */
    max?: number
    className?: string
    compact?: boolean
    'aria-label'?: string
  }
>(function SystemDiagram({ module, routes, table, app, secret, max = 3, className, compact, ...rest }, ref) {
  const uid = useId().replace(/:/g, '')
  const root = useRef<SVGSVGElement>(null)
  useImperativeHandle(ref, () => ({ pulse: (wire, opts) => pulseWire(root.current, wire, opts) }), [])

  const shown = routes.slice(0, max)
  const more = routes.length - shown.length
  const n = Math.max(1, shown.length)
  const top = 24
  const gap = n === 1 ? 0 : Math.min(44, 88 / (n - 1))
  const chipY = (i: number) => (n === 1 ? 68 : top + i * gap)
  const tableCy = 74
  const H = 172
  const hasTable = !!table
  const label =
    rest['aria-label'] ??
    `${routes.length} route${routes.length === 1 ? '' : 's'}${module ? ` in ${module}` : ''}${table ? ` use the ${table.name} table` : ''}${app ? `, which feeds ${app.name}` : ''}.${secret ? ` ${secret.key} is missing in ${secret.env}.` : ''}`

  // where wires land on the table: spread over its height
  const landY = (i: number) => (n === 1 ? tableCy + 1 : tableCy - 8 + (16 * i) / (n - 1))
  const target = hasTable ? TABLE_X : APP_X
  const lastChipBottom = chipY(n - 1) + CHIP_H

  return (
    <svg
      ref={root}
      viewBox={`0 0 ${W} ${H}`}
      className={cn('art block h-auto w-full overflow-visible', className)}
      role="img"
      aria-label={label}
      data-art="system"
    >
      <defs>
        <linearGradient id={`${uid}-brand`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" style={{ stopColor: 'var(--brand-1)' }} /><stop offset="1" style={{ stopColor: 'var(--brand-1)' }} /></linearGradient>
        <pattern id={`${uid}-fade`} width="6" height="6" patternUnits="userSpaceOnUse"><path d="M0 0 L6 6 M6 0 L0 6" fill="none" strokeWidth=".6" style={{ stroke: 'var(--brand-1)', strokeOpacity: 0.35 }} /></pattern>
        <radialGradient id={`${uid}-glow`} cx=".5" cy=".5" r=".5">
          <stop offset="0" style={{ stopColor: 'var(--brand-1)', stopOpacity: 0.22 }} />
          <stop offset="1" style={{ stopColor: 'var(--brand-1)', stopOpacity: 0 }} />
        </radialGradient>
        <pattern id={`${uid}-dots`} width="12" height="12" patternUnits="userSpaceOnUse">
          <circle cx="1" cy="1" r=".9" style={{ fill: 'var(--art-faint)' }} />
        </pattern>
        <radialGradient id={`${uid}-mask-g`} cx=".55" cy=".5" r=".6">
          <stop offset=".3" stopColor="white" />
          <stop offset="1" stopColor="black" />
        </radialGradient>
        <mask id={`${uid}-mask`}>
          <rect width="100%" height="100%" fill={`url(#${uid}-mask-g)`} />
        </mask>
      </defs>

      {!compact && <rect width={W} height={H} fill={`url(#${uid}-dots)`} mask={`url(#${uid}-mask)`} />}

      {/* wires */}
      {shown.map((r, i) => {
        const y = chipY(i) + CHIP_H / 2
        const ly = landY(i)
        return (
          <path
            key={`w-${r.key}`}
            data-wire={`route:${r.key}`}
            d={`M${20 + CHIP_W} ${y} C ${20 + CHIP_W + 50} ${y}, ${target - 37} ${ly}, ${target} ${ly}`}
            className={cn('a-wire', r.hot && 'a-hot')}
          />
        )
      })}
      {hasTable && app && <path data-wire="app" d={`M${TABLE_X + 76} ${tableCy + 1} C 360 ${tableCy + 1}, 362 82, ${APP_X} 82`} className="a-wire a-hot" />}
      {secret && <path data-wire="secret" d={`M150 ${lastChipBottom} C 150 158, 196 160, 222 160`} className="a-wire a-warn" />}

      {/* routes */}
      {module && (
        <text x="20" y="16" className="a-label">
          {module}
        </text>
      )}
      {shown.map((r, i) => (
        <g key={r.key} transform={`translate(20 ${chipY(i)})`}>
          <rect width={CHIP_W} height={CHIP_H} rx="8" className="a-card" />
          <text x="12" y="19.5" className="a-mono a-strong" style={{ fill: METHOD_VAR[r.method.toUpperCase()] ?? 'var(--muted-foreground)' }}>
            {r.method.toUpperCase()}
          </text>
          <text x={r.method.length > 3 ? 48 : 42} y="19.5" className="a-mono">
            {r.path.length > 16 ? `${r.path.slice(0, 15)}…` : r.path}
          </text>
        </g>
      ))}
      {more > 0 && (
        <text x="20" y={Math.min(H - 4, lastChipBottom + 14)} className="a-label">
          +{more} more
        </text>
      )}
      {shown.length === 0 && (
        <g transform="translate(20 68)">
          <rect width={CHIP_W} height={CHIP_H} rx="8" className="a-slot" />
          <text x="12" y="19.5" className="a-label">
            no routes yet
          </text>
        </g>
      )}

      {/* table: the focal node */}
      {hasTable ? (
        <g transform={`translate(${TABLE_X} 40)`} className="a-glow">
          <path d="M0 10 v44 a38 10 0 0 0 76 0 v-44" className="a-solid a-stroke-brand" />
          <path d="M0 10 v44 a38 10 0 0 0 76 0 v-44" fill={`url(#${uid}-fade)`} />
          <path d="M0 32 a38 10 0 0 0 76 0" className="a-stroke-brand" fill="none" opacity=".5" />
          <ellipse cx="38" cy="10" rx="38" ry="10" className="a-solid a-stroke-brand" />
          <ellipse cx="38" cy="10" rx="38" ry="10" fill={`url(#${uid}-fade)`} />
          <text x="38" y="84" textAnchor="middle" className="a-mono a-strong">
            {table!.name.length > 14 ? `${table!.name.slice(0, 13)}…` : table!.name}
          </text>
          {table!.rows != null && (
            <text x="38" y="97" textAnchor="middle" className="a-label">
              {table!.rows} row{table!.rows === 1 ? '' : 's'}
            </text>
          )}
        </g>
      ) : null}

      {/* app */}
      {app ? (
        <g transform={`translate(${APP_X} 30)`}>
          <rect width="136" height="104" rx="10" className="a-card" />
          <path d="M0 10 a10 10 0 0 1 10 -10 h116 a10 10 0 0 1 10 10 v8 h-136z" className="a-tile" />
          <circle cx="10" cy="9" r="2.2" className="a-dot" />
          <circle cx="17" cy="9" r="2.2" className="a-dot" />
          <circle cx="24" cy="9" r="2.2" className="a-dot" />
          <rect x="10" y="26" width="34" height="18" rx="4" className="a-tile" />
          <rect x="51" y="26" width="34" height="18" rx="4" className="a-tile" />
          <rect x="92" y="26" width="34" height="18" rx="4" className="a-tile" />
          <rect x="14" y="31" width="14" height="3" rx="1.5" className="a-bar" />
          <rect x="14" y="37" width="22" height="3" rx="1.5" style={{ fill: `url(#${uid}-brand)` }} />
          <path d="M10 92 L28 80 L44 84 L62 68 L80 74 L98 58 L126 62 L126 96 L10 96Z" fill={`url(#${uid}-fade)`} />
          <path
            d="M10 92 L28 80 L44 84 L62 68 L80 74 L98 58 L126 62"
            fill="none"
            style={{ stroke: `url(#${uid}-brand)` }}
            strokeWidth="1.8"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          <circle cx="126" cy="62" r="3" style={{ fill: 'var(--brand-2)' }} />
          <text x="68" y="122" textAnchor="middle" className="a-label">
            {app.name.length > 22 ? `${app.name.slice(0, 21)}…` : app.name}
          </text>
        </g>
      ) : (
        <g transform={`translate(${APP_X} 30)`}>
          <rect width="136" height="104" rx="10" className="a-slot" />
          <text x="68" y="57" textAnchor="middle" className="a-label">
            no app yet
          </text>
        </g>
      )}

      {/* missing secret */}
      {secret && (
        <g transform="translate(222 150)" data-secret-pill>
          <rect width={Math.max(120, 44 + (secret.key.length + secret.env.length) * 6.6)} height="20" rx="10" className="a-warn-pill" />
          <circle cx="11" cy="10" r="3" style={{ fill: 'var(--warning)' }} />
          <text x="20" y="14" className="a-mono" style={{ fill: 'var(--warning-text)' }}>
            {secret.key} · {secret.env}
          </text>
        </g>
      )}
    </svg>
  )
})

/** The small app preview (Overview apps, Projects cards). */
export function AppThumb({ className }: { className?: string }) {
  const uid = useId().replace(/:/g, '')
  return (
    <svg viewBox="0 0 96 60" className={cn('art block h-auto w-full', className)} role="img" aria-label="App preview">
      <defs>
        <linearGradient id={`${uid}-brand`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" style={{ stopColor: 'var(--brand-1)' }} /><stop offset="1" style={{ stopColor: 'var(--brand-1)' }} /></linearGradient>
        <pattern id={`${uid}-fade`} width="6" height="6" patternUnits="userSpaceOnUse"><path d="M0 0 L6 6 M6 0 L0 6" fill="none" strokeWidth=".6" style={{ stroke: 'var(--brand-1)', strokeOpacity: 0.35 }} /></pattern>
      </defs>
      <rect x=".5" y=".5" width="95" height="59" rx="7" className="a-card" />
      <path d="M.5 7.5 a7 7 0 0 1 7 -7 h81 a7 7 0 0 1 7 7 v3 h-95z" className="a-tile" />
      <circle cx="6" cy="6" r="1.4" className="a-dot" />
      <circle cx="10.5" cy="6" r="1.4" className="a-dot" />
      <circle cx="15" cy="6" r="1.4" className="a-dot" />
      <rect x="6" y="15" width="25" height="11" rx="2.5" className="a-tile" />
      <rect x="35.5" y="15" width="25" height="11" rx="2.5" className="a-tile" />
      <rect x="65" y="15" width="25" height="11" rx="2.5" className="a-tile" />
      <rect x="9" y="19" width="12" height="2.5" rx="1.2" style={{ fill: `url(#${uid}-brand)` }} />
      <path d="M6 53 L18 46 L28 48 L40 39 L52 43 L64 34 L76 37 L90 31 L90 55 L6 55Z" fill={`url(#${uid}-fade)`} />
      <path d="M6 53 L18 46 L28 48 L40 39 L52 43 L64 34 L76 37 L90 31" fill="none" style={{ stroke: `url(#${uid}-brand)` }} strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
