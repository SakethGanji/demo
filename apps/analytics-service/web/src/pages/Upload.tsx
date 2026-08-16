import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { Card, ErrorBanner, Field, Spinner, cx, fmtNum, useAsync, useToast } from "../components/ui";
import { useIdentity } from "../app/identity";
import type { DatasetInfo } from "./Catalog";

interface UploadResult { dataset_id: string; version_id: string; status: string; row_count?: number; column_count?: number; message?: string; }

export function Upload() {
  const nav = useNavigate();
  const toast = useToast();
  const { identity } = useIdentity();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [sheets, setSheets] = useState("");
  const [target, setTarget] = useState("");   // "" = new dataset; else existing dataset id
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const existing = useAsync(() => api.get<{ items: DatasetInfo[] }>("/datasets", { limit: 200 }), [identity.userId, identity.teamId]);

  const submit = async () => {
    if (!file) return;
    // A zero-row/zero-byte upload is legitimate (an empty export is a valid
    // thing to version), so this doesn't block — the toast below reports when
    // 0 rows landed so a mistaken file isn't mistaken for a good one.
    setBusy(true); setError(null);
    try {
      const fields: Record<string, string> = {};
      if (sheets.trim()) fields.include_sheets = sheets.trim();
      if (target) fields.dataset_id = target;   // adds a new version to the existing dataset
      const res = await api.upload<UploadResult>("/upload", file, fields, { sync: true });
      // A file that parsed but yielded no rows is worth flagging — it looks like
      // a success otherwise, and the user gets an empty dataset.
      toast({
        kind: res.row_count ? "good" : "error",
        title: res.row_count ? (target ? "New version added" : "Upload complete") : "Ingested 0 rows",
        msg: res.row_count
          ? `${fmtNum(res.row_count)} rows · ${res.column_count ?? "?"} columns`
          : "The file parsed but contained no data rows — check its contents.",
      });
      nav(`/datasets/${res.dataset_id}`);
    } catch (e) {
      setError(e);
      if (e instanceof ApiError && e.code === "sheet-not-found") {
        // help the user pick a sheet
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 640 }}>
      <div className="page-head">
        <div>
          <h1 className="page-title">Upload dataset</h1>
          <div className="page-sub">CSV or a multi-tab Excel workbook. It's converted, profiled, and versioned automatically.</div>
        </div>
      </div>

      <Card>
        {/* A div+hidden input made the page's only purpose mouse-only. It is a
            button now, and the file input is visually hidden but focusable. */}
        <div
          role="button"
          tabIndex={0}
          aria-label="Choose a file to upload"
          className={cx("dropzone", drag && "drag")}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inputRef.current?.click(); } }}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); }}
        >
          <div style={{ fontSize: 28, opacity: 0.6 }}>↑</div>
          {file ? (
            <div className="mt-8"><strong>{file.name}</strong><div className="small muted">{(file.size / 1024).toFixed(1)} KB · click to replace</div></div>
          ) : (
            <div className="mt-8"><strong>Drop a file here</strong> or click to browse<div className="small muted mt-8">.csv · .xlsx</div></div>
          )}
          <input ref={inputRef} type="file" accept=".csv,.xlsx,.xls" hidden onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </div>

        <div className="mt-16">
          <Field label="Destination">
            <select className="select" value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">New dataset</option>
              {(existing.data?.items || []).map((d) => (
                <option key={d.id} value={d.id}>New version of · {d.name}</option>
              ))}
            </select>
          </Field>
        </div>

        <div className="mt-16">
          <Field label="Sheets to include (optional, comma-separated — for multi-tab workbooks)">
            <input className="input" placeholder="e.g. Revenue,Expenses — leave blank for all" value={sheets} onChange={(e) => setSheets(e.target.value)} />
          </Field>
        </div>

        {error != null && <div className="mt-16"><ErrorBanner error={error} /></div>}

        <div className="row mt-16" style={{ justifyContent: "flex-end" }}>
          <button className="btn btn-primary" disabled={!file || busy} onClick={submit}>
            {busy ? <><Spinner /> Uploading…</> : "Upload & process"}
          </button>
        </div>
      </Card>
    </div>
  );
}
