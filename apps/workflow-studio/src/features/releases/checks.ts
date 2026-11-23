import type { ReleaseCheck } from "@/shared/api/types";

const ORDER = [
  "secrets_present",
  "app_calls_resolve",
  "handlers_exist",
  "modules_parse",
  "stub_routes",
  "breaking_vs_active",
];

/** The four-step release maps the dry-run checks onto its first three steps. */
export const CHECK_GROUPS = {
  code: ["modules_parse", "handlers_exist", "app_calls_resolve"],
  secrets: ["secrets_present"],
  routes: ["stub_routes", "breaking_vs_active"],
} as const;
export type CheckGroup = keyof typeof CHECK_GROUPS;

export function groupChecks(
  checks: ReleaseCheck[],
  g: CheckGroup,
): ReleaseCheck[] {
  const ids = CHECK_GROUPS[g] as readonly string[];
  return checks.filter((c) =>
    g === "code"
      ? ids.includes(c.id) ||
        !Object.values(CHECK_GROUPS)
          .flat()
          .includes(c.id as never)
      : ids.includes(c.id),
  );
}

export function groupStatus(checks: ReleaseCheck[]): "fail" | "warn" | "pass" {
  return checks.some((c) => c.status === "fail")
    ? "fail"
    : checks.some((c) => c.status === "warn")
      ? "warn"
      : "pass";
}

export function sortChecks(checks: ReleaseCheck[]): ReleaseCheck[] {
  const rank = (c: ReleaseCheck) =>
    (c.status === "fail" ? 0 : c.status === "warn" ? 1 : 2) * 10 +
    Math.max(0, ORDER.indexOf(c.id));
  return [...checks].sort((a, b) => rank(a) - rank(b));
}

export interface SubworkflowEdge {
  parent_workflow_id: string
  parent_version_id: number
  node: string
  child_workflow_id: string
  child_version_id: number | null
  child_version_number: number | null
  spec: string
  error?: string
  cycle?: boolean
  pin_node?: string
}

export function edgesOf(release: { snapshot?: Record<string, unknown> } | undefined): SubworkflowEdge[] {
  const e = release?.snapshot?.subworkflows
  return Array.isArray(e) ? (e as SubworkflowEdge[]) : []
}
