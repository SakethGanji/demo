/**
 * Who "you" are in attribution lines (triggered_by / approved_by / decided by). The studio has no login, so this is
 * a display name kept per browser; default "you".
 */
const KEY = 'studio.notebook.me'

export function myName(): string {
  try {
    return localStorage.getItem(KEY)?.trim() || 'you'
  } catch {
    return 'you'
  }
}

export function setMyName(name: string) {
  try {
    if (name.trim()) localStorage.setItem(KEY, name.trim())
    else localStorage.removeItem(KEY)
  } catch {
    /* storage blocked: the name lasts this page only */
  }
}

/** A remembered per-viewer UI flag (outline rail open, …). Storage may be blocked; the default then holds. */
export function readFlag(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(`studio.notebook.${key}`)
    return v === null ? fallback : v === '1'
  } catch {
    return fallback
  }
}

export function writeFlag(key: string, value: boolean) {
  try {
    localStorage.setItem(`studio.notebook.${key}`, value ? '1' : '0')
  } catch {
    /* ignore */
  }
}
