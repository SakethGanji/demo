// Shared bits for the Admin console tabs.
import type { ReactNode } from "react";
import { ApiError } from "../../api/client";
import { Modal } from "../../components/ui";

// team_members.role — see app/features/auth/permissions.py (owner > admin > editor > viewer).
export const ROLES = ["owner", "admin", "editor", "viewer"] as const;
export type RoleName = (typeof ROLES)[number];

// Webhook lifecycle events — app/features/webhooks/schemas.py EVENT_TYPES.
export const EVENT_TYPES = [
  "validation.passed",
  "validation.failed",
  "tag.promoted",
  "tag.rolled_back",
  "version.ready",
  "transformation.completed",
  "dataset.published",
] as const;

/** Human-readable message for a caught error, carrying the problem+json code. */
export function errText(e: unknown): string {
  if (e instanceof ApiError) return `${e.detail}${e.code && e.code !== "error" ? ` (${e.code})` : ""}`;
  return String((e as Error)?.message || e);
}

/** A small confirm dialog built on the shared Modal. */
export function ConfirmModal({
  title, body, confirmLabel = "Confirm", danger, busy, onConfirm, onClose,
}: {
  title: string; body: ReactNode; confirmLabel?: string; danger?: boolean;
  busy?: boolean; onConfirm: () => void; onClose: () => void;
}) {
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button className={danger ? "btn btn-danger" : "btn btn-primary"} onClick={onConfirm} disabled={busy}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </>
      }
    >
      {body}
    </Modal>
  );
}
