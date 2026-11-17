import { LineChart, type Series } from '@/shared/components/LineChart'
import { PlotFrame } from '@/shared/components/PlotFrame'
import { ParamChip } from '@/shared/primitives/ParamChip'
import { fmtSci, logC, pow10, trainF1, valF1, type Params } from '../params'

/** Live preview: where the current C sits on the validation curve. */
export function ValidationCurve({ params }: { params: Params }) {
  const series: Series[] = [
    { name: 'Validation', color: 'var(--viz-a)', data: Array.from({ length: 51 }, (_, i) => valF1(logC(i), params)) },
    { name: 'Train', color: 'var(--viz-c)', dash: '6 4', data: Array.from({ length: 51 }, (_, i) => trainF1(logC(i))) },
  ]
  const x = Math.log10(params.C)
  return (
    <PlotFrame title="Validation Curve" legend={series}
      chips={<><ParamChip sym="k" value="5" /><ParamChip sym="n" value="20k" /></>}
      caption={<span>Macro <span className="math" style={{ fontSize: 14 }}>F1</span> against <span className="math" style={{ fontSize: 14 }}>C</span> on a 20k-row subsample, estimated</span>}>
      <LineChart series={series} xMax={50} yMin={0.75} yMax={1} yTicks={[0.75, 0.8, 0.85, 0.9, 0.95, 1]} xTicks={[0, 10, 20, 30, 40, 50]}
        fmtX={(i) => (Number.isInteger(logC(i)) ? pow10(10 ** logC(i)) : `10^${logC(i).toFixed(1)}`)} xTitle="C" yTitle="F1" height={210}
        marker={{ x: (x + 3) * 10, label: `C=${fmtSci(params.C)}` }} />
    </PlotFrame>
  )
}
