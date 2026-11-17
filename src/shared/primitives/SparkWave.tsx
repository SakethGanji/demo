/** Inline telemetry: a small series drawn as a string with a live end node. */
export function SparkWave({ data, width = 96, height = 28, color = 'var(--accent-primary)' }: { data: number[]; width?: number; height?: number; color?: string }) {
  const min = Math.min(...data), max = Math.max(...data)
  const sx = (i: number) => (i / (data.length - 1)) * (width - 6) + 1
  const sy = (v: number) => height - 3 - ((v - min) / (max - min || 1)) * (height - 6)
  const d = data.map((v, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')
  const lx = sx(data.length - 1), ly = sy(data[data.length - 1])
  return (
    <svg width={width} height={height} aria-hidden>
      <path d={d} stroke={color} strokeWidth={1.5} fill="none" strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r={2.5} fill={color} />
    </svg>
  )
}
