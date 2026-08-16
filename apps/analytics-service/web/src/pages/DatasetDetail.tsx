import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { AsyncView, Badge, Field, Modal, Tabs, cx, statusKind, useAsync, useToast } from "../components/ui";
import { useIdentity, useCanWrite } from "../app/identity";
import type { DatasetInfo } from "./Catalog";
import { Overview } from "./dataset/Overview";
import { Explore } from "./dataset/Explore";
import { Quality } from "./dataset/Quality";
import { Versions } from "./dataset/Versions";
import { Analytics } from "./dataset/Analytics";
import { Relationships } from "./dataset/Relationships";
import { Transform } from "./dataset/Transform";
import { Library } from "./dataset/Library";

export interface DatasetTabProps { dataset: DatasetInfo; reload: () => void; }

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "explore", label: "Explore" },
  { id: "quality", label: "Quality" },
  { id: "versions", label: "Versions" },
  { id: "analytics", label: "Analytics" },
  { id: "relationships", label: "Relationships" },
  { id: "transform", label: "Transform" },
  { id: "library", label: "Library" },
];

export function DatasetDetail() {
  const { id = "" } = useParams();
  const { identity } = useIdentity();
  const [tab, setTab] = useState("overview");
  const canWrite = useCanWrite();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // NOTE: GET /datasets/{id} returns the dataset's DATA preview (dataset_id,
  // sheets, preview), not its catalog metadata — there is no single-dataset
  // metadata endpoint that returns DatasetInfo. So we resolve name/version/etc.
  // from the catalog list. (See UI-INTEGRATION-GUIDE.md.)
  const state = useAsync(async () => {
    // Existence + authorization probe. It 404s for an unknown id, for another
    // team's dataset (404-hides-existence), AND for a dataset whose upload never
    // produced a ready version. Letting an unknown id throw is what makes
    // AsyncView show a real error instead of the phantom page it used to render.
    let hasData = true;
    try {
      await api.get(`/datasets/${id}`);
    } catch (e) {
      hasData = false;
      // A failed upload leaves a real catalog row with no readable version. If
      // it's in OUR catalog it exists and must stay openable — otherwise it's a
      // dead end the user can see but never remove. Anything not in the catalog
      // is genuinely unknown/not ours: rethrow.
      const page = await api.get<{ items: DatasetInfo[] }>("/datasets", { limit: 200 });
      if (!page.items.some((d) => d.id === id)) throw e;
    }

    // Page the catalog until we find it, so a deep link to a dataset beyond the
    // first page still resolves its metadata.
    let found: DatasetInfo | undefined;
    for (let offset = 0; offset < 2000 && !found; offset += 200) {
      const page = await api.get<{ items: DatasetInfo[]; total: number }>("/datasets", { limit: 200, offset });
      found = page.items.find((d) => d.id === id);
      if (page.items.length === 0 || offset + page.items.length >= page.total) break;
    }
    // It exists (the probe passed, or the catalog vouched for it).
    let ds = { ...(found || ({ id, name: id } as DatasetInfo)), _hasData: hasData } as DatasetInfo & { _hasData: boolean };
    if (ds.current_version == null) {
      try {
        const vs = await api.get<{ items: { version_number: number }[] }>(`/datasets/${id}/versions`);
        const cur = Math.max(0, ...vs.items.map((v) => v.version_number));
        if (cur) ds = { ...ds, current_version: cur };
      } catch { /* leave unset */ }
    }
    return ds;
  }, [id, identity.userId, identity.teamId]);

  // A single static <title> for every route leaves screen-reader and
  // tab-switching users with no way to tell pages apart.
  useEffect(() => {
    const name = state.data?.name;
    document.title = name ? `${name} · Analytics Studio` : "Analytics Studio";
    return () => { document.title = "Analytics Studio"; };
  }, [state.data?.name]);

  return (
    <AsyncView state={state}>
      {(ds) => {
        const hasData = (ds as DatasetInfo & { _hasData?: boolean })._hasData !== false;
        return (
        <div>
          <div className="breadcrumb"><Link to="/">Catalog</Link> / {ds.name}</div>
          <div className="page-head">
            <div>
              <h1 className="page-title">{ds.name}{ds.deprecated && <Badge kind="warning">deprecated</Badge>}</h1>
              <div className="row row-wrap mt-8">
                {ds.validation_status && <Badge kind={statusKind(ds.validation_status)}>{ds.validation_status}</Badge>}
                {ds.domain && <Badge kind="neutral">{ds.domain}</Badge>}
                {ds.has_schema_drift && <Badge kind="warning">schema drift</Badge>}
                {ds.current_version != null && <span className="small muted">v{ds.current_version}</span>}
              </div>
              {ds.description && <div className="page-sub mt-8">{ds.description}</div>}
            </div>
            <div className="spacer" />
            <div className="row gap-6">
              <FavoriteButton ds={ds} onDone={state.reload} />
              {canWrite ? (
                <>
                  <button className="btn" onClick={() => setEditing(true)}>Edit</button>
                  <button className="btn btn-danger" onClick={() => setDeleting(true)}>Delete</button>
                </>
              ) : (
                <Badge kind="neutral">read-only</Badge>
              )}
            </div>
          </div>

          {editing && <EditDatasetModal ds={ds} onClose={() => setEditing(false)} onSaved={state.reload} />}
          {deleting && <DeleteDatasetModal ds={ds} onClose={() => setDeleting(false)} />}

          {/* One clear statement of the seat's capability. The tabs still offer
              their own write controls (they refuse correctly and change
              nothing), but a read-only user is told up front why. */}
          {canWrite === false && hasData && (
            <div className="banner info" style={{ marginTop: 8 }}>
              <span>◔</span>
              <span>
                You have <strong>read-only</strong> access to this workspace. You can explore,
                query and chart; actions that write (rules, tags, views, runs, publishing)
                will be refused.
              </span>
            </div>
          )}

          {/* A dataset whose upload never produced a readable version: the row
              exists in the catalog, but nothing can be explored. Say so and
              offer the way out, rather than letting every tab fail oddly. */}
          {!hasData ? (
            <div className="banner error" style={{ marginTop: 8 }}>
              <span>⚠</span>
              <span>
                This dataset has no readable version — its upload didn't finish
                successfully. There's nothing to explore; use <strong>Delete</strong> to remove it.
              </span>
            </div>
          ) : (
            <>
              <Tabs tabs={TABS} active={tab} onChange={setTab} />
              {tab === "overview" && <Overview dataset={ds} reload={state.reload} />}
              {tab === "explore" && <Explore dataset={ds} reload={state.reload} />}
              {tab === "quality" && <Quality dataset={ds} reload={state.reload} />}
              {tab === "versions" && <Versions dataset={ds} reload={state.reload} />}
              {tab === "analytics" && <Analytics dataset={ds} reload={state.reload} />}
              {tab === "relationships" && <Relationships dataset={ds} reload={state.reload} />}
              {tab === "transform" && <Transform dataset={ds} reload={state.reload} />}
              {tab === "library" && <Library dataset={ds} reload={state.reload} />}
            </>
          )}
        </div>
        );
      }}
    </AsyncView>
  );
}

