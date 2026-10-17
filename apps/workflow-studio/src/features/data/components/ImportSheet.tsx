/**
 * CSV import (DESIGN §8 "Table tools"): a dropzone, a preview of the first rows and a header → column
 * mapping, then one single-row insert per CSV row (the engine has no bulk endpoint), with progress and a
 * toast at the end. Columns are matched by name; anything unmatched is skipped unless mapped by hand.
 */
import { useEffect, useRef, useState } from 'react'
import { FileText, FileUp } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { pluralize } from '@/features/project/lib/format'
import { errorMessage } from '@/shared/api/http'
import type { DataTable } from '@/shared/api/types'
import { Button } from '@/shared/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/shared/components/ui/sheet'
import { Progress, SectionHeader, Well } from '@/shared/components/ui/surface'
import { floatingSheetCls } from '@/features/settings/SettingsSheet'
import { cn } from '@/shared/lib/utils'
import { isNumeric } from '../lib/cells'
import { autoMap, parseCsv, rowToData, type ParsedCsv } from '../lib/csv'
import { useInsertRow } from '../useTableMutations'

const SKIP = '__skip'
const MAX_BYTES = 5_000_000
const PREVIEW_ROWS = 4

export function ImportSheet({
  table,
  open,
  file,
  onClose,
  onWrote,
}: {
  table: DataTable
  open: boolean
  /** a file dropped on the table, or null (choose one inside the sheet) */
  file: File | null
  onClose: () => void
  /** called after each row that was written */
  onWrote: () => void
}) {
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" showCloseButton className={`${floatingSheetCls} data-[side=right]:w-[460px]`} data-testid="import-sheet">
        {open && <ImportBody table={table} file={file} onClose={onClose} onWrote={onWrote} />}
      </SheetContent>
    </Sheet>
  )
}

