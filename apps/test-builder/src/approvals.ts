// Explore vs. approved policy split.
// During exploration, forbiddenRequests are always aborted - the agent must never move money or delete.
// A human then reviews a generated/edited spec and runs `tb approve <spec>`, which records the spec's
// sha256 in approvals.json. At RUN time (and only there), the fixture relaxes forbiddenRequests for a
// spec whose current content hash is approved. The host allowlist is NEVER relaxed. This keeps the
// dangerous-operation gate on exploration while letting a vetted test exercise a real mutation.
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

export interface ApprovalRecord { spec: string; sha256: string; approvedAt: string; note?: string }
export interface ApprovalsFile { schema: 'test-builder/approvals@1'; approvals: Record<string, ApprovalRecord> }

export function approvalsPath(root: string): string {
  return process.env.TB_APPROVALS ?? join(root, 'approvals.json');
}

export const specHash = (specPath: string): string => createHash('sha256').update(readFileSync(resolve(specPath))).digest('hex');

export function loadApprovals(root: string): ApprovalsFile {
  const p = approvalsPath(root);
  if (!existsSync(p)) return { schema: 'test-builder/approvals@1', approvals: {} };
  return JSON.parse(readFileSync(p, 'utf8')) as ApprovalsFile;
}

export function approve(specPath: string, root: string, note?: string): ApprovalRecord {
  const file = loadApprovals(root);
  const sha = specHash(specPath);
  const rec: ApprovalRecord = { spec: resolve(specPath), sha256: sha, approvedAt: new Date().toISOString(), note };
  file.approvals[sha] = rec;
  const p = approvalsPath(root);
  mkdirSync(dirname(p), { recursive: true });
  writeFileSync(p, JSON.stringify(file, null, 2));
  return rec;
}

/** True iff the file's CURRENT content hash is recorded as approved (edit it and approval lapses). */
export function isApproved(specPath: string, root: string): boolean {
  const file = loadApprovals(root);
  try { return specHash(specPath) in file.approvals; } catch { return false; }
}
