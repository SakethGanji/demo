/** A flat icon button that copies `text` and turns into a check (the run id on a row preview and in the sheet). */
import { Check, Copy } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { useCopy } from '@/shared/components/ui/snippet'
import { Tip } from '@/shared/components/ui/tooltip'

export function GhostCopy({ text, what, testId, className }: { text: string; what: string; testId: string; className?: string }) {
  const { copied, copy } = useCopy()
  return (
    <Tip label={copied ? 'Copied' : `Copy ${what}`}>
      <Button variant="ghost" size="icon-xs" aria-label={`Copy ${what}`} data-testid={testId} className={className} onClick={() => copy(text, what)}>
        {copied ? <Check className="text-success" /> : <Copy />}
      </Button>
    </Tip>
  )
}
