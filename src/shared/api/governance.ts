// U2: governance (P18 teams and members, P19 /authz/check, P20 PII tags, P21 contracts, certifications and shares,
// P24 legal holds).
import { ApiError, request } from './client'

const e = encodeURIComponent
const json = <T,>(path: string, method: string, body: unknown, timeoutMs = 30_000) =>
  request<T>(path, { method, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs })

export type Role = 'viewer' | 'member' | 'maintainer' | 'owner' | 'risk_reviewer'
export type Team = { id: string; slug: string; name: string; description: string; state: string; policy: Record<string, unknown>; created_at: string; created_by: string
  n_members: number; n_owners: number; n_projects: number; my_role: string | null }
export type Member = { principal_id: string; display_name: string; identity_key: string; kind: string; email: string | null; is_admin: boolean; role: Role; source: string; added_by: string; added_at: string }

/** P19: what the API would decide (to grey out a button). It changes nothing. */
export type AuthzDecision = { action: string; allow: boolean; reason?: string; needed?: string | null; role?: string | null; team?: string | null; mode: string
  principal?: { id: string; display_name: string } | null; object_kind?: string; object_id?: string }

export const teamsApi = {
  list: (all = false) => request<{ teams: Team[] }>(`/teams${all ? '?all=true' : ''}`).then((r) => r.teams),
  get: (t: string) => request<Team & { members?: Member[] }>(`/teams/${e(t)}`),
  create: (name: string, by: string, description = '') => json<Team>('/teams', 'POST', { name, by, description }),
  update: (t: string, body: { name?: string; description?: string; policy?: Record<string, unknown> }, by: string) => json<Team>(`/teams/${e(t)}`, 'PATCH', { ...body, by }),
  members: (t: string) => request<{ team_id: string; members: Member[] }>(`/teams/${e(t)}/members`).then((r) => r.members),
  addMember: (t: string, name: string, role: Role, by: string) => json<{ team_id: string; members: Member[] }>(`/teams/${e(t)}/members`, 'POST', { name, role, by }),
  setRole: (t: string, principal: string, role: Role, by: string) => json<{ team_id: string; members: Member[] }>(`/teams/${e(t)}/members/${e(principal)}`, 'PATCH', { role, by }),
  remove: (t: string, principal: string, by: string) => request<{ team_id: string; members: Member[] }>(`/teams/${e(t)}/members/${e(principal)}?by=${e(by)}`, { method: 'DELETE' }),
}

export const authz = {
  check: (action: string, object_kind?: string | null, object_id?: string | null) =>
    json<AuthzDecision>('/authz/check', 'POST', { action, ...(object_kind ? { object_kind } : {}), ...(object_id ? { object_id } : {}) }),
}

// P20: PII tags per (root, table, column). Suggested tags mask like confirmed ones until a maintainer reviews them.
export type PiiTag = { column: string; tag: 'pii_direct' | 'pii_quasi' | 'protected' | 'sensitive' | 'key' | 'none'; mask: 'redact' | 'hash' | 'last4' | 'year_only' | 'bucket' | 'none'
  source: 'suggested' | 'confirmed'; set_by: string; set_at: string }
export type PiiTags = { dataset_id: string; root_id: string; table: string; tags: PiiTag[]; masked_columns: string[]; pending_review: string[]; note?: string }
export const PII_TAGS = ['pii_direct', 'pii_quasi', 'sensitive', 'protected', 'key', 'none'] as const
export const PII_MASKS = ['redact', 'hash', 'last4', 'year_only', 'bucket', 'none'] as const
export const pii = {
  get: (v: string, table?: string | null) => request<PiiTags>(`/datasets/${e(v)}/pii${table ? `?table=${e(table)}` : ''}`),
  set: (v: string, table: string | null, columns: { column: string; tag: string; mask?: string }[], by: string) =>
    json<PiiTags & { changed?: unknown[] }>(`/datasets/${e(v)}/pii`, 'PUT', { columns, by, ...(table ? { table } : {}) }),
  confirmSuggested: (v: string, table: string | null, by: string) => json<PiiTags>(`/datasets/${e(v)}/pii`, 'PUT', { confirm: 'suggested', by, ...(table ? { table } : {}) }),
}

