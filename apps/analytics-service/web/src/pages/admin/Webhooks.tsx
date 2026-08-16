import { useState } from "react";
import { api } from "../../api/client";
import type { Page } from "../../api/client";
import {
  AsyncView, Badge, Card, EmptyState, Field, Modal, cx, fmtDate, statusKind, useAsync, useToast,
} from "../../components/ui";
import { useIdentity } from "../../app/identity";
import { ConfirmModal, EVENT_TYPES, errText } from "./common";

interface Webhook {
  id: string; team_id: string; name: string; url: string; events: string[];
  enabled: boolean; created_by?: string | null; created_at: string; updated_at: string;
}
interface WebhookCreated extends Webhook { secret: string; }
interface Delivery {
  id: string; subscription_id: string; event_type: string; dataset_id?: string | null;
  status: string; attempts: number; response_status?: number | null; error?: string | null;
  created_at: string; delivered_at?: string | null;
}

export function WebhooksTab() {
  const { identity } = useIdentity();
  const toast = useToast();
  const hooks = useAsync(() => api.get<Page<Webhook>>("/webhooks", { limit: 100 }), [identity.userId, identity.teamId]);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Webhook | null>(null);
  const [deliveriesOf, setDeliveriesOf] = useState<Webhook | null>(null);
  const [secret, setSecret] = useState<{ hook: Webhook; secret: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function del() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/webhooks/${deleting.id}`);
      toast({ kind: "good", title: "Webhook deleted", msg: deleting.name });
      setDeleting(null);
      hooks.reload();
    } catch (e) {
      toast({ kind: "error", title: "Could not delete webhook", msg: errText(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Webhook subscriptions"
      actions={<button className="btn btn-primary" onClick={() => setCreating(true)}>+ New webhook</button>}
      pad={false}
    >
      <AsyncView state={hooks} empty={<EmptyState icon="🔔" title="No webhooks" />}>
        {(page) => page.items.length === 0 ? (
          <EmptyState icon="🔔" title="No webhooks yet"
            hint="Subscribe a receiver to lifecycle events."
            action={<button className="btn btn-primary" onClick={() => setCreating(true)}>New webhook</button>} />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th scope="col">Name</th><th scope="col">URL</th><th scope="col">Events</th><th scope="col">Status</th><th scope="col" /></tr></thead>
              <tbody>
                {page.items.map((h) => (
                  <tr key={h.id}>
                    <td style={{ fontWeight: 600 }}>{h.name}</td>
                    <td className="secondary wrap-anywhere small">{h.url}</td>
                    <td>
                      {h.events.length === 0
                        ? <Badge kind="neutral">all events</Badge>
                        : <div className="row row-wrap gap-6">{h.events.map((e) => <Badge key={e} kind="accent">{e}</Badge>)}</div>}
                    </td>
                    <td><Badge kind={h.enabled ? "good" : "neutral"}>{h.enabled ? "enabled" : "disabled"}</Badge></td>
                    <td className="num">
                      <div className="row gap-6" style={{ justifyContent: "flex-end" }}>
                        <button className="btn" onClick={() => setDeliveriesOf(h)}>Deliveries</button>
                        <button className="btn btn-danger" onClick={() => setDeleting(h)}>Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncView>

      {creating && (
        <CreateWebhookModal
          onClose={() => setCreating(false)}
          onCreated={(w) => { setCreating(false); setSecret({ hook: w, secret: w.secret }); hooks.reload(); }}
        />
      )}

      {secret && <SecretModal name={secret.hook.name} secret={secret.secret} onClose={() => setSecret(null)} />}

      {deliveriesOf && <DeliveriesModal hook={deliveriesOf} onClose={() => setDeliveriesOf(null)} />}

      {deleting && (
        <ConfirmModal
          title="Delete webhook"
          danger
          confirmLabel="Delete"
          busy={busy}
          onConfirm={del}
          onClose={() => setDeleting(null)}
          body={<>Delete <strong>{deleting.name}</strong> and its delivery history? This cannot be undone.</>}
        />
      )}
    </Card>
  );
}

function CreateWebhookModal({ onClose, onCreated }: { onClose: () => void; onCreated: (w: WebhookCreated) => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [events, setEvents] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  function toggle(ev: string) {
    setEvents((cur) => cur.includes(ev) ? cur.filter((x) => x !== ev) : [...cur, ev]);
  }

  async function submit() {
    if (!name.trim() || !url.trim()) return;
    setBusy(true);
    try {
      const w = await api.post<WebhookCreated>("/webhooks", { name: name.trim(), url: url.trim(), events, enabled });
      toast({ kind: "good", title: "Webhook created", msg: w.name });
      onCreated(w);
    } catch (e) {
      toast({ kind: "error", title: "Could not create webhook", msg: errText(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="New webhook"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn btn-primary" onClick={submit} disabled={busy || !name.trim() || !url.trim()}>
            {busy ? "Creating…" : "Create"}
          </button>
        </>
      }
    >
      <Field label="Name">
        <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Slack notifier" />
      </Field>
      <Field label="Receiver URL">
        <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/hook" />
      </Field>
      <Field label="Events">
        <div className="row row-wrap gap-6" style={{ marginTop: 4 }}>
          {EVENT_TYPES.map((ev) => (
            <button type="button" key={ev} className={cx("btn", events.includes(ev) && "btn-primary")} onClick={() => toggle(ev)}>
              {ev}
            </button>
          ))}
        </div>
        <div className="small muted mt-8">
          {events.length === 0 ? "None selected — the subscription will receive all event types." : `${events.length} selected.`}
        </div>
      </Field>
      <label className="row gap-6" style={{ marginTop: 8, cursor: "pointer" }}>
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        <span>Enabled</span>
      </label>
    </Modal>
  );
}

function SecretModal({ name, secret, onClose }: { name: string; secret: string; onClose: () => void }) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
      toast({ kind: "good", title: "Secret copied" });
    } catch {
      toast({ kind: "info", title: "Copy manually", msg: "Clipboard is unavailable — select and copy the value." });
    }
  }
  return (
    <Modal
      title="Signing secret — shown once"
      onClose={onClose}
      footer={<button className="btn btn-primary" onClick={onClose}>Done</button>}
    >
      <div className="banner warning" role="alert" style={{ marginBottom: 12 }}>
        <span>⚠</span>
        <span>Copy the signing secret for <strong>{name}</strong> now. It is never shown again and cannot be retrieved.</span>
      </div>
      <Field label="Secret">
        <input className="input" readOnly value={secret} onFocus={(e) => e.currentTarget.select()} style={{ fontFamily: "monospace" }} />
      </Field>
      <button className="btn" onClick={copy}>{copied ? "Copied ✓" : "Copy to clipboard"}</button>
    </Modal>
  );
}

function DeliveriesModal({ hook, onClose }: { hook: Webhook; onClose: () => void }) {
  const state = useAsync(() => api.get<Page<Delivery>>(`/webhooks/${hook.id}/deliveries`, { limit: 50 }), [hook.id]);
  return (
    <Modal title={`Deliveries · ${hook.name}`} onClose={onClose} wide footer={<button className="btn" onClick={onClose}>Close</button>}>
      <AsyncView state={state} empty={<EmptyState icon="📭" title="No deliveries" />}>
        {(page) => page.items.length === 0 ? (
          <EmptyState icon="📭" title="No delivery attempts yet" hint="Attempts appear here once events fire." />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th scope="col">Event</th><th scope="col">Status</th><th scope="col" className="num">HTTP</th><th scope="col" className="num">Tries</th><th scope="col">When</th><th scope="col">Error</th></tr></thead>
              <tbody>
                {page.items.map((d) => (
                  <tr key={d.id}>
                    <td>{d.event_type}</td>
                    <td><Badge kind={statusKind(d.status)}>{d.status}</Badge></td>
                    <td className="num">{d.response_status ?? "—"}</td>
                    <td className="num">{d.attempts}</td>
                    <td className="small secondary">{fmtDate(d.delivered_at || d.created_at)}</td>
                    <td className="small critical wrap-anywhere">{d.error || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncView>
    </Modal>
  );
}