function ImportBody({ table, file, onClose, onWrote }: { table: DataTable; file: File | null; onClose: () => void; onWrote: () => void }) {
  const { pid } = useProjectCtx()
  const insert = useInsertRow(pid, table.id)
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [name, setName] = useState<string | null>(null)
  const [csv, setCsv] = useState<ParsedCsv | null>(null)
  const [map, setMap] = useState<(string | null)[]>([])
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<{ done: number; total: number; failed: number } | null>(null)
  const cancelled = useRef(false)

  const read = (f: File | undefined) => {
    if (!f) return
    setName(f.name)
    if (f.size > MAX_BYTES) {
      setCsv(null)
      return setError(`${f.name} is ${(f.size / 1e6).toFixed(1)} MB. Previews take files up to 5 MB.`)
    }
    const r = new FileReader()
    r.onload = () => {
      const p = parseCsv(String(r.result))
      if ('error' in p) {
        setCsv(null)
        return setError(`${f.name}: ${p.error}`)
      }
      const m = autoMap(p.headers, table.columns)
      if (!m.some(Boolean)) {
        const head = p.headers.slice(0, 5).map((h) => h || '(blank)').join(', ')
        setError(`${f.name} has no column named ${table.columns.map((c) => c.name).join(' or ')}. Its header row has ${head}${p.headers.length > 5 ? ` and ${p.headers.length - 5} more` : ''}. Map them below, or choose another file.`)
      } else setError(null)
      setCsv(p)
      setMap(m)
    }
    r.onerror = () => {
      setCsv(null)
      setError(`Couldn't read ${f.name}.`)
    }
    r.readAsText(f)
  }
  useEffect(() => {
    if (file) read(file)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file])

  const mapped = map.filter(Boolean).length
  const total = csv?.rows.length ?? 0
  const busy = progress !== null

  const run = async () => {
    if (!csv || !mapped) return
    cancelled.current = false
    setProgress({ done: 0, total, failed: 0 })
    let ok = 0
    let failed = 0
    let firstError = ''
    for (let i = 0; i < csv.rows.length; i++) {
      if (cancelled.current) break
      const conv = rowToData(csv.rows[i], map, table.columns)
      if ('error' in conv) {
        failed++
        firstError ||= `row ${i + 2}: ${conv.error}`
      } else {
        try {
          await insert.mutateAsync(conv.data)
          ok++
          onWrote()
        } catch (e) {
          failed++
          firstError ||= `row ${i + 2}: ${errorMessage(e)}`
        }
      }
      setProgress({ done: i + 1, total, failed })
    }
    setProgress(null)
    const stopped = cancelled.current
    if (failed) toast.warning(`Imported ${pluralize(ok, 'row')}, ${failed} failed`, { description: firstError })
    else toast.success(`${stopped ? 'Stopped after' : 'Imported'} ${pluralize(ok, 'row')}`, { description: `${name} → ${table.name}` })
    onClose()
  }

  return (
    <>
      <div className="px-5 pt-4 pb-3">
        <div className="flex items-center gap-2 pr-8">
          <SheetTitle className="text-[15px] font-semibold tracking-[-0.01em]">
            Import into <span className="font-mono">{table.name}</span>
          </SheetTitle>
        </div>
        <SheetDescription className="mt-1 text-[13px] leading-[1.5] text-muted-foreground">
          Add rows to <span className="font-mono">{table.name}</span> from a CSV with a header row. Columns are matched by name. Rows are added one at a time.
        </SheetDescription>
      </div>

      <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto pb-4" data-testid="import-body">
        <div className="px-5">
          <div
            role="button"
            tabIndex={0}
            aria-label="Choose a CSV file, or drop one here"
            data-testid="import-dropzone"
            data-over={over || undefined}
            className={cn(
              'flex cursor-pointer flex-col items-center gap-1.5 rounded-well bg-recess text-center outline-none transition-[background-color,box-shadow,padding] duration-(--d-fast) hover:bg-(--hover-fill) focus-visible:shadow-focus',
              csv ? 'flex-row justify-center gap-2 px-4 py-2.5' : 'px-4 py-6',
              over && 'bg-brand-soft shadow-[inset_0_0_0_1.5px_var(--ring)]',
              error && !csv && 'shadow-[inset_0_0_0_1px_var(--destructive)]',
            )}
            onClick={() => input.current?.click()}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                input.current?.click()
              }
            }}
            onDragOver={(e) => {
              e.preventDefault()
              setOver(true)
            }}
            onDragLeave={(e) => !e.currentTarget.contains(e.relatedTarget as Node) && setOver(false)}
            onDrop={(e) => {
              e.preventDefault()
              setOver(false)
              read(e.dataTransfer.files[0])
            }}
          >
            <span className={cn('grid place-items-center rounded-ctl bg-raise text-muted-foreground shadow-raise-sm', csv ? 'size-6 [&_svg]:size-3.5' : 'mb-1 size-8 [&_svg]:size-[15px]', over && 'text-primary')}>
              <FileUp />
            </span>
            <span className={cn('text-[13px]', csv ? 'text-[12.5px] text-muted-foreground' : 'font-medium')}>
              {csv ? 'Drop another CSV to replace, or ' : 'Drop a CSV here, or '}
              <span className="text-primary">choose a file</span>
            </span>
            {!csv && (
              <span className="text-[12px] text-muted-foreground">
                Header row required ·{' '}
                {table.columns.slice(0, 3).map((c, i) => (
                  <span key={c.name}>
                    {i > 0 && ', '}
                    <span className="font-mono text-[11.5px]">{c.name}</span>
                  </span>
                ))}
              </span>
            )}
          </div>
          <input
            ref={input}
            type="file"
            accept=".csv,text/csv"
            hidden
            data-testid="import-file"
            onChange={(e) => {
              read(e.target.files?.[0])
              e.target.value = ''
            }}
          />
          {error && (
            <div className="mt-2.5 flex items-start gap-2 text-[12.5px] leading-[18px] text-destructive" role="alert" data-testid="import-error">
              <span className="mt-[6px] size-1.5 shrink-0 rounded-full bg-destructive" />
              <span>{error}</span>
            </div>
          )}
        </div>

        {csv && (
          <div className="animate-page-in" data-testid="import-preview">
            <div className="flex flex-wrap items-center gap-1.5 px-5 pt-5 pb-1 text-[12px] text-muted-foreground">
              <span className="inline-flex h-[22px] items-center gap-1.5 rounded-chip bg-recess px-2 font-mono text-[11.5px] text-foreground">
                <FileText className="size-3 text-muted-foreground" />
                {name}
              </span>
              <span className="text-faint">·</span>
              {pluralize(total, 'row')}
              <span className="text-faint">·</span>
              {mapped} {mapped === 1 ? 'column' : 'columns'} match
            </div>
            <div className="px-5 pt-3 pb-2">
              <SectionHeader title="Preview" sub={total > PREVIEW_ROWS ? `first ${PREVIEW_ROWS} rows` : total === 1 ? 'the row' : `all ${total} rows`} />
            </div>
            <div className="px-5">
              <Well className="overflow-x-auto">
                <table className="w-full border-collapse text-[12.5px] tabular-nums" data-testid="import-preview-table">
                  <thead>
                    <tr>
                      {csv.headers.map((h, i) => (
                        <th key={i} className={cn('h-8 px-3 text-left font-mono text-[11.5px] font-medium whitespace-nowrap first:pl-4 last:pr-4', map[i] ? 'text-muted-foreground' : 'text-faint')}>
                          {h || '—'}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {csv.rows.slice(0, PREVIEW_ROWS).map((r, ri) => (
                      <tr key={ri}>
                        {csv.headers.map((_, i) => (
                          <td key={i} title={r[i] ?? ''} className={cn('hairline-b h-8 max-w-[150px] truncate px-3 whitespace-nowrap first:pl-4 last:pr-4', !map[i] && 'text-faint')}>
                            {r[i] ?? ''}
                          </td>
                        ))}
                      </tr>
                    ))}
                    {total > PREVIEW_ROWS && (
                      <tr>
                        <td colSpan={csv.headers.length} className="h-8 px-4 text-[12px] text-muted-foreground">
                          and {total - PREVIEW_ROWS} more
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </Well>
            </div>
            <div className="px-5 pt-5 pb-2">
              <SectionHeader title="Column mapping" sub="matched by name" />
            </div>
            <div className="flex flex-col gap-1.5 px-5" data-testid="import-mapping">
              {csv.headers.map((h, i) => (
                <div key={i} className="grid grid-cols-[minmax(0,1fr)_16px_minmax(0,1fr)] items-center gap-2" data-testid="import-map-row">
                  <span className={cn('truncate font-mono text-[11.5px]', map[i] ? 'text-foreground' : 'text-muted-foreground')} title={h}>
                    {h || '(blank)'}
                  </span>
                  <span className="text-center text-faint">→</span>
                  <Select
                    value={map[i] ?? SKIP}
                    onValueChange={(v) => setMap((m) => m.map((x, j) => (j === i ? (v === SKIP || !v ? null : String(v)) : x)))}
                    items={[{ value: SKIP, label: 'skip' }, ...table.columns.map((c) => ({ value: c.name, label: c.name }))]}
                  >
                    <SelectTrigger size="sm" className="h-7 w-full font-mono text-xs" data-testid={`import-map-${i}`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={SKIP} className="text-xs" data-testid={`import-map-${i}-skip`}>
                        skip
                      </SelectItem>
                      {table.columns.map((c) => (
                        <SelectItem key={c.name} value={c.name} className="font-mono text-xs" data-testid={`import-map-${i}-${c.name}`}>
                          {c.name}
                          <span className="ml-auto pl-3 font-sans text-[11px] text-muted-foreground">
                            {c.type}
                            {isNumeric(c.type) ? ' · number' : ''}
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-3 px-5 pt-3 pb-4">
        {progress && (
          <div className="flex flex-col gap-1.5" data-testid="import-progress">
            <Progress value={(progress.done / Math.max(1, progress.total)) * 100} />
            <span className="text-[12px] text-muted-foreground">
              Importing {progress.done} of {progress.total}
              {progress.failed ? ` · ${progress.failed} failed` : ''}
            </span>
          </div>
        )}
        <div className="flex items-center justify-end gap-2">
          <Button
            variant="ghost"
            onClick={() => {
              cancelled.current = true
              if (!busy) onClose()
            }}
            data-testid="import-cancel"
          >
            {busy ? 'Stop' : 'Cancel'}
          </Button>
          <Button onClick={run} disabled={!csv || !mapped || busy} data-testid="import-submit">
            <FileUp /> {csv ? `Import ${pluralize(total, 'row')}` : 'Import rows'}
          </Button>
        </div>
      </div>
    </>
  )
}
