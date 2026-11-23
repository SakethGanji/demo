/**
 * Datasets (`/p/$project/datasets`, `/p/$project/datasets/$datasetId`): files imported into the analytics service
 * and named in this project. Profile / Sample / Aggregate nodes pick them by name.
 * The list page holds the import drop zone and one WellList; the detail page shows columns, a preview,
 * rename and delete. Profiling is a workflow node (Profile), not a page action.
 */
import { useRef, useState } from 'react'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { ArrowLeft, ChevronRight, FileSpreadsheet, Info, Pencil, Plus, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { pluralize, timeAgo } from '@/features/project/lib/format'
import { errorMessage, isApiError } from '@/shared/api/http'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { EmptyState } from '@/shared/components/ui/empty-state'
import { Input } from '@/shared/components/ui/input'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Callout, Dot, Page, PageHeader, SectionHeader, Tile, Well, WellList, WellRow } from '@/shared/components/ui/surface'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/components/ui/table'
import { Tip } from '@/shared/components/ui/tooltip'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/components/ui/alert-dialog'
import { DatasetDropZone } from './components/DatasetDropZone'
import { cellText, formatBytes, shapeLabel } from './lib/datasets'
import type { DatasetDetail } from './lib/types'
import { useDataset, useDatasets, useDeleteDataset, useRenameDataset } from './useDatasets'

export function DatasetsView() {
  const { datasetId } = useParams({ strict: false }) as { datasetId?: string }
  return (
    <div className="h-full" data-testid="datasets-view">
      <Page data-testid="datasets-page">{datasetId ? <DatasetDetailPage id={datasetId} /> : <DatasetListPage />}</Page>
    </div>
  )
}

function DatasetListPage() {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const list = useDatasets(pid)
  const fileInput = useRef<HTMLInputElement>(null)
  const items = list.data ?? []

  return (
    <>
      <PageHeader
        title="Datasets"
        icon={<FileSpreadsheet />}
        hue="data"
        meta={
          <>
            <span data-testid="datasets-count">{pluralize(items.length, 'dataset')}</span>
            <Dot />
            <span>Files your Profile, Sample and Aggregate nodes read by name.</span>
          </>
        }
        actions={
          <Button size="sm" onClick={() => fileInput.current?.click()} data-testid="datasets-import">
            <Upload /> Import dataset
          </Button>
        }
      />

      <Tile className="mt-6 p-3">
        <DatasetDropZone
          pid={pid}
          inputRef={fileInput}
          onUploaded={(d) => {
            toast.success(`Imported ${d.name}`)
            void navigate({ to: '/p/$project/datasets/$datasetId', params: { project: slug, datasetId: d.id } })
          }}
        />
      </Tile>

      {list.isPending ? (
        <Tile className="mt-4 flex flex-col gap-2 p-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-9" />
          ))}
        </Tile>
      ) : list.isError ? (
        <Tile className="mt-4">
          <EmptyState
            title="Couldn't load datasets"
            data-testid="datasets-error"
            actions={
              <Button size="sm" variant="outline" onClick={() => void list.refetch()}>
                Retry
              </Button>
            }
          >
            {errorMessage(list.error)}
          </EmptyState>
        </Tile>
      ) : items.length === 0 ? (
        <Tile className="mt-4 p-3">
          <EmptyState
            icon={<FileSpreadsheet />}
            title="No datasets in this project yet"
            data-testid="datasets-empty"
            actions={
              <Button size="sm" variant="outline" onClick={() => fileInput.current?.click()} data-testid="datasets-empty-import">
                <Plus /> Import dataset
              </Button>
            }
          >
            Import a CSV, Parquet or Excel file, then pick it in a Profile, Sample or Aggregate node.
          </EmptyState>
        </Tile>
      ) : (
        <Tile className="mt-4 p-3" data-testid="datasets-list">
          <WellList>
            {items.map((d) => (
              <WellRow key={d.id} className="p-0">
                <Link
                  to="/p/$project/datasets/$datasetId"
                  params={{ project: slug, datasetId: d.id }}
                  className="flex min-w-0 flex-1 items-center gap-3 px-3 py-2.5 outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus"
                  data-testid={`dataset-row-${d.name}`}
                >
                  <FileSpreadsheet className="size-[15px] shrink-0 stroke-[1.75] text-hue-data" />
                  <span className="min-w-0 truncate font-medium">{d.name}</span>
                  <Badge variant="neutral">{d.format}</Badge>
                  <span className="ml-auto shrink-0 tabular-nums text-muted-foreground">{shapeLabel(d)}</span>
                  <span className="w-16 shrink-0 text-right tabular-nums text-muted-foreground">{formatBytes(d.size_bytes)}</span>
                  <span className="w-20 shrink-0 text-right text-muted-foreground">{timeAgo(d.created_at)}</span>
                  <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
                </Link>
              </WellRow>
            ))}
          </WellList>
        </Tile>
      )}
    </>
  )
}

