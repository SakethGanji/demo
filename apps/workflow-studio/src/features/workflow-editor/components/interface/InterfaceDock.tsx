/**
 * The "Try it" dock (like n8n's chat panel): the workflow's interface, used live over the canvas while it is being
 * built. Chat talks to the draft (`interface/preview/chat`), a form submits a preview run, a custom screen renders on
 * run data. Each run it starts is followed on the canvas (`useFollowRun`). Opened from the canvas toolbar or the
 * Interface tab. A solid floating surface (`raise`, no blur): the canvas never gets a new blur layer.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { History, Maximize2, Minimize2, RotateCcw, X } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'
import { previewChatTransport, resetChat } from '@/features/interfaces/lib/chatTransport'
import { previewScreenIds } from '@/features/interfaces/lib/interfaceDraft'
import { useFollowRun } from '../../hooks/useViewingRun'
import { useEditorLayoutStore } from '../../stores/editorLayoutStore'
import { useEditorProjectStore } from '../../stores/editorProjectStore'
import { useWorkflowStore } from '../../stores/workflowStore'
import { InterfacePreview } from './InterfacePreview'
import { DOCK_LABEL, useEditorInterface } from './useEditorInterface'

const RIGHT_PANEL_W = 'min(384px, 50vw - 24px)'

export default function InterfaceDock() {
  const qc = useQueryClient()
  const open = useEditorLayoutStore((s) => s.interfaceDockOpen)
  const close = useEditorLayoutStore((s) => s.closeInterfaceDock)
  const rightPanelOpen = useEditorLayoutStore((s) => s.rightPanelOpen)
  const projectId = useEditorProjectStore((s) => s.projectId)
  const viewingId = useWorkflowStore((s) => s.viewingExecution?.id ?? null)
  const { workflowId, iface } = useEditorInterface()
  const follow = useFollowRun()
  const [picked, setPicked] = useState('start')
  const [lastRun, setLastRun] = useState<string | null>(null)
  const [large, setLarge] = useState(false)

  if (!open || !workflowId || !iface) return null

  const ids = previewScreenIds(iface)
  const screenId = ids.includes(picked) ? picked : ids[0]
  const { title, icon: Icon } = DOCK_LABEL[iface.kind]
  const onRun = (executionId: string) => {
    setLastRun(executionId)
    follow(executionId)
  }

  return (
    <section
      aria-label={`Try the ${title.toLowerCase()}`}
      data-testid="interface-dock"
      data-kind={iface.kind}
      data-size={large ? 'large' : 'default'}
      className="raise absolute bottom-3 z-20 flex flex-col overflow-hidden rounded-panel shadow-overlay"
      style={{
        right: rightPanelOpen ? `calc(${RIGHT_PANEL_W} + 24px)` : 12,
        width: large ? 'min(640px, calc(100% - 24px))' : 'min(400px, calc(100% - 24px))',
        height: large ? 'calc(100% - 92px)' : 'min(560px, calc(100% - 92px))',
      }}
    >
      <header className="flex h-10 shrink-0 items-center gap-2 border-b border-hairline pr-1.5 pl-3">
        <Icon className="size-3.5 shrink-0 text-hue-flow" aria-hidden />
        <span className="text-panel-title font-semibold">{title}</span>
        <span className="min-w-0 truncate text-[12px] text-muted-foreground">Draft · runs show on the canvas</span>
        <div className="ml-auto flex shrink-0 items-center gap-0.5">
          {iface.kind === 'chat' && (
            <Tip label="New conversation" side="top">
              <Button variant="ghost" size="icon-sm" aria-label="New conversation" onClick={() => void resetChat(qc, previewChatTransport(workflowId))} data-testid="interface-dock-reset">
                <RotateCcw />
              </Button>
            </Tip>
          )}
          <Tip label={large ? 'Smaller' : 'Larger'} side="top">
            <Button variant="ghost" size="icon-sm" aria-label={large ? 'Smaller' : 'Larger'} aria-pressed={large} onClick={() => setLarge((v) => !v)} data-testid="interface-dock-size">
              {large ? <Minimize2 /> : <Maximize2 />}
            </Button>
          </Tip>
          <Tip label="Close" side="top">
            <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={close} data-testid="interface-dock-close">
              <X />
            </Button>
          </Tip>
        </div>
      </header>

      <div className={cn('flex min-h-0 flex-1 flex-col', iface.kind === 'chat' ? 'p-0' : 'overflow-y-auto p-3')}>
        <InterfacePreview
          workflowId={workflowId}
          projectId={projectId}
          iface={iface}
          screenId={screenId}
          onScreen={setPicked}
          surface="dock"
          onRun={onRun}
          className={iface.kind === 'chat' ? 'flex-1' : undefined}
        />
      </div>

      {lastRun && (
        <footer className="flex h-9 shrink-0 items-center gap-2 border-t border-hairline px-3 text-[12px]" data-testid="interface-dock-run">
          <History className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="min-w-0 truncate text-muted-foreground" title={lastRun}>
            Run <span className="font-mono">#{lastRun}</span>{viewingId === lastRun ? ' · on the canvas' : ''}
          </span>
          {viewingId !== lastRun && (
            <Button variant="link" size="xs" className="ml-auto" onClick={() => follow(lastRun)} data-testid="interface-dock-view-run">
              View run
            </Button>
          )}
        </footer>
      )}
    </section>
  )
}
