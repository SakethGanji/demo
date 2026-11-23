/**
 * Structured runtime error (D16): `type: message`, the parsed traceback as a `StackTrace` (your frames first,
 * library frames folded; a frame opens that module at that line) and a "Go to line N" button when the error
 * points at a user module.
 */
import { Bug } from 'lucide-react'
import type { RunError } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'
import { StackTrace, type StackFrame } from '@/shared/components/ui/stack-trace'
import { isUserFrame, moduleOfFrame } from '../lib/frames'

export function RunErrorBox({
  error,
  onGoToLine,
  goToLabel,
  onOpenFrame,
  className,
}: {
  error: RunError
  onGoToLine?: (line: number, module: string | null | undefined) => void
  goToLabel?: string
  /** a clicked stack frame in one of the user's modules */
  onOpenFrame?: (module: string, line: number) => void
  className?: string
}) {
  const open = onOpenFrame ? (f: StackFrame) => onOpenFrame(moduleOfFrame(f.file), f.line) : undefined
  return (
    <div className={className} data-testid="run-error" data-code={error.code ?? ''}>
      <div className="flex items-start gap-2.5 rounded-ctl bg-destructive-soft px-3 py-2.5">
        <Bug className="mt-0.5 size-3.5 shrink-0 text-destructive" />
        <div className="min-w-0 flex-1 break-words text-[13px] font-medium text-destructive" data-testid="run-error-message">
          {error.type}: {error.message}
        </div>
        {error.line != null && onGoToLine && (
          <Button
            variant="ghost"
            size="xs"
            className="shrink-0"
            data-testid="go-to-line"
            onClick={() => onGoToLine(error.line!, error.module)}
          >
            {goToLabel ?? `Go to line ${error.line}`}
          </Button>
        )}
      </div>
      {error.traceback && (
        <StackTrace className="mt-3" traceback={error.traceback} isOwn={isUserFrame} onOpenFrame={open} testId="run-stack" />
      )}
    </div>
  )
}