function DatasetDetailPage({ id }: { id: string }) {
  const { pid, slug } = useProjectCtx()
  const ds = useDataset(pid, id)
  const back = (
    <Button size="sm" variant="ghost" render={<Link to="/p/$project/datasets" params={{ project: slug }} />} nativeButton={false} data-testid="dataset-back">
      <ArrowLeft /> All datasets
    </Button>
  )

  if (ds.isPending)
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40" />
      </div>
    )
  if (ds.isError)
    return (
      <Tile>
        <EmptyState
          title={isApiError(ds.error, undefined, 404) ? 'Dataset not found' : "Couldn't load this dataset"}
          data-testid="dataset-error"
          actions={back}
        >
          {errorMessage(ds.error)}
        </EmptyState>
      </Tile>
    )
  return <DatasetDetailBody d={ds.data} back={back} />
}

function DatasetDetailBody({ d, back }: { d: DatasetDetail; back: React.ReactNode }) {
  const { pid, slug } = useProjectCtx()
  const navigate = useNavigate()
  const rename = useRenameDataset(pid)
  const remove = useDeleteDataset(pid)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(d.name)
  const [confirm, setConfirm] = useState(false)
  const preview = d.preview ?? []
  const previewCols = d.columns.length ? d.columns.map((c) => c.name) : Object.keys(preview[0] ?? {})

  const saveName = () => {
    const name = draft.trim()
    if (!name || name === d.name) {
      setEditing(false)
      setDraft(d.name)
      return
    }
    rename.mutate(
      { id: d.id, name },
      {
        onSuccess: (r) => {
          setEditing(false)
          toast.success(`Renamed to ${r.name}`)
        },
        onError: (e) => toast.error(errorMessage(e)),
      },
    )
  }

  return (
    <>
      <PageHeader
        icon={<FileSpreadsheet />}
        hue="data"
        title={
          editing ? (
            <Input
              autoFocus
              value={draft}
              disabled={rename.isPending}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={saveName}
              onKeyDown={(e) => {
                if (e.key === 'Enter') saveName()
                if (e.key === 'Escape') {
                  setDraft(d.name)
                  setEditing(false)
                }
              }}
              aria-label="Dataset name"
              className="h-8 w-72 text-[15px]"
              data-testid="dataset-name-input"
            />
          ) : (
            <span data-testid="dataset-name">{d.name}</span>
          )
        }
        meta={
          <>
            <Badge variant="neutral">{d.format}</Badge>
            <span className="tabular-nums">{shapeLabel(d)}</span>
            <Dot />
            <span className="tabular-nums">{formatBytes(d.size_bytes)}</span>
            <Dot />
            <Tip label={d.file_name}>
              <span className="max-w-60 truncate font-mono text-[11.5px]">{d.file_name}</span>
            </Tip>
            <Dot />
            <span>imported {timeAgo(d.created_at)}</span>
          </>
        }
        actions={
          <>
            {back}
            {!editing && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setDraft(d.name)
                  setEditing(true)
                }}
                data-testid="dataset-rename"
              >
                <Pencil /> Rename
              </Button>
            )}
            <Button size="sm" variant="destructive" onClick={() => setConfirm(true)} data-testid="dataset-delete">
              <Trash2 /> Delete
            </Button>
          </>
        }
      />

      {d.missing && (
        <Callout tone="warn" icon={<Info />} className="mt-5" role="alert" data-testid="dataset-missing">
          The stored file for this dataset is gone from the analytics service. Delete it here and import the file again.
        </Callout>
      )}
      <Callout tone="info" icon={<Info />} className="mt-5" data-testid="dataset-use-hint">
        Use in a workflow: in a Profile, Sample or Aggregate node, pick <span className="font-mono text-[12px] font-medium">{d.name}</span> as the dataset.
        Workflows keep pointing at this dataset when you rename it.
      </Callout>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <Tile className="flex flex-col gap-3 p-3" data-testid="dataset-columns">
          <SectionHeader title="Columns" sub={pluralize(d.columns.length, 'column')} />
          {d.columns.length ? (
            <WellList className="max-h-96 overflow-y-auto">
              {d.columns.map((c) => (
                <WellRow key={c.name}>
                  <span className="min-w-0 truncate font-mono text-[12px]">{c.name}</span>
                  <span className="ml-auto shrink-0 text-[12px] text-muted-foreground">{c.type}</span>
                </WellRow>
              ))}
            </WellList>
          ) : (
            <EmptyState compact title="No column information" />
          )}
        </Tile>

        <Tile className="flex min-w-0 flex-col gap-3 p-3" data-testid="dataset-preview">
          <SectionHeader title="Preview" sub={preview.length ? `first ${pluralize(preview.length, 'row')}` : undefined} />
          {preview.length ? (
            <Well className="max-h-96 overflow-auto p-0">
              <Table dense>
                <TableHeader>
                  <TableRow>
                    {previewCols.map((c) => (
                      <TableHead key={c} className="font-mono">
                        {c}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {preview.map((row, i) => (
                    <TableRow key={i}>
                      {previewCols.map((c) => (
                        <TableCell key={c} className="max-w-60 truncate tabular-nums">
                          {cellText(row[c])}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Well>
          ) : (
            <EmptyState compact title="No preview rows" />
          )}
        </Tile>
      </div>

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {d.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Workflows that use this dataset will fail until you pick another one. The stored file is deleted too.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              data-testid="dataset-delete-confirm"
              onClick={() =>
                remove.mutate(d.id, {
                  onSuccess: () => {
                    toast.success(`Deleted ${d.name}`)
                    void navigate({ to: '/p/$project/datasets', params: { project: slug } })
                  },
                  onError: (e) => toast.error(errorMessage(e)),
                })
              }
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
