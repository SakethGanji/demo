export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: T[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t} role="tab" aria-selected={t === value} className={`tab ${t === value ? 'on' : ''}`} onClick={() => onChange(t)}>{t}</button>
      ))}
    </div>
  )
}
