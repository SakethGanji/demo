import { useApi } from '@/shared/api/mode'

const TEXT: Record<string, string> = {
  autopilot: 'Vela Autopilot is still the scripted prototype: the run below plays in your browser and fits nothing. Train for real in New Model.',
}

/** Live mode on a page that is still a prototype: say plainly that what it shows is simulated. */
export function PrototypeBanner({ route }: { route: string }) {
  const { mode } = useApi()
  if (mode !== 'live' || !TEXT[route]) return null
  return <div className="proto-banner" role="note" data-testid="prototype-banner"><span className="pill warning">SIMULATED</span><span>{TEXT[route]}</span></div>
}
