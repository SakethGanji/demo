// Dependency-free SVG charts following the dataviz method: single axis, fixed
// categorical order (never cycled), legend for >=2 series, selective direct
// labels, recessive grid, 4px rounded data-ends, hover tooltip, table fallback.
import { useState } from "react";
import { cx, fmtNum } from "./ui";

// Categorical palette by role (CSS vars swap for light/dark automatically).
const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6", "--series-7", "--series-8"];
export function seriesColor(i: number): string {
  return `var(${SERIES[i % SERIES.length]})`;
}

export interface Series { name: string; data: (number | null)[]; }

function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <div className="chart-legend">
      {series.map((s, i) => (
        <span className="legend-item" key={`${s.name}-${i}`}>
          <span className="legend-swatch" style={{ background: seriesColor(i) }} /> {s.name}
        </span>
      ))}
    </div>
  );
}

function TableFallback({ categories, series }: { categories: string[]; series: Series[] }) {
  return (
    <div className="table-wrap mt-8">
      <table className="data">
        <thead><tr><th scope="col">Category</th>{series.map((s, i) => <th scope="col" className="num" key={`${s.name}-${i}`}>{s.name}</th>)}</tr></thead>
        <tbody>
          {categories.map((c, r) => (
            <tr key={c}><td>{c}</td>{series.map((s, i) => <td className="num" key={`${s.name}-${i}`}>{fmtNum(s.data[r])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface Tip { x: number; y: number; lines: string[] }

// Past this many categories a bar chart is not a chart: at 5,200 groups each
// bar is 0.14px wide, and at 50,000 the browser spends ~10s building 400k DOM
// nodes and stays janky afterwards. Draw the first N and say so.
const MAX_CATEGORIES = 60;

export function BarChart({ categories: allCategories, series: allSeries, height = 300, valueFormat = fmtNum }: {
  categories: string[]; series: Series[]; height?: number; valueFormat?: (n: unknown) => string;
}) {
  const [showTable, setShowTable] = useState(false);
  const [tip, setTip] = useState<Tip | null>(null);
  if (!allCategories.length || !allSeries.length) return <div className="empty small">No data to chart.</div>;

  const clipped = allCategories.length > MAX_CATEGORIES;
  const categories = clipped ? allCategories.slice(0, MAX_CATEGORIES) : allCategories;
  const series = clipped
    ? allSeries.map((s) => ({ ...s, data: s.data.slice(0, MAX_CATEGORIES) }))
    : allSeries;

  const padL = 52, padR = 16, padT = 14, padB = 46;
  const W = 720, H = height;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = Math.max(1, ...series.flatMap((s) => s.data.map((v) => v ?? 0)));
  // "nice" ticks
  const ticks = 4;
  const step = niceStep(max / ticks);
  const top = step * Math.ceil(max / step) || 1;
  const y = (v: number) => padT + plotH - (v / top) * plotH;

  const groupW = plotW / categories.length;
  const barCount = series.length;
  const bandPad = Math.min(14, groupW * 0.2);
  const bandW = groupW - bandPad;
  const barW = Math.max(3, Math.min(46, bandW / barCount - 2));
  const single = series.length === 1;

  return (
    <div>
      <div className="row" style={{ justifyContent: "flex-end" }}>
        {clipped && (
          <span className="small muted" style={{ marginRight: "auto" }}>
            Showing the first {MAX_CATEGORIES} of {allCategories.length.toLocaleString()} categories —
            too many to plot legibly. Use “Show table”, or group by fewer values.
          </span>
        )}
        <button className="btn btn-ghost btn-sm" onClick={() => setShowTable((s) => !s)}>{showTable ? "Show chart" : "Show table"}</button>
      </div>
      {showTable ? (
        <TableFallback categories={allCategories} series={allSeries} />
      ) : (
        <div style={{ position: "relative", width: "100%", overflowX: "auto" }}>
          <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", minWidth: 360 }} role="img">
            {/* gridlines + y labels */}
            {Array.from({ length: ticks + 1 }, (_, i) => {
              const val = (top / ticks) * i;
              const yy = y(val);
              return (
                <g key={i}>
                  <line x1={padL} x2={W - padR} y1={yy} y2={yy} stroke="var(--grid)" strokeWidth={1} />
                  <text x={padL - 8} y={yy + 4} textAnchor="end" fontSize={11} fill="var(--text-muted)">{valueFormat(val)}</text>
                </g>
              );
            })}
            {/* baseline */}
            <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} stroke="var(--baseline)" strokeWidth={1} />
            {/* bars */}
            {categories.map((cat, ci) => {
              const gx = padL + ci * groupW + bandPad / 2;
              return (
                <g key={cat}>
                  {series.map((s, si) => {
                    const v = s.data[ci] ?? 0;
                    const bx = gx + si * (barW + 2) + (bandW - (barW + 2) * barCount) / 2;
                    const by = y(v), bh = Math.max(0, y(0) - by);
                    return (
                      <g key={`${s.name}-${si}`}
                        onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, lines: [cat, `${s.name}: ${valueFormat(v)}`] })}
                        onMouseLeave={() => setTip(null)}>
                        <rect x={bx} y={by} width={barW} height={bh} rx={3} fill={seriesColor(si)} />
                        {single && bh > 14 && (
                          <text x={bx + barW / 2} y={by - 4} textAnchor="middle" fontSize={10.5} fill="var(--text-secondary)">{valueFormat(v)}</text>
                        )}
                      </g>
                    );
                  })}
                  <text x={gx + bandW / 2} y={H - padB + 16} textAnchor="middle" fontSize={11} fill="var(--text-muted)">
                    {cat.length > 12 ? cat.slice(0, 11) + "…" : cat}
                  </text>
                </g>
              );
            })}
          </svg>
          {tip && (
            <div className="chart-tooltip" style={{ left: tip.x + 12, top: tip.y + 12 }}>
              {tip.lines.map((l, i) => <div key={i} style={{ fontWeight: i === 0 ? 600 : 400 }}>{l}</div>)}
            </div>
          )}
        </div>
      )}
      <Legend series={series} />
    </div>
  );
}

export function LineChart({ categories, series, height = 280, valueFormat = fmtNum }: {
  categories: string[]; series: Series[]; height?: number; valueFormat?: (n: unknown) => string;
}) {
  const [tip, setTip] = useState<Tip | null>(null);
  if (!categories.length || !series.length) return <div className="empty small">No data to chart.</div>;
  const padL = 52, padR = 16, padT = 14, padB = 40;
  const W = 720, H = height, plotW = W - padL - padR, plotH = H - padT - padB;
  const max = Math.max(1, ...series.flatMap((s) => s.data.map((v) => v ?? 0)));
  const min = Math.min(0, ...series.flatMap((s) => s.data.map((v) => v ?? 0)));
  const step = niceStep((max - min) / 4);
  const top = step * Math.ceil(max / step) || 1;
  const x = (i: number) => padL + (categories.length === 1 ? plotW / 2 : (i / (categories.length - 1)) * plotW);
  const y = (v: number) => padT + plotH - ((v - min) / (top - min)) * plotH;
  return (
    <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }} role="img">
        {Array.from({ length: 5 }, (_, i) => {
          const val = min + ((top - min) / 4) * i; const yy = y(val);
          return <g key={i}><line x1={padL} x2={W - padR} y1={yy} y2={yy} stroke="var(--grid)" strokeWidth={1} /><text x={padL - 8} y={yy + 4} textAnchor="end" fontSize={11} fill="var(--text-muted)">{valueFormat(val)}</text></g>;
        })}
        {series.map((s, si) => (
          <g key={`${s.name}-${si}`}>
            <path fill="none" stroke={seriesColor(si)} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
              d={s.data.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(v ?? 0)}`).join(" ")} />
            {s.data.map((v, i) => (
              <circle key={i} cx={x(i)} cy={y(v ?? 0)} r={3.5} fill={seriesColor(si)}
                onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, lines: [categories[i], `${s.name}: ${valueFormat(v)}`] })}
                onMouseLeave={() => setTip(null)} />
            ))}
          </g>
        ))}
        {categories.map((c, i) => (i % Math.ceil(categories.length / 8 || 1) === 0) && (
          <text key={c} x={x(i)} y={H - padB + 16} textAnchor="middle" fontSize={11} fill="var(--text-muted)">{c.length > 10 ? c.slice(0, 9) + "…" : c}</text>
        ))}
      </svg>
      {tip && <div className="chart-tooltip" style={{ left: tip.x + 12, top: tip.y + 12 }}>{tip.lines.map((l, i) => <div key={i} style={{ fontWeight: i === 0 ? 600 : 400 }}>{l}</div>)}</div>}
      <Legend series={series} />
    </div>
  );
}

function niceStep(raw: number): number {
  if (raw <= 0) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / mag;
  const nice = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return nice * mag;
}

// Progress/meter bar (for health scores, coverage, null rates).
export function Meter({ value, max = 100, kind = "accent" }: { value: number; max?: number; kind?: string }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const color = kind === "good" ? "var(--good)" : kind === "warning" ? "var(--warning)" : kind === "critical" ? "var(--critical)" : "var(--accent)";
  return (
    <div className={cx("meter")} style={{ background: "var(--surface-2)", borderRadius: 999, height: 8, overflow: "hidden" }}>
      <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 999 }} />
    </div>
  );
}
