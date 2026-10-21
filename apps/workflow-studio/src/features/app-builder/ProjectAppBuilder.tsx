/**
 * `/p/$project/apps/$appId`: the app builder inside a project (UI-DESIGN §5.2). `apps/new` is New app: the chat that
 * plans and builds the app before it exists.
 */
import { useParams } from '@tanstack/react-router'
import { AppBuilderShell } from './components/AppBuilderShell'
import { NewAppBuilder } from './components/NewAppBuilder'

export function ProjectAppBuilder() {
  const { appId } = useParams({ strict: false }) as { appId: string }
  return appId === 'new' ? <NewAppBuilder /> : <AppBuilderShell appId={appId} />
}
