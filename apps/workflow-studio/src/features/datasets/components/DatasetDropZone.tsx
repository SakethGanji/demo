/**
 * Import drop zone: drag a file in or click to choose one. Uploads through `useUploadDataset`; server errors
 * (400 bad type, 503 analytics service not running, 502 analytics error) show as an error Callout with the
 * engine's own message.
 */
import { useRef, useState } from 'react'
import { AlertTriangle, FileUp } from 'lucide-react'
import { errorMessage } from '@/shared/api/http'
import { Callout } from '@/shared/components/ui/surface'
import { Spinner } from '@/shared/components/ui/spinner'
import { cn } from '@/shared/lib/utils'
import { DATASET_ACCEPT, uploadProblem } from '../lib/datasets'
import type { DatasetItem } from '../lib/types'
import { useUploadDataset } from '../useDatasets'

export function DatasetDropZone({
  pid,
  onUploaded,
  inputRef,
  className,
}: {
  pid: string
  onUploaded?: (d: DatasetItem) => void
  /** lets the page header's "Import dataset" button open the same file chooser */
  inputRef?: React.RefObject<HTMLInputElement>
  className?: string
}) {
  const ownRef = useRef<HTMLInputElement>(null)
  const input = inputRef ?? ownRef
  const [over, setOver] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const upload = useUploadDataset(pid)
  const busy = upload.isPending

  const send = (file: File | undefined) => {
    if (!file || busy) return
    const problem = uploadProblem(file.name)
    setLocalError(problem)
    upload.reset()
    if (problem) return
    upload.mutate({ file }, { onSuccess: (d) => onUploaded?.(d) })
  }

  const error = localError ?? (upload.isError ? errorMessage(upload.error) : null)

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div
        role="button"
        tabIndex={0}
        aria-busy={busy}
        aria-label="Import a dataset file"
        data-testid="datasets-dropzone"
        data-state={busy ? 'uploading' : over ? 'over' : 'idle'}
        className={cn(
          'flex cursor-pointer flex-col items-center gap-1.5 rounded-well bg-recess px-4 py-6 text-center outline-none transition-[background-color,box-shadow] duration-(--d-fast) hover:bg-(--hover-fill) focus-visible:shadow-focus',
          over && 'bg-brand-soft shadow-[inset_0_0_0_1.5px_var(--ring)]',
          busy && 'cursor-progress',
        )}
        onClick={() => !busy && input.current?.click()}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            if (!busy) input.current?.click()
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
          send(e.dataTransfer.files[0])
        }}
      >
        <span className={cn('mb-1 grid size-8 place-items-center rounded-ctl bg-raise text-muted-foreground shadow-raise-sm [&_svg]:size-[15px]', over && 'text-primary')}>
          {busy ? <Spinner /> : <FileUp />}
        </span>
        {busy ? (
          <span className="text-[13px] font-medium" data-testid="datasets-uploading">
            Importing {upload.variables?.file.name}…
          </span>
        ) : (
          <span className="text-[13px] font-medium">
            Drop a file here, or <span className="text-primary">choose a file</span>
          </span>
        )}
        <span className="text-[12px] text-muted-foreground">CSV, Parquet or Excel (.xlsx, .xls)</span>
      </div>
      <input
        ref={input}
        type="file"
        accept={DATASET_ACCEPT}
        hidden
        data-testid="datasets-file"
        onChange={(e) => {
          send(e.target.files?.[0])
          e.target.value = ''
        }}
      />
      {error && (
        <Callout tone="error" role="alert" icon={<AlertTriangle />} data-testid="datasets-upload-error">
          {error}
        </Callout>
      )}
    </div>
  )
}
