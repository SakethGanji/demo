/**
 * Governance reads: what is actually DECLARED, and therefore what is actually
 * masked.
 *
 * The single fact this file exists to get right: **masking is driven only by
 * column-level `sensitivity` in the data dictionary, and every entry is put
 * there by a human.** There is no scanner in the service. So the only honest
 * source for "is this column masked" is the dictionary itself.
 *
 * Two tempting shortcuts are both wrong, which is why neither is used here:
 *
 *  - `masked_columns` on a query/sheet/preview response is the masked set FOR
 *    THIS CALLER. An empty array means "nothing is declared" OR "you are admin,
 *    owner or superuser" and the two are indistinguishable, so reading it as a
 *    policy fact would report a governance hole as clean whenever an admin is
 *    the one looking.
 *  - Dataset `classification` is a catalog label that enforces nothing. It is
 *    read here only so the screen can show where a label and the declarations
 *    disagree — never as evidence that anything is protected.
 *
 * There is no dataset-wide dictionary route, so the scan fans out: one call per
 * dataset for its sheets, then one paged call per sheet for that sheet's
 * dictionary. That is why it is bounded by `MAX_SCANNED_DATASETS` and reports
 * what it did not reach rather than quietly ranking a fraction.
 *
 * Its sibling `useOps.ts` holds the audit log, object storage, webhooks and
 * team administration. Nothing in that file is a control, and the boundary is
 * worth keeping sharp: a retention rule removes nothing on its own (there is no
 * scheduler), a webhook subscription is an annotation, and a team role grants
 * access inside one team without deciding whether a value is masked. Column
 * `sensitivity` is still the only thing that masks, and tag promotion is still
 * the only quality gate.
 */

import { useQuery } from '@tanstack/react-query';
import { analytics, type Page } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import type { DatasetInfo } from './useDatasets';

/**
 * The levels that actually turn masking on, mirroring `SENSITIVE_LEVELS` in
 * `app/shared/masking.py`.
 *
 * `sensitivity` is free text with no server-side validation, and only these six
 * values mask. That is the trap this screen has to make visible: a column
 * declared `internal` — one of the field's OWN documented examples — is not
 * masked, and neither is a typo.
 */
export const SENSITIVE_LEVELS = [
  'confidential',
  'restricted',
  'pii',
  'sensitive',
  'secret',
  'phi',
] as const;

/** Matched exactly as the service matches it: trimmed, case-insensitive. */
export function isSensitiveLevel(level: string | null | undefined): boolean {
  if (!level) return false;
  return (SENSITIVE_LEVELS as readonly string[]).includes(level.trim().toLowerCase());
}

/**
 * How many datasets the scan will open. Each one costs 1 + (sheets) requests,
 * so this is a real bound rather than a formality — and whatever it does not
 * reach is reported as unreached, never silently dropped from the ranking.
 */
export const MAX_SCANNED_DATASETS = 20;

/** The API caps a page at 200; a very wide sheet's dictionary is paged. */
const PAGE_LIMIT = 200;

/** Hard stop on one sheet's dictionary, so a pathological sheet cannot hang the page. */
const MAX_DICT_ENTRIES = 1000;

interface DictionaryEntry {
  column_name: string;
  sensitivity?: string | null;
}

interface GovernanceSheet {
  sheet_key?: string | null;
  column_count?: number | null;
  columns?: { name: string; normalized_name?: string | null }[];
}

export interface DatasetGovernance {
  id: string;
  name: string;
  /** The catalog label. Enforces nothing; shown only to expose disagreement. */
  classification: string;
  /**
   * False when this dataset could not be opened from this seat. A cross-tenant
   * read is a 404 by design, so this never means "denied" — it means the seat
   * got no answer, and the UI must not name a reason it cannot know.
   */
  readable: boolean;
  /** Columns across every sheet scanned. */
  columns: number;
  /** Declared at a level that masks. */
  masked: number;
  /** Declared, but at a level that does NOT mask (`internal`, a typo, …). */
  declaredOpen: number;
  /** No declaration at all — never masked, whatever the values are. */
  undeclared: number;
  sheets: number;
}

export interface GovernanceScan {
  datasets: DatasetGovernance[];
  /** Datasets opened by this scan. */
  scanned: number;
  /** Datasets the catalog says exist for this seat. */
  total: number;
  /** False when the scan bound was hit before the catalog ran out. */
  complete: boolean;
  /**
   * Declared levels that do NOT mask, and how many columns wear each. This is
   * where a typo shows up: `pii ` masks, `pii-maybe` silently does not.
   */
  inertLevels: Record<string, number>;
}

function useSeat() {
  return useIdentityStore((s) => s.identity.userId);
}

/** One sheet's dictionary, paged to the end (or to the hard stop). */
async function fetchDictionary(datasetId: string, sheetKey: string): Promise<DictionaryEntry[]> {
  const out: DictionaryEntry[] = [];
  let offset = 0;

  for (;;) {
    const page = await analytics.get<Page<DictionaryEntry>>(
      `/datasets/${datasetId}/sheet-metadata/${encodeURIComponent(sheetKey)}/columns`,
      { limit: PAGE_LIMIT, offset },
    );
    out.push(...page.items);
    offset += PAGE_LIMIT;

    // A short page is the real end, whatever `total` claims.
    if (page.items.length < PAGE_LIMIT) break;
    if (out.length >= Math.min(page.total, MAX_DICT_ENTRIES)) break;
  }

  return out;
}

