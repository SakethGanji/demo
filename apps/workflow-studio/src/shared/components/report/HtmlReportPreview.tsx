/**
 * HTML report preview: one look wherever node output is shown (NDV output, the editor's Logs tab, the
 * run sheet). The document renders in `<iframe sandbox="">` (no scripts, no same-origin, no forms, no
 * popups) with a strict CSP injected first (`injectCsp`). Never `dangerouslySetInnerHTML`.
 * A SendEmail item gets a compact To / Subject header and either a Sent badge or the "not sent" note.
 */
import { useCallback, useMemo, useState } from 'react'
import { AlertTriangle, Download, ExternalLink, FileCode2 } from 'lucide-react'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Callout, Well } from '@/shared/components/ui/surface'
import { Segmented } from '@/shared/components/ui/segmented'
import { cn } from '@/shared/lib/utils'
import { findHtmlReport, injectCsp, outputView, reportFilename, type HtmlReport } from './htmlReport'

// Reports are light documents: the frame is paper-white in both themes.
const FRAME = 'min-h-[480px] w-full flex-1 rounded-lg bg-white' // design-ok: a light report document, not studio chrome

function blobUrl(doc: string): string {
  return URL.createObjectURL(new Blob([doc], { type: 'text/html;charset=utf-8' }))
}

export function HtmlReportPreview({ report, className }: { report: HtmlReport; className?: string }) {
  const doc = useMemo(() => injectCsp(report.html), [report.html])
  const name = report.title ?? report.email?.subject ?? null

  const openInTab = useCallback(() => {
    const url = blobUrl(doc)
    const w = window.open(url, '_blank')
    if (w) w.opener = null
    // The tab has loaded the document by then; the URL is not needed after that.
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }, [doc])

  const download = useCallback(() => {
    const url = blobUrl(doc)
    const a = document.createElement('a')
    a.href = url
    a.download = reportFilename(name)
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }, [doc, name])

  const email = report.email

  return (
    <div className={cn('flex h-full min-h-0 min-w-0 flex-col gap-2', className)} data-testid="html-preview">
      {email && (
        <Well className="grid grid-cols-[56px_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-3 py-2 text-[12px]" data-testid="html-preview-email">
          <span className="text-muted-foreground">To</span>
          <span className="truncate font-mono text-[11.5px]" title={email.to} data-testid="html-preview-email-to">
            {email.to || '—'}
          </span>
          <span className="row-span-2 self-start">
            {email.sent ? (
              <Badge variant="success" data-testid="html-preview-email-sent">
                Sent
              </Badge>
            ) : (
              <Badge variant="warning" data-testid="html-preview-email-not-sent">
                Not sent
              </Badge>
            )}
          </span>
          <span className="text-muted-foreground">Subject</span>
          <span className="truncate" title={email.subject} data-testid="html-preview-email-subject">
            {email.subject || '—'}
          </span>
        </Well>
      )}
      {email && !email.sent && (
        <Callout tone="warn" icon={<AlertTriangle />} data-testid="html-preview-email-note">
          {email.note ?? 'The email was not sent.'}
        </Callout>
      )}

      <div className="flex min-w-0 items-center gap-1.5">
        <FileCode2 className="size-3.5 shrink-0 stroke-[1.75] text-hue-data" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-[12px] text-muted-foreground" title={name ?? undefined}>
          {name ?? 'HTML report'}
        </span>
        <Button size="xs" variant="ghost" onClick={openInTab} data-testid="html-preview-open">
          <ExternalLink /> Open in new tab
        </Button>
        <Button size="xs" variant="ghost" onClick={download} data-testid="html-preview-download">
          <Download /> Download .html
        </Button>
      </div>

      <iframe
        sandbox=""
        srcDoc={doc}
        title={name ?? 'HTML report'}
        className={FRAME}
        data-testid="html-preview-frame"
      />
    </div>
  )
}

/**
 * Output items with a Preview / JSON switch when one of them is an HTML report; plain `children` (the
 * data view) otherwise. For viewers whose data view has no switch of its own (Logs tab, run sheet);
 * the NDV output panel puts Preview into its existing view switch instead. Key it by node so a new node
 * opens on Preview again.
 */
export function ReportOrData({
  items,
  children,
  testId = 'output-view',
}: {
  items: readonly unknown[] | null | undefined
  children: React.ReactNode
  testId?: string
}) {
  const report = useMemo(() => findHtmlReport(items), [items])
  const [preview, setPreview] = useState<boolean | null>(null)
  if (!report) return <>{children}</>
  const view = outputView('json', preview, true)
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Segmented
        size="xs"
        value={view}
        onChange={(v) => setPreview(v === 'preview')}
        aria-label="Output view"
        data-testid={testId}
        options={[
          { key: 'preview', label: 'Preview', testId: `${testId}-preview` },
          { key: 'json', label: 'JSON', testId: `${testId}-json` },
        ]}
      />
      {view === 'preview' ? <HtmlReportPreview report={report} /> : children}
    </div>
  )
}
