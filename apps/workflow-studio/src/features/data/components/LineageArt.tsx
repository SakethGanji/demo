/**
 * Table lineage (DESIGN §6): who writes this table and who reads it, drawn from the project's routes and
 * apps. Lines are neutral; the table is the one focal node (brand stroke + glow). Wires are still until a
 * real write happens: `handle.pulseWrite()` sends one packet down every write wire (`shared/components/art/pulse`).
 */
import { forwardRef, useId, useImperativeHandle, useRef } from 'react'
import { pulseWire } from '@/shared/components/art/pulse'
import type { Route } from '@/shared/api/types'

export interface LineageHandle {
  pulseWrite: () => void
}

const METHOD_VAR: Record<string, string> = {
  GET: 'var(--method-get)',
  POST: 'var(--method-post)',
  PUT: 'var(--method-put)',
  PATCH: 'var(--method-patch)',
  DELETE: 'var(--method-delete)',
}

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)
const READER_Y: Record<number, number[]> = { 1: [35], 2: [20, 50], 3: [5, 35, 65] }
const WRITER_Y: Record<number, number[]> = { 1: [34.5], 2: [15, 54] }

type Reader = { kind: 'route'; route: Route } | { kind: 'app'; id: string; name: string }

export const LineageArt = forwardRef<
  LineageHandle,
  { table: string; writers: Route[]; readers: Route[]; apps: { id: string; name: string }[]; columns: number }
