import { Link } from '@tanstack/react-router'
import { ArrowRight, Braces, Database, GitFork, LayoutDashboard, KeyRound, Rocket } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Tile, IconTile, type EntityHue } from '@/shared/components/ui/surface'
import { HomeSky } from '@/shared/components/space/HomeSky'
import { Brand, ThemeToggle, TopBarFrame } from '@/features/project/components/TopBar'

const FEATURES: { hue: EntityHue; icon: React.ReactNode; title: string; body: string }[] = [
  { hue: 'backend', icon: <Braces />, title: 'Backend', body: 'Write Python modules, expose them as routes, and test each one against the live engine.' },
  { hue: 'data', icon: <Database />, title: 'Data', body: 'Tables with typed columns and row editing, wired to your routes without a separate database setup.' },
  { hue: 'flow', icon: <GitFork />, title: 'Workflows', body: 'Chain steps on a visual canvas, run them on demand and watch each node report back.' },
  { hue: 'apps', icon: <LayoutDashboard />, title: 'Apps', body: 'Build a front end on top of your routes and tables, and preview it as you go.' },
  { hue: 'secrets', icon: <KeyRound />, title: 'Secrets', body: 'Keep credentials out of code. Missing ones are flagged before anything ships.' },
  { hue: 'release', icon: <Rocket />, title: 'Releases', body: 'Check the code, fix secrets, review routes and promote to a live version in four steps.' },
]

export default function LandingPage() {
  return (
    <div className="relative h-screen w-full overflow-y-auto" data-testid="landing-page">
      <HomeSky />
      <TopBarFrame position="sticky" className="gap-2">
        <Brand />
        <div className="flex-1" />
        <Button variant="ghost" size="sm" render={<Link to="/projects" />} data-testid="landing-projects-link">
          Projects
        </Button>
        <Button
          variant="ghost"
          size="sm"
          render={<Link to="/p/$project/workflows/$workflowId" params={{ project: 'default', workflowId: 'new' }} />}
          data-testid="landing-editor-link"
        >
          Editor
        </Button>
        <ThemeToggle />
      </TopBarFrame>

      <div className="relative z-10 mx-auto flex w-full max-w-[60rem] flex-col gap-14 px-8 pt-24 pb-16">
        <section className="flex flex-col items-center gap-5 text-center" data-testid="landing-hero">
          <h1 className="text-[44px] leading-[1.08] font-[650] tracking-[-0.03em] sm:text-[52px]">
            Build, run and ship
            <br />
            your backend in one place.
          </h1>
          <p className="max-w-[34rem] text-[15px] leading-relaxed text-muted-foreground">
            Command Studio brings routes, data, workflows and apps into a single workspace, with runs and releases tracked for every project.
          </p>
          <div className="mt-2 flex flex-wrap items-center justify-center gap-3">
            <Button size="lg" render={<Link to="/projects" />} data-testid="landing-get-started">
              Get started <ArrowRight />
            </Button>
            <Button
              size="lg"
              variant="outline"
              render={<Link to="/p/$project/workflows/$workflowId" params={{ project: 'default', workflowId: 'new' }} />}
              data-testid="landing-new-flow"
            >
              Create new flow
            </Button>
          </div>
        </section>

        <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="landing-features">
          {FEATURES.map((f) => (
            <Tile key={f.title} className="flex flex-col gap-3 p-5">
              <IconTile hue={f.hue} className="size-8">
                {f.icon}
              </IconTile>
              <h2 className="text-panel-title font-semibold tracking-[-0.01em]">{f.title}</h2>
              <p className="text-[13px] leading-relaxed text-muted-foreground">{f.body}</p>
            </Tile>
          ))}
        </section>

        <footer className="text-center text-[12px] text-muted-foreground">Command Studio</footer>
      </div>
    </div>
  )
}
