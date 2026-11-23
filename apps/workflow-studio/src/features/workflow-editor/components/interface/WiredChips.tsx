/** "Wired to the workflow automatically" (DESIGN-STAGE5-UIS §6.3, §11.1): computed by the engine from the bindings. */
import type { Wired } from '@/features/interfaces/lib/types'

function Chip({ testId, label, value }: { testId: string; label: string; value: string }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-chip bg-recess px-2 py-1 text-[12px]" data-testid={testId}>
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 truncate font-medium text-foreground" title={value}>{value}</span>
    </span>
  )
}

const join = (xs: string[]) => (xs.length ? xs.join(', ') : 'none')

export function WiredChips({ wired }: { wired: Wired | undefined }) {
  const w: Wired = wired ?? { input: [], tasks: [], context: [], result: null }
  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-[12px] font-medium text-muted-foreground">Wired to the workflow automatically</div>
      <div className="flex flex-wrap gap-1.5" data-testid="interface-wired">
        <Chip testId="wired-chip-input" label="Input" value={join(w.input)} />
        <Chip testId="wired-chip-task" label="Task" value={join(w.tasks)} />
        <Chip testId="wired-chip-context" label="Context" value={join(w.context)} />
        <Chip testId="wired-chip-result" label="Result" value={w.result ?? 'none'} />
      </div>
    </div>
  )
}
