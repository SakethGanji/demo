function sinePath(w: number, h: number, cycles: number, amp: number) {
  const pts: string[] = []
  const steps = Math.max(40, Math.round(cycles * 24))
  for (let i = 0; i <= steps; i++) {
    const x = (i / steps) * w
    const y = h / 2 - Math.sin((i / steps) * cycles * Math.PI * 2) * amp
    pts.push(`${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`)
  }
  return pts.join(' ')
}

/** Loading / live computation. Replaces every spinner. */
export function ResonanceWave({ width = 120, height = 20, color = 'var(--accent-secondary)', fast = false }: { width?: number; height?: number; color?: string; fast?: boolean }) {
  const d = sinePath(width * 2, height, 6, height / 2 - 2)
  const d2 = sinePath(width * 2, height, 6, height / 4)
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-label="Working" role="img" style={{ overflow: 'hidden' }}>
      <path className={`wave-path ${fast ? 'fast' : ''}`} d={d2} stroke={color} strokeOpacity={0.35} strokeWidth={1.5} fill="none" />
      <path className={`wave-path ${fast ? 'fast' : ''}`} d={d} stroke={color} strokeWidth={1.75} fill="none" />
    </svg>
  )
}
