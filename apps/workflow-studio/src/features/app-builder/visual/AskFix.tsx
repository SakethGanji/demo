import { Sparkles } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import type { ElementView } from './editor'
import { askAboutElement } from './askAi'

/** One click from a refusal: pre-fills the chat with a sentence about the element and focuses it. */
export function AskFix({ view, about, label = 'Ask AI to do this' }: { view: ElementView | null; about?: string; label?: string }) {
  if (!view) return null
  return (
    <Button variant="ai" size="xs" className="self-start" onClick={() => askAboutElement(view, about)} data-testid="vedit-ask-fix">
      <Sparkles /> {label}
    </Button>
  )
}
