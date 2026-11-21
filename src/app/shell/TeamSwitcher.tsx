import { readActor, saveActor, saveTeam, useTeam } from '@/shared/lib/team'

/** U1 (C §2.1): the team switcher in the sidebar header, not a nav item. Names are typed (no sign-in yet): without one
 *  it asks for it, since teams belong to a person. */
export function TeamSwitcher() {
  const { actor, teams, team, err } = useTeam()
  if (!actor) return (
    <div className="team-switch" data-testid="team-switch">
      <span className="eyebrow">TEAM</span>
      <input className="team-name" placeholder="your name" maxLength={80} defaultValue={readActor()} aria-label="Your name"
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
        onBlur={(e) => { const v = e.target.value.trim(); if (v) saveActor(v) }} data-testid="team-actor" />
    </div>
  )
  return (
    <div className="team-switch" data-testid="team-switch" title={err || `${actor} · ${team?.role ?? ''}`}>
      <span className="eyebrow">TEAM</span>
      <select value={team?.id ?? ''} onChange={(e) => saveTeam(e.target.value)} aria-label="Current team" data-testid="team-select" disabled={!teams.length}>
        {!teams.length && <option value="">{err ? 'API offline' : '…'}</option>}
        {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
      <small>{actor}{team ? ` · ${team.role}` : ''}</small>
    </div>
  )
}
