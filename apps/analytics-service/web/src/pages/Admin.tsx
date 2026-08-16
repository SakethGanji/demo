import { useState } from "react";
import { Tabs } from "../components/ui";
import { TeamsTab } from "./admin/Teams";
import { WebhooksTab } from "./admin/Webhooks";
import { AuditTab } from "./admin/Audit";
import { StorageTab } from "./admin/Storage";

const TABS = [
  { id: "teams", label: "Teams & members" },
  { id: "webhooks", label: "Webhooks" },
  { id: "audit", label: "Audit log" },
  { id: "storage", label: "Storage & retention" },
];

export function Admin() {
  const [tab, setTab] = useState("teams");
  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">Administration</h1>
          <div className="page-sub">Teams, webhook subscriptions, the audit trail, and storage housekeeping.</div>
        </div>
      </div>

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      <div style={{ marginTop: 18 }}>
        {tab === "teams" && <TeamsTab />}
        {tab === "webhooks" && <WebhooksTab />}
        {tab === "audit" && <AuditTab />}
        {tab === "storage" && <StorageTab />}
      </div>
    </div>
  );
}
