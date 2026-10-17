/**
 * SDK Reference (UI-DESIGN §4.6): searchable, grouped Routes · Data · Secrets · Node functions,
 * with egress badges and Insert/Copy. One source: `/api/sdk/reference` + `/api/sdk/functions`.
 */
import { useMemo, useState } from 'react'
import { Copy, CornerDownLeft, Search } from 'lucide-react'
import { toast } from 'sonner'
import { useSdkFunctions, useSdkReference } from '@/features/project/hooks/useProjectData'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { signatureDetail } from '../cm/completions'

interface Item {
  group: 'Routes' | 'Data' | 'Secrets' | 'Node functions' | 'Responses'
  name: string
  signature: string
  doc?: string
  insert: string
  egress?: boolean
  tag?: string
}

export function ReferencePanel({ onInsert }: { onInsert: (text: string) => void }) {
  const ref = useSdkReference()
  const fns = useSdkFunctions()
  const [q, setQ] = useState('')

  const items = useMemo<Item[]>(() => {
    const out: Item[] = []
    for (const e of ref.data?.entries ?? []) {
      const group: Item['group'] = e.name.startsWith('api.')
        ? 'Routes'
        : e.name.startsWith('studio.db')
          ? 'Data'
          : e.name === 'studio.secret'
            ? 'Secrets'
            : e.name.startsWith('studio.nodes')
              ? 'Node functions'
              : 'Responses'
      const insert = e.name.startsWith('api.')
        ? `@${e.name}("/path")`
        : e.name.startsWith('studio.db.table(name).')
          ? `.${e.name.split('.').pop()}()`
          : e.signature.replace(/:\s*[^,)=]+/g, '').replace(/\s*=\s*[^,)]+/g, '')
      out.push({ group, name: e.name, signature: e.signature, doc: e.doc, insert })
    }
    for (const f of fns.data ?? []) {
      out.push({
        group: 'Node functions',
        name: `studio.nodes.${f.function}`,
        signature: `${f.function}${signatureDetail(f)}`,
        doc: f.description,
        insert: `studio.nodes.${f.function}()`,
        egress: f.egress,
        tag: f.node_type,
      })
    }
    return out
  }, [ref.data, fns.data])

  const rules = ref.data?.rules
  const ruleList = Array.isArray(rules) ? rules : rules && typeof rules === 'object' ? Object.values(rules as Record<string, string>) : rules ? [rules] : []
  const filtered = items.filter((i) => !q || `${i.name} ${i.doc ?? ''} ${i.tag ?? ''}`.toLowerCase().includes(q.toLowerCase()))
  const groups: Item['group'][] = ['Routes', 'Data', 'Secrets', 'Responses', 'Node functions']

  if (ref.isLoading || fns.isLoading)
    return (
      <div className="flex flex-col gap-2 px-4 pt-1">
        <Skeleton className="h-8" />
        <Skeleton className="h-16" />
        <Skeleton className="h-16" />
      </div>
    )
  return (
    <div className="flex h-full min-h-0 flex-col pb-2" data-testid="reference-panel">
      <div className="px-4 pt-1 pb-3">
        <div className="flex h-8 items-center gap-2 rounded-ctl bg-recess px-2.5 focus-within:shadow-focus" data-recess>
          <Search className="size-3.5 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search the SDK…"
            className="flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
            data-testid="reference-search"
          />
        </div>
      </div>
      <div className="well mx-2 min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {!q && ruleList.length > 0 && (
          <div className="mt-3 rounded-ctl bg-brand-soft p-3 text-[11.5px] leading-[17px] text-muted-foreground" data-testid="reference-rules">
            {ruleList.map((r, i) => (
              <p key={i} className="mb-1 last:mb-0">
                {String(r)}
              </p>
            ))}
          </div>
        )}
        {groups.map((g) => {
          const xs = filtered.filter((i) => i.group === g)
          if (!xs.length) return null
          return (
            <section key={g} className="mt-3">
              <div className="mb-1 px-2 text-[11.5px] font-medium text-muted-foreground">{g}</div>
              {xs.map((i) => (
                <div key={i.name} className="group rounded-ctl px-2 py-1.5 transition-colors duration-(--d-instant) hover:bg-foreground/5" data-testid={`ref-${i.name}`}>
                  <div className="flex items-center gap-2">
                    <code className="min-w-0 flex-1 truncate text-[12px]" title={i.signature}>
                      {i.signature}
                    </code>
                    {i.tag && <span className="text-[11px] text-muted-foreground">{i.tag}</span>}
                    {i.egress && <Badge variant="warning">egress</Badge>}
                    <span className="hidden gap-0.5 group-hover:flex">
                      <Button variant="ghost" size="icon-xs" aria-label={`Insert ${i.name}`} onClick={() => onInsert(i.insert)}>
                        <CornerDownLeft />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`Copy ${i.name}`}
                        onClick={() => {
                          navigator.clipboard?.writeText(i.insert).catch(() => undefined)
                          toast.success('Copied')
                        }}
                      >
                        <Copy />
                      </Button>
                    </span>
                  </div>
                  {i.doc && <div className="mt-0.5 text-[11.5px] leading-4 text-muted-foreground">{i.doc}</div>}
                </div>
              ))}
            </section>
          )
        })}
      </div>
    </div>
  )
}
