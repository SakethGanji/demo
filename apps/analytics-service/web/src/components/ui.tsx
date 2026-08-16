// Shared UI primitives. Pages compose these; styling comes from index.css classes.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Spinner() {
  return <span className="spinner" aria-label="Loading" />;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="center" style={{ padding: 48, gap: 10 }} role="status" aria-live="polite">
      <Spinner />
      <span className="muted small">{label}</span>
    </div>
  );
}

export function EmptyState({ icon = "◇", title, hint, action }: { icon?: string; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <div style={{ fontWeight: 600, color: "var(--text-secondary)" }}>{title}</div>
      {hint && <div className="small mt-8">{hint}</div>}
      {action && <div className="mt-16">{action}</div>}
    </div>
  );
}

export function Card({ title, actions, children, pad = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; pad?: boolean }) {
  return (
    <div className="card">
      {title && (
        <div className="card-head">
          {typeof title === "string" ? <h3>{title}</h3> : title}
          {actions && <div className="row gap-6">{actions}</div>}
        </div>
      )}
      <div className={pad ? "card-pad" : undefined}>{children}</div>
    </div>
  );
}

export function StatTile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub != null && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

type BadgeKind = "good" | "warning" | "serious" | "critical" | "accent" | "neutral";
export function Badge({ kind = "neutral", children }: { kind?: BadgeKind; children: ReactNode }) {
  return <span className={cx("badge", kind !== "neutral" && kind)}>{children}</span>;
}

// Maps a health/status word to a badge kind.
export function statusKind(status?: string): BadgeKind {
  switch ((status || "").toLowerCase()) {
    case "ok": case "good": case "ready": case "completed": case "passed": return "good";
    case "warning": case "warn": case "running": case "pending": return "warning";
    case "serious": case "degraded": return "serious";
    case "critical": case "failed": case "error": return "critical";
    default: return "neutral";
  }
}

export function ErrorBanner({ error }: { error: unknown }) {
  const msg = error instanceof ApiError ? `${error.detail}${error.code ? ` (${error.code})` : ""}` : String((error as Error)?.message || error);
  return <div className="banner error" role="alert"><span>⚠</span><span className="wrap-anywhere">{msg}</span></div>;
}

/* ---- Tabs ---- */
export function Tabs({ tabs, active, onChange }: { tabs: { id: string; label: ReactNode }[]; active: string; onChange: (id: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={active === t.id} className={cx("tab", active === t.id && "active")} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ---- Modal ---- */
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
let modalSeq = 0;

export function Modal({ title, onClose, children, footer, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useRef(`modal-title-${++modalSeq}`).current;

  useEffect(() => {
    // Full focus management: previously the dialog only bound Escape, so focus
    // never entered it, one Tab escaped to the page behind, and on close focus
    // could land on <body> — a keyboard user restarted from the top of the doc.
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const focusables = () => Array.from(node?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    (focusables()[0] ?? node)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { onClose(); return; }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) { e.preventDefault(); return; }
      const first = items[0], last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      // Wrap at both ends, and pull focus back in if it has escaped.
      if (e.shiftKey && (active === first || !node?.contains(active))) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (active === last || !node?.contains(active))) {
        e.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";   // stop the page behind scrolling
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      previouslyFocused?.focus?.();            // restore focus to the trigger
    };
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div ref={ref} tabIndex={-1} className={cx("modal", wide && "wide")} onClick={(e) => e.stopPropagation()}
        role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="modal-head">
          {typeof title === "string" ? <h3 id={titleId}>{title}</h3> : <div id={titleId}>{title}</div>}
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/* ---- Toasts ---- */
interface Toast { id: number; kind: "good" | "error" | "info"; title: string; msg?: string; }
const ToastCtx = createContext<(t: Omit<Toast, "id">) => void>(() => {});
export function useToast() { return useContext(ToastCtx); }

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.floor(performance.now());
    setToasts((cur) => [...cur, { ...t, id }]);
    setTimeout(() => setToasts((cur) => cur.filter((x) => x.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite" aria-atomic="false">
        {toasts.map((t) => (
          <div key={t.id} className={cx("toast", t.kind)}>
            <div className="toast-title">{t.title}</div>
            {t.msg && <div className="toast-msg wrap-anywhere">{t.msg}</div>}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ---- Data fetching hook ---- */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: unknown; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fnRef.current().then(
      (d) => { if (alive) { setData(d); setLoading(false); } },
      (e) => { if (alive) { setError(e); setLoading(false); } },
    );
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);
  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}

/* ---- Async view wrapper: handles loading/error/empty uniformly ---- */
export function AsyncView<T>({ state, children, empty }: { state: { data: T | null; error: unknown; loading: boolean }; children: (data: T) => ReactNode; empty?: ReactNode }) {
  if (state.loading && state.data === null) return <Loading />;
  if (state.error) return <ErrorBanner error={state.error} />;
  if (state.data === null) return <>{empty ?? <EmptyState title="Nothing here yet" />}</>;
  return <>{children(state.data)}</>;
}

/* ---- Simple field wrappers ---- */
export function Field({ label, children }: { label: string; children: ReactNode }) {
  // The control is NESTED inside the <label>, which associates the two without
  // threading an id through ~80 call sites. Previously the label was a sibling,
  // so every input announced as "(no name)" — or leaked its placeholder, which
  // disappears as soon as the user types.
  return <label className="field"><span className="field-label">{label}</span>{children}</label>;
}

/* ---- Cell rendering for tabular data (masking + null aware) ---- */
export function Cell({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="cell-null">—</span>;
  if (value === "***" || (typeof value === "string" && value.includes("***"))) return <span className="cell-masked">{String(value)}</span>;
  return <>{String(value)}</>;
}

export function fmtNum(n: unknown): string {
  if (n === null || n === undefined || n === "") return "—";
  const v = typeof n === "number" ? n : Number(n);
  if (Number.isNaN(v)) return String(n);
  return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

export function fmtBytes(n: unknown): string {
  const v = Number(n);
  if (!v || Number.isNaN(v)) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, x = v;
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(x < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

export function fmtDate(s: unknown): string {
  if (!s) return "—";
  const d = new Date(String(s));
  if (Number.isNaN(d.getTime())) return String(s);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
