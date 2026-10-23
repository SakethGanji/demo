import React, { useState } from "react";
import { AppShell, Section, Stats, Stat, Tabs, DataTable, ActionButton, StatusBadge, Modal, History,
  useTable, useQuery, useRow, useUser, formatMoney } from "./kit";

export default function App() {
  const me = useUser();
  const all = useTable("invoices", { sort: "-created_at" });
  const byVendor = useQuery("open_by_vendor");
  const overdue = useQuery("overdue_unpaid");
  const [openId, setOpenId] = useState<number | null>(null);

  // filter table rows when you need their columns (status, id) for buttons; queries return only their own columns
  const waiting = { rows: all.rows.filter((r) => r.status === "needs_approval") };
  const open = all.rows.filter((r) => r.status !== "paid" && r.status !== "rejected");
  const openTotal = open.reduce((s, r) => s + Number(r.amount || 0), 0);

  const rowActions = (r: any) => (
    <span className="kit-row" onClick={(e) => e.stopPropagation()}>
      {r.status === "needs_approval" && <ActionButton action="approve_invoice" inputs={{ invoice_id: r.id }} label="Approve" variant="primary" />}
      {r.status === "needs_approval" && <ActionButton action="reject_invoice" inputs={{ invoice_id: r.id }} label="Reject" variant="danger" />}
      {r.status === "ready_to_pay" && <ActionButton action="mark_paid" inputs={{ invoice_id: r.id }} label="Mark paid" confirm="Mark this invoice as paid?" />}
    </span>
  );
  const columns = [
    { key: "vendor" },
    { key: "amount", format: "money" as const },
    { key: "due_date", label: "Due", format: "date" as const },
    { key: "status", format: "status" as const },
    { key: "actions", label: "", render: rowActions },
  ];

  return (
    <AppShell title="Vendor invoices" subtitle="Add invoices, approve the big ones, pay them.">
      <Stats>
        <Stat label="Open invoices" value={open.length} format="number" />
        <Stat label="Open amount" value={openTotal} format="money" />
        <Stat label="Waiting for approval" value={waiting.rows.length} format="number" tone={waiting.rows.length ? "warn" : undefined} />
        <Stat label="Overdue" value={overdue.rows.length} format="number" tone={overdue.rows.length ? "bad" : undefined} />
      </Stats>
      <Tabs tabs={[
        { label: "All invoices", content: (
          <Section title="Invoices" actions={<ActionButton action="add_invoice" label="Add invoice" variant="primary" />}>
            <DataTable rows={all.rows} columns={columns} loading={all.loading} onRowClick={(r) => setOpenId(r.id)} empty="No invoices yet." />
          </Section>) },
        ...(me.is("manager") ? [{ label: `Waiting for me (${waiting.rows.length})`, content: (
          <Section title="Waiting for approval">
            <DataTable rows={waiting.rows} columns={columns} empty="Nothing waiting for approval." />
          </Section>) }] : []),
        { label: "By vendor", content: (
          <Section title="Open amount by vendor">
            <DataTable rows={byVendor.rows} columns={[{ key: "vendor" }, { key: "total", label: "Open", format: "money" }]} />
          </Section>) },
      ]} />
      {openId && <InvoiceDetail id={openId} onClose={() => setOpenId(null)} />}
    </AppShell>
  );
}

function InvoiceDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { row } = useRow("invoices", id);
  if (!row) return null;
  return (
    <Modal title={`${row.vendor} · ${formatMoney(row.amount)}`} onClose={onClose}>
      <div className="kit-row"><StatusBadge value={row.status} />{row.rejection_reason && <span className="kit-muted">Reason: {row.rejection_reason}</span>}</div>
      <History items={row._history} />
    </Modal>
  );
}
