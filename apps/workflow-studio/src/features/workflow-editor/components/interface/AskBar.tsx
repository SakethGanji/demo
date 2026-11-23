/**
 * "Change the screen…" (DESIGN-STAGE5-UIS §6.3): one instruction, one validated spec back. The result is applied to
 * the draft interface straight away and can be undone once. 503 LLM_NOT_CONFIGURED and 422 AI_SPEC_INVALID are
 * shown as plain sentences; nothing is applied on failure.
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Loader2, Sparkles, Undo2 } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Callout } from '@/shared/components/ui/surface'
import { Input } from '@/shared/components/ui/input'
import { interfacesApi } from '@/shared/api/endpoints'
import { askErrorText } from '@/features/interfaces/lib/askErrors'
import { withScreen } from '@/features/interfaces/lib/interfaceDraft'
import { useSaveInterface } from '@/features/interfaces/hooks/useInterfaceState'
import type { Interface, ScreenSpec } from '@/features/interfaces/lib/types'

interface Applied {
  summary: string
  warnings: string[]
  screenId: string
  before: ScreenSpec
}

export function AskBar({ workflowId, iface, screenId, disabledReason }: { workflowId: string; iface: Interface; screenId: string; disabledReason?: string }) {
  const [text, setText] = useState('')
  const [applied, setApplied] = useState<Applied | null>(null)
  const [err, setErr] = useState<{ text: string; reasons: string[] } | null>(null)
  const save = useSaveInterface(workflowId)
  const spec = iface.screens?.[screenId]

  const ask = useMutation({
    mutationFn: async (instruction: string) => {
      if (!spec) throw new Error('This screen does not exist yet')
      const res = await interfacesApi.aiEdit(workflowId, screenId, { instruction, spec })
      await save.mutateAsync(withScreen(iface, screenId, res.spec))
      return { res, before: spec }
    },
    onSuccess: ({ res, before }) => {
      setErr(null)
      setText('')
      setApplied({ summary: res.summary, warnings: res.warnings ?? [], screenId, before })
    },
    onError: (e) => {
      setApplied(null)
      setErr(askErrorText(e))
    },
  })

  const undo = async () => {
    if (!applied) return
    await save.mutateAsync(withScreen(iface, applied.screenId, applied.before))
    setApplied(null)
  }

  const instruction = text.trim()
  const disabled = !!disabledReason || !spec || ask.isPending
  const submit = () => {
    if (!instruction || disabled || instruction.length > 2000) return
    ask.mutate(instruction)
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Input
          value={text}
          maxLength={2000}
          placeholder={disabledReason ?? 'Change the screen…'}
          disabled={!!disabledReason || !spec}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); submit() } }}
          data-testid="interface-ask-input"
        />
        <Button size="sm" variant="ai" onClick={submit} disabled={disabled || !instruction} data-testid="interface-ask-submit">
          {ask.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} Ask
        </Button>
      </div>
      {(applied || err) && (
        <div data-testid="interface-ask-summary" data-state={err ? 'error' : 'applied'}>
          {applied && (
            <Callout tone="ai" className="items-start">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div>{applied.summary}</div>
                  {applied.warnings.length > 0 && (
                    <ul className="mt-1 list-disc pl-4 text-[12px]">
                      {applied.warnings.map((w, i) => <li key={i}>{w}</li>)}
                    </ul>
                  )}
                </div>
                <Button variant="ghost" size="xs" onClick={undo} disabled={save.isPending} data-testid="interface-ask-undo"><Undo2 /> Undo</Button>
              </div>
            </Callout>
          )}
          {err && (
            <Callout tone="warn" className="items-start" role="alert">
              <div>{err.text}</div>
              {err.reasons.length > 0 && <ul className="mt-1 list-disc pl-4 text-[12px]">{err.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
            </Callout>
          )}
        </div>
      )}
    </div>
  )
}
