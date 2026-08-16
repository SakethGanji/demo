// "Act as" identity: the service authenticates by X-User-Id / X-Team-Id headers.
// The System superuser is the default; the switcher lets you view the app as any
// user/team to exercise RBAC and masking from a real seat.
import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api, getIdentity, loadIdentity, setIdentity } from "../api/client";
import type { Identity } from "../api/client";

interface IdentityCtx { identity: Identity; setAs: (id: Identity) => void; }
const Ctx = createContext<IdentityCtx>({ identity: getIdentity(), setAs: () => {} });
export function useIdentity() { return useContext(Ctx); }

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [identity, setId] = useState<Identity>(() => loadIdentity());
  const setAs = (id: Identity) => { setIdentity(id); setId(id); };
  return <Ctx.Provider value={{ identity, setAs }}>{children}</Ctx.Provider>;
}

export const SYSTEM_ADMIN: Identity = { userId: "00000000-0000-0000-0000-000000000001", label: "System (admin)" };

interface Me { user?: { is_superuser?: boolean }; memberships?: { team_id: string; role: string }[] }
const WRITE_ROLES = new Set(["owner", "admin", "editor"]);

/** Whether this seat can write anywhere. The server is always the enforcement
 *  point (and stays so); this only lets the UI present a read-only state
 *  instead of offering buttons that will certainly 403. A viewer-everywhere
 *  seat gets read-only; anything else keeps the controls. */
export function useCanWrite(): boolean {
  const { identity } = useIdentity();
  const [canWrite, setCanWrite] = useState(true);
  useEffect(() => {
    let alive = true;
    api.get<Me>("/auth/me")
      .then((me) => {
        if (!alive) return;
        const writes = !!me.user?.is_superuser
          || (me.memberships || []).some((m) => WRITE_ROLES.has(m.role));
        setCanWrite(writes);
      })
      .catch(() => { if (alive) setCanWrite(true); });  // unknown → don't hide UI
    return () => { alive = false; };
  }, [identity.userId, identity.teamId]);
  return canWrite;
}
