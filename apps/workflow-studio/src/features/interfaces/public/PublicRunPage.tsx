/**
 * `/i/$project/$slug/runs/$executionId?t=<run_token>` (DESIGN-STAGE5-UIS §7.3): progress then result. The token
 * comes from the URL (bookmarkable, works on another device) and is kept in sessionStorage as a fallback.
 */
import { Link, useParams, useSearch } from '@tanstack/react-router'
import { isApiError } from '@/shared/api/http'
import { useRunView } from '../hooks/useRunView'
import { usePublicSchema } from '../hooks/usePublicSchema'
import { RunStatusView } from '../run/RunStatusView'
import { resolveRunToken } from '../lib/keys'
import { PublicNotFound, PublicShell } from './PublicShell'

export function PublicRunPage() {
  const { project, slug, executionId } = useParams({ strict: false }) as { project: string; slug: string; executionId: string }
  const { t } = useSearch({ strict: false }) as { t?: string }
  const token = resolveRunToken(executionId, t)
  const schema = usePublicSchema(project, slug)
  const run = useRunView(project, slug, executionId, token)

  if (!token || (run.isError && isApiError(run.error, undefined, 404))) {
    return (
      <PublicShell>
        <div className="tile flex flex-col gap-1 p-6 text-center" data-testid="iface-run-missing">
          <div className="text-[14px] font-semibold">We can't find this run</div>
          <div className="text-[12.5px] text-muted-foreground">Open the link you were given after submitting, including everything after the “?”.</div>
        </div>
      </PublicShell>
    )
  }
  if (schema.isError && isApiError(schema.error, undefined, 404)) return <PublicNotFound />

  return (
    <PublicShell title={schema.data?.title}>
      <div className="tile flex flex-col gap-4 p-5">
        <RunStatusView view={run.data} resultScreen={schema.data?.screens?.result} />
        {run.isError && !isApiError(run.error, undefined, 404) && (
          <div className="text-[12px] text-muted-foreground" data-testid="iface-run-retrying">Having trouble reaching the server. Retrying…</div>
        )}
      </div>
      <div>
        <Link to="/i/$project/$slug" params={{ project, slug }} className="text-[12.5px] text-primary hover:underline" data-testid="iface-submit-another">
          Submit another
        </Link>
      </div>
    </PublicShell>
  )
}