function FavoriteButton({ ds, onDone }: { ds: DatasetInfo; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  // Track the state locally as well as from the (slower) catalog refetch, so a
  // second click sends the opposite verb rather than repeating the first one
  // and 404ing ("not in your favorites") on a perfectly fine action.
  const [override, setOverride] = useState<boolean | null>(null);
  const fav = override ?? !!ds.is_favorite;
  const toggle = async () => {
    setBusy(true);
    const next = !fav;
    setOverride(next);
    try {
      if (fav) await api.del(`/datasets/${ds.id}/favorite`);
      else await api.put(`/datasets/${ds.id}/favorite`);
      onDone();
    } catch (e) {
      setOverride(null);   // resync with the server view
      toast({ kind: "error", title: "Couldn't update favorite", msg: String((e as Error).message) });
    } finally { setBusy(false); }
  };
  return (
    <button className={cx("btn")} onClick={toggle} disabled={busy} title={fav ? "Unfavorite" : "Favorite"}>
      <span style={{ color: fav ? "var(--warning)" : "inherit" }}>{fav ? "★" : "☆"}</span> Favorite
    </button>
  );
}

function EditDatasetModal({ ds, onClose, onSaved }: { ds: DatasetInfo; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [name, setName] = useState(ds.name);
  const [description, setDescription] = useState(ds.description || "");
  const [domain, setDomain] = useState(ds.domain || "");
  const [deprecated, setDeprecated] = useState(!!ds.deprecated);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const save = async () => {
    setBusy(true); setError(null);
    try {
      const body: Record<string, unknown> = { name: name.trim(), description: description.trim() || null, domain: domain.trim() || null, deprecated };
      if (deprecated && reason.trim()) body.deprecation_reason = reason.trim();
      await api.patch(`/datasets/${ds.id}`, body);
      toast({ kind: "good", title: "Dataset updated" });
      onSaved(); onClose();
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <Modal title="Edit dataset" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy || !name.trim()} onClick={save}>Save</button></>}>
      <div className="col">
        <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Description"><textarea className="input" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label="Domain"><input className="input" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="e.g. sales, finance" /></Field>
        <label className="row gap-6" style={{ cursor: "pointer" }}><input type="checkbox" checked={deprecated} onChange={(e) => setDeprecated(e.target.checked)} /> Mark deprecated</label>
        {deprecated && <Field label="Deprecation reason"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>}
        {error != null && <ErrorLine error={error} />}
      </div>
    </Modal>
  );
}

function DeleteDatasetModal({ ds, onClose }: { ds: DatasetInfo; onClose: () => void }) {
  const toast = useToast();
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const del = async () => {
    setBusy(true); setError(null);
    try {
      await api.del(`/datasets/${ds.id}`);
      toast({ kind: "good", title: "Dataset deleted", msg: ds.name });
      nav("/");
    } catch (e) { setError(e); setBusy(false); }
  };
  return (
    <Modal title="Delete dataset" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn btn-danger" disabled={busy} onClick={del}>Delete permanently</button></>}>
      <p>Delete <strong>{ds.name}</strong> and all its versions, artifacts, and definitions? This cannot be undone.</p>
      {error != null && <div className="mt-16"><ErrorLine error={error} /></div>}
    </Modal>
  );
}

function ErrorLine({ error }: { error: unknown }) {
  return <div className="banner error"><span>⚠</span><span className="wrap-anywhere">{String((error as Error)?.message || error)}</span></div>;
}