async function scanDataset(
  d: DatasetInfo,
  inertLevels: Record<string, number>,
): Promise<DatasetGovernance> {
  const base = {
    id: d.id,
    name: d.name,
    classification: d.classification ?? 'internal',
  };

  try {
    const sheets = await analytics.get<Page<GovernanceSheet>>(`/datasets/${d.id}/sheets`);

    let columns = 0;
    let masked = 0;
    let declaredOpen = 0;
    let undeclared = 0;

    for (const sheet of sheets.items) {
      const key = sheet.sheet_key;
      // Without a sheet key there is no dictionary route to ask, so every
      // column on it is undeclared as far as anyone can tell.
      const entries = key ? await fetchDictionary(d.id, key) : [];

      // The dictionary keys on the NORMALIZED column name; the sheet's column
      // list carries both, so look up by normalized first and fall back.
      const declared = new Map<string, string>();
      for (const e of entries) {
        const level = e.sensitivity?.trim();
        if (level) declared.set(e.column_name, level);
      }

      const cols = sheet.columns ?? [];
      if (cols.length > 0) {
        for (const c of cols) {
          const level = declared.get(c.normalized_name ?? c.name) ?? declared.get(c.name);
          if (!level) undeclared += 1;
          else if (isSensitiveLevel(level)) masked += 1;
          else {
            declaredOpen += 1;
            const k = level.toLowerCase();
            inertLevels[k] = (inertLevels[k] ?? 0) + 1;
          }
        }
        columns += cols.length;
      } else {
        // No column list on the response: count what the dictionary holds and
        // treat the remainder of `column_count` as undeclared. Names are not
        // attributable here, but the counts still are.
        let s = 0;
        let o = 0;
        for (const level of declared.values()) {
          if (isSensitiveLevel(level)) s += 1;
          else {
            o += 1;
            const k = level.toLowerCase();
            inertLevels[k] = (inertLevels[k] ?? 0) + 1;
          }
        }
        const count = sheet.column_count ?? 0;
        masked += s;
        declaredOpen += o;
        undeclared += Math.max(0, count - s - o);
        columns += count;
      }
    }

    return {
      ...base,
      readable: true,
      columns,
      masked,
      declaredOpen,
      undeclared,
      sheets: sheets.items.length,
    };
  } catch {
    // Deliberately reason-free. A dataset that is not this seat's returns 404,
    // not 403, so "why" is a question the client is not allowed to answer.
    return {
      ...base,
      readable: false,
      columns: 0,
      masked: 0,
      declaredOpen: 0,
      undeclared: 0,
      sheets: 0,
    };
  }
}

/**
 * The review queue.
 *
 * Ranked by undeclared columns descending, because undeclared IS the exposure:
 * a column nobody has looked at is readable by everyone who can read the
 * dataset, and unlike a declared-open column that is nobody's decision.
 */
export function useGovernanceScan() {
  const seat = useSeat();

  return useQuery({
    queryKey: ['analytics', seat, 'governance-scan'],
    queryFn: async (): Promise<GovernanceScan> => {
      // `limit` is the scan bound itself, so this fetches no more than it will
      // open. `total` still reports the whole catalog for this seat.
      const page = await analytics.get<Page<DatasetInfo>>('/datasets', {
        limit: MAX_SCANNED_DATASETS,
      });

      const inertLevels: Record<string, number> = {};
      const datasets = await Promise.all(page.items.map((d) => scanDataset(d, inertLevels)));

      datasets.sort(
        (a, b) =>
          b.undeclared - a.undeclared ||
          b.columns - a.columns ||
          a.name.localeCompare(b.name),
      );

      return {
        datasets,
        scanned: page.items.length,
        total: page.total,
        complete: page.items.length >= page.total,
        inertLevels,
      };
    },
    retry: false,
  });
}

/** Dataset counts per facet value, team-scoped. Only `classification` is used here. */
export interface DatasetFacets {
  classification?: Record<string, number>;
}

export function useDatasetFacets() {
  const seat = useSeat();

  return useQuery({
    queryKey: ['analytics', seat, 'facets'],
    queryFn: () => analytics.get<DatasetFacets>('/datasets/facets'),
    retry: false,
  });
}

/**
 * The four team roles, ranked. `viewer < editor < admin < owner`, mirroring
 * `ROLE_RANK` in `app/features/auth/permissions.py`.
 */
export const ROLE_RANK: Record<string, number> = {
  owner: 40,
  admin: 30,
  editor: 20,
  viewer: 10,
};

/**
 * Whether a team role carries `dataset:read_sensitive` — i.e. sees raw values
 * rather than `***`.
 *
 * `editor` is absent on purpose. It is withheld by policy, not by rank: an
 * editor can upload, transform and publish rows they are never allowed to read
 * unmasked. A platform superuser bypasses team roles entirely and so is not
 * expressible here.
 */
export function roleSeesRaw(role: string | null | undefined): boolean {
  const r = (role ?? '').toLowerCase();
  return r === 'admin' || r === 'owner';
}

/**
 * What one WHOLE SEAT resolves — its team role together with the platform
 * superuser flag on its user record.
 *
 * `roleSeesRaw` cannot answer this and says so: a role is scoped to a team,
 * while `is_superuser` bypasses team scope entirely and resolves raw values
 * everywhere. The two are only ever available together on `GET /auth/me` and on
 * the `UserOut` that `POST /auth/users` returns, which is exactly where the
 * admin console needs the combined answer — a new seat created with the
 * superuser box ticked is not a `viewer` who sees masked values, whatever the
 * membership row beside it says.
 *
 * Still not governance state. It describes who is looking, never what is
 * declared; `masked_columns` on any response is this same per-seat answer and
 * reads empty for anyone here, which is why nothing in the console infers
 * policy from it.
 */
export function seatSeesRaw(
  role: string | null | undefined,
  isSuperuser: boolean | null | undefined,
): boolean {
  return Boolean(isSuperuser) || roleSeesRaw(role);
}
