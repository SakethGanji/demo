/**
 * The "add" card that ends each Overview grid ("New app", "New workflow"): the same Tile + Well + name rows as the
 * preview cards beside it, so it sits in the grid at their size, with a drawn picture in place of a preview and a
 * dashed edge on the well. Hover lifts it like any card (glass-hover) and lights the picture up.
 *
 * The pictures are inline SVG drawn in neutral greys from the theme tokens (foreground, muted-foreground, art-line), so they follow light and dark:
 *   app       three isometric pages stacked like layers, the top one wireframed (bar, sidebar, cards).
 *   workflow  a trigger, two steps on a branch and a join, linked by flowing edges, with a ghost step to add.
 */
import { useId } from 'react'
import { Tile, Well } from '@/shared/components/ui/surface'

/* one isometric plane: x runs down-right, y runs down-left */
const ISO = (x: number, y: number) => `matrix(0.866 0.5 -0.866 0.5 ${x} ${y})`

function AppArt() {
  const id = useId().replace(/:/g, '')
  const stroke = `url(#${id}-s)`
  return (
    <svg viewBox="0 0 160 100" className="size-full" aria-hidden>
      <defs>
        <linearGradient id={`${id}-s`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: 'var(--muted-foreground)' }} />
          <stop offset="1" style={{ stopColor: 'var(--foreground)' }} />
        </linearGradient>
        <linearGradient id={`${id}-f`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={{ stopColor: 'var(--muted-foreground)', stopOpacity: 0.22 }} />
          <stop offset="1" style={{ stopColor: 'var(--foreground)', stopOpacity: 0.1 }} />
        </linearGradient>
        <radialGradient id={`${id}-g`} cx="0.5" cy="0.55" r="0.5">
          <stop offset="0" style={{ stopColor: 'var(--foreground)', stopOpacity: 0.18 }} />
          <stop offset="1" style={{ stopColor: 'var(--foreground)', stopOpacity: 0 }} />
        </radialGradient>
      </defs>
      <ellipse cx="80" cy="62" rx="62" ry="30" fill={`url(#${id}-g)`} className="opacity-60 transition-opacity duration-(--d-slow) group-hover/c:opacity-100" />
      {/* the two pages under, then the top page lifting on hover */}
      <g transform={ISO(80, 40)} fill="none" style={{ stroke: 'var(--muted-foreground)' }} strokeWidth="1" strokeOpacity="0.35" strokeLinejoin="round">
        <rect x="0" y="0" width="56" height="44" rx="4" style={{ fill: 'var(--muted-foreground)' }} fillOpacity="0.06" />
      </g>
      <g transform={ISO(80, 32)} fill="none" style={{ stroke: 'var(--muted-foreground)' }} strokeWidth="1" strokeOpacity="0.55" strokeLinejoin="round">
        <rect x="0" y="0" width="56" height="44" rx="4" style={{ fill: 'var(--muted-foreground)' }} fillOpacity="0.1" />
      </g>
      <g className="transition-transform duration-(--d-slow) ease-(--ease-spring) group-hover/c:-translate-y-1.5 motion-reduce:group-hover/c:translate-y-0">
        <g transform={ISO(80, 20)} strokeLinejoin="round" strokeLinecap="round">
          <rect x="0" y="0" width="56" height="44" rx="4" fill={`url(#${id}-f)`} stroke={stroke} strokeWidth="1.4" />
          {/* window dots and the bar */}
          <circle cx="5" cy="4.5" r="1.1" style={{ fill: 'var(--muted-foreground)' }} />
          <circle cx="9" cy="4.5" r="1.1" style={{ fill: 'var(--muted-foreground)' }} fillOpacity="0.6" />
          <line x1="0" y1="9" x2="56" y2="9" stroke={stroke} strokeWidth="0.9" strokeOpacity="0.7" />
          {/* sidebar */}
          <rect x="4" y="13" width="11" height="27" rx="2" style={{ fill: 'var(--muted-foreground)' }} fillOpacity="0.22" />
          {/* cards */}
          <rect x="19" y="13" width="15" height="11" rx="2" style={{ fill: 'var(--foreground)' }} fillOpacity="0.28" />
          <rect x="37" y="13" width="15" height="11" rx="2" style={{ fill: 'var(--muted-foreground)' }} fillOpacity="0.3" />
          <line x1="19" y1="29" x2="52" y2="29" style={{ stroke: 'var(--art-line)' }} strokeWidth="1.4" />
          <line x1="19" y1="33.5" x2="44" y2="33.5" style={{ stroke: 'var(--art-line)' }} strokeWidth="1.4" />
          <line x1="19" y1="38" x2="48" y2="38" style={{ stroke: 'var(--art-line)' }} strokeWidth="1.4" />
        </g>
        <Plus cx={124} cy={26} id={id} />
      </g>
    </svg>
  )
}

