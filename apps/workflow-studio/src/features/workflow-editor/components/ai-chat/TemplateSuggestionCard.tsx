import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { AlertTriangle, ArrowUpRight, LayoutTemplate, Loader2 } from 'lucide-react';
import { Button } from '@/shared/components/ui/button';
import { Callout } from '@/shared/components/ui/surface';
import { aiBuilderApi, describeAiError } from '@/features/ai-builder/api';
import { useInstallTemplate } from '@/features/templates/hooks';
import type { InstallResult } from '@/features/templates/types';
import { suggestionOf } from './templateSuggestion';

/**
 * Offered under a plan the chat drafted when a ready-made template fits the request. Installing is a separate, explicit click
 * (the builder itself never installs); declining just hides the card and the plan carries on.
 */
export function TemplateSuggestionCard({ proposalId, projectId, projectSlug }: { proposalId: string; projectId: string; projectSlug: string }) {
  const navigate = useNavigate();
  const [dismissed, setDismissed] = useState(false);
  const [result, setResult] = useState<InstallResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const install = useInstallTemplate(projectId);
  const { data: proposal } = useQuery({
    queryKey: ['ai-chat', 'proposal', proposalId],
    queryFn: () => aiBuilderApi.get(proposalId, projectId || undefined),
    enabled: !!proposalId,
    staleTime: 60_000,
  });
  const suggestion = suggestionOf(proposal?.spec);
  if (!suggestion || dismissed) return null;

  const entry = result?.workflows.find((w) => w.role === 'entry') ?? result?.workflows[0];
  const onInstall = () => {
    setError(null);
    install.mutate(
      { slug: suggestion.slug, opts: { run_tests: true } },
      {
        onSuccess: (r) => setResult(r),
        onError: (e) => setError(describeAiError(e).message),
      },
    );
  };

  return (
    <div className="rounded-ctl bg-recess p-2.5 text-[12.5px]" data-testid="ai-template-suggestion" data-slug={suggestion.slug}>
      <div className="flex items-center gap-1.5 font-medium text-foreground">
        <LayoutTemplate className="size-3.5 text-muted-foreground" /> Start from the “{suggestion.name}” template?
      </div>
      <p className="mt-1 text-muted-foreground">{suggestion.reason || suggestion.summary}</p>
      {error && <Callout tone="error" icon={<AlertTriangle />} className="mt-1.5" role="alert" data-testid="ai-template-suggestion-error">{error}</Callout>}
      {result ? (
        <div className="mt-2 flex flex-wrap items-center gap-2" data-testid="ai-template-suggestion-installed">
          <span className="text-muted-foreground">Installed. Nothing in your plan was changed.</span>
          {entry && (
            <Button size="sm" onClick={() => void navigate({ to: '/p/$project/workflows/$workflowId', params: { project: projectSlug, workflowId: entry.id } })}
              data-testid="ai-template-suggestion-open">
              Open {entry.name ?? 'workflow'} <ArrowUpRight />
            </Button>
          )}
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={onInstall} disabled={install.isPending} data-testid="ai-template-suggestion-install">
            {install.isPending ? <Loader2 className="animate-spin" /> : null}
            {install.isPending ? 'Installing…' : 'Install this template'}
          </Button>
          <Button size="sm" variant="outline" onClick={() => setDismissed(true)} disabled={install.isPending} data-testid="ai-template-suggestion-dismiss">
            No thanks, keep my plan
          </Button>
        </div>
      )}
    </div>
  );
}
