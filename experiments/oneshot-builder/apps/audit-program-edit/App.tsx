import React, { useState } from "react";
import { AppShell, Section, Stats, Stat, Tabs, DataTable, ActionButton, StatusBadge, Modal, History,
  useTable, useQuery, useRow, useUser, useLookup, formatDate } from "./kit";

export default function App() {
  const me = useUser();
  const audits = useTable("audits", { sort: "-created_at" });
  const findings = useTable("findings", { sort: "-created_at" });
  const items = useTable("action_items", { sort: "due_date" });
  const extensions = useTable("extension_requests", { sort: "-created_at" });
  const overdueQ = useQuery("overdue_action_items");
  const openByAudit = useQuery("open_findings_by_audit");
  const pendingExt = useQuery("pending_extension_requests");
  const auditName = useLookup("audits");
  const findingName = useLookup("findings");
  const [open, setOpen] = useState<{ table: string; id: number } | null>(null);

  const today = "2026-10-22";
  const openAudits = audits.rows.filter((a) => a.status === "open");
  const openFindings = findings.rows.filter((f) => f.status === "open");
  const openItems = items.rows.filter((i) => i.status === "open");
  const overdueItems = openItems.filter((i) => String(i.due_date) < today);
  const mine = items.rows.filter((i) => i.owner_email === me.email);
  const myOpen = mine.filter((i) => i.status === "open");

  const itemsOf = (fid: number) => items.rows.filter((i) => i.finding_id === fid);
  const findingsOf = (aid: number) => findings.rows.filter((f) => f.audit_id === aid);
  const readyFindings = openFindings.filter((f) => itemsOf(f.id).every((i) => i.status === "done"));
  const itemName = (id: any) => items.rows.find((i) => i.id === Number(id))?.description ?? `#${id}`;
  // an item can be extended once: no new request while one is pending or approved
  const canRequest = (r: any) =>
    r.status === "open" && r.owner_email === me.email &&
    !extensions.rows.some((e) => e.action_item_id === r.id && (e.status === "pending" || e.status === "approved"));

  const itemActions = (r: any) => (
    <span className="kit-row" onClick={(e) => e.stopPropagation()}>
      {r.status === "open" && <ActionButton action="complete_action_item" inputs={{ action_item_id: r.id }} label="Mark done" confirm="Mark this action item as done?" />}
      {canRequest(r) && <ActionButton action="request_extension" inputs={{ action_item_id: r.id }} label="Request extension" />}
    </span>
  );
  const findingActions = (r: any) => (
    <span className="kit-row" onClick={(e) => e.stopPropagation()}>
      {r.status === "open" && <ActionButton action="add_action_item" inputs={{ finding_id: r.id }} label="Add action item" />}
      {r.status === "open" && <ActionButton action="close_finding" inputs={{ finding_id: r.id }} label="Close" variant="primary" confirm="Close this finding?" />}
    </span>
  );
  const auditActions = (r: any) => (
    <span className="kit-row" onClick={(e) => e.stopPropagation()}>
      {r.status === "open" && <ActionButton action="add_finding" inputs={{ audit_id: r.id }} label="Add finding" />}
      {r.status === "open" && <ActionButton action="close_audit" inputs={{ audit_id: r.id }} label="Close" variant="primary" confirm="Close this audit?" />}
    </span>
  );
  const extensionActions = (r: any) => (
    <span className="kit-row" onClick={(e) => e.stopPropagation()}>
      <ActionButton action="approve_extension" inputs={{ request_id: r.id }} label="Approve" variant="primary" />
      <ActionButton action="decline_extension" inputs={{ request_id: r.id }} label="Decline" variant="danger" />
    </span>
  );

  const itemCols = [
    { key: "finding_id", label: "Finding", render: (r: any) => findingName(r.finding_id) },
    { key: "description" },
    { key: "owner_email", label: "Owner" },
    { key: "due_date", label: "Due", format: "date" as const },
    { key: "status", format: "status" as const },
    { key: "actions", label: "", render: itemActions },
  ];
  const findingCols = [
    { key: "audit_id", label: "Audit", render: (r: any) => auditName(r.audit_id) },
    { key: "title" },
    { key: "severity", format: "status" as const },
    { key: "progress", label: "Action items done", render: (r: any) => {
      const l = itemsOf(r.id);
      return `${l.filter((i) => i.status === "done").length} of ${l.length}`;
    } },
    { key: "status", format: "status" as const },
    { key: "actions", label: "", render: findingActions },
  ];
  const auditCols = [
    { key: "title" },
    { key: "scope" },
    { key: "lead_email", label: "Lead" },
    { key: "open_findings", label: "Open findings", render: (r: any) => findingsOf(r.id).filter((f) => f.status === "open").length },
    { key: "status", format: "status" as const },
    { key: "actions", label: "", render: auditActions },
  ];
  const requestCols = [
    { key: "action_item_id", label: "Action item", render: (r: any) => itemName(r.action_item_id) },
    { key: "requested_by", label: "Requested by" },
    { key: "current_due_date", label: "Current due", format: "date" as const },
    { key: "new_due_date", label: "Requested due", format: "date" as const },
    { key: "reason" },
    { key: "status", format: "status" as const },
    { key: "decision_note", label: "Decision note" },
  ];

  const tabs: { label: string; content: React.ReactNode }[] = [];
  if (me.is("auditor")) {
    tabs.push({
      label: `Ready to close (${readyFindings.length})`,
      content: (
        <Section title="Findings with all action items done">
          <DataTable rows={readyFindings} columns={findingCols} onRowClick={(r) => setOpen({ table: "findings", id: r.id })} empty="No findings are ready to close." />
        </Section>
      ),
    });
    tabs.push({
      label: `Extension requests (${pendingExt.rows.length})`,
      content: (
        <Section title="Waiting for a decision">
          <DataTable
            rows={pendingExt.rows}
            columns={[
              { key: "description", label: "Action item" },
              { key: "requested_by", label: "Requested by" },
              { key: "current_due_date", label: "Current due", format: "date" as const },
              { key: "new_due_date", label: "Requested due", format: "date" as const },
              { key: "reason" },
              { key: "actions", label: "", render: extensionActions },
            ]}
            loading={pendingExt.loading}
            empty="No extension requests are waiting."
          />
        </Section>
      ),
    });
  }
  if (me.is("owner")) {
    tabs.push({
      label: `My action items (${myOpen.length})`,
      content: (
        <Section title="Assigned to me">
          <DataTable rows={mine} columns={itemCols} onRowClick={(r) => setOpen({ table: "action_items", id: r.id })} empty="No action items are assigned to you." />
        </Section>
      ),
    });
  }
  tabs.push(
    {
      label: "Audits",
      content: (
        <Section title="Audits" actions={<ActionButton action="create_audit" label="New audit" variant="primary" />}>
          <DataTable rows={audits.rows} columns={auditCols} loading={audits.loading} onRowClick={(r) => setOpen({ table: "audits", id: r.id })} empty="No audits yet." />
        </Section>
      ),
    },
    {
      label: "Findings",
      content: (
        <Section title="Findings">
          <DataTable rows={findings.rows} columns={findingCols} loading={findings.loading} onRowClick={(r) => setOpen({ table: "findings", id: r.id })} empty="No findings yet." />
        </Section>
      ),
    },
    {
      label: "Action items",
      content: (
        <Section title="Action items">
          <DataTable rows={items.rows} columns={itemCols} loading={items.loading} onRowClick={(r) => setOpen({ table: "action_items", id: r.id })} empty="No action items yet." />
        </Section>
      ),
    },
    {
      label: "Extensions",
      content: (
        <Section title="All extension requests">
          <DataTable rows={extensions.rows} columns={requestCols} loading={extensions.loading} onRowClick={(r) => setOpen({ table: "action_items", id: r.action_item_id })} empty="No extension requests yet." />
        </Section>
      ),
    },
    {
      label: "Reports",
      content: (
        <>
          <Section title="Open findings by audit">
            <DataTable rows={openByAudit.rows} columns={[{ key: "audit" }, { key: "open_findings", label: "Open findings", format: "number" }]} loading={openByAudit.loading} empty="No open findings." />
          </Section>
          <Section title="Overdue action items">
            <DataTable
              rows={overdueQ.rows}
              columns={[
                { key: "finding_id", label: "Finding", render: (r: any) => findingName(r.finding_id) },
                { key: "description" },
                { key: "owner_email", label: "Owner" },
                { key: "due_date", label: "Due", format: "date" as const },
              ]}
              loading={overdueQ.loading}
              empty="Nothing is overdue."
            />
          </Section>
        </>
      ),
    },
  );

  return (
    <AppShell title="Audit program" subtitle="Audits, findings and the action items that close them.">
      <Stats>
        <Stat label="Open audits" value={openAudits.length} format="number" />
        <Stat label="Open findings" value={openFindings.length} format="number" tone={openFindings.length ? "warn" : undefined} />
        <Stat label="Open action items" value={openItems.length} format="number" />
        <Stat label="Overdue action items" value={overdueItems.length} format="number" tone={overdueItems.length ? "bad" : undefined} />
        {me.is("auditor") && <Stat label="Findings ready to close" value={readyFindings.length} format="number" tone={readyFindings.length ? "ok" : undefined} />}
        {me.is("auditor") && <Stat label="Extension requests waiting" value={pendingExt.rows.length} format="number" tone={pendingExt.rows.length ? "warn" : undefined} />}
        {me.is("owner") && <Stat label="My open items" value={myOpen.length} format="number" tone={myOpen.length ? "warn" : undefined} />}
      </Stats>
      <Tabs tabs={tabs} />
      {open && <Detail table={open.table} id={open.id} onClose={() => setOpen(null)} />}
    </AppShell>
  );
}

