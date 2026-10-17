import { useRef, useEffect } from 'react'
import { Trash2 } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { useConsoleStore, type ConsoleEntry } from './stores'

// ════════════════════════════════════════════════════════════════════════════════
// Console Panel
// ════════════════════════════════════════════════════════════════════════════════

const levelColors: Record<string, string> = {
  info: 'text-primary',
  warn: 'text-warning-text',
  error: 'text-destructive',
  success: 'text-success-text',
}

const levelBg: Record<string, string> = {
  error: 'bg-destructive-soft',
  warn: 'bg-warning-soft',
}

function ConsoleEntryRow({ entry }: { entry: ConsoleEntry }) {
  const time = new Date(entry.timestamp)
  const timeStr = time.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })

  return (
    <div className={cn('flex items-start gap-2 px-3 py-1 hairline-b font-mono text-[10.5px]', levelBg[entry.level])}>
      <span className="text-muted-foreground/60 shrink-0">{timeStr}</span>
      <span className={cn('shrink-0 font-semibold w-10', levelColors[entry.level])}>
        {entry.level === 'success' ? 'ok' : entry.level}
      </span>
      <span className="text-muted-foreground shrink-0">[{entry.source}]</span>
      <span className="text-foreground break-all">{entry.message}</span>
      {entry.detail !== undefined && (
        <span className="ml-auto shrink-0 text-muted-foreground">
          {JSON.stringify(entry.detail)}
        </span>
      )}
    </div>
  )
}

function ConsoleTab() {
  const entries = useConsoleStore((s) => s.entries)
  const clear = useConsoleStore((s) => s.clear)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [entries.length])

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center px-3 py-1.5 hairline-b shrink-0">
        <span className="text-[12px] font-medium text-muted-foreground">
          Console ({entries.length})
        </span>
        <div className="flex-1" />
        <button
          onClick={clear}
          className="text-muted-foreground hover:text-foreground transition-colors p-0.5"
          title="Clear console"
        >
          <Trash2 size={11} />
        </button>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {entries.length === 0 ? (
          <div className="flex items-center justify-center h-full text-[11px] text-muted-foreground">
            No logs yet
          </div>
        ) : (
          entries.map((entry) => (
            <ConsoleEntryRow key={entry.id} entry={entry} />
          ))
        )}
      </div>
    </div>
  )
}

export function BottomPanel() {
  return (
    <div className="h-full flex flex-col">
      <ConsoleTab />
    </div>
  )
}
