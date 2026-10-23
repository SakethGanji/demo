import React, { useState } from "react";
import { AppShell, Section, Stats, Stat, Tabs, DataTable, ActionButton, Modal, History, StatusBadge,
  useTable, useQuery, useRow, useUser, useLookup, formatMoney, formatDate } from "./kit";

export default function App() {
  const me = useUser();
  const counts = useTable("cash_counts", { sort: "-count_date" });
  const tellers = useTable("tellers", { sort: "name" });
  const branches = useTable("branches", { sort: "name" });
  const monthly = useQuery("monthly_variance_by_branch");
  const branchName = useLookup("branches");
  const tellerName = useLookup("tellers");
  const [openId, setOpenId] = useState<number | null>(null);

  const waiting = counts.rows.filter((r) => r.status === "needs_signoff");
  const waitingTotal = waiting.reduce((s, r) => s + Math.abs(Number(r.variance || 0)), 0);
  const thisMonth = new Date().toISOString().slice(0, 7);
  const monthRows = counts.rows.filter((r) => String(r.count_date).startsWith(thisMonth));
  const monthNet = monthRows.reduce((s, r) => s + Number(r.variance || 0), 0);

  const countActions = (r: any) => (
    <span className="kit-row" onClick={(e) => e.stopPropagation()}>
      {r.status === "needs_signoff" && (
        <ActionButton action="sign_off_count" inputs={{ count_id: r.id }} label="Sign off" variant="primary" confirm="Sign off this cash difference?" />
      )}
    </span>
  );
  const countColumns = [
    { key: "count_date", label: "Date", format: "date" as const },
    { key: "teller_id", label: "Teller", render: (r: any) => tellerName(r.teller_id) },
    { key: "branch_id", label: "Branch", render: (r: any) => branchName(r.branch_id) },
    { key: "expected_amount", label: "Expected", format: "money" as const },
    { key: "counted_amount", label: "Counted", format: "money" as const },
    { key: "variance", label: "Difference", format: "money" as const },
    { key: "status", format: "status" as const },
    { key: "actions", label: "", render: countActions },
  ];

  const tabs = [
    ...(me.is("manager") ? [{ label: `Waiting for sign-off (${waiting.length})`, content: (
      <Section title="Differences over $50 waiting for sign-off">
        <DataTable rows={waiting} columns={countColumns} loading={counts.loading} onRowClick={(r) => setOpenId(r.id)} empty="Nothing waiting for sign-off." />
      </Section>) }] : []),
    { label: "Cash counts", content: (
      <Section title="Daily cash counts" actions={<ActionButton action="submit_cash_count" label="Submit my count" variant="primary" />}>
        <DataTable rows={counts.rows} columns={countColumns} loading={counts.loading} onRowClick={(r) => setOpenId(r.id)} empty="No cash counts yet." />
      </Section>) },
    { label: "Monthly variance", content: (
      <Section title="Monthly variance by branch">
        <DataTable rows={monthly.rows} loading={monthly.loading} empty="No data yet." columns={[
          { key: "branch" },
          { key: "month" },
          { key: "counts", label: "Counts", format: "number" },
          { key: "net_variance", label: "Net difference", format: "money" },
          { key: "gross_variance", label: "Total absolute difference", format: "money" },
        ]} />
      </Section>) },
    { label: "Branches and tellers", content: (
      <>
        <Section title="Branches" actions={<ActionButton action="add_branch" label="Add branch" variant="primary" />}>
          <DataTable rows={branches.rows} loading={branches.loading} columns={[
            { key: "name" },
            { key: "code" },
            { key: "manager_email", label: "Manager" },
            { key: "actions", label: "", render: (r) => (
              <span className="kit-row" onClick={(e) => e.stopPropagation()}>
                <ActionButton action="add_teller" inputs={{ branch_id: r.id }} label="Add teller" />
              </span>) },
          ]} />
        </Section>
        <Section title="Tellers">
          <DataTable rows={tellers.rows} loading={tellers.loading} columns={[
            { key: "name" },
            { key: "email" },
            { key: "branch_id", label: "Branch", render: (r) => branchName(r.branch_id) },
            { key: "active", label: "Active", format: "boolean" },
            { key: "actions", label: "", render: (r) => (
              <span className="kit-row" onClick={(e) => e.stopPropagation()}>
                <ActionButton action="move_teller" inputs={{ teller_id: r.id }} label="Move" />
              </span>) },
          ]} />
        </Section>
      </>) },
  ];

  return (
    <AppShell title="Branch cash" subtitle="Daily teller cash counts. Differences over $50 need the branch manager's sign-off.">
      <Stats>
        <Stat label="Waiting for sign-off" value={waiting.length} format="number" tone={waiting.length ? "warn" : undefined} />
        <Stat label="Amount waiting" value={waitingTotal} format="money" tone={waiting.length ? "warn" : undefined} />
        <Stat label="Counts this month" value={monthRows.length} format="number" />
        <Stat label="Net difference this month" value={monthNet} format="money" tone={Math.abs(monthNet) > 50 ? "bad" : undefined} />
      </Stats>
      <Tabs tabs={tabs} />
      {openId && <CountDetail id={openId} onClose={() => setOpenId(null)} />}
    </AppShell>
  );
}

function CountDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { row } = useRow("cash_counts", id);
  const branchName = useLookup("branches");
  const tellerName = useLookup("tellers");
  if (!row) return null;
  return (
    <Modal title={`${tellerName(row.teller_id)} · ${formatDate(row.count_date)}`} onClose={onClose}>
      <div className="kit-row">
        <StatusBadge value={row.status} />
        <span className="kit-muted">{branchName(row.branch_id)}</span>
      </div>
      <p>Expected {formatMoney(row.expected_amount)}, counted {formatMoney(row.counted_amount)}, difference {formatMoney(row.variance)}.</p>
      {row.note && <p className="kit-muted">Note: {row.note}</p>}
      {row.signed_off_by && <p className="kit-muted">Signed off by {row.signed_off_by} on {formatDate(row.signed_off_at)}</p>}
      {row.status === "needs_signoff" && (
        <div className="kit-row">
          <ActionButton action="sign_off_count" inputs={{ count_id: row.id }} label="Sign off" variant="primary" confirm="Sign off this cash difference?" onDone={onClose} />
        </div>
      )}
      <History items={row._history} />
    </Modal>
  );
}