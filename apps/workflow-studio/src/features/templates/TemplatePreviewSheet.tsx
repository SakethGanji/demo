/**
 * Preview drawer (DESIGN-STAGE8-REUSE §12.7): README, one mini canvas per workflow, tables, APIs, what the install
 * needs (secrets), the clash / reuse options and Install. After the install it shows the result in place.
 */
import { useMemo, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { ArrowUpRight, CircleAlert, CircleCheck, KeyRound, Loader2, Plug, Table2, TriangleAlert, Workflow } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useTables } from '@/features/project/hooks/useProjectData'
import { floatingSheetCls } from '@/features/settings/SettingsSheet'
import WorkflowSVG from '@/features/workflow-editor/components/WorkflowSVG'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Segmented } from '@/shared/components/ui/segmented'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/shared/components/ui/sheet'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Callout, Well } from '@/shared/components/ui/surface'
import { cn } from '@/shared/lib/utils'
import { errorMessage } from '@/shared/api/http'
import { useInstallPreview, useInstallTemplate, useTemplate } from './hooks'
import { installProblem, previewToFlow, sortNeeds, testsTotals, type InstallProblem } from './lib'
import type { ClashMode, InstallOptions, InstallResult, Need, TemplateDetail } from './types'

export function TemplatePreviewSheet({ slug, onClose }: { slug: string | null; onClose: () => void }) {
  return (
    <Sheet open={!!slug} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" showCloseButton className={`${floatingSheetCls} data-[side=right]:w-[680px]`} data-testid="template-preview">
        {slug && <PreviewBody key={slug} slug={slug} />}
      </SheetContent>
    </Sheet>
  )
}

