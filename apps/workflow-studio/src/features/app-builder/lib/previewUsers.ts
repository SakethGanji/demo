import type { PreviewUser } from '../sandbox/requestPolicy'

/** Editable "view as" list, remembered per app. Null selection = the app itself (no user); an app with no stored choice starts as the first preset user. */
export interface ViewAsState { users: PreviewUser[]; selected: string | null }

export const PRESET_USERS: PreviewUser[] = [
  { id: 'alice', name: 'Alice', roles: ['staff'] },
  { id: 'bob', name: 'Bob', roles: ['manager'] },
  { id: 'carol', name: 'Carol', roles: ['admin'] },
]

const key = (appId: string) => `studio.preview-view-as.${appId}`

export function loadViewAs(appId: string): ViewAsState {
  try {
    const raw = JSON.parse(localStorage.getItem(key(appId)) ?? 'null') as Partial<ViewAsState> | null
    const users = Array.isArray(raw?.users)
      ? raw.users.filter((u): u is PreviewUser => !!u && typeof u.id === 'string' && typeof u.name === 'string' && Array.isArray(u.roles))
      : PRESET_USERS
    const chosen = typeof raw?.selected === 'string' && users.some((u) => u.id === raw.selected) ? raw.selected : null
    // Never chosen (nothing stored for this app): default to the first user so role-gated routes are not refused.
    const selected = chosen ?? (raw?.selected === null ? null : (users[0]?.id ?? null))
    return { users, selected }
  } catch {
    return { users: PRESET_USERS, selected: PRESET_USERS[0].id }
  }
}

export function saveViewAs(appId: string, state: ViewAsState): void {
  try {
    localStorage.setItem(key(appId), JSON.stringify(state))
  } catch {
    /* storage unavailable: the choice lasts for this session only */
  }
}

export function parseRoles(text: string): string[] {
  return text.split(',').map((r) => r.trim()).filter(Boolean)
}
