/** One cell of the profile row under the header: a caption plus a stacked bar, 5-bin histogram or created ticks. */
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'
import type { Profile } from '../lib/profile'

const OPACITY = [1, 0.35, 0.2, 0.12]
const cap = 'text-[11px] leading-[13px] whitespace-nowrap text-muted-foreground'

export function ColumnProfile({ profile, right, testId }: { profile: Profile; right?: boolean; testId: string }) {
  if (profile.kind === 'bar') {
    return (
      <div className="flex flex-col justify-center gap-[5px]" data-testid={testId}>
        <div className="flex h-1 w-full max-w-[280px] gap-px overflow-hidden rounded-xs bg-(--art-faint)">
          {profile.segments.map((s, i) => (
            <i
              key={s.label}
              title={`${s.label} ${s.count}`}
              className="h-full bg-brand-1 transition-[flex-grow] duration-(--d-slow) ease-(--ease-out)"
              style={{ flexGrow: s.count, opacity: OPACITY[Math.min(i, 3)] }}
            />
          ))}
        </div>
        <span className={cap}>{profile.caption}</span>
      </div>
    )
  }
  if (profile.kind === 'hist') {
    const mx = Math.max(1, ...profile.bins)
    return (
      <div className={cn('flex items-center gap-2', right && 'justify-end')} data-testid={testId}>
        <Tip label={profile.ranges.join(' · ')}>
          <div className="flex h-4 items-end gap-0.5">
            {profile.bins.map((c, i) => (
              <i
                key={i}
                className="min-h-[1.5px] w-[9px] rounded-t-xs transition-[height,background-color] duration-(--d-slow) ease-(--ease-out)"
                style={{
                  height: c ? 4 + (c / mx) * 12 : 1.5,
                  background: c ? 'color-mix(in srgb, var(--brand-1) 55%, transparent)' : 'var(--art-line)',
                }}
              />
            ))}
          </div>
        </Tip>
        <span className={cn(cap, 'font-mono')}>{profile.caption}</span>
      </div>
    )
  }
  if (profile.kind === 'ticks') {
    return (
      <div className="flex items-center gap-2" data-testid={testId}>
        <Tip label={profile.tip}>
          <div className="relative h-3 w-16 shadow-[inset_0_-1px_0_var(--art-line)]">
            {[0.25, 0.5, 0.75].map((x) => (
              <span key={x} className="absolute bottom-0 h-[3px] w-px bg-(--art-line)" style={{ left: `${x * 100}%` }} />
            ))}
            {profile.marks.slice(0, 200).map((m, i) => (
              <b key={i} className="absolute bottom-0 h-[9px] w-[1.5px] rounded-xs bg-primary" style={{ left: `calc(${Math.min(1, m) * 100}% - .75px)` }} />
            ))}
          </div>
        </Tip>
        <span className={cap}>{profile.caption}</span>
      </div>
    )
  }
  return (
    <span className={cap} data-testid={testId}>
      {profile.caption}
    </span>
  )
}
