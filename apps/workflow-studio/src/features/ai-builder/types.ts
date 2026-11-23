/**
 * Wire types of the Stage 6 AI builder (DESIGN-STAGE6-AI-BUILDER §3, §4, as the engine really answers: services/ai_builder.py).
 * Where the engine and the design differ, the engine's shape is the one written here.
 */
import type { TestCase, TestRunReport, Scorecard } from '@/features/workflow-editor/lib/tests';

export type ProposalStatus =
  | 'clarify' | 'spec_draft' | 'spec_approved' | 'building' | 'ready' | 'failed' | 'kept' | 'undone' | 'discarded' | 'answered';
export type ProposalKind = 'build' | 'modify' | 'node_assist' | 'chat' | 'explain';

export type TriggerKind = 'form' | 'webhook' | 'cron' | 'manual' | 'chat' | 'execute';

export interface TriggerField {
  name: string;
  type: string;
  required: boolean;
  label?: string;
}

export interface SpecRule { id: string; text: string }

export interface SpecStep {
  name: string;
  node_type: string;
  purpose: string;
  rules: string[];
  api_ref?: string;
}

export interface SpecInterface { kind: 'form' | 'chat'; slug: string; title: string }

/** The engine adds `level` (error | warning); the design lists only path, code and message. */
export interface SpecIssue {
  path: string;
  code: string;
  message: string;
  level?: 'error' | 'warning';
}

export interface Spec {
  spec_version: number;
  title: string;
  summary: string;
  trigger: { kind: TriggerKind | ''; fields: TriggerField[] };
  rules: SpecRule[];
  steps: SpecStep[];
  interface: SpecInterface | null;
  tests: TestCase[];
  rule_tests: Record<string, string[]>;
  assumptions: string[];
  questions: string[];
  /** read-only: the server stamps these on every response; they are not sent back */
  issues?: SpecIssue[];
  warnings?: string[];
}

export interface DiffNodeChange { name: string; fields: string[] }

/** §4.9 */
export interface ProposalDiff {
  nodes: { added: string[]; removed: string[]; changed: DiffNodeChange[] };
  connections: { added: string[][]; removed: string[][] };
  tests: { added: string[]; changed: string[]; removed: string[] };
  interface: { changed: boolean };
  new_destinations: string[];
  new_code: string[];
  counts: { added: number; removed: number; changed: number };
}

export interface CheckerFinding {
  code: string;
  severity: 'error' | 'warning' | 'info';
  message: string;
  node: string | null;
  param?: string | null;
  path?: string | null;
}

export interface ReportRound {
  round: number;
  ops: number;
  rejected: number;
  auto_fixed: number;
  errors: number;
  warnings: number;
  tests: { passed: number; failed: number };
  notes?: string;
}

export type StoppedBecause = 'passed' | 'rounds_exhausted' | 'deadline' | 'call_budget' | 'no_progress' | 'ai_failed';

/** §4.10; `warnings`, `mock_warnings` and `ai_error` are extra fields the engine writes. */
export interface ProposalReport {
  passed: boolean;
  rounds: ReportRound[];
  best_round: number;
  checker: { errors: number; warnings: number; findings: CheckerFinding[] };
  tests: TestRunReport | null;
  spec_coverage: { steps_missing?: string[]; steps_wrong_type?: string[]; rules_untested?: string[] };
  grounding: Array<{ code: string; node: string | null; message: string }>;
  score: Scorecard | null;
  stopped_because: StoppedBecause;
  warnings?: string[];
  mock_warnings?: Array<{ node?: string; message?: string; [k: string]: unknown }>;
  ai_error?: { code: string; message: string };
}

export interface TestChangeRequest {
  test_id: string;
  reason: string;
  proposed: unknown;
  round?: number;
}

export interface ProposalUsage {
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  duration_ms: number;
}

/** The built definition, in the engine's workflow-definition shape. */
export interface ProposedDefinition {
  name?: string;
  description?: string;
  nodes: ProposedNode[];
  connections: ProposedConnection[];
  tests?: TestCase[];
  interface?: unknown;
  [k: string]: unknown;
}
export interface ProposedNode {
  name: string;
  type: string;
  parameters?: Record<string, unknown>;
  position?: { x: number; y: number };
  [k: string]: unknown;
}
export interface ProposedConnection {
  source_node: string;
  source_output: string;
  target_node: string;
  target_input: string;
  [k: string]: unknown;
}

// ---------------------------------------------------------------- understanding (CONTRACTS-CAPGRAPH §3, §4.1; the engine's kg_understand)

