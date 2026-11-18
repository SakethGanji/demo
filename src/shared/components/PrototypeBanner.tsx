import { useApi } from '@/shared/api/mode'

/** Pages that are still prototypes in live mode. None since S7 (Autopilot is real); kept for the next prototype. */
const TEXT: Record<string, string> = {}

/** Live mode on a page that is still a prototype: say plainly that what it shows is simulated. */
export function PrototypeBanner({ route }: { route: string }) {
  const { mode } = useApi()
  if (mode !== 'live' || !TEXT[route]) return null
  return <div className="proto-banner" role="note" data-testid="prototype-banner"><span className="pill warning">SIMULATED</span><span>{TEXT[route]}</span></div>
}