function Detail({ table, id, onClose }: { table: string; id: number; onClose: () => void }) {
  const { row } = useRow(table, id);
  const auditName = useLookup("audits");
  const findingName = useLookup("findings");
  const itemExt = useTable("extension_requests", { where: { action_item_id: table === "action_items" ? id : "" }, sort: "-created_at" });
  if (!row) return null;
  const title = table === "action_items" ? row.description : row.title;
  return (
    <Modal title={title} onClose={onClose}>
      <div className="kit-row"><StatusBadge value={row.status} />{row.severity && <StatusBadge value={row.severity} />}</div>
      {table === "audits" && (
        <>
          <p className="kit-muted">Scope: {row.scope || "None"} · Lead: {row.lead_email}</p>
          {row.closed_at && <p className="kit-muted">Closed {formatDate(row.closed_at)}</p>}
        </>
      )}
      {table === "findings" && (
        <>
          <p className="kit-muted">Audit: {auditName(row.audit_id)}</p>
          {row.description && <p>{row.description}</p>}
          {row.closed_at && <p className="kit-muted">Closed {formatDate(row.closed_at)}</p>}
        </>
      )}
      {table === "action_items" && (
        <>
          <p className="kit-muted">Finding: {findingName(row.finding_id)} · Owner: {row.owner_email} · Due {formatDate(row.due_date)}</p>
          {row.done_at && <p className="kit-muted">Done {formatDate(row.done_at)}</p>}
          <h2>Extension requests</h2>
          <DataTable
            rows={itemExt.rows}
            columns={[
              { key: "requested_by", label: "Requested by" },
              { key: "new_due_date", label: "Requested due", format: "date" as const },
              { key: "reason" },
              { key: "status", format: "status" as const },
              { key: "decision_note", label: "Decision note" },
            ]}
            empty="No extension requested."
          />
        </>
      )}
      <h2>History</h2>
      <History items={row._history} />
    </Modal>
  );
}