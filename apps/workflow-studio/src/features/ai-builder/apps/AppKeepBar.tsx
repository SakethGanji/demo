import { useState } from 'react';
import { Link } from '@tanstack/react-router';
import { Check, ExternalLink, GitBranch, Inbox, Loader2, PackageCheck, Sparkles, Table2, Trash2 } from 'lucide-react';
import { Construct, type ConstructMode } from '@/features/knowledge/kit/Construct';
import { knowledgeLink } from '@/features/knowledge/kit/links';
import { Button } from '@/shared/components/ui/button';
import { Callout } from '@/shared/components/ui/surface';
import { cn } from '@/shared/lib/utils';
import type { AppBuildView } from './appTypes';
import type { AppBusy } from './useAppBuild';
import { failingParts, keepLabel, keepMode } from './lib/appState';

const LINK = 'inline-flex h-7 max-w-full items-center gap-1.5 rounded-chip bg-recess px-2.5 text-[12.5px] text-foreground outline-none hover:bg-(--hover-fill) focus-visible:shadow-focus [&_svg]:size-3.5 [&_svg]:shrink-0 [&_svg]:text-muted-foreground';

/** What the person gets once the app is kept: its workflows, its screens, the approvals inbox, and what it taught the knowledge graph. */
function KeptLinks({ app, projectSlug }: { app: AppBuildView; projectSlug: string }) {
  const [mode, setMode] = useState<ConstructMode>('enter');
  const inst = app.installed;
  const titleOf = (key: string) => app.parts.find((p) => p.key === key)?.title ?? key;
  const workflows = Object.entries(inst?.workflows ?? {});
  const interfaces = Object.entries(inst?.interfaces ?? {});
  const tables = Object.values(inst?.tables ?? {});
  return (
    <Construct mode={mode} tone="success" scale={0.8} onDone={() => setMode('idle')}>
      <div className="flex flex-col gap-2.5 rounded-panel bg-success-soft p-3" data-testid="ai-app-kept-links">
        <p className="flex items-center gap-1.5 text-[13px] font-medium text-success-text"><PackageCheck className="size-4" aria-hidden /> App kept. Your workflows are saved as drafts you can open now; publish to go live.</p>
        {workflows.length > 0 && (
          <div className="flex flex-col gap-1">
            <h3 className="text-[12px] font-medium text-muted-foreground">Workflows</h3>
            <div className="flex flex-wrap gap-1">
              {workflows.map(([key, id]) => (
                <Link key={key} to="/p/$project/workflows/$workflowId" params={{ project: projectSlug, workflowId: id }} className={LINK} data-testid={`ai-app-kept-workflow-${key}`}>
                  <GitBranch aria-hidden /><span className="truncate">{titleOf(key)}</span>
                </Link>
              ))}
            </div>
          </div>
        )}
        {(interfaces.length > 0 || tables.length > 0) && (
          <div className="flex flex-col gap-1">
            <h3 className="text-[12px] font-medium text-muted-foreground">Screens and data{interfaces.length > 0 && <span data-testid="ai-app-kept-publish-note"> · screens open once their workflow is published (publish to go live)</span>}</h3>
            <div className="flex flex-wrap gap-1">
              {interfaces.map(([key, i]) => (
                <a key={key} href={i.path} className={LINK} data-testid={`ai-app-kept-interface-${key}`}>
                  <ExternalLink aria-hidden /><span className="truncate">{titleOf(key)}</span>
                </a>
              ))}
              {tables.map((t) => (
                <Link key={t.id} to="/p/$project/data" params={{ project: projectSlug }} className={LINK} data-testid={`ai-app-kept-table-${t.name ?? t.id}`}>
                  <Table2 aria-hidden /><span className="truncate">{t.name ?? 'Table'}</span>
                </Link>
              ))}
            </div>
          </div>
        )}
        <div className="flex flex-wrap gap-1">
          <Link to="/p/$project/approvals" params={{ project: projectSlug }} className={LINK} data-testid="ai-app-kept-approvals"><Inbox aria-hidden /> Approvals inbox</Link>
          <Link {...knowledgeLink(projectSlug, { tab: 'memory' })} className={LINK} data-testid="ai-app-kept-knowledge"><Sparkles aria-hidden /> Open in Knowledge</Link>
        </div>
      </div>
    </Construct>
  );
}

/**
 * Keep and Discard for the whole app (CONTRACTS §5.1). Keep installs every workflow and table at once, or none; it stays off
 * until every part is built. When a built part has a failing approved test the button becomes "Keep anyway".
 */
export function AppKeepBar({ app, projectSlug, busy, onKeep, onDiscard }: {
  app: AppBuildView;
  projectSlug: string;
  busy: AppBusy;
  onKeep: (acceptFailing: boolean) => void;
  onDiscard: () => void;
}) {
  if (app.status === 'kept') return <KeptLinks app={app} projectSlug={projectSlug} />;
  if (app.status === 'discarded') return <Callout tone="info" data-testid="ai-app-discarded">App discarded. Nothing was saved.</Callout>;
  if (app.status === 'clarify') return null;
  const mode = keepMode(app);
  const red = failingParts(app);
  const working = busy !== 'idle';
  return (
    <div className="flex flex-col gap-2" data-testid="ai-app-keepbar" data-keep={mode}>
      {mode === 'override' && (
        <Callout tone="warn" data-testid="ai-app-keep-warning">An approved test is failing in {red.join(', ')}. You can still keep the app.</Callout>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={mode === 'blocked' ? 'outline' : 'default'} disabled={mode === 'blocked' || working}
          onClick={() => onKeep(mode === 'override')} data-testid="ai-app-keep"
        >
          {busy === 'keeping' ? <Loader2 className="animate-spin" /> : <Check />}
          {mode === 'override' ? 'Keep anyway' : keepLabel(app)}
        </Button>
        <Button variant="ghost" disabled={working} onClick={onDiscard} className={cn(busy === 'discarding' && 'opacity-70')} data-testid="ai-app-discard">
          {busy === 'discarding' ? <Loader2 className="animate-spin" /> : <Trash2 />} Discard app
        </Button>
      </div>
    </div>
  );
}
