Built: a covenant monitoring page with attention stats, and tabs for covenants, open breaches, waivers (an approval queue for credit officers), loans and tests, plus a covenant detail view with test history.

```tsx file="App.tsx"
import React, { useState } from "react";
import { AppShell, Section, Stats, Stat, Tabs, DataTable, ActionButton, StatusBadge, Modal, History,
  useTable, useQuery, useRow, useUser, useLookup, humanize, formatMoney } from "./kit";

export default function App() {
  const me = useUser();
  const loans = useTable("loans", { sort: "borrower" });
  const covenants = useTable("covenants", { sort: "loan_id" });
  const tests = useTable("covenant_tests", { sort: "-test_date" });
  const waivers = useTable("waivers", { sort: "-created_at" });
  const breaches = useQuery("open_breaches");
  const loanName = useLookup("loans");
  const [openId, setOpenId] = useState<number | null>(null);

  const covLoan = new Map(covenants.rows.map((c) => [c.id, c.loan_id]));
  const covLabel = (id: any) => {
    const c = covenants.rows.find((x) => x.id === id);
    return c ? `${loanName(c.loan_id)} · ${humanize(c.covenant_type)}` : `#${id}`;
  };

  const pending = waivers.rows.filter((w) => w.status === "requested");
  const pendingCov = new Set(pending.map((w) => w.covenant_id));
  const breached = covenants.rows.filter((c) => c.status === "breached");
  const watch = loans.rows.filter((l) => l.status === "watchlist");
  const activePrincipal = loans.rows.filter((l) => l.status !== "paid_off").reduce((s, l) => s + Number(l.principal || 0), 0);

  const covActions = (c: any) => (
    <span className="kit-row" onClick={(e) => e.stopPropagation()}>
      {c.status !== "waived" && <ActionButton action="record_covenant_test" inputs={{ covenant_id: c.id }} label="Record test" />}
      {c.status === "breached" && !pendingCov.has(c.id) && (
        <ActionButton action="request_waiver" inputs={{ covenant_id: c.id }} label="Request waiver" />
      )}
    </span>
  );
  const covColumns = [
    { key: "loan_id", label: "Borrower", render: (c: any) => loanName(c.loan_id) },
    { key: "covenant_type", label: "Covenant", render: (c: any) => humanize(c.covenant_type) },
    { key: "direction", render: (c: any) => humanize(c.direction) },
    { key: "threshold", format: "number" as const },
    { key: "frequency", render: (c: any) => humanize(c.frequency) },
    { key: "status", format: "status" as const },
    { key: "actions", label: "", render: covActions },
  ];

  const waiverActions = (w: any) => (
    <span className="kit-row" onClick={(e) => e.stopPropagation()}>
      {w.status === "requested" && <ActionButton action="approve_waiver" inputs={{ waiver_id: w.id }} label="Approve" variant="primary" confirm="Approve this waiver? The covenant will be marked waived." />}
      {w.status === "requested" && <ActionButton action="reject_waiver" inputs={{ waiver_id: w.id }} label="Reject" variant="danger" confirm="Reject this waiver?" />}
    </span>
  );
  const waiverColumns = [
    { key: "covenant_id", label: "Covenant", render: (w: any) => covLabel(w.covenant_id) },
    { key: "reason" },
    { key: "requested_by", label: "Requested by" },
    { key: "status", format: "status" as const },
    { key: "decided_by", label: "Decided by" },
    { key: "decided_on", label: "Decided on", format: "date" as const },
    { key: "actions", label: "", render: waiverActions },
  ];

  const tabs = [
    { label: "Covenants", content: (
      <Section title="All covenants">
        <DataTable rows={covenants.rows} columns={covColumns} loading={covenants.loading} onRowClick={(r) => setOpenId(r.id)} empty="No covenants yet." />
      </Section>) },
    ...(me.is("credit_officer") ? [{ label: `Waivers to decide (${pending.length})`, content: (
      <Section title="Waiting for credit decision">
        <DataTable rows={pending} columns={waiverColumns} loading={waivers.loading} empty="No waivers waiting." />
      </Section>) }] : []),
    { label: "Open breaches", content: (
      <Section title="Open breaches">
        <DataTable rows={breaches.rows} loading={breaches.loading} empty="No open breaches."
          columns={[
            { key: "borrower" },
            { key: "relationship_manager", label: "Relationship manager" },
            { key: "covenant_type", label: "Covenant", render: (r: any) => humanize(r.covenant_type) },
            { key: "direction", render: (r: any) => humanize(r.direction) },
            { key: "threshold", format: "number" },
            { key: "period" },
            { key: "actual_value", label: "Actual", format: "number" },
          ]} />
      </Section>) },
    { label: "Waivers", content: (
      <Section title="All waivers">
        <DataTable rows={waivers.rows} columns={waiverColumns} loading={waivers.loading} empty="No waivers." />
      </Section>) },
    { label: "Tests", content: (
      <Section title="Covenant tests">
        <DataTable rows={tests.rows} loading={tests.loading} empty="No tests recorded."
          columns={[
            { key: "covenant_id", label: "Covenant", render: (t: any) => covLabel(t.covenant_id) },
            { key: "period" },
            { key: "test_date", label: "Tested", format: "date" },
            { key: "actual_value", label: "Actual", format: "number" },
            { key: "result", format: "status" },
          ]} />
      </Section>) },
    { label: "Loans", content: (
      <Section title="Loans" actions={<ActionButton action="add_loan" label="Add loan" variant="primary" />}>
        <DataTable rows={loans.rows} loading={loans.loading} empty="No loans yet."
          columns={[
            { key: "borrower" },
            { key: "principal", format: "money" },
            { key: "status", format: "status" },
            { key: "relationship_manager", label: "Relationship manager" },
            { key: "actions", label: "", render: (l: any) => (
              <span className="kit-row" onClick={(e) => e.stopPropagation()}>
                {l.status !== "paid_off" && <ActionButton action="add_covenant" inputs={{ loan_id: l.id }} label="Add covenant" />}
              </span>) },
          ]} />
      </Section>) },
  ];

  return (
    <AppShell title="Loan covenants" subtitle="Track covenants, record tests, and manage waivers.">
      <Stats>
        <Stat label="Breached covenants" value={breached.length} format="number" tone={breached.length ? "bad" : undefined} />
        <Stat label="Waivers waiting for credit" value={pending.length} format="number" tone={pending.length ? "warn" : undefined} />
        <Stat label="Loans on watchlist" value={watch.length} format="number" tone={watch.length ? "warn" : undefined} />
        <Stat label="Active principal" value={activePrincipal} format="money" />
      </Stats>
      <Tabs tabs={tabs} />
      {openId && <CovenantDetail id={openId} loanId={covLoan.get(openId)} onClose={() => setOpenId(null)} />}
    </AppShell>
  );
}

function CovenantDetail({ id, loanId, onClose }: { id: number; loanId?: number; onClose: () => void }) {
  const { row } = useRow("covenants", id);
  const loanName = useLookup("loans");
  const tests = useTable("covenant_tests", { where: { covenant_id: id }, sort: "-test_date" });
  if (!row) return null;
  return (
    <Modal title={`${loanName(loanId ?? row.loan_id)} · ${humanize(row.covenant_type)}`} onClose={onClose}>
      <div className="kit-row">
        <StatusBadge value={row.status} />
        <span className="kit-muted">{humanize(row.direction)} {row.threshold} · {humanize(row.frequency)}</span>
      </div>
      <h3>Tests</h3>
      <DataTable rows={tests.rows} loading={tests.loading} empty="No tests recorded."
        columns={[
          { key: "period" },
          { key: "test_date", label: "Tested", format: "date" },
          { key: "actual_value", label: "Actual", format: "number" },
          { key: "result", format: "status" },
        ]} />
      <h3>History</h3>
      <History items={row._history} />
    </Modal>
  );
}
```
