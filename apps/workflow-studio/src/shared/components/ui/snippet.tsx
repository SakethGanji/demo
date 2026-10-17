/**
 * Snippet (DESIGN §8, Overview): a small segmented control picks the form (URL / curl / Python…), the
 * value sits in a recess, and a raised copy button turns into a check for 2s.
 */
import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/shared/lib/utils'
import { Segmented } from './segmented'
import { Tip } from './tooltip'

export interface SnippetForm {
  key: string
  label: string
  /** the text that is shown and copied */
  value: string
  /** a dim prefix shown before the value (e.g. "curl") but still copied as part of `value` */
  lead?: string
}

export function useCopy(ms = 2000) {
  const [copied, setCopied] = useState(false)
  const copy = (text: string, what?: string) => {
    const done = () => {
      setCopied(true)
      setTimeout(() => setCopied(false), ms)
      if (what) toast.success(`Copied ${what}`)
    }
    try {
      void navigator.clipboard?.writeText(text).then(done, done)
    } catch {
      done()
    }
  }
  return { copied, copy }
}

/** A raised copy button that swaps to a check. */
export function CopyButton({ text, what, className, testId }: { text: string; what?: string; className?: string; testId?: string }) {
  const { copied, copy } = useCopy()
  return (
    <Tip label={copied ? 'Copied' : what ? `Copy ${what}` : 'Copy'}>
      <button
        type="button"
        onClick={() => copy(text, what)}
        className={cn(
          'inline-flex size-7 shrink-0 items-center justify-center rounded-ctl bg-raise text-muted-foreground shadow-raise-sm hover:text-foreground [&_svg]:size-3.5',
          className,
        )}
        aria-label={what ? `Copy ${what}` : 'Copy'}
        data-testid={testId}
      >
        {copied ? <Check className="text-success" /> : <Copy />}
      </button>
    </Tip>
  )
}

export function Snippet({
  forms,
  className,
  testId = 'snippet',
  label,
}: {
  forms: SnippetForm[]
  className?: string
  testId?: string
  /** text left of the form picker (e.g. "Python · 1 module · 3 routes") */
  label?: React.ReactNode
}) {
  const [key, setKey] = useState(forms[0]?.key ?? '')
  const form = forms.find((f) => f.key === key) ?? forms[0]
  if (!form) return null
  return (
    <div className={cn('flex min-w-0 flex-col gap-2', className)} data-testid={testId}>
      {(label || forms.length > 1) && (
        <div className="flex min-w-0 items-center gap-2">
          {label && <div className="min-w-0 flex-1 truncate text-[12.5px] text-muted-foreground">{label}</div>}
          {forms.length > 1 && (
            <Segmented
              value={form.key}
              onChange={setKey}
              aria-label="Snippet format"
              className="ml-auto"
              options={forms.map((f) => ({ key: f.key, label: f.label, testId: `${testId}-${f.key}` }))}
            />
          )}
        </div>
      )}
      <div className="flex min-h-11 min-w-0 items-center gap-2 rounded-well bg-recess py-1.5 pr-1.5 pl-3" data-recess>
        <pre className="min-w-0 flex-1 overflow-x-auto font-mono text-[12px] leading-5 whitespace-pre text-foreground" data-scroll-x="allowed" data-testid={`${testId}-value`}>
          {form.lead && <span className="text-muted-foreground">{form.lead} </span>}
          {form.lead && form.value.startsWith(form.lead) ? form.value.slice(form.lead.length).trimStart() : form.value}
        </pre>
        <CopyButton text={form.value} what={form.label === 'URL' ? 'URL' : `${form.label} snippet`} testId={`${testId}-copy`} />
      </div>
    </div>
  )
}
