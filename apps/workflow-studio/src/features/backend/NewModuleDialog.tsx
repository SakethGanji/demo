/**
 * New module dialog (UI-DESIGN §4.3, `dialogs.png` right): name (grammar + reserved names;
 * `409 name_taken` inline) and a starting template. The module is created as an unsaved draft
 * and saved with ⌘S like any other edit.
 */
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { FilePlus2 } from 'lucide-react'
import { useProjectCtx } from '@/features/project/ProjectContext'
import { useModules } from '@/features/project/hooks/useProjectData'
import { Button } from '@/shared/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/shared/components/ui/dialog'
import { Field } from '@/shared/components/ui/field'
import { Input } from '@/shared/components/ui/input'
import { cn } from '@/shared/lib/utils'
import { moduleNameError, templateSource, TEMPLATE_LABELS, type TemplateKey } from './templates'
import { useDocStore } from './docStore'

export function NewModuleDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { pid, slug } = useProjectCtx()
  const modules = useModules(pid)
  const navigate = useNavigate()
  const put = useDocStore((s) => s.put)
  const openTab = useDocStore((s) => s.openTab)
  const [name, setName] = useState('')
  const [tpl, setTpl] = useState<TemplateKey>('empty')
  const [touched, setTouched] = useState(false)
  const taken = (modules.data ?? []).some((m) => m.name === name)
  const error = moduleNameError(name) ?? (taken ? `A module called "${name}" already exists.` : null)

  const create = () => {
    setTouched(true)
    if (error) return
    put({
      projectId: pid,
      module: name,
      moduleId: null,
      source: templateSource(tpl, name),
      savedSource: '',
      baseSha: null,
      isNew: true,
      draft: null,
      prompts: [],
      recentTests: [],
      editorKey: `${name}-${Date.now()}`,
    })
    openTab(pid, name)
    setName('')
    setTouched(false)
    onClose()
    navigate({ to: '/p/$project/backend/$module', params: { project: slug, module: name } })
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md" data-testid="new-module-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FilePlus2 className="size-4 text-primary" /> New module
          </DialogTitle>
          <DialogDescription>A Python file with route decorators. Routes appear when you save it.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            create()
          }}
        >
          <Field label="Module name" error={touched || name ? error : null} errorTestId="new-module-error">
            <Input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value.trim())}
              placeholder="refunds"
              className="font-mono"
              aria-invalid={!!(touched || name) && !!error}
              data-testid="new-module-name"
            />
          </Field>
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium">Start from</span>
            <div className="flex flex-wrap gap-2">
              {(Object.keys(TEMPLATE_LABELS) as TemplateKey[]).map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setTpl(k)}
                  data-testid={`template-${k}`}
                  aria-pressed={tpl === k}
                  className={cn(
                    'h-7 rounded-full px-3 text-xs transition-colors duration-(--d-fast)',
                    tpl === k ? 'bg-raise text-foreground shadow-raise-sm' : 'bg-recess text-muted-foreground hover:text-foreground',
                  )}
                >
                  {TEMPLATE_LABELS[k]}
                </button>
              ))}
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name} data-testid="new-module-create">
              Create module
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
