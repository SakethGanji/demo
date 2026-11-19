export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: T[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t} role="tab" aria-selected={t === value} className={`tab ${t === value ? 'on' : ''}`} onClick={() => onChange(t)}>{t}</button>
      ))}
    </div>
  )
}

/** F19: many tabs in a few named groups. The group switch (left) picks a group; the tabs of that group follow it on
 * the same line, so a page with a dozen views never overflows at 1366 px. A group with one tab shows only the switch. */
export function GroupedTabs<T extends string>({ groups, value, onChange }: {
  groups: { label: string; tabs: T[] }[]; value: T; onChange: (v: T) => void
}) {
  const live = groups.filter((g) => g.tabs.length)
  const cur = live.find((g) => g.tabs.includes(value)) ?? live[0]
  return (
    <div className="tabs grouped" data-testid="grouped-tabs">
      <div className="seg tab-groups" role="group" aria-label="View group">
        {live.map((g) => (
          <button key={g.label} aria-pressed={g === cur} className={g === cur ? 'on' : ''} data-testid={`tab-group-${g.label}`}
            onClick={() => { if (g !== cur) onChange(g.tabs[0]) }}>
            {g.label}{g.tabs.length > 1 && <span className="tab-count">{g.tabs.length}</span>}
          </button>
        ))}
      </div>
      <div className="tab-row" role="tablist">
        {cur?.tabs.map((t) => (
          <button key={t} role="tab" aria-selected={t === value} className={`tab ${t === value ? 'on' : ''}`} onClick={() => onChange(t)}>{t}</button>
        ))}
      </div>
    </div>
  )
}
