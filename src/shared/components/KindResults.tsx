import type { ReactNode } from 'react'
import { flows } from '@/shared/api/flows'
import type { DatasetDetail, ModelVersion, Run } from '@/shared/api/types'
import { useFetch } from '@/shared/hooks/useFetch'
import type { RunKind } from '@/shared/lib/runKinds'
import { ErrorCard, Loading } from '@/shared/components/Status'
import { ElResults } from '@/features/expected-loss/ExpectedLossPage'
import { SegResults } from '@/features/segmented/SegmentedPage'
import { SurvResults, SurvTryIt } from '@/features/survival/SurvivalPage'
import { MlResults, MlTryIt } from '@/features/multilabel/MultilabelPage'
import { RiResults } from '@/features/reject-inference/RejectInferencePage'
import { TryIt } from '@/features/registry/components/TryIt'

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = any
/** One per G-kind: how to read its run, when its results are there, and the view (the run page and Registry → Results). */
export type KindResult = { get: (id: string) => Promise<R>; ready: (r: R) => boolean; View: (p: { run: R; reload: () => void }) => ReactNode }

export const RESULTS: Partial<Record<RunKind, KindResult>> = {
  expected_loss: { get: (id) => flows.el.get(id), ready: (r) => !!r.expected_loss, View: ({ run, reload }) => <ElResults run={run} onChanged={reload} /> },
  segmented: { get: (id) => flows.seg.get(id), ready: (r) => !!r.segmented, View: ({ run }) => <SegResults run={run} /> },
  survival: { get: (id) => flows.surv.get(id), ready: (r) => !!r.survival, View: ({ run }) => <SurvResults run={run} /> },
  multilabel: { get: (id) => flows.ml.get(id), ready: (r) => !!r.multilabel, View: ({ run }) => <MlResults m={run.multilabel} /> },
  reject_inference: { get: (id) => flows.ri.get(id), ready: (r) => !!r.reject_inference, View: ({ run }) => <RiResults r={run.reject_inference} decision={run.config.found.decision_column} /> },
}

/** Registry → Results: the version's run results, the same view as its run page. */
export function KindResults({ kind, runId }: { kind: RunKind; runId: string }) {
  const k = RESULTS[kind]!
  const r = useFetch(() => k.get(runId), [runId])
  if (r.err) return <ErrorCard err={r.err} />
  if (!r.data || !k.ready(r.data)) return <Loading>Loading the results…</Loading>
  return <k.View run={r.data} reload={r.reload} />
}

const NO_ROW_TRY: Partial<Record<string, (v: ModelVersion) => ReactNode>> = {
  expected_loss: (v) => <>An expected-loss version scores loans: PD̂, LGD̂, EAD̂ and EL per row. Score a file in Batch & Drift, or one loan over HTTP: <span className="mono">POST /api/registry/versions/{v.id}/predict</span>.</>,
  forecast: (v) => <>A forecasting version forecasts whole series, not one row: open the run to see its fan chart and upload new history to forecast from (no refit), or <span className="mono">POST /api/forecast/runs/{v.run_id}/predict</span>.</>,
  anomaly: (v) => <>An anomaly version scores rows against its threshold s*: score a file in Batch & Drift, or one row over HTTP: <span className="mono">POST /api/registry/versions/{v.id}/predict</span>.</>,
}

/** Registry → Try It, by kind: one row through a TabularPredictor (tabular, segmented), a survival curve, a label set, or
 *  a short card saying where this kind is tried instead. */
export function TryItFor({ v, kind, run, ds, open }: { v: ModelVersion; kind: string; run: Run | null; ds: DatasetDetail | null; open: () => void }) {
  if (kind === 'tabular' || kind === 'segmented') return <TryIt v={v} run={run} ds={ds} />
  if (kind === 'survival') return <SurvTryIt key={v.id} v={v} />
  if (kind === 'multilabel') return <MlTryIt key={v.id} v={v} />
  return (
    <div className="card card-body" data-testid="try-help"><p className="helper">{NO_ROW_TRY[kind]?.(v)}{' '}
      <button className="btn sm" onClick={open}>Open the run →</button></p></div>
  )
}
