import { useState, useRef, useEffect } from 'react'
import { ScrollText, Trash2 } from 'lucide-react'
import { cn } from '@/shared/lib/utils'
import { useConsoleStore, type ConsoleEntry } from './stores'

// ════════════════════════════════════════════════════════════════════════════════
// Console Panel
// ════════════════════════════════════════════════════════════════════════════════

const levelColors: Record<string, string> = {
  info: 'text-blue-500 dark:text-blue-400',
  warn: 'text-amber-500 dark:text-amber-400',
  error: 'text-red-500 dark:text-red-400',
  success: 'text-emerald-500 dark:text-emerald-400',
}

const levelBg: Record<string, string> = {
  error: 'bg-red-500/5',
  warn: 'bg-amber-500/5',
}

function ConsoleEntryRow({ entry }: { entry: ConsoleEntry }) {
  const time = new Date(entry.timestamp)
  const timeStr = time.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })

  return (
    <div className={cn('flex items-start gap-2 px-3 py-1 border-b border-border/30 font-mono text-[10px]', levelBg[entry.level])}>
      <span className="text-muted-foreground/60 shrink-0">{timeStr}</span>
      <span className={cn('shrink-0 uppercase font-semibold w-10', levelColors[entry.level])}>
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
      <div className="flex items-center px-3 py-1 border-b border-border/50 bg-muted/30 shrink-0">
        <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
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
    <div className="h-full flex flex-col bg-card">
      <ConsoleTab />
    </div>
  )
}
