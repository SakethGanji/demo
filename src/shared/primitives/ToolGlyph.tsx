/** Tool marks: each one is drawn from strings, nodes, frets or waves. 40px grid. */
export function ToolGlyph({ id }: { id: string }) {
  const a = 'var(--accent-primary)', b = 'var(--accent-secondary)', t = 'var(--border-track)'
  const p = { fill: 'none', strokeWidth: 1.75, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  const g: Record<string, React.ReactNode> = {
    quick: <><path d="M4 12c5-8 9 8 14 0s9 8 18 0" stroke={b} {...p} /><path d="M4 21c6-4 10 4 16 0" stroke={b} {...p} strokeOpacity=".6" /><line x1="20" x2="36" y1="21" y2="21" stroke={a} {...p} /><line x1="4" x2="36" y1="30" y2="30" stroke={a} {...p} /><circle cx="4" cy="30" r="2.5" fill={a} /><circle cx="36" cy="30" r="2.5" fill={a} /></>,
    autopilot: <><line x1="4" x2="36" y1="14" y2="14" stroke={a} {...p} /><circle cx="4" cy="14" r="2.5" fill={a} /><circle cx="14" cy="14" r="2.5" fill={a} /><path d="M14 14C16 28 22 30 28 29" stroke={t} {...p} strokeDasharray="3 3" /><circle cx="28" cy="29" r="2.5" fill="var(--bg-surface)" stroke={t} strokeWidth="1.5" /><rect x="21" y="10" width="8" height="8" transform="rotate(45 25 14)" fill="var(--bg-surface)" stroke={b} strokeWidth="1.75" /><circle cx="36" cy="14" r="2.5" fill={a} /></>,
    train: <><line x1="4" x2="20" y1="20" y2="20" stroke={a} {...p} /><path d="M20 20c3-9 5-9 8 0s5 9 8 0" stroke={b} {...p} /><circle cx="4" cy="20" r="2.5" fill={a} /><circle cx="12" cy="20" r="2.5" fill={a} /><circle cx="20" cy="20" r="3" fill={b} /></>,
    compare: <>{[8, 20, 32].map((x) => <line key={x} x1={x} x2={x} y1="6" y2="34" stroke={t} strokeWidth="1.25" />)}<path d="M8 12L20 26L32 10" stroke={a} {...p} /><path d="M8 28L20 14L32 24" stroke={b} {...p} /><path d="M8 20L20 20L32 30" stroke={t} {...p} strokeDasharray="3 3" /></>,
    register: <><line x1="12" x2="12" y1="6" y2="34" stroke={a} {...p} /><circle cx="12" cy="8" r="3" fill={a} /><circle cx="12" cy="20" r="3" fill={a} /><circle cx="12" cy="32" r="3" fill="var(--bg-surface)" stroke={b} strokeWidth="1.75" /><path d="M12 20C22 22 22 30 30 30" stroke={b} {...p} strokeDasharray="3 3" /><circle cx="30" cy="30" r="2.5" fill={b} /></>,
    vela: <><path d="M4 22c4-12 8-12 12 0s8 12 12 0 6-8 8-4" stroke={b} {...p} /><path d="M4 22c4-6 8-6 12 0s8 6 12 0" stroke={a} {...p} strokeOpacity=".5" /></>,
  }
  return <svg width="40" height="40" viewBox="0 0 40 40" aria-hidden>{g[id]}</svg>
}
