import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useIdentity, SYSTEM_ADMIN } from "./app/identity";
import { Field, Modal, cx } from "./components/ui";
import { Catalog } from "./pages/Catalog";
import { Upload } from "./pages/Upload";
import { DatasetDetail } from "./pages/DatasetDetail";
import { Storage } from "./pages/Storage";
import { Admin } from "./pages/Admin";

export function App() {
  return (
    <div className="app">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <Sidebar />
      <div className="main">
        <TopBar />
        <main id="main-content" className="content" tabIndex={-1}>
          <Routes>
            <Route path="/" element={<Catalog />} />
            <Route path="/upload" element={<Upload />} />
            <Route path="/storage" element={<Storage />} />
            <Route path="/admin" element={<Admin />} />
            <Route path="/datasets/:id" element={<DatasetDetail />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function Sidebar() {
  return (
    <nav className="sidebar" aria-label="Primary">
      <div className="brand">
        <div className="brand-mark" />
        <div className="brand-name">Analytics Studio</div>
      </div>
      <div className="nav-section">Workspace</div>
      <NavLink to="/" end className={({ isActive }) => cx("nav-link", isActive && "active")}>◈ Catalog</NavLink>
      <NavLink to="/upload" className={({ isActive }) => cx("nav-link", isActive && "active")}>↑ Upload</NavLink>
      <NavLink to="/storage" className={({ isActive }) => cx("nav-link", isActive && "active")}>▤ Storage</NavLink>
      <div className="nav-section">Administration</div>
      <NavLink to="/admin" className={({ isActive }) => cx("nav-link", isActive && "active")}>⚙ Admin</NavLink>
      <div className="spacer" />
      <div className="small muted" style={{ padding: "8px" }}>Analytics Service · reference UI</div>
    </nav>
  );
}

function TopBar() {
  const { identity } = useIdentity();
  const [showId, setShowId] = useState(false);
  const [theme, setTheme] = useState<string>(document.documentElement.getAttribute("data-theme") || "light");
  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("analytics.theme", next);
    setTheme(next);
  };
  return (
    <div className="topbar">
      <div className="spacer" />
      <button className="btn btn-sm" onClick={() => setShowId(true)} title="Switch identity">
        ● {identity.label || identity.userId.slice(0, 8)}
      </button>
      <button className="icon-btn" onClick={toggleTheme} title="Toggle theme" aria-label="Toggle theme">
        {theme === "dark" ? "☀" : "☾"}
      </button>
      {showId && <IdentityModal onClose={() => setShowId(false)} />}
    </div>
  );
}

function IdentityModal({ onClose }: { onClose: () => void }) {
  const { identity, setAs } = useIdentity();
  const [userId, setUserId] = useState(identity.userId);
  const [teamId, setTeamId] = useState(identity.teamId || "");
  const [label, setLabel] = useState(identity.label || "");
  const [labelEdited, setLabelEdited] = useState(false);
  const save = () => {
    const uid = userId.trim();
    // Don't carry the previous seat's label onto a different user — the top bar
    // would keep saying "System (admin)" while every request went out as the new
    // user. Only keep a label the user actually typed for this seat.
    const keepLabel = labelEdited || uid === identity.userId;
    setAs({ userId: uid, teamId: teamId.trim() || null, label: (keepLabel && label.trim()) || undefined });
    onClose();
  };
  return (
    <Modal
      title="Act as"
      onClose={onClose}
      footer={<>
        <button className="btn" onClick={() => { setAs(SYSTEM_ADMIN); onClose(); }}>Reset to admin</button>
        <button className="btn btn-primary" onClick={save}>Apply</button>
      </>}
    >
      <p className="small muted" style={{ marginTop: 0 }}>
        The service authenticates by <code>X-User-Id</code> / <code>X-Team-Id</code> headers. Switch seats to see RBAC and column masking from a viewer or another team.
      </p>
      <div className="col mt-16">
        <Field label="User ID"><input className="input mono" value={userId} onChange={(e) => setUserId(e.target.value)} /></Field>
        <Field label="Team ID (optional)"><input className="input mono" value={teamId} onChange={(e) => setTeamId(e.target.value)} placeholder="defaults to the user's team" /></Field>
        <Field label="Label"><input className="input" value={label} onChange={(e) => { setLabel(e.target.value); setLabelEdited(true); }} placeholder="defaults to the user id" /></Field>
      </div>
    </Modal>
  );
}
