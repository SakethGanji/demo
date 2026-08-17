/**
 * Upload — the one write that creates data rather than annotating it.
 *
 * `POST /upload` is create-dataset-and-ingest in a single call; there is no
 * separate "create dataset" verb. Passing `dataset_id` turns the same call into
 * "add a new version of this dataset", which is the only way to change a
 * dataset's contents at all — versions are immutable, so nothing is ever
 * overwritten. The dialog makes that fork explicit rather than hiding it behind
 * a filename heuristic.
 */

import { useRef, useState } from 'react';
import { FileUp, Upload } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog';
import { Button } from '@/shared/components/ui/button';
import { useUpload } from '../hooks/useDatasetActions';

const ACCEPT = '.csv,.parquet,.xlsx,.xls';

interface UploadDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The dataset in view — the target when "new version" is chosen. */
  currentDatasetId: string | null;
  currentDatasetName: string | null;
  /** Select whatever the upload produced, so the result is immediately visible. */
  onUploaded: (datasetId: string) => void;
}

export function UploadDialog({
  open,
  onOpenChange,
  currentDatasetId,
  currentDatasetName,
  onUploaded,
}: UploadDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [asNewVersion, setAsNewVersion] = useState(false);
  const [includeSheets, setIncludeSheets] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const upload = useUpload();

  const close = () => {
    setFile(null);
    setAsNewVersion(false);
    setIncludeSheets('');
    onOpenChange(false);
  };

  const submit = () => {
    if (!file) return;
    upload.mutate(
      {
        file,
        datasetId: asNewVersion ? currentDatasetId : null,
        includeSheets: includeSheets || null,
      },
      {
        onSuccess: (res) => {
          onUploaded(res.dataset_id);
          close();
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" data-testid="upload-dialog">
        <DialogHeader>
          <DialogTitle>Upload data</DialogTitle>
          <DialogDescription>
            CSV, Parquet or Excel. Ingestion is synchronous, so the result is queryable as soon as
            this closes.
          </DialogDescription>
        </DialogHeader>

        <div>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            aria-label="Data file"
            data-testid="upload-file"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-body file:mr-2 file:rounded-md file:border file:border-border file:bg-background file:px-2 file:py-1 file:text-body file:text-foreground hover:file:bg-muted"
          />

          {file && (
            <p className="mt-1.5 flex items-center gap-1.5 text-small text-muted-foreground">
              <FileUp className="size-3" />
              {file.name} · {(file.size / 1024).toFixed(1)} KB
            </p>
          )}

          {currentDatasetId && (
            <label className="mt-3 flex items-start gap-2 text-body">
              <input
                type="checkbox"
                checked={asNewVersion}
                onChange={(e) => setAsNewVersion(e.target.checked)}
                data-testid="upload-as-version"
                className="mt-0.5"
              />
              <span>
                Add as a new version of{' '}
                <span className="font-medium">{currentDatasetName ?? 'the current dataset'}</span>
                <span className="block text-small text-muted-foreground">
                  Leave unchecked to create a separate dataset.
                </span>
              </span>
            </label>
          )}

          <label className="mt-3 block text-small text-muted-foreground">
            Sheets to include <span className="text-muted-foreground/60">(optional)</span>
          </label>
          <input
            value={includeSheets}
            onChange={(e) => setIncludeSheets(e.target.value)}
            placeholder="Orders, Customers"
            aria-label="Sheets to include"
            data-testid="upload-include-sheets"
            className="h-7 w-full rounded-md border border-border bg-background px-2 text-body outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          />
          {/* Naming a sheet that isn't in the workbook fails the whole upload. */}
          <p className="mt-1 text-small text-muted-foreground/70">
            Multi-sheet workbooks only. Comma-separated; leave blank to ingest every sheet.
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={close} disabled={upload.isPending}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!file || upload.isPending} data-testid="upload-submit">
            <Upload className="size-3" />
            {upload.isPending ? 'Ingesting…' : 'Upload'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