export type ConceptSource = 'lexical' | 'llm' | 'implied';

export interface UnderstandingConcept {
  key: string;
  label: string;
  /** only used to order chips: never shown */
  score: number;
  source: ConceptSource;
  evidence: string;
  confirmed: boolean;
}

export type QuestionKind = 'role' | 'duration' | 'cron' | 'field' | 'choice' | 'text' | 'api_capability';

export interface UnderstandingQuestion {
  /** `q_<concept slug>.<slot>`; `q_shape` is the optional app-or-workflow choice */
  id: string;
  concept: string;
  slot: string;
  kind: QuestionKind;
  text: string;
  options: string[];
  required: boolean;
  answer: unknown;
  multiple?: boolean;
  /** role questions: {role key: label} */
  option_labels?: Record<string, string>;
}

export interface UnderstandingSlot { value: unknown; from: 'request' | 'llm' | 'answer' | 'default' | 'pattern' }

export interface RetrievalSubworkflow { key: string; label: string; why: string; concepts?: string[] }
export interface RetrievalTemplate { key: string; label: string; why: string }
export interface RetrievalPattern { key: string; label: string; validated_count: number; source_project: string | null; adapt: boolean }
export interface RetrievalRole { key: string; label: string; used_for: string[]; apps: number }

export interface Retrieval {
  subworkflows: RetrievalSubworkflow[];
  templates: RetrievalTemplate[];
  patterns: RetrievalPattern[];
  apis: Array<{ key: string; why: string }>;
  node_types: string[];
  roles: RetrievalRole[];
}

export interface UnderstandingSummary {
  understood: string[];
  reusing: { subworkflows: number; templates: number; patterns: number };
  questions: number;
}

export interface Understanding {
  v: number;
  concepts: UnderstandingConcept[];
  slots: Record<string, UnderstandingSlot>;
  questions: UnderstandingQuestion[];
  assumptions: string[];
  retrieval: Retrieval;
  shape: 'workflow' | 'app';
  adapt_from: { pattern: string; label: string } | null;
  summary: UnderstandingSummary;
  warnings: string[];
}

/** ProposalView (§4.8). `base_definition` is the draft the proposal started from (null for a new workflow); `model` lives under `policy`. */
export interface Proposal {
  id: string;
  project_id: string;
  team_id: string | null;
  workflow_id: string | null;
  parent_id: string | null;
  kind: ProposalKind;
  status: ProposalStatus;
  actor: string;
  request: string;
  request_sha256: string;
  spec: Spec | null;
  spec_approved_by: string | null;
  spec_approved_at: string | null;
  spec_fingerprint: string | null;
  base_definition: ProposedDefinition | null;
  base_draft_hash: string | null;
  proposed_definition: ProposedDefinition | null;
  ops_log: unknown[];
  diff: ProposalDiff | null;
  report: ProposalReport | null;
  test_change_requests: TestChangeRequest[];
  answer: string | null;
  error_code: string | null;
  error_message: string | null;
  kept_by: string | null;
  kept_at: string | null;
  kept_draft_hash: string | null;
  accepted_failing: boolean;
  created_at: string;
  updated_at: string;
  usage: ProposalUsage;
  policy: { model: string | null; provider: string | null; region: string | null };
  /** set when the request was grounded in the knowledge graph (`use_graph`) */
  understanding?: Understanding | null;
  app_build_id?: string | null;
  part_key?: string | null;
}

export interface ProposalSummary {
  id: string;
  kind: ProposalKind;
  status: ProposalStatus;
  actor: string;
  created_at: string;
  model: string | null;
  llm_calls: number;
  cost_usd: number | null;
  passed: boolean | null;
  workflow_id: string | null;
}

export interface ProposalList { items: ProposalSummary[]; next_cursor: string | null }

/** §2.2 event names the progress view reads. */
export interface AiEvent {
  seq: number;
  at: string;
  event: string;
  payload: Record<string, unknown>;
  actor: string | null;
}
export interface AiEventList { items: AiEvent[]; last_seq: number }

export interface BuilderConfig {
  model: string;
  provider: string;
  configured: boolean;
  allowed_models: string[];
  region: string | null;
  send_run_values: boolean;
  prices_configured: boolean;
  budgets: Record<string, unknown>;
}

export interface KeepResult { workflow_id: string; draft_hash: string; proposal: Proposal }

export type NodeAiTask = 'fix_error' | 'write_code' | 'write_expression' | 'explain';
export interface NodeAiExplain { answer: string; proposal_id: string }