function PreviewBody({ slug }: { slug: string }) {
  const { pid } = useProjectCtx()
  const detail = useTemplate(slug)
  const tables = useTables(pid)
  const [clash, setClash] = useState<ClashMode>('rename')
  const [reuse, setReuse] = useState<string[]>([])
  const [asCopy, setAsCopy] = useState(false)
  const [result, setResult] = useState<InstallResult | null>(null)
  const [problem, setProblem] = useState<InstallProblem | null>(null)
  const install = useInstallTemplate(pid)

  const opts: InstallOptions = useMemo(() => ({ clash, reuse_tables: reuse, as_copy: asCopy, run_tests: true }), [clash, reuse, asCopy])
  const preview = useInstallPreview(pid, result ? null : slug, opts)
  const existingTables = useMemo(() => new Set((tables.data ?? []).map((t) => t.name)), [tables.data])

  const doInstall = async () => {
    setProblem(null)
    try {
      setResult(await install.mutateAsync({ slug, opts }))
    } catch (e) {
      setProblem(installProblem(e))
    }
  }

  const t = detail.data
  const status = preview.data?.status
  const blockedByVersion = status === 'version_installed' && !asCopy
  const alreadyInstalled = status === 'already_installed' && !asCopy
  const clashes = preview.data?.plan.clashes ?? []

  return (
    <>
      <div className="flex shrink-0 flex-col gap-0.5 px-5 pb-3 pt-3.5 pr-14">
        <SheetTitle className="truncate text-[15px] font-semibold tracking-[-0.012em]">{t?.name ?? 'Template'}</SheetTitle>
        <SheetDescription className="text-[13px] leading-[1.5] text-muted-foreground">
          {t ? `${t.category} · v${t.version}` : 'Loading…'}
        </SheetDescription>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
        {detail.isLoading ? (
          <div className="flex flex-col gap-3 px-5 pb-5">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : detail.isError || !t ? (
          <div className="px-5 pb-5">
            <Callout tone="error" role="alert" data-testid="template-preview-error">
              Couldn't load the template · {errorMessage(detail.error)}
            </Callout>
          </div>
        ) : result ? (
          <InstallResultPanel result={result} />
        ) : (
          <div className="flex flex-col gap-4 px-5 pb-5">
            <p className="text-[13px] leading-5 text-foreground">{t.summary}</p>
            {t.readme && (
              <Well className="whitespace-pre-wrap p-3 text-[12.5px] leading-5 text-muted-foreground" data-testid="template-preview-readme">
                {t.readme.trim()}
              </Well>
            )}

            <Section icon={<Workflow />} title="Workflows">
              {t.workflows.map((w) => (
                <WorkflowPreviewCard key={w.key} t={t} wkey={w.key} />
              ))}
            </Section>

            {t.tables.length > 0 && (
              <Section icon={<Table2 />} title="Tables">
                <Well className="flex flex-col divide-y divide-border/60 p-0" data-testid="template-preview-tables">
                  {t.tables.map((tb) => (
                    <div key={tb.key} className="flex items-center gap-2 px-3 py-2 text-[12.5px]">
                      <span className="font-mono">{tb.name}</span>
                      <span className="truncate text-muted-foreground">{tb.columns.map((c) => c.name).join(', ')}</span>
                      <div className="flex-1" />
                      {tb.seed_rows && tb.seed_rows.length > 0 && <span className="text-muted-foreground">{tb.seed_rows.length} sample rows</span>}
                      {existingTables.has(tb.name) && !asCopy && (
                        <label className="flex items-center gap-1.5 text-muted-foreground">
                          <Checkbox
                            checked={reuse.includes(tb.key)}
                            onCheckedChange={(c) => setReuse((r) => (c ? [...r, tb.key] : r.filter((k) => k !== tb.key)))}
                            aria-label={`Reuse the existing ${tb.name} table`}
                            data-testid={`template-reuse-table-${tb.key}`}
                          />
                          Reuse existing
                        </label>
                      )}
                    </div>
                  ))}
                </Well>
              </Section>
            )}

            {t.api_summaries.length > 0 && (
              <Section icon={<Plug />} title="APIs">
                <Well className="flex flex-col divide-y divide-border/60 p-0" data-testid="template-preview-apis">
                  {t.api_summaries.map((a) => (
                    <div key={a.key} className="px-3 py-2 text-[12.5px]">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{a.name}</span>
                        {a.base_url && <span className="truncate font-mono text-[11.5px] text-muted-foreground">{a.base_url}</span>}
                      </div>
                      <div className="mt-0.5 text-muted-foreground">{a.operations.length} operations</div>
                    </div>
                  ))}
                </Well>
              </Section>
            )}

            <Section icon={<KeyRound />} title="Needs">
              <NeedsList t={t} needs={preview.data?.needs} loading={preview.isLoading} />
              {(preview.data?.egress_hosts ?? []).length > 0 && (
                <div className="text-[12px] text-muted-foreground" data-testid="template-preview-egress">
                  Calls out to {preview.data!.egress_hosts.join(', ')}. These hosts are added to the project allowlist if it is not empty.
                </div>
              )}
            </Section>

            <Section title="Install options">
              <div className="flex flex-col gap-2.5">
                <div className="flex items-center gap-3 text-[12.5px]">
                  <span className="text-muted-foreground">If a name is taken</span>
                  <Segmented<ClashMode>
                    value={clash}
                    onChange={setClash}
                    aria-label="If a name is taken"
                    data-testid="template-clash-mode"
                    options={[
                      { key: 'rename', label: 'Rename', testId: 'template-clash-rename' },
                      { key: 'fail', label: 'Stop', testId: 'template-clash-fail' },
                    ]}
                  />
                </div>
                {(status === 'already_installed' || status === 'version_installed' || asCopy) && (
                  <label className="flex items-center gap-2 text-[12.5px]">
                    <Checkbox checked={asCopy} onCheckedChange={(c) => setAsCopy(!!c)} data-testid="template-as-copy" />
                    Install another copy
                  </label>
                )}
              </div>
            </Section>

            {preview.isError && (
              <Callout tone="error" role="alert" data-testid="template-preview-plan-error">
                {installProblem(preview.error).message}
              </Callout>
            )}
            {alreadyInstalled && (
              <Callout tone="info" icon={<CircleCheck />} data-testid="template-already-installed">
                This template is already installed in this project. Installing again changes nothing.
              </Callout>
            )}
            {blockedByVersion && (
              <Callout tone="warn" icon={<TriangleAlert />} data-testid="template-version-installed">
                Another version{preview.data?.installed ? ` (${preview.data.installed.version})` : ''} is installed. Install another copy to add this one.
              </Callout>
            )}
            {clashes.length > 0 && (
              <Callout tone="warn" icon={<TriangleAlert />} data-testid="template-clashes">
                {clashes.length} name{clashes.length === 1 ? ' is' : 's are'} already taken
                {clash === 'fail' ? ', so the install would stop. Switch to rename.' : '. They will be renamed.'}
              </Callout>
            )}
            {(preview.data?.plan.renamed ?? []).length > 0 && clash === 'rename' && (
              <Well className="flex flex-col gap-1 p-3 text-[12.5px]" data-testid="template-plan-renamed">
                {preview.data!.plan.renamed.map((r, i) => (
                  <div key={i}>
                    {r.kind} <span className="text-muted-foreground line-through">{r.from}</span> becomes <span className="font-medium">{r.to}</span>
                  </div>
                ))}
              </Well>
            )}
            {problem && (
              <Callout tone="error" icon={<CircleAlert />} role="alert" data-testid="template-install-error">
                <div>{problem.message}</div>
                {problem.lines.map((l, i) => (
                  <div key={i} className="mt-0.5 font-mono text-[11.5px]">
                    {l}
                  </div>
                ))}
              </Callout>
            )}
          </div>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-2 px-5 pb-4 pt-3">
        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
          {result ? 'Installed. Entry workflows stay drafts; turn triggers on when you are ready.' : 'Sub-workflows are published; entry workflows stay drafts.'}
        </span>
        {!result && (
          <Button
            size="sm"
            disabled={!t || install.isPending || alreadyInstalled || blockedByVersion || (clash === 'fail' && clashes.length > 0)}
            onClick={() => void doInstall()}
            data-testid="template-install"
          >
            {install.isPending && <Loader2 className="animate-spin" />}
            {install.isPending ? 'Installing…' : 'Install'}
          </Button>
        )}
      </div>
    </>
  )
}

function Section({ icon, title, children }: { icon?: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1.5 text-[12.5px] font-semibold text-muted-foreground [&_svg]:size-3.5">
        {icon}
        {title}
      </div>
      {children}
    </div>
  )
}

function WorkflowPreviewCard({ t, wkey }: { t: TemplateDetail; wkey: string }) {
  const p = t.preview[wkey]
  const flow = useMemo(() => (p ? previewToFlow(p) : null), [p])
  if (!p || !flow) return null
  return (
    <Well className="flex flex-col gap-2 p-3" data-testid={`template-preview-workflow-${wkey}`}>
      <div className="flex items-center gap-2">
        <span className="truncate text-[13px] font-medium">{p.name}</span>
        <Badge variant={p.role === 'entry' ? 'default' : 'neutral'}>{p.role === 'entry' ? 'Entry' : 'Sub-workflow'}</Badge>
        <div className="flex-1" />
        <span className="shrink-0 text-xs text-muted-foreground">
          {p.nodes.length} steps{p.tests.length > 0 ? ` · ${p.tests.length} tests` : ''}
          {p.interface ? ` · ${p.interface}` : ''}
        </span>
      </div>
      <div className="h-36 overflow-hidden rounded-ctl bg-recess text-muted-foreground">
        <WorkflowSVG nodes={flow.nodes} edges={flow.edges} showIcons showDotGrid width={600} height={144} className="h-full w-full" />
      </div>
    </Well>
  )
}

/** Required secrets and variables. `needs` (from install-preview) says which are missing; before it arrives all show as unchecked. */
function NeedsList({ t, needs, loading }: { t: TemplateDetail; needs: Need[] | undefined; loading: boolean }) {
  const { slug } = useProjectCtx()
  const missing = new Map((needs ?? []).map((n) => [`${n.kind}:${n.name}`, n]))
  const rows = [
    ...t.requires.secrets.map((s) => ({ kind: 'secret' as const, name: s.name, usedBy: s.used_by })),
    ...t.requires.variables.map((v) => ({ kind: 'variable' as const, name: v.name, usedBy: [] as string[] })),
  ]
  if (rows.length === 0) return <div className="text-[12.5px] text-muted-foreground">Nothing: no secrets or variables are required.</div>
  return (
    <Well className="flex flex-col divide-y divide-border/60 p-0" data-testid="template-preview-needs">
      {rows.map((r) => {
        const m = missing.get(`${r.kind}:${r.name}`)
        return (
          <div key={`${r.kind}:${r.name}`} className="flex items-center gap-2 px-3 py-2 text-[12.5px]" data-testid={`template-preview-secret-${r.name}`} data-missing={m ? 'true' : undefined}>
            <span className="font-mono">{r.name}</span>
            <span className="text-muted-foreground">{r.kind}</span>
            <div className="flex-1" />
            {loading || needs === undefined ? (
              <span className="text-muted-foreground">checking…</span>
            ) : m ? (
              <>
                <Badge variant="warning">missing in {m.environments_missing.join(', ')}</Badge>
                <Link to="/p/$project/secrets" params={{ project: slug }} search={{ create: r.name }} className="inline-flex items-center gap-0.5 text-primary hover:underline">
                  Add <ArrowUpRight className="size-3" />
                </Link>
              </>
            ) : (
              <Badge variant="success">set</Badge>
            )}
          </div>
        )
      })}
    </Well>
  )
}

function InstallResultPanel({ result }: { result: InstallResult }) {
  const { slug } = useProjectCtx()
  const entry = result.workflows.find((w) => w.role === 'entry') ?? result.workflows[0]
  const totals = testsTotals(result.tests)
  const failing = Object.entries(result.tests ?? {}).filter(([, s]) => s.failed > 0)
  return (
    <div className="flex flex-col gap-4 px-5 pb-5" data-testid="template-install-result" data-status={result.status}>
      <Callout tone="ok" icon={<CircleCheck />}>
        {result.status === 'already_installed' ? 'Already installed. Nothing changed.' : `Installed as ${result.install_name}.`}
      </Callout>

      <Section icon={<Workflow />} title="Workflows">
        <Well className="flex flex-col divide-y divide-border/60 p-0">
          {result.workflows.map((w) => (
            <div key={w.key} className="flex items-center gap-2 px-3 py-2 text-[12.5px]">
              <Link to="/p/$project/workflows/$workflowId" params={{ project: slug, workflowId: w.id }} className="truncate font-medium hover:underline">
                {w.name ?? w.key}
              </Link>
              <div className="flex-1" />
              <Badge variant={w.published_version != null ? 'success' : 'neutral'}>{w.published_version != null ? `v${w.published_version} published` : 'Draft'}</Badge>
            </div>
          ))}
        </Well>
      </Section>

      {result.renamed.length > 0 && (
        <Section title="Renamed">
          <Well className="flex flex-col gap-1 p-3 text-[12.5px]">
            {result.renamed.map((r, i) => (
              <div key={i} data-testid="template-renamed-row">
                {r.kind} <span className="text-muted-foreground line-through">{r.from}</span> became <span className="font-medium">{r.to}</span>
              </div>
            ))}
          </Well>
        </Section>
      )}

      {result.needs.length > 0 && (
        <Section icon={<KeyRound />} title="Still needed">
          <Well className="flex flex-col divide-y divide-border/60 p-0">
            {sortNeeds(result.needs).map((n) => (
              <div key={`${n.kind}-${n.name}`} className="flex items-center gap-2 px-3 py-2 text-[12.5px]" data-testid={`template-need-${n.kind}-${n.name}`}>
                <span className="font-mono">{n.name}</span>
                <span className="text-muted-foreground">{n.kind} missing in {n.environments_missing.join(', ')}</span>
                <div className="flex-1" />
                <Link to="/p/$project/secrets" params={{ project: slug }} search={{ create: n.name }} className="inline-flex items-center gap-0.5 text-primary hover:underline">
                  {n.kind === 'secret' ? 'Add secret' : 'Add variable'} <ArrowUpRight className="size-3" />
                </Link>
              </div>
            ))}
          </Well>
        </Section>
      )}

      {result.tests && (
        <div
          className={cn('rounded-ctl px-3 py-2 text-[12.5px]', totals.failed > 0 ? 'bg-destructive-soft text-destructive' : 'bg-success-soft text-success-text')}
          data-testid="template-tests-summary"
          data-failed={totals.failed}
        >
          {totals.failed > 0
            ? `${totals.failed} of ${totals.total} tests failed`
            : totals.total === 0
              ? 'No tests ran'
              : `${totals.passed} of ${totals.total} tests passed`}
          {failing.map(([key, s]) => (
            <div key={key} className="mt-0.5 font-mono text-[11.5px]">
              {key}: {(s.failing ?? []).map((f) => f.name).join(', ') || `${s.failed} failing`}
            </div>
          ))}
        </div>
      )}

      {result.egress_added.length > 0 && (
        <div className="text-[12px] text-muted-foreground">Added to the egress allowlist: {result.egress_added.join(', ')}</div>
      )}

      {entry && (
        <div>
          <Button
            size="sm"
            render={<Link to="/p/$project/workflows/$workflowId" params={{ project: slug, workflowId: entry.id }} />}
            data-testid="template-open-entry"
          >
            Open {entry.name ?? 'workflow'} <ArrowUpRight />
          </Button>
        </div>
      )}
    </div>
  )
}
