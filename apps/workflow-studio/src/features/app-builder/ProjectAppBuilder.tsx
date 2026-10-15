/**
 * `/p/$project/apps/$appId`: the app builder inside a project (UI-DESIGN §5.2).
 */
import { useParams } from '@tanstack/react-router'
import { AppBuilderShell } from './components/AppBuilderShell'

export function ProjectAppBuilder() {
  const { appId } = useParams({ strict: false }) as { appId: string }
  return <AppBuilderShell appId={appId} />
}
