import { useState } from "react";
import { api } from "../../api/client";
import type { Page } from "../../api/client";
import {
  AsyncView, Badge, Card, EmptyState, Field, Modal, cx, useAsync, useToast,
} from "../../components/ui";
import { useIdentity } from "../../app/identity";
import { ConfirmModal, ROLES, errText } from "./common";
import type { RoleName } from "./common";

interface Membership { team_id: string; team_name: string; role: string; }
interface Member { user_id: string; email: string; name: string; role: string; }

function roleKind(role: string) {
  return role === "owner" ? "accent" : role === "admin" ? "good" : role === "editor" ? "warning" : "neutral";
}

export function TeamsTab() {
  const { identity } = useIdentity();
  const toast = useToast();
  const teams = useAsync(() => api.get<Page<Membership>>("/teams"), [identity.userId, identity.teamId]);
  const [selected, setSelected] = useState<Membership | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);

  async function createTeam() {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      const t = await api.post<{ id: string; name: string }>("/teams", { name: newName.trim() });
      toast({ kind: "good", title: "Team created", msg: t.name });
      setCreating(false);
      setNewName("");
      teams.reload();
    } catch (e) {
      toast({ kind: "error", title: "Could not create team", msg: errText(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid grid-2-1" style={{ display: "grid", gridTemplateColumns: "minmax(240px, 340px) 1fr", gap: 18, alignItems: "start" }}>
      <Card
        title="Teams"
        actions={<button className="btn btn-primary" onClick={() => setCreating(true)}>+ New team</button>}
        pad={false}
      >
        <AsyncView state={teams} empty={<EmptyState icon="◇" title="No teams" />}>
          {(page) => page.items.length === 0 ? (
            <EmptyState icon="◇" title="No teams" hint="Create a team to get started." />
          ) : (
            <div className="table-wrap" style={{ maxHeight: 460 }}>
              <table className="data">
                <tbody>
                  {page.items.map((t) => (
                    <tr
                      key={t.team_id}
                      className={cx("clickable", selected?.team_id === t.team_id && "active")}
                      onClick={() => setSelected(t)}
                    >
                      <td>
                        <div style={{ fontWeight: 600 }}>{t.team_name}</div>
                        <div className="small muted wrap-anywhere">{t.team_id}</div>
                      </td>
                      <td className="num"><Badge kind={roleKind(t.role)}>{t.role}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncView>
      </Card>

      {selected
        ? <MembersPanel key={selected.team_id} team={selected} />
        : <Card title="Members"><EmptyState icon="👥" title="Select a team" hint="Pick a team on the left to manage its members." /></Card>}

      {creating && (
        <Modal
          title="New team"
          onClose={() => setCreating(false)}
          footer={
            <>
              <button className="btn" onClick={() => setCreating(false)} disabled={busy}>Cancel</button>
              <button className="btn btn-primary" onClick={createTeam} disabled={busy || !newName.trim()}>
                {busy ? "Creating…" : "Create team"}
              </button>
            </>
          }
        >
          <Field label="Team name">
            <input className="input" autoFocus value={newName} onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && createTeam()} placeholder="e.g. Payments" />
          </Field>
          <div className="small muted">You become the team&rsquo;s owner.</div>
        </Modal>
      )}
    </div>
  );
}

function MembersPanel({ team }: { team: Membership }) {
  const toast = useToast();
  const members = useAsync(() => api.get<Page<Member>>(`/teams/${team.team_id}/members`), [team.team_id]);
  const [email, setEmail] = useState("");
  const [addRole, setAddRole] = useState<RoleName>("viewer");
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<Member | null>(null);
  const [rowBusy, setRowBusy] = useState<string | null>(null);

  async function addMember() {
    if (!email.trim()) return;
    setAdding(true);
    try {
      await api.post(`/teams/${team.team_id}/members`, { email: email.trim(), role: addRole });
      toast({ kind: "good", title: "Member added", msg: `${email.trim()} · ${addRole}` });
      setEmail("");
      setAddRole("viewer");
      members.reload();
    } catch (e) {
      toast({ kind: "error", title: "Could not add member", msg: errText(e) });
    } finally {
      setAdding(false);
    }
  }

  async function changeRole(m: Member, role: string) {
    if (role === m.role) return;
    setRowBusy(m.user_id);
    try {
      await api.patch(`/teams/${team.team_id}/members/${m.user_id}`, { role });
      toast({ kind: "good", title: "Role updated", msg: `${m.email} → ${role}` });
      members.reload();
    } catch (e) {
      toast({ kind: "error", title: "Could not change role", msg: errText(e) });
      members.reload(); // revert the optimistic <select> value
    } finally {
      setRowBusy(null);
    }
  }

  async function removeMember() {
    if (!removing) return;
    setRowBusy(removing.user_id);
    try {
      await api.del(`/teams/${team.team_id}/members/${removing.user_id}`);
      toast({ kind: "good", title: "Member removed", msg: removing.email });
      setRemoving(null);
      members.reload();
    } catch (e) {
      toast({ kind: "error", title: "Could not remove member", msg: errText(e) });
    } finally {
      setRowBusy(null);
    }
  }

  return (
    <Card
      title={<h3>Members · {team.team_name}</h3>}
      pad={false}
    >
      <div className="card-pad" style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="row row-wrap" style={{ alignItems: "flex-end" }}>
          <Field label="Add member by email">
            <input className="input" style={{ minWidth: 260 }} type="email" placeholder="colleague@example.com"
              value={email} onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addMember()} />
          </Field>
          <Field label="Role">
            <select className="select" value={addRole} onChange={(e) => setAddRole(e.target.value as RoleName)}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </Field>
          <button className="btn btn-primary" onClick={addMember} disabled={adding || !email.trim()}>
            {adding ? "Adding…" : "Add"}
          </button>
        </div>
      </div>

      <AsyncView state={members} empty={<EmptyState icon="👥" title="No members" />}>
        {(page) => page.items.length === 0 ? (
          <EmptyState icon="👥" title="No members yet" hint="Add one by email above." />
        ) : (
          <div className="table-wrap" style={{ maxHeight: 460 }}>
            <table className="data">
              <thead><tr><th scope="col">Name</th><th scope="col">Email</th><th scope="col">Role</th><th scope="col" /></tr></thead>
              <tbody>
                {page.items.map((m) => (
                  <tr key={m.user_id}>
                    <td style={{ fontWeight: 600 }}>{m.name || "—"}</td>
                    <td className="secondary wrap-anywhere">{m.email}</td>
                    <td>
                      <select className="select" value={m.role} disabled={rowBusy === m.user_id}
                        onChange={(e) => changeRole(m, e.target.value)}>
                        {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </td>
                    <td className="num">
                      <button className="btn btn-danger" disabled={rowBusy === m.user_id}
                        onClick={() => setRemoving(m)}>Remove</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncView>

      {removing && (
        <ConfirmModal
          title="Remove member"
          danger
          confirmLabel="Remove"
          busy={rowBusy === removing.user_id}
          onConfirm={removeMember}
          onClose={() => setRemoving(null)}
          body={<>Remove <strong>{removing.email}</strong> from <strong>{team.team_name}</strong>?</>}
        />
      )}
    </Card>
  );
}
