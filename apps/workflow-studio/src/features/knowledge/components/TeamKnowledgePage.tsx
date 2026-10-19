/**
 * `/knowledge`: the studio-wide Knowledge page, outside any project. The same graph, lenses and panel as a project's
 * Knowledge page, read in team scope (every project's knowledge plus the shared nodes), under the studio's own top
 * bar with the Projects / Templates / Knowledge nav.
 */
import { Brand, HomeNav, ThemeToggle, TopBarFrame } from '@/features/project/components/TopBar'
import { TEAM_SCOPE } from '../lib/types'
import { KnowledgePage } from './KnowledgeView'

export function TeamKnowledgePage() {
  return (
    <div className="relative flex h-full w-full flex-col" data-testid="team-knowledge">
      <TopBarFrame position="sticky" className="shrink-0 gap-2">
        <Brand />
        <span className="mx-1 h-4 w-px bg-hairline" aria-hidden />
        <HomeNav />
        <div className="flex-1" />
        <ThemeToggle />
      </TopBarFrame>
      <div className="min-h-0 flex-1">
        <KnowledgePage scope={TEAM_SCOPE} />
      </div>
    </div>
  )
}