// P21: contracts (versioned per root), certifications, shares.
export type ContractRule = { table?: string; column?: string; rule: string; status: 'pass' | 'warn' | 'block'; severity?: string; message?: string; n?: number; [k: string]: unknown }
export type ContractResult = { status: 'pass' | 'warn' | 'block'; n_rules: number; n_blocks: number; n_warnings: number; rules: ContractRule[]; contract_id?: string; version?: number }
export type Contract = { id: string; root_id: string; version: number; spec: Record<string, unknown>; active?: boolean | number; created_by?: string; created_at?: string; reason?: string }
export const contracts = {
  get: (v: string) => request<{ root_id: string; active: Contract | null; versions: Contract[] }>(`/datasets/${e(v)}/contract`),
  put: (v: string, spec: Record<string, unknown>, by: string, reason = '') => json<Contract>(`/datasets/${e(v)}/contract`, 'PUT', { spec, by, reason }),
  /** Freeze what this version looks like: every table required, its columns and kinds, its keys and relations. */
  freeze: (root: string, v: string, by: string, opts: Record<string, string> = {}) => json<Contract & { result: ContractResult }>(`/datasets/${e(root)}/contract/from-version/${e(v)}`, 'POST', { by, ...opts }, 120_000),
  check: (v: string) => json<ContractResult & { version_id: string }>(`/datasets/${e(v)}/contract/check`, 'POST', {}, 120_000),
}

export type Certification = { id: string; root_id: string; version_id: string; level: 'certified' | 'approved_for_model'; status: string; contract_id: string | null
  requested_by: string; requested_at: string; reason: string; decided_by: string | null; decided_at: string | null; comment: string; expires_at: string | null }
export const certifications = {
  list: (v: string) => request<{ root_id: string; certifications: Certification[]; live: Certification[] }>(`/datasets/${e(v)}/certifications`),
  request: (v: string, level: Certification['level'], reason: string, by: string) => json<Certification>(`/datasets/${e(v)}/certifications`, 'POST', { version_id: v, level, reason, by }, 120_000),
  decide: (id: string, approve: boolean, comment: string, by: string) => json<Certification>(`/certifications/${e(id)}/decide`, 'POST', { approve, comment, by }, 120_000),
}

export type Share = { id: string; object_kind: 'dataset' | 'project'; object_id: string; owner_team: string; grantee_team: string; scope: 'certified' | 'default' | 'pinned' | 'all'
  pinned_version: string | null; pii: 'masked' | 'unmasked'; status: 'pending' | 'active' | 'rejected' | 'revoked' | 'expired'; requested_by: string; requested_at: string; reason: string
  decided_by: string | null; decided_at: string | null; expires_at: string | null; revoked_by: string | null; revoked_at: string | null; runs_kept?: unknown[] }
export const shares = {
  list: (q: { object_id?: string; team?: string; status?: string } = {}) => {
    const p = new URLSearchParams(Object.entries(q).filter(([, v]) => v) as [string, string][])
    return request<{ shares: Share[]; total: number }>(`/shares${p.toString() ? `?${p}` : ''}`).then((r) => r.shares)
  },
  create: (b: { object_id: string; grantee_team: string; scope: Share['scope']; pii: Share['pii']; reason: string; pinned_version?: string; expires_at?: string }, by: string) =>
    json<Share>('/shares', 'POST', { ...b, by }),
  /** A pii 'unmasked' share is approved only with grant_unmasked: true (else 400 grant_unmasked_required). */
  decide: (id: string, approve: boolean, by: string, comment = '', grant_unmasked = false) =>
    json<Share>(`/shares/${e(id)}/decide`, 'POST', { approve, comment, by, ...(grant_unmasked ? { grant_unmasked: true } : {}) }),
  revoke: (id: string, reason: string, by: string) => json<Share>(`/shares/${e(id)}/revoke`, 'POST', { reason, by }),
}

// P24: legal holds (catalog/holds.py). Placing and releasing are a platform admin's (`legal_hold.place` /
// `legal_hold.release`); the release is four eyes (409 `four_eyes` for the placer). A hold has no end date.
export type LegalHold = { id: string; scope_kind: 'team' | 'project' | 'dataset' | 'version' | 'run' | 'model'; scope_id: string; case_ref: string; reason: string
  placed_by: string; placed_at: string; released_by: string | null; released_at: string | null; active: boolean; team_id?: string | null; label?: string | null }
export const holds = {
  list: (q: { team?: string; include_released?: boolean } = {}) =>
    request<{ holds: LegalHold[]; n_active: number }>(`/legal-holds?${new URLSearchParams({ ...(q.team ? { team: q.team } : {}), ...(q.include_released ? { include_released: 'true' } : {}) })}`),
  place: (b: { scope_kind: LegalHold['scope_kind']; scope_id: string; case_ref: string; reason: string }, by: string) => json<LegalHold>('/legal-holds', 'POST', { ...b, by }),
  release: (id: string, reason: string, by: string) => json<LegalHold>(`/legal-holds/${e(id)}/release`, 'POST', { reason, by }),
}
export const notServed = (err: unknown) => err instanceof ApiError && (err.status === 404 || err.status === 405)

/** The structured refusal's code and message, for a readable line. */
export const refusal = (err: unknown): { code?: string; message: string } => {
  if (err instanceof ApiError) {
    const d = err.detail as { code?: string; message?: string } | string | undefined
    if (d && typeof d === 'object') return { code: d.code, message: d.message ?? err.message }
    return { message: err.message }
  }
  return { message: (err as Error).message }
}
