/** What the Overview's app and workflow cards say about an item: its status line and the UI an app draws. */
import { entrySource, stripClient } from '@/features/app-builder/lib/appModel'
import type { EntityStatus } from '@/features/project/lib/entities'
import { pluralize, timeAgo } from '../../lib/format'

export const iso = (at: number) => new Date(at).toISOString()

export function statusOf(s: EntityStatus | undefined, now: number, meta?: string): { text: string; tone?: 'err' | 'warn' | 'live' } {
  if (s?.failed && s.last) return { text: `Failed ${timeAgo(iso(s.last.at), now)}`, tone: 'err' }
  if (s?.running) return { text: 'Running now', tone: 'live' }
  if (s && s.approvals > 0) return { text: `${s.approvals} to approve`, tone: 'warn' }
  if (s && s.runs24h.length > 0) return { text: `${pluralize(s.runs24h.length, 'run')} today` }
  return { text: meta ?? (s?.last ? `Ran ${timeAgo(iso(s.last.at), now)}` : 'Not run yet') }
}

/** Everything the app draws: the entry file plus its screens and components (a one-shot build keeps its UI in
 * pages/ and components/, so the entry alone is only a router and sketches as empty). */
export function appUi(d: { files?: { path: string; content: string }[]; source_code?: string | null }): string {
  const files = stripClient(d.files) ?? []
  const entry = entrySource(files) || (d.source_code ?? '')
  const rest = files.filter((f) => /\.(t|j)sx$/.test(f.path) && f.content !== entry).map((f) => f.content)
  return [entry, ...rest].join('\n')
}
