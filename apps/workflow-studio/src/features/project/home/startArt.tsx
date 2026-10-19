/**
 * Small line art for the six start cards ("Where do you want to start?"). Same language as `art.tsx`: neutral
 * `--art-line` strokes and one focal mark in the brand stroke. Static: nothing moves, so reduced motion is moot.
 */
import type { StartKind } from '../lib/nextSteps'

const brand = { stroke: 'var(--brand-1)' }

export function StartArt({ kind }: { kind: StartKind }) {
  return (
    <svg viewBox="0 0 160 64" className="art block h-auto w-full" aria-hidden>
      {kind === 'interface' && (
        <>
          <rect x="38" y="6" width="84" height="52" rx="6" className="a-card" />
          <path d="M38 17 H122" className="a-wire" />
          <circle cx="45" cy="11.5" r="1.6" className="a-dot" />
          <circle cx="51" cy="11.5" r="1.6" className="a-dot" />
          <rect x="46" y="24" width="30" height="12" rx="3" className="a-tile" />
          <rect x="80" y="24" width="34" height="12" rx="3" className="a-tile" />
          <path d="M46 43 H96" className="a-wire" strokeLinecap="round" />
          <rect x="96" y="40" width="18" height="10" rx="3" fill="none" strokeWidth="1.3" style={brand} />
        </>
      )}
      {kind === 'backend' && (
        <>
          <rect x="18" y="12" width="64" height="16" rx="5" className="a-card" />
          <text x="25" y="23" className="a-mono a-strong" style={{ fontSize: 7.5, fill: 'var(--method-get)' }}>
            GET
          </text>
          <text x="44" y="23" className="a-mono" style={{ fontSize: 7.5 }}>
            /items
          </text>
          <rect x="18" y="36" width="64" height="16" rx="5" className="a-card" />
          <text x="25" y="47" className="a-mono a-strong" style={{ fontSize: 7.5, fill: 'var(--method-post)' }}>
            POST
          </text>
          <text x="48" y="47" className="a-mono" style={{ fontSize: 7.5 }}>
            /items
          </text>
          <path d="M82 20 C 100 20, 100 30, 116 30" className="a-wire a-hot" />
          <path d="M82 44 C 100 44, 100 34, 116 34" className="a-wire" />
          <path d="M116 22 v20 a14 4.5 0 0 0 28 0 v-20" fill="none" strokeWidth="1.25" style={brand} />
          <ellipse cx="130" cy="22" rx="14" ry="4.5" fill="none" strokeWidth="1.25" style={brand} />
        </>
      )}
      {kind === 'workflow' && (
        <>
          <path d="M46 32 H66 M94 32 H114" className="a-wire a-hot" />
          <rect x="18" y="20" width="28" height="24" rx="6" className="a-card" />
          <rect x="66" y="20" width="28" height="24" rx="6" fill="none" strokeWidth="1.3" style={brand} />
          <rect x="114" y="20" width="28" height="24" rx="6" className="a-card" />
          <path d="M27 32 l4 -4 v8z" className="a-dot" />
          <path d="M74 32 h12 M80 26 v12" fill="none" strokeWidth="1.3" strokeLinecap="round" style={brand} />
          <path d="M121 28 h14 M121 32 h10 M121 36 h12" className="a-wire" strokeLinecap="round" />
        </>
      )}
      {kind === 'dataset' && (
        <>
          <rect x="22" y="8" width="70" height="48" rx="5" className="a-card" />
          <path d="M22 20 H92 M22 32 H92 M22 44 H92 M46 8 V56 M70 8 V56" className="a-wire" />
          <path d="M104 52 V40 M116 52 V28 M128 52 V34 M140 52 V18" strokeWidth="5" strokeLinecap="round" style={{ stroke: 'var(--art-line)' }} />
          <path d="M140 52 V18" strokeWidth="5" strokeLinecap="round" style={brand} />
        </>
      )}
      {kind === 'template' && (
        <>
          <rect x="54" y="4" width="64" height="40" rx="6" className="a-slot" />
          <rect x="47" y="11" width="64" height="40" rx="6" className="a-card" />
          <rect x="40" y="18" width="64" height="40" rx="6" fill="none" strokeWidth="1.3" style={{ ...brand, fill: 'var(--card)' }} />
          <path d="M49 30 h28 M49 38 h44 M49 46 h20" className="a-wire" strokeLinecap="round" />
        </>
      )}
      {kind === 'ai' && (
        <>
          <rect x="18" y="34" width="124" height="20" rx="6" className="a-card" />
          <path d="M26 44 H96" className="a-wire" strokeLinecap="round" />
          <path d="M80 6 C 81.5 14, 84 16.5, 92 18 C 84 19.5, 81.5 22, 80 30 C 78.5 22, 76 19.5, 68 18 C 76 16.5, 78.5 14, 80 6 Z" fill="none" strokeWidth="1.3" strokeLinejoin="round" style={brand} />
          <path d="M100 8 v6 M97 11 h6" fill="none" strokeWidth="1.1" strokeLinecap="round" style={{ stroke: 'var(--art-line)' }} />
          <rect x="120" y="38" width="16" height="12" rx="3.5" fill="none" strokeWidth="1.3" style={brand} />
        </>
      )}
    </svg>
  )
}
