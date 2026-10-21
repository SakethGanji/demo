import { badgeVariants } from '@/shared/components/ui/badge'
import { useState } from 'react'
import { CheckCircle2, ChevronDown, XCircle } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { checksLabel, failedChecks, type Autotest } from '../lib/autotest'

/** "Checks 9/10 passing": the build's scenario checks, with the failures one click away. */
export function ChecksChip({ autotest }: { autotest: Autotest }) {
  const [open, setOpen] = useState(false)
  const failed = failedChecks(autotest)
  const allOk = failed.length === 0
  return (
    <div className="px-3 py-1.5" data-testid="build-checks">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={allOk}
        aria-expanded={open}
        className={cn(
          badgeVariants({ variant: allOk ? 'success' : 'warning' }),
          'h-6 cursor-pointer outline-none disabled:cursor-default',
        )}
        data-testid="build-checks-chip"
      >
        {allOk ? <CheckCircle2 className="size-3" /> : <XCircle className="size-3" />}
        {checksLabel(autotest)}
        {!allOk && <ChevronDown className={cn('size-3 transition-transform', open && 'rotate-180')} />}
      </button>
      {open && !allOk && (
        <ul className="mt-1.5 space-y-1 text-xs" data-testid="build-checks-failures">
          {failed.map((f) => (
            <li key={f.name} className="rounded-ctl bg-recess px-2 py-1">
              <span className="font-medium text-foreground">{f.name}</span>
              {f.reason && <span className="text-muted-foreground"> · {f.reason}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