function WorkflowArt() {
  const id = useId().replace(/:/g, '')
  const edge = `url(#${id}-s)`
  const edgeCls = 'group-hover/c:[stroke-dasharray:4_4] group-hover/c:animate-[dash-flow_.7s_linear_infinite] motion-reduce:group-hover/c:animate-none'
  return (
    <svg viewBox="0 0 160 100" className="size-full" aria-hidden>
      <defs>
        <linearGradient id={`${id}-s`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" style={{ stopColor: 'var(--muted-foreground)' }} />
          <stop offset="1" style={{ stopColor: 'var(--foreground)' }} />
        </linearGradient>
        <radialGradient id={`${id}-g`} cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" style={{ stopColor: 'var(--muted-foreground)', stopOpacity: 0.2 }} />
          <stop offset="1" style={{ stopColor: 'var(--muted-foreground)', stopOpacity: 0 }} />
        </radialGradient>
      </defs>
      <ellipse cx="80" cy="52" rx="66" ry="34" fill={`url(#${id}-g)`} className="opacity-60 transition-opacity duration-(--d-slow) group-hover/c:opacity-100" />
      {/* edges: trigger → two steps → join → ghost */}
      <g fill="none" stroke={edge} strokeWidth="1.5" strokeLinecap="round">
        <path d="M30 52 C 42 52, 42 32, 54 32" className={edgeCls} />
        <path d="M30 52 C 42 52, 42 72, 54 72" className={edgeCls} />
        <path d="M78 32 C 90 32, 90 52, 102 52" className={edgeCls} />
        <path d="M78 72 C 90 72, 90 52, 102 52" className={edgeCls} />
        <path d="M118 52 L 128 52" strokeOpacity="0.5" className={edgeCls} />
      </g>
      {/* trigger */}
      <circle cx="22" cy="52" r="8" style={{ fill: 'var(--muted-foreground)', stroke: 'var(--muted-foreground)' }} fillOpacity="0.2" strokeWidth="1.4" />
      <path d="M20.5 48.5 L 25 52 L 20.5 55.5 Z" style={{ fill: 'var(--muted-foreground)' }} />
      {/* two steps */}
      {[32, 72].map((y, i) => (
        <g key={y}>
          <rect x="54" y={y - 8} width="24" height="16" rx="4" style={{ fill: i ? 'var(--foreground)' : 'var(--muted-foreground)', stroke: i ? 'var(--foreground)' : 'var(--muted-foreground)' }} fillOpacity="0.16" strokeWidth="1.3" />
          <line x1="59" y1={y - 1.5} x2="73" y2={y - 1.5} style={{ stroke: 'var(--art-line)' }} strokeWidth="1.4" strokeLinecap="round" />
          <line x1="59" y1={y + 2.5} x2="68" y2={y + 2.5} style={{ stroke: 'var(--art-line)' }} strokeWidth="1.4" strokeLinecap="round" />
        </g>
      ))}
      {/* join */}
      <rect x="102" y="44" width="16" height="16" rx="8" fill="none" stroke={edge} strokeWidth="1.4" />
      <circle cx="110" cy="52" r="2.6" style={{ fill: 'var(--foreground)' }} />
      {/* the step you are about to add */}
      <g className="transition-transform duration-(--d-slow) ease-(--ease-spring) group-hover/c:-translate-y-1 motion-reduce:group-hover/c:translate-y-0">
        <rect x="128" y="43" width="20" height="18" rx="4" fill="none" style={{ stroke: 'var(--art-line)' }} strokeWidth="1.3" strokeDasharray="3 2.5" />
        <Plus cx={138} cy={52} id={id} small />
      </g>
    </svg>
  )
}

/** A round + in the foreground grey, the picture's call to action. */
function Plus({ cx, cy, id, small }: { cx: number; cy: number; id: string; small?: boolean }) {
  const r = small ? 6 : 9
  const a = small ? 2.6 : 3.8
  return (
    <g>
      <circle cx={cx} cy={cy} r={r} style={{ fill: 'var(--foreground)' }} className="opacity-75 transition-opacity group-hover/c:opacity-100" />
      <circle cx={cx} cy={cy} r={r} fill="none" stroke={`url(#${id}-s)`} strokeWidth="1" strokeOpacity="0.6" transform={`translate(${cx} ${cy}) scale(1.35) translate(${-cx} ${-cy})`} />
      <path d={`M${cx - a} ${cy} H${cx + a} M${cx} ${cy - a} V${cy + a}`} style={{ stroke: 'var(--background)' }} strokeWidth={small ? 1.5 : 1.8} strokeLinecap="round" />
    </g>
  )
}

export function AddTile({ kind, onClick }: { kind: 'app' | 'workflow'; onClick: () => void }) {
  const app = kind === 'app'
  const label = app ? 'New app' : 'New workflow'
  return (
    <button
      type="button"
      onClick={onClick}
      className="group/c block min-w-0 rounded-panel text-left outline-none focus-visible:shadow-focus"
      aria-label={label}
      data-testid={app ? 'home-new-app' : 'home-new-workflow'}
    >
      <Tile interactive className="relative flex flex-col p-2">
        <Well className="relative aspect-[16/10] overflow-hidden outline-1 outline-offset-[-5px] outline-art-line outline-dashed transition-[outline-color] duration-(--d-slow) group-hover/c:outline-foreground/30">
          {app ? <AppArt /> : <WorkflowArt />}
        </Well>
        <span className="flex min-w-0 flex-col gap-0.5 px-1.5 pt-2.5 pb-1">
          <span className="truncate text-[13.5px] font-semibold tracking-[-0.01em] text-foreground">{label}</span>
          <span className="truncate text-[11.5px] text-muted-foreground">{app ? 'Describe it, we plan and build it' : 'Chain steps on a trigger'}</span>
        </span>
      </Tile>
    </button>
  )
}
