/**
 * "Act as" — switch the acting seat.
 *
 * Not a debug toy: this service masks sensitive columns from a viewer *and* an
 * editor, and hides cross-tenant resources behind 404s. Those behaviours are
 * invisible from an admin seat, so being able to switch is how you confirm the
 * controls actually bite before this goes anywhere near a bank.
 *
 * Drawn as the prototype's avatar chip rather than a dropdown: it sits in the
 * app chrome, where a native `<select>` brings its own font, arrow and popup
 * and reads as a browser control dropped into the design. The initials and the
 * role are the two things you actually need at a glance.
 */

import { UserCog } from 'lucide-react';
import { useIdentityStore } from '@/shared/lib/identity';
import { useAuthMe, useTeamMembers } from '../hooks/useDatasets';
import { ScopePicker } from './ScopePicker';

/** Two letters from a name — "Dana Viewer" → "DV", "System" → "SY". */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '··';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function SeatSwitcher() {
  const identity = useIdentityStore((s) => s.identity);
  const setIdentity = useIdentityStore((s) => s.setIdentity);

  const me = useAuthMe();
  const teamId = me.data?.memberships?.[0]?.team_id ?? null;
  const members = useTeamMembers(teamId);

  const options = members.data?.items ?? [];
  const current = options.find((o) => o.user_id === identity.userId);
  const shown = current ? `${current.name} (${current.role})` : identity.label;

  return (
    <div className="flex items-center gap-2" title="Act as another seat">
      <UserCog className="size-3.5 shrink-0 text-muted-foreground" />
      <ScopePicker
        label="Acting seat"
        testid="seat-picker"
        className="max-w-52 font-sans"
        value={identity.userId}
        onValueChange={(userId) => {
          const m = options.find((o) => o.user_id === userId);
          // Carry `teamId` through. This used to write only { userId, label },
          // which silently dropped the team on every switch — so `X-Team-Id`
          // was never sent after the first seat change, and the service fell
          // back to the user's default team. That is right by luck for a
          // single-team user and wrong for anyone in two.
          if (m) setIdentity({ userId: m.user_id, teamId, label: `${m.name} (${m.role})` });
        }}
        options={
          options.length > 0
            ? options.map((m) => ({ value: m.user_id, label: `${m.name} (${m.role})` }))
            : // Keep the current seat listed even before members resolve.
              [{ value: identity.userId, label: identity.label }]
        }
      />
      <span
        aria-hidden="true"
        title={shown}
        className="grid size-[27px] shrink-0 place-items-center rounded-[7px] bg-secondary font-mono text-footnote font-semibold text-muted-foreground shadow-[var(--hi)]"
      >
        {initials(current?.name ?? identity.label)}
      </span>
    </div>
  );
}