>(function LineageArt({ table, writers, readers, apps, columns }, ref) {
  const uid = useId().replace(/:/g, '')
  const root = useRef<SVGSVGElement>(null)
  useImperativeHandle(ref, () => ({ pulseWrite: () => pulseWire(root.current, 'write') }), [])

  const w = writers.slice(0, 2)
  const all: Reader[] = [...readers.map((route) => ({ kind: 'route' as const, route })), ...apps.map((a) => ({ kind: 'app' as const, ...a }))]
  const shown = all.slice(0, 3)
  const more = all.length - shown.length
  const wy = WRITER_Y[Math.max(1, w.length)]
  const ry = READER_Y[Math.max(1, shown.length)]
  const label =
    `${writers.length ? writers.map((r) => `${r.method} ${r.path}`).join(', ') : 'No route'} writes into the ${table} table. ` +
    `${all.length ? all.map((r) => (r.kind === 'route' ? `${r.route.method} ${r.route.path}` : r.name)).join(', ') : 'Nothing'} reads it.`

  return (
    <svg ref={root} viewBox="0 0 360 90" className="art block h-auto w-[360px] max-w-full overflow-visible" role="img" aria-label={label} data-testid="data-lineage">
      <defs>
        <linearGradient id={`${uid}-brand`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" style={{ stopColor: 'var(--brand-1)' }} /><stop offset="1" style={{ stopColor: 'var(--brand-1)' }} /></linearGradient>
        <pattern id={`${uid}-fade`} width="6" height="6" patternUnits="userSpaceOnUse"><path d="M0 0 L6 6 M6 0 L0 6" fill="none" strokeWidth=".6" style={{ stroke: 'var(--brand-1)', strokeOpacity: 0.35 }} /></pattern>
        <radialGradient id={`${uid}-glow`} cx=".5" cy=".5" r=".5">
          <stop offset="0" style={{ stopColor: 'var(--brand-1)', stopOpacity: 0.22 }} />
          <stop offset="1" style={{ stopColor: 'var(--brand-1)', stopOpacity: 0 }} />
        </radialGradient>
        <linearGradient id={`${uid}-wg`} gradientUnits="userSpaceOnUse" x1="99" y1="0" x2="152" y2="0"><stop offset="0" style={{ stopColor: 'var(--brand-1)' }} /><stop offset="1" style={{ stopColor: 'var(--brand-1)' }} /></linearGradient>
      </defs>

      {/* write */}
      {w.length === 0 ? (
        <g data-testid="lineage-no-writers">
          <title>No route writes this table yet. Code writes it with studio.db.table("{table}").insert(...)</title>
          <rect x=".5" y="34.5" width="98" height="21" rx="6" className="a-slot" />
          <text x="9" y="49" className="a-label" style={{ fontSize: 9.5 }}>
            no writers yet
          </text>
        </g>
      ) : (
        w.map((r, i) => {
          const cy = wy[i] + 10.5
          return (
            <g key={r.id} data-testid="lineage-writer" data-method={r.method}>
              <title>{`${r.method} ${r.path} writes to ${table}`}</title>
              <rect x=".5" y={wy[i]} width="98" height="21" rx="6" className="a-card" />
              <text x="9" y={wy[i] + 14.5} className="a-mono" style={{ fontSize: 9.5 }}>
                <tspan className="a-strong" style={{ fill: METHOD_VAR[r.method] ?? 'var(--muted-foreground)' }}>
                  {r.method}
                </tspan>{' '}
                {clip(r.path, Math.max(4, 14 - r.method.length))}
              </text>
              <path
                data-wire="write"
                d={Math.abs(cy - 45) < 1 ? 'M99 45 H151' : `M99 ${cy} C 122 ${cy}, 128 45, 151 45`}
                fill="none"
                style={{ stroke: `url(#${uid}-wg)` }}
                strokeWidth="1.25"
              />
              <circle cx="99" cy={cy} r="1.6" style={{ fill: 'var(--brand-1)' }} />
            </g>
          )
        })
      )}
      {w.length === 1 && (
        <text x="124.5" y="39" textAnchor="middle" className="a-label" style={{ fontSize: 9 }}>
          write
        </text>
      )}
      {w.length === 0 && <path d="M99 45 H151" className="a-wire" strokeDasharray="3 4" />}

      {/* read */}
      {shown.length === 0 ? (
        <g data-testid="lineage-no-readers">
          <title>No route or app reads this table yet</title>
          <rect x="240.5" y="34.5" width="119" height="21" rx="6" className="a-slot" />
          <text x="249" y="49" className="a-label" style={{ fontSize: 9.5 }}>
            no readers yet
          </text>
          <path d="M194 45 H238" className="a-wire" strokeDasharray="3 4" />
        </g>
      ) : (
        <>
          {shown.map((r, i) => {
            const y = ry[i]
            const cy = y + 10.5
            return (
              <g key={r.kind === 'route' ? r.route.id : `app-${r.id}`}>
                <path d={Math.abs(cy - 45) < 1 ? 'M194 45 H238' : `M194 45 C 216 45, 216 ${cy}, 238 ${cy}`} className="a-wire" />
                <g transform={`translate(240 ${y})`} data-testid="lineage-reader" data-kind={r.kind}>
                  <title>{r.kind === 'route' ? `${r.route.method} ${r.route.path} reads ${table}` : `The ${r.name} app reads this table`}</title>
                  <rect x=".5" y=".5" width="119" height="20" rx="6" className="a-card" />
                  {r.kind === 'route' ? (
                    <text x="9" y="14" className="a-mono" style={{ fontSize: 9.5 }}>
                      <tspan className="a-strong" style={{ fill: METHOD_VAR[r.route.method] ?? 'var(--muted-foreground)' }}>
                        {r.route.method}
                      </tspan>{' '}
                      {clip(r.route.path, Math.max(4, 19 - r.route.method.length))}
                    </text>
                  ) : (
                    <>
                      <g transform="translate(9 6)" style={{ fill: 'none', stroke: 'var(--muted-foreground)' }} strokeWidth="1">
                        <rect x=".5" y=".5" width="10" height="8" rx="1.5" />
                        <path d="M.5 3 H10.5" />
                      </g>
                      <text x="25" y="14" className="a-label" style={{ fontSize: 9.5, fill: 'var(--foreground)' }}>
                        {clip(r.name, 17)}
                      </text>
                    </>
                  )}
                </g>
              </g>
            )
          })}
          <circle cx="194" cy="45" r="1.6" style={{ fill: 'var(--art-line)' }} />
          {shown.length > 1 && (
            <text x="224" y="41" textAnchor="middle" className="a-label" style={{ fontSize: 9 }}>
              read
            </text>
          )}
          {more > 0 && (
            <text x="240" y="89" className="a-label" style={{ fontSize: 9 }}>
              +{more} more
            </text>
          )}
        </>
      )}

      {/* the table: the focal node */}
      <g className="a-glow" transform="translate(152 29)">
        <title>{`${table} · ${columns} column${columns === 1 ? '' : 's'}, written by ${writers.length} route${writers.length === 1 ? '' : 's'}, read by ${all.length}`}</title>
        <path d="M0 5 v22 a20 5 0 0 0 40 0 v-22" className="a-solid" style={{ stroke: `url(#${uid}-brand)` }} strokeWidth="1.25" />
        <path d="M0 5 v22 a20 5 0 0 0 40 0 v-22" fill={`url(#${uid}-fade)`} />
        <path d="M0 16 a20 5 0 0 0 40 0" fill="none" style={{ stroke: `url(#${uid}-brand)` }} strokeWidth="1" opacity=".45" />
        <ellipse cx="20" cy="5" rx="20" ry="5" className="a-solid" style={{ stroke: `url(#${uid}-brand)` }} strokeWidth="1.25" />
        <ellipse cx="20" cy="5" rx="20" ry="5" fill={`url(#${uid}-fade)`} />
      </g>
      <text x="172" y="80" textAnchor="middle" className="a-mono a-strong" style={{ fontSize: 10 }}>
        {clip(table, 16)}
      </text>
    </svg>
  )
})
