/**
 * Wire types of the AI app composer (CONTRACTS-CAPGRAPH §5.1, §5.2; the engine's `services/app_composer.view`).
 * Where the engine and the contract differ, the engine's shape is the one written here and the difference is noted.
 * Every route answers the bare view (no `{app}` wrapper).
 */
import type { Understanding, UnderstandingQuestion } from '../types';

export type AppStatus = 'clarify' | 'planned' | 'specs' | 'building' | 'ready' | 'kept' | 'discarded';

/** What one part of the app is doing. `pending` = no proposal yet; `reused` = an existing sub-workflow stands in for it (nothing is built). */
export type PartStatus =
  | 'pending' | 'spec_draft' | 'spec_approved' | 'building' | 'ready' | 'failed' | 'kept' | 'reused';

export type PartRole = 'entry' | 'subworkflow';
export type PartTrigger = 'form' | 'cron' | 'webhook' | 'execute' | 'manual' | 'chat';

export interface PlanColumn { name: string; type: string }
export interface PlanTable { key: string; name: string; columns: PlanColumn[] }

export interface PlanPart {
  key: string;
  role: PartRole;
  title: string;
  trigger: PartTrigger | string;
  concepts: string[];
  depends_on: string[];
  cron?: string | null;
  interface?: { kind: string; slug: string; screen?: string } | null;
  tables?: string[];
  reuse?: { capability: string; label?: string } | null;
}

/** One slot that differs between the validated pattern and this request. */
export interface SlotDiff { slot: string; from: unknown; to: unknown }

export interface AdaptedFrom {
  pattern: string;
  label: string;
  fit?: number | null;
  validated_count?: number | null;
  source_project?: string | null;
  /** only when the engine sends it (the contract's banner shows "21 tests") */
  tests?: number | null;
  diff?: SlotDiff[];
}

export interface AppPlan {
  v: number;
  title: string;
  entity?: string;
  adapted_from: AdaptedFrom | null;
  tables: PlanTable[];
  parts: PlanPart[];
  screens?: string[];
}

export interface PlanIssue { code: string; path?: string; message: string; severity?: string }

export interface PartView {
  key: string;
  title: string;
  role: PartRole;
  trigger: string;
  concepts: string[];
  depends_on: string[];
  interface: { kind: string; slug: string; screen?: string } | null;
  cron: string | null;
  reuse: { capability: string; label?: string } | null;
  status: PartStatus;
  proposal_id: string | null;
  workflow_id: string | null;
  passed: boolean | null;
  accepted_failing: boolean;
  tests: { passed: number | null; total: number | null } | null;
  rounds: number | null;
  error: string | null;
}

/** Why Keep is not simply allowed: `not_ready` blocks it, `tests_failing` asks for "keep anyway", `no_plan` = nothing planned yet. */
export interface KeepBlocker { part_key?: string; status?: string; reason: 'not_ready' | 'tests_failing' | 'no_plan' | (string & {}) }

export interface InstalledInterface { slug: string; kind: string | null; path: string }

export interface Installed {
  install_id: string;
  workflows: Record<string, string>;
  tables: Record<string, { id: string; name: string | null }>;
  published: string[];
  drafts: string[];
  interfaces: Record<string, InstalledInterface>;
  renamed: string[];
  needs: unknown[];
}

export interface AppBuildView {
  id: string;
  project_id: string;
  status: AppStatus;
  actor: string;
  request: string;
  understanding: Understanding | null;
  summary: Understanding['summary'] | null;
  questions: UnderstandingQuestion[];
  plan: AppPlan | null;
  plan_issues: PlanIssue[];
  adapted_from: AdaptedFrom | null;
  parts: PartView[];
  next_part: string | null;
  can_keep: boolean;
  keep_blockers: KeepBlocker[];
  /** what Keep taught the knowledge graph (null before Keep, and on routes other than `keep`) */
  learned?: Record<string, unknown> | null;
  install_id: string | null;
  installed: Installed | null;
  kept_by: string | null;
  kept_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** `build-next` answers the view plus what it just built (null when nothing was left). */
export interface BuildNextResult extends AppBuildView {
  built: { part_key: string; proposal_id: string | null; status: PartStatus; passed: boolean | null } | null;
}

/** `keep` answers the view (with `learned`) plus the install's test run. */
export interface KeepResult extends AppBuildView {
  tests: unknown;
}

export interface AppListItem {
  id: string;
  status: AppStatus;
  actor: string;
  title: string | null;
  install_id: string | null;
  kept_by: string | null;
  kept_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}
