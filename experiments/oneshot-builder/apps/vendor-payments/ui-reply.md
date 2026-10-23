Built: a vendor payments page with attention stats, an approval queue for managers, all invoices with status-based actions, vendors, payments, and balance reports, plus invoice detail with history.

```tsx file="App.tsx"
import React, { useState } from "react";
import { AppShell, Section, Stats, Stat, Tabs, DataTable, ActionButton, StatusBadge, Modal, History, RecordTable,
  useTable, useQuery, useRow, useUser, useLookup, formatMoney } from "./kit";

export default function App() {
  const me = useUser();
  const invoices = useTable("invoices", { sort: "-created_at" });
  const vendors = useTable("vendors", { sort: "name" });
  const payments = useTable("payments", { sort: "-paid_on" });
  const balances = useQuery("open_balance_by_vendor");
  const vendorName = useLookup("vendors");
  const [invoiceId, setInvoiceId] = useState<number | null>(null);
  const [vendorId, setVendorId] = useState<number | null>(null);

  const num = (v: any) => Number(v || 0);
  const waiting = invoices.rows.filter((r) => r.status === "needs_approval");
  const payable = invoices.rows.filter((r) => r.status === "ready_to_pay" || r.status === "partially_paid");
  const owed = payable.reduce((s, r) => s + num(r.amount) - num(r.amount_paid), 0);
  const waitingTotal = waiting.reduce((s, r) => s + num(r.amount), 0);
  const blocked = vendors.rows.filter((v) => v.status === "blocked");

  const invoiceActions = (r: any) => (
    <span className="kit-row" onClick={(e) => e.stopPropagation()}>
      {r.status === "needs_approval" && <ActionButton action="approve_invoice" inputs={{ invoice_id: r.id }} label="Approve" variant="primary" />}
      {r.status === "needs_approval" && <ActionButton action="reject_invoice" inputs={{ invoice_id: r.id }} label="Reject" variant="danger" />}
      {(r.status === "ready_to_pay" || r.status === "partially_paid") && <ActionButton action="record_payment" inputs={{ invoice_id: r.id }} label="Record payment" />}
    </span>
  );

  const invoiceColumns = [
    { key: "invoice_number", label: "Invoice" },
    { key: "vendor_id", label: "Vendor", render: (r: any) => vendorName(r.vendor_id) },
    { key: "amount", format: "money" as const },
    { key: "amount_paid", label: "Paid", format: "money" as const },
    { key: "balance", label: "Balance", format: "money" as const, render: (r: any) => formatMoney(num(r.amount) - num(r.amount_paid)) },
    { key: "status", format: "status" as const },
    { key: "actions", label: "", render: invoiceActions },
  ];

  const vendorColumns = [
    { key: "name" },
    { key: "contact_email", label: "Contact" },
    { key: "status", format: "status" as const },
    { key: "actions", label: "", render: (v: any) => (
      <span className="kit-row" onClick={(e) => e.stopPropagation()}>
        <ActionButton action="add_invoice" inputs={{ vendor_id: v.id }} label="Add invoice" />
        {v.status === "active" && <ActionButton action="block_vendor" inputs={{ vendor_id: v.id }} label="Block" variant="danger" confirm="Block this vendor? No new invoices can be added." />}
        {v.status === "blocked" && <ActionButton action="unblock_vendor" inputs={{ vendor_id: v.id }} label="Unblock" />}
      </span>) },
  ];

  const tabs = [
    ...(me.is("manager") ? [{ label: `Approval queue (${waiting.length})`, content: (
      <Section title="Waiting for approval">
        <DataTable rows={waiting} columns={[
          { key: "invoice_number", label: "Invoice" },
          { key: "vendor_id", label: "Vendor", render: (r: any) => vendorName(r.vendor_id) },
          { key: "amount", format: "money" as const },
          { key: "submitter_email", label: "Submitted by" },
          { key: "actions", label: "", render: invoiceActions },
        ]} loading={invoices.loading} onRowClick={(r) => setInvoiceId(r.id)} empty="Nothing waiting for approval." />
      </Section>) }] : []),
    { label: "Invoices", content: (
      <Section title="All invoices" actions={<ActionButton action="add_invoice" label="Add invoice" variant="primary" />}>
        <DataTable rows={invoices.rows} columns={invoiceColumns} loading={invoices.loading} onRowClick={(r) => setInvoiceId(r.id)} empty="No invoices yet." />
      </Section>) },
    { label: "Vendors", content: (
      <>
        <Section title="Vendors">
          <DataTable rows={vendors.rows} columns={vendorColumns} loading={vendors.loading} onRowClick={(r) => setVendorId(r.id)} empty="No vendors yet." />
        </Section>
        {me.is("manager") && <RecordTable table="vendors" title="Edit vendor list" columns={[{ key: "name" }, { key: "contact_email", label: "Contact" }, { key: "status", format: "status" as const }]} />}
      </>) },
    { label: "Payments", content: (
      <Section title="Payments">
        <DataTable rows={payments.rows} columns={[
          { key: "invoice_id", label: "Invoice", render: (p: any) => invoices.rows.find((i) => i.id === p.invoice_id)?.invoice_number ?? `#${p.invoice_id}` },
          { key: "amount", format: "money" as const },
          { key: "paid_on", label: "Paid on", format: "date" as const },
          { key: "paid_by", label: "Paid by" },
        ]} loading={payments.loading} empty="No payments yet." />
      </Section>) },
    { label: "Balance by vendor", content: (
      <Section title="Open balance by vendor">
        <DataTable rows={balances.rows} columns={[{ key: "vendor" }, { key: "balance", format: "money" as const }]} loading={balances.loading} empty="No open balances." />
      </Section>) },
  ];

  return (
    <AppShell title="Vendor payments" subtitle="Track vendors, invoices and payments. Invoices over $5,000 need manager approval before payment.">
      <Stats>
        <Stat label="Waiting for approval" value={waiting.length} format="number" tone={waiting.length ? "warn" : undefined} />
        <Stat label="Amount awaiting approval" value={waitingTotal} format="money" tone={waitingTotal ? "warn" : undefined} />
        <Stat label="Open to pay" value={owed} format="money" />
        <Stat label="Blocked vendors" value={blocked.length} format="number" tone={blocked.length ? "bad" : undefined} />
      </Stats>
      <Tabs tabs={tabs} />
      {invoiceId && <InvoiceDetail id={invoiceId} onClose={() => setInvoiceId(null)} />}
      {vendorId && <VendorDetail id={vendorId} onClose={() => setVendorId(null)} />}
    </AppShell>
  );
}

function InvoiceDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { row } = useRow("invoices", id);
  const vendorName = useLookup("vendors");
  const pay = useQuery("payments_by_invoice");
  if (!row) return null;
  const mine = pay.rows.filter((p) => p.invoice_number === row.invoice_number);
  return (
    <Modal title={`${row.invoice_number} · ${formatMoney(row.amount)}`} onClose={onClose}>
      <div className="kit-row">
        <StatusBadge value={row.status} />
        <span className="kit-muted">{vendorName(row.vendor_id)} · Paid {formatMoney(row.amount_paid)} of {formatMoney(row.amount)}</span>
      </div>
      <p className="kit-muted">Submitted by {row.submitter_email || "unknown"}{row.approved_by ? ` · Approved by ${row.approved_by}` : ""}</p>
      <div className="kit-row">
        {row.status === "needs_approval" && <ActionButton action="approve_invoice" inputs={{ invoice_id: row.id }} label="Approve" variant="primary" />}
        {row.status === "needs_approval" && <ActionButton action="reject_invoice" inputs={{ invoice_id: row.id }} label="Reject" variant="danger" />}
        {(row.status === "ready_to_pay" || row.status === "partially_paid") && <ActionButton action="record_payment" inputs={{ invoice_id: row.id }} label="Record payment" />}
      </div>
      <h3>Payments</h3>
      <DataTable rows={mine as any} columns={[
        { key: "amount", format: "money" as const },
        { key: "paid_on", label: "Paid on", format: "date" as const },
        { key: "paid_by", label: "Paid by" },
      ]} empty="No payments yet." />
      <h3>History</h3>
      <History items={row._history} />
    </Modal>
  );
}

function VendorDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { row } = useRow("vendors", id);
  const invoices = useTable("invoices", { where: { vendor_id: id }, sort: "-created_at" });
  if (!row) return null;
  return (
    <Modal title={row.name} onClose={onClose}>
      <div className="kit-row">
        <StatusBadge value={row.status} />
        <span className="kit-muted">{row.contact_email}</span>
      </div>
      <h3>Invoices</h3>
      <DataTable rows={invoices.rows} columns={[
        { key: "invoice_number", label: "Invoice" },
        { key: "amount", format: "money" as const },
        { key: "amount_paid", label: "Paid", format: "money" as const },
        { key: "status", format: "status" as const },
      ]} loading={invoices.loading} empty="No invoices for this vendor." />
      <h3>History</h3>
      <History items={row._history} />
    </Modal>
  );
}
```
