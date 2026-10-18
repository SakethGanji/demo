/**
 * `/p/$project/templates` (DESIGN-STAGE8-REUSE §12.7): the read-only catalog of ready-made automations.
 * A grid of cards with a category filter and search; a card opens the preview drawer, where the install happens.
 */
import { useMemo, useState } from 'react'
import { LayoutTemplate, Search, Table2, Plug, Workflow, KeyRound } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { errorMessage } from '@/shared/api/http'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Input } from '@/shared/components/ui/input'
import { Segmented } from '@/shared/components/ui/segmented'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Dot, IconTile, PageHeader, Tile } from '@/shared/components/ui/surface'
import { useTemplates } from './hooks'
import { categoriesOf, filterTemplates, isInstalled } from './lib'
import { TemplatePreviewSheet } from './TemplatePreviewSheet'
import type { TemplateSummary } from './types'

export function TemplatesPage() {
  const { pid, project } = useProjectCtx()
  const list = useTemplates(pid)
  const [category, setCategory] = useState('all')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  const all = useMemo(() => list.data ?? [], [list.data])
  const categories = useMemo(() => categoriesOf(all), [all])
  const shown = useMemo(() => filterTemplates(all, category, q), [all, category, q])

  return (
    <section className="flex h-full flex-col overflow-hidden" data-testid="templates-page">
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-8 py-7">
        <PageHeader
          title="Templates"
          icon={<LayoutTemplate />}
          hue="flow"
          meta={
            <>
              <span>Ready-made automations you can install into {project.name}</span>
              {all.length > 0 && (
                <>
                  <Dot />
                  <span>{all.length} available</span>
                </>
              )}
            </>
          }
        />

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <div className="relative w-64">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search templates"
              aria-label="Search templates"
              className="pl-8"
              data-testid="templates-search"
            />
          </div>
          {categories.length > 0 && (
            <Segmented<string>
              value={category}
              onChange={setCategory}
              aria-label="Category"
              data-testid="templates-category"
              options={[{ key: 'all', label: 'All', testId: 'templates-category-all' }, ...categories.map((c) => ({ key: c, label: c, testId: `templates-category-${c}` }))]}
            />
          )}
        </div>

        {list.isLoading ? (
          <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="templates-loading">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-[176px] w-full rounded-panel" />
            ))}
          </div>
        ) : list.isError ? (
          <Tile className="mt-6" data-testid="templates-error">
            <EmptyState
              title="Couldn't load templates"
              actions={
                <Button variant="outline" size="sm" onClick={() => list.refetch()} data-testid="templates-retry">
                  Retry
                </Button>
              }
            >
              {errorMessage(list.error)}
            </EmptyState>
          </Tile>
        ) : shown.length === 0 ? (
          <Tile className="mt-6 p-3">
            <EmptyState icon={<LayoutTemplate />} title={all.length === 0 ? 'No templates' : 'No template matches'} data-testid="templates-empty">
              {all.length === 0 ? 'The catalog is empty.' : 'Try another search or category.'}
            </EmptyState>
          </Tile>
        ) : (
          <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="templates-grid">
            {shown.map((t) => (
              <TemplateCard key={t.slug} t={t} installed={isInstalled(t, pid)} onOpen={() => setOpen(t.slug)} />
            ))}
          </div>
        )}
      </div>
      <TemplatePreviewSheet slug={open} onClose={() => setOpen(null)} />
    </section>
  )
}

function TemplateCard({ t, installed, onOpen }: { t: TemplateSummary; installed: boolean; onOpen: () => void }) {
  return (
    <Tile
      interactive
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onOpen())}
      className="flex min-h-[176px] flex-col focus-visible:shadow-focus focus-visible:outline-none"
      data-testid={`template-card-${t.slug}`}
    >
      <div className="flex items-center gap-2.5 px-4 pt-4">
        <IconTile hue="flow" className="size-8">
          <Workflow />
        </IconTile>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[14px] font-semibold tracking-[-0.01em]">{t.name}</div>
          <div className="mt-px truncate text-[12px] text-muted-foreground">
            {t.category} · v{t.version}
          </div>
        </div>
        {installed && (
          <Badge variant="success" data-testid="template-installed-badge">
            Installed
          </Badge>
        )}
      </div>
      <p className="line-clamp-3 px-4 pt-3 text-[12.5px] leading-5 text-muted-foreground">{t.summary}</p>
      <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pb-3.5 pt-3 text-[12px] text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <Workflow className="size-3" /> {t.workflows.length}
        </span>
        {t.tables.length > 0 && (
          <span className="inline-flex items-center gap-1">
            <Table2 className="size-3" /> {t.tables.length}
          </span>
        )}
        {t.apis.length > 0 && (
          <span className="inline-flex items-center gap-1">
            <Plug className="size-3" /> {t.apis.length}
          </span>
        )}
        {t.requires.secrets.length > 0 && (
          <span className="inline-flex items-center gap-1">
            <KeyRound className="size-3" /> {t.requires.secrets.length}
          </span>
        )}
      </div>
    </Tile>
  )
}
