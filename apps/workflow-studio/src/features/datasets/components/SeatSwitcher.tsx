/**
 * "Act as" — switch the acting seat.
 *
 * Not a debug toy: this service masks sensitive columns from a viewer *and* an
 * editor, and hides cross-tenant resources behind 404s. Those behaviours are
 * invisible from an admin seat, so being able to switch is how you confirm the
 * controls actually bite before this goes anywhere near a bank.
 */

import { UserCog } from 'lucide-react';
import { useIdentityStore } from '@/shared/lib/identity';
import { useAuthMe, useTeamMembers } from '../hooks/useDatasets';

export function SeatSwitcher() {
  const identity = useIdentityStore((s) => s.identity);
  const setIdentity = useIdentityStore((s) => s.setIdentity);

  const me = useAuthMe();
  const teamId = me.data?.memberships?.[0]?.team_id ?? null;
  const members = useTeamMembers(teamId);

  const options = members.data?.items ?? [];

  return (
    <label className="flex items-center gap-1.5" title="Act as another seat">
      <UserCog className="size-3.5 text-muted-foreground" />
      <select
        value={identity.userId}
        onChange={(e) => {
          const m = options.find((o) => o.user_id === e.target.value);
          if (m) setIdentity({ userId: m.user_id, label: `${m.name} (${m.role})` });
        }}
        className="h-6 max-w-52 rounded-md border border-border bg-background px-1.5 text-[11px] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        aria-label="Acting seat"
      >
        {/* Keep the current seat listed even before members resolve. */}
        {options.length === 0 && <option value={identity.userId}>{identity.label}</option>}
        {options.map((m) => (
          <option key={m.user_id} value={m.user_id}>
            {m.name} ({m.role})
          </option>
        ))}
      </select>
    </label>
  );
}
