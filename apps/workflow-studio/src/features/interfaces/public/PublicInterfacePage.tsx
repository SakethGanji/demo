/**
 * `/i/$project/$slug` (DESIGN-STAGE5-UIS §7.3): the form, the chat, or the custom start screen. Anonymous: it only
 * talks to `/api/i/...`. One Idempotency-Key is generated per page open, so a retried click replays the same run.
 */
import { Callout } from '@/shared/components/ui/surface'
import { useMemo, useState } from 'react'
import { useNavigate, useParams } from '@tanstack/react-router'
import { Loader2 } from 'lucide-react'
import { isApiError } from '@/shared/api/http'
import { publicInterfaceApi } from '../api/interfaceApi'
import { usePublicSchema } from '../hooks/usePublicSchema'
import { InterfaceForm } from '../form/InterfaceForm'
import { InterfaceChat } from '../chat/InterfaceChat'
import { ScreenRenderer } from '../renderer/ScreenRenderer'
import { newIdempotencyKey } from '../lib/keys'
import { publicChatTransport } from '../lib/chatTransport'
import { PublicNotFound, PublicShell } from './PublicShell'

export function PublicInterfacePage() {
  const { project, slug } = useParams({ strict: false }) as { project: string; slug: string }
  const navigate = useNavigate()
  const schema = usePublicSchema(project, slug)
  const [idemKey] = useState(newIdempotencyKey)
  const chatTransport = useMemo(() => publicChatTransport(project, slug), [project, slug])

  if (schema.isPending) {
    return (
      <PublicShell>
        <div className="flex items-center gap-2 text-[13px] text-muted-foreground" data-testid="iface-loading"><Loader2 className="size-4 animate-spin" /> Loading…</div>
      </PublicShell>
    )
  }
  if (schema.isError) {
    if (isApiError(schema.error, undefined, 404)) return <PublicNotFound />
    return (
      <PublicShell>
        <Callout tone="error" role="alert" data-testid="iface-load-error">Could not load this page. Try again in a moment.</Callout>
      </PublicShell>
    )
  }

  const s = schema.data
  const submit = async (fields: Record<string, unknown>) => {
    const res = await publicInterfaceApi.submit(project, slug, fields, idemKey)
    await navigate({
      to: '/i/$project/$slug/runs/$executionId',
      params: { project, slug, executionId: res.execution_id },
      search: { t: res.run_token },
    })
  }
  const form = <InterfaceForm fields={s.fields} submitLabel={s.submit_label ?? 'Submit'} onSubmit={submit} />

  return (
    <PublicShell title={s.title} description={s.description}>
      {s.kind === 'chat' ? (
        <InterfaceChat transport={chatTransport} />
      ) : s.kind === 'custom' && s.screens?.start ? (
        <ScreenRenderer spec={s.screens.start} data={{}} slots={{ input_form: () => form }} />
      ) : (
        <div className="tile p-5">{form}</div>
      )}
    </PublicShell>
  )
}
