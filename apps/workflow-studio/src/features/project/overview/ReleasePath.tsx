/**
 * The release path (DESIGN §6 line art): dev → blocker → prod, drawn from the project's environments,
 * the active releases and the secrets missing in the live environment. One focal node: the live end
 * when it serves a release, otherwise dev.
 */
import { useId } from 'react'
import { cn } from '@/shared/lib/utils'

export function ReleasePath({
  draft,
  live,
  releaseNumber,
  blockers,
  className,
}: {
  draft: string
  live: string
  releaseNumber: number | null
  blockers: number
  className?: string
}) {
  const uid = useId().replace(/:/g, '')
  const released = releaseNumber != null
  const label = `${draft} is live with the latest saved code. ${live} ${released ? `serves release ${releaseNumber}` : 'has no release yet'}${blockers ? ` and ${blockers} blocker${blockers === 1 ? '' : 's'}` : ''}.`
  return (
    <svg viewBox="0 0 300 58" className={cn('art block h-auto w-full', className)} role="img" aria-label={label} data-art="release">
      <defs>
        <linearGradient id={`${uid}-brand`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" style={{ stopColor: 'var(--brand-1)' }} /><stop offset="1" style={{ stopColor: 'var(--brand-1)' }} /></linearGradient>
      </defs>
      <path d="M22 22 H278" className="a-wire" strokeDasharray="3 5" />
      <path d={released && !blockers ? 'M22 22 H278' : 'M22 22 H120'} style={{ stroke: `url(#${uid}-brand)` }} fill="none" strokeWidth="2" strokeLinecap="round" />
      <circle cx="22" cy="22" r="9" style={{ fill: 'var(--brand-1)', fillOpacity: 0.14 }} />
      <circle cx="22" cy="22" r="5" style={{ fill: `url(#${uid}-brand)` }} />
      {blockers > 0 && (
        <g transform="translate(150 22)">
          <circle r="11" style={{ fill: 'var(--card)', stroke: 'var(--warning)' }} strokeWidth="1.5" />
          <path d="M-2.5 2.5 l5 -5 M1 -4 a2.6 2.6 0 1 1 3 3" style={{ stroke: 'var(--warning)' }} strokeWidth="1.5" fill="none" strokeLinecap="round" />
        </g>
      )}
      {released && !blockers ? (
        <circle cx="278" cy="22" r="6" style={{ fill: `url(#${uid}-brand)` }} />
      ) : (
        <circle cx="278" cy="22" r="6" style={{ fill: 'var(--card)', stroke: 'var(--art-line)' }} strokeWidth="1.5" />
      )}
      <text x="22" y="50" className="a-mono a-strong">
        {draft.slice(0, 8)}
      </text>
      <text x={22 + Math.min(draft.length, 8) * 6.4 + 6} y="50" className="a-label">
        latest code
      </text>
      {blockers > 0 && (
        <text x="150" y="50" textAnchor="middle" className="a-label" style={{ fill: 'var(--warning-text)' }}>
          {blockers} blocker{blockers === 1 ? '' : 's'}
        </text>
      )}
      <text x="278" y="50" textAnchor="end" className="a-label">
        {released ? `#${releaseNumber}` : 'no release'}
      </text>
      <text x={278 - (released ? `#${releaseNumber}`.length : 10) * 5.3 - 6} y="50" textAnchor="end" className="a-mono a-strong">
        {live.slice(0, 8)}
      </text>
    </svg>
  )
}
