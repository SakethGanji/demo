/**
 * Coordinated sampling — `POST /sample/coordinated`.
 *
 * ── What it is, precisely ──────────────────────────────────────────────────
 *
 * One dataset, ONE resolved version, several sheets. The driver sheet runs
 * through the ordinary `/sample` pipeline; each related sheet is semi-joined
 * down to the rows whose `right_on` value appears in an already-sampled
 * parent's `left_on` values. The result is a referentially consistent slice of
 * the workbook — a mini-version where the orders in one sheet still have their
 * line items in another.
 *
 * It is NOT a sample across several datasets: `CoordinatedSampleRequest` has a
 * single `dataset_id`, and the docstring on `run_coordinated_sampling` says
 * "all sheets come from a single resolved version". The version resolves in the
 * order `version_id > version_number > tag > current`.
 *
 * ── The reading model is the single draw's, deliberately ───────────────────
 *
 * `CoordinatedSampleResponse.driver` is a plain `SampleResponse` — the same
 * `steps_summary`, `goal_validation` and `reproducibility` the sampling studio
 * already renders in full. So the studio renders the driver through the code it
 * already has, and this module only adds the per-related-sheet accounting. A
 * second, parallel way of reading step results would be a second thing to keep
 * honest.
 *
 * ── The population trap ────────────────────────────────────────────────────
 *
 * Every sheet has its OWN population. `original_count` on a related sheet is
 * that sheet's row count, not the driver's, and the response never reports a
 * combined total — correctly, because adding rows drawn from an orders sheet to
 * rows drawn from a line-items sheet produces a number that is a count of
 * nothing. Nothing in this module or its consumers may sum them.
 *
 * ── Keys, and where they come from ─────────────────────────────────────────
 *
 * Precedence in the handler is explicit columns > confirmed relationship (§22)
 * > enabled `foreign_key` quality rule. The response says which was used in
 * `key_source`, so the screen can state how the join was decided rather than
 * implying the user chose it.
 *
 * ── The §24 referential guard ──────────────────────────────────────────────
 *
 * A related sheet that is itself another link's `parent_sheet` may not be
 * sub-sampled: dropping a parent row would silently drop the child rows that
 * depend on it. The service rejects that with `cannot-subsample-parent` rather
 * than returning a quietly inconsistent slice. This surface sends no per-link
 * `sampling_steps` at all, so every referenced row is kept and the guard is
 * never reached — stated in the UI so the absence reads as a decision.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { analytics, errorText } from '@/shared/lib/analyticsClient';
import { useIdentityStore } from '@/shared/lib/identity';
import type {
  DistributionGoalsSpec,
  SampleColumnSummary,
  SampleResponse,
  SamplingStepSpec,
} from './useSampling';

/* ---------------------------------------------------------------- requests */

/**
 * One related sheet to keep consistent with the draw.
 *
 * `left_on`/`right_on` are omitted together or given together — a half-declared
 * key is not "partly explicit", it is a request the service will reject.
 */
export interface RelatedSheetLinkSpec {
  sheet: string;
  left_on?: string | null;
  right_on?: string | null;
  /** Defaults to the driver sheet server-side; set it to chain one link off another. */
  parent_sheet?: string | null;
  relationship_id?: string | null;
}

export interface CoordinatedSampleRequestBody {
  dataset_id: string;
  version_number?: number | null;
  /** The sheet that is actually sampled. Its keys drive every filter. */
  driver_sheet: string;
  target_total_volume: number;
  sampling_steps: SamplingStepSpec[];
  distribution_goals?: DistributionGoalsSpec | null;
  /** Makes the driver draw — and therefore every filtered sheet — reproducible. */
  seed?: number | null;
  return_data?: boolean;
  deduplicate?: boolean;
  deduplicate_columns?: string[] | null;
  shuffle?: boolean;
  sort_by?: string | null;
  sort_descending?: boolean;
  related: RelatedSheetLinkSpec[];
}

/* --------------------------------------------------------------- responses */

/** `explicit | relationship | fk_rule`, said in words. */
export const KEY_SOURCE_NOTE: Record<string, string> = {
  explicit: 'keys named on this request',
  relationship: 'keys from a confirmed relationship',
  fk_rule: 'keys from an enabled foreign_key quality rule',
};

export interface RelatedSheetSample {
  sheet: string;
  parent_sheet: string;
  left_on: string;
  right_on: string;
  /** Rows in this sheet before filtering — this sheet's own population. */
  original_count: number;
  sampled_count: number;
  relationship_id?: string | null;
  /** How the keys were resolved. Defaults to `explicit` server-side. */
  key_source?: string | null;
  /**
   * Rows the parent referenced BEFORE any sub-sampling. Equals `sampled_count`
   * whenever no sub-sampling was requested, which is always, here.
   */
  referenced_count?: number | null;
  columns?: SampleColumnSummary[] | null;
  preview?: Record<string, unknown>[] | null;
  /** Its own registered `sample_output` artifact — one per sheet, not one per run. */
  sample_file?: string | null;
  data?: Record<string, unknown>[] | null;
}

export interface CoordinatedSampleResponse {
  success: boolean;
  dataset_id: string;
  driver_sheet: string;
  /** The ordinary single-draw response. Read it with the ordinary reading model. */
  driver: SampleResponse;
  related: RelatedSheetSample[];
}

/* ------------------------------------------------------------------- write */

/**
 * Run the coordinated draw.
 *
 * The toast counts SHEETS, not rows. There is no honest single row figure for
 * this response, and inventing one by summing populations is exactly the
 * silent-wrong-answer this codebase exists to prevent.
 *
 * `retry` is off for the same reason `/sample` turns it off: the endpoint runs
 * `ensure_raw_access`, and retrying a refusal three times only delays the
 * message.
 */
export function useRunCoordinatedSample() {
  const qc = useQueryClient();
  const seat = useIdentityStore((s) => s.identity.userId);
  return useMutation<CoordinatedSampleResponse, unknown, CoordinatedSampleRequestBody>({
    mutationFn: (body) => analytics.post<CoordinatedSampleResponse>('/sample/coordinated', body),
    retry: false,
    onSuccess: (d) => {
      // Every sheet writes its own artifact, so the library lens is stale.
      void qc.invalidateQueries({ queryKey: ['analytics', seat] });
      const sheets = 1 + d.related.length;
      toast.success(
        `Consistent slice across ${sheets} sheet${sheets === 1 ? '' : 's'} — ${d.driver_sheet} drawn, ${d.related.length} filtered to it.`,
      );
    },
    onError: (e) =>
      toast.error(
        errorText(e, {
          notFound: 'That dataset, version or sheet is not available to this seat.',
        }),
      ),
  });
}
