/**
 * "View as" for the preview toolbar: pick (or add) the user the preview's requests act as. The choice is remembered
 * per app; picking reloads the preview (the panel keys the sandbox on it). "App (no user)" sends no identity.
 */
import { useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { Check, UserRound, X } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/components/ui/popover'
import { cn } from '@/shared/lib/utils'
import { FADE, SPRING, useCalm } from '@/features/plan/motion'
import type { PreviewUser } from '../sandbox/requestPolicy'
import { parseRoles, type ViewAsState } from '../lib/previewUsers'

interface Props {
  state: ViewAsState
  onChange: (next: ViewAsState) => void
}

export function ViewAsMenu({ state, onChange }: Props) {
  const calm = useCalm()
  const [draft, setDraft] = useState({ id: '', name: '', roles: '' })
  const current = state.users.find((u) => u.id === state.selected) ?? null

  const select = (id: string | null) => onChange({ ...state, selected: id })
  const add = () => {
    const id = draft.id.trim()
    if (!id) return
    const user: PreviewUser = { id, name: draft.name.trim() || id, roles: parseRoles(draft.roles) }
    onChange({ users: [...state.users.filter((u) => u.id !== id), user], selected: id })
    setDraft({ id: '', name: '', roles: '' })
  }
  const remove = (id: string) =>
    onChange({ users: state.users.filter((u) => u.id !== id), selected: state.selected === id ? null : state.selected })

  const row = 'flex w-full items-center gap-2 rounded-ctl px-2 py-1.5 text-left text-xs hover:bg-raise'
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button variant="ghost" size="xs" className={cn('max-w-[170px] font-sans', current && 'bg-raise text-foreground shadow-raise-sm')} data-testid="preview-view-as" aria-label="View as" />
        }
      >
        <UserRound />
        <span className="truncate">{current ? current.name : 'App (no user)'}</span>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-1.5" align="start">
        <div className="px-2 pb-1 pt-0.5 text-[11px] text-muted-foreground">View as</div>
        {[null, ...state.users].map((u) => (
          <div key={u?.id ?? 'app'} className="group flex items-center">
            <button type="button" className={row} onClick={() => select(u?.id ?? null)} data-testid={`preview-view-as-${u?.id ?? 'app'}`}>
              <span className="flex size-3.5 shrink-0 items-center justify-center">
                <AnimatePresence initial={false}>
                  {(u?.id ?? null) === state.selected && (
                    <motion.span key="on" initial={calm ? { opacity: 0 } : { opacity: 0, scale: 0.5 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }} transition={calm ? FADE : SPRING}>
                      <Check className="size-3.5" />
                    </motion.span>
                  )}
                </AnimatePresence>
              </span>
              <span className="min-w-0 flex-1 truncate">{u ? u.name : 'App (no user)'}</span>
              {u && <span className="truncate font-mono text-[10.5px] text-muted-foreground">{u.roles.join(', ') || u.id}</span>}
            </button>
            {u && (
              <button type="button" className="shrink-0 rounded p-1 text-muted-foreground opacity-0 hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100" onClick={() => remove(u.id)} aria-label={`Remove ${u.name}`}>
                <X className="size-3" />
              </button>
            )}
          </div>
        ))}
        <form className="mt-1.5 grid gap-1 px-1 pt-1.5 shadow-[inset_0_1px_0_var(--hairline)]" onSubmit={(e) => { e.preventDefault(); add() }}>
          <Input value={draft.id} onChange={(e) => setDraft({ ...draft, id: e.target.value })} placeholder="User id" aria-label="User id" className="h-7 text-xs" />
          <div className="flex gap-1">
            <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Display name" aria-label="Display name" className="h-7 text-xs" />
            <Input value={draft.roles} onChange={(e) => setDraft({ ...draft, roles: e.target.value })} placeholder="roles, comma" aria-label="Roles" className="h-7 text-xs" />
          </div>
          <Button type="submit" size="xs" variant="ghost" disabled={!draft.id.trim()}>Add and view as</Button>
        </form>
      </PopoverContent>
    </Popover>
  )
}
