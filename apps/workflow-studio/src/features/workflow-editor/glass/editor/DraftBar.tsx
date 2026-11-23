/**
 * The draft bar (mockup `.draftbar`): Draft · vN live ▾ (versions) · N changes ▾ · saved state · problems ·
 * tests · undo/redo · Publish ▾ (message + what changes, impact on calling workflows, Unpublish).
 */
import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, ChevronDown, FlaskConical, Loader2, Redo2, Undo2 } from 'lucide-react'
import { useWorkflowStore } from '../../stores/workflowStore'
import { useHistoryStore } from '../../stores/historyStore'
import { useCheckStore } from '../../stores/checkStore'
import { useTestStore } from '../../stores/testStore'
import { usePublishWorkflow, useSaveWorkflow } from '../../hooks/useWorkflowApi'
import { useUsedBy, usedByChipLabel } from '../../hooks/useSubworkflows'
import { PublishImpactDialog } from '../../components/workflow-navbar/PublishImpactDialog'
import { hasBreakingImpact, parsePublishImpact, type PublishImpactRow } from '../../lib/subworkflowsApi'
import { barState, compactBarText } from '../../lib/tests'
import { cx } from '@/shared/glass/cx'
import { useGlassStore } from '../state/glassStore'
import { ChangesPopover } from './ChangesPopover'
import { CompareBar, VersionsPopover } from './VersionsPopover'
import { MenuItem, Pop } from './ui'
import { useDraftChanges, useVersions } from './versions'
import { plural } from './status'

function PublishPop({ open, onClose, anchor, changes, next }: { open: boolean; onClose: () => void; anchor: React.RefObject<HTMLElement | null>; changes: number; next: number }) {
  const isActive = useWorkflowStore((s) => s.isActive)
  const workflowId = useWorkflowStore((s) => s.workflowId)
  const { publish, unpublish, isPublishing } = usePublishWorkflow()
  const usedBy = useUsedBy(workflowId && workflowId !== 'new' ? workflowId : undefined)
  const usedLabel = usedByChipLabel(usedBy.data)
  const [msg, setMsg] = useState('')
  const [impact, setImpact] = useState<PublishImpactRow[]>([])
  const [impactOpen, setImpactOpen] = useState(false)
  const { refetch } = useVersions()
  const go = async () => {
    const res = await publish(msg.trim() || undefined)
    if (!res) return
    setMsg('')
    onClose()
    void refetch()
    const rows = parsePublishImpact(res)
    if (hasBreakingImpact(rows)) {
      setImpact(rows)
      setImpactOpen(true)
    }
  }
  return (
    <>
      <Pop open={open} onClose={onClose} anchor={anchor} className="ge-pubpop" testId="ge-publish-pop" label="Publish">
        <h6>Publish v{next}</h6>
        <p className="ge-hint">
          {changes ? `${plural(changes, 'change')} go live` : 'Re-publish the draft as it is'}
          {usedLabel ? ` · ${usedLabel.toLowerCase()}` : ''}. Triggers turn on.
        </p>
        <div className="ge-fld" style={{ padding: '4px 8px' }}>
          <div className="ge-inp">
            <input value={msg} onChange={(e) => setMsg(e.target.value)} placeholder="What changed? (optional)" aria-label="Version message" data-testid="ge-publish-message" onKeyDown={(e) => e.key === 'Enter' && void go()} />
          </div>
        </div>
        <div className="ge-pbtns" style={{ padding: '0 8px 6px' }}>
          <button type="button" className="ge-btn primary sm" onClick={() => void go()} disabled={isPublishing || !workflowId} data-testid="editor-publish">
            {isPublishing ? <Loader2 className="animate-spin" /> : null} Publish v{next}
          </button>
        </div>
        {isActive && (
          <>
            <i className="sep" />
            <MenuItem
              danger
              onClick={() => {
                onClose()
                void unpublish()
              }}
              hint="Take it offline; triggers stop"
              disabled={isPublishing}
              testId="editor-unpublish"
            >
              Unpublish
            </MenuItem>
          </>
        )}
      </Pop>
      <PublishImpactDialog impact={impact} open={impactOpen} onOpenChange={setImpactOpen} />
    </>
  )
}

export function DraftBar() {
  const dirty = useWorkflowStore((s) => s._isDirty)
  const canUndo = useWorkflowStore((s) => s._canUndo)
  const canRedo = useWorkflowStore((s) => s._canRedo)
  const undo = useWorkflowStore((s) => s.undo)
  const redo = useWorkflowStore((s) => s.redo)
  const isActive = useWorkflowStore((s) => s.isActive)
  const hasTests = useWorkflowStore((s) => s.tests.length > 0)
  const counts = useCheckStore((s) => s.counts)
  const report = useTestStore((s) => s.report)
  const scorecard = useTestStore((s) => s.scorecard)
  const testsRunning = useTestStore((s) => s.running)
  const openDrawer = useGlassStore((s) => s.openDrawer)
  const { saveWorkflow, isSaving } = useSaveWorkflow()
  const { latest } = useVersions()
  const changes = useDraftChanges()
  const histOpen = useHistoryStore((s) => s.open)
  const selectedVersion = useHistoryStore((s) => s.selectedVersion)

  const [pop, setPop] = useState<null | 'ver' | 'chg' | 'pub'>(null)
  const verRef = useRef<HTMLButtonElement>(null)
  const chgRef = useRef<HTMLButtonElement>(null)
  const pubRef = useRef<HTMLDivElement>(null)

  // ⌘K "Version history" (and anything else that sets historyStore.open) opens the versions popover
  useEffect(() => {
    if (histOpen && selectedVersion == null) setPop('ver')
  }, [histOpen, selectedVersion])
  const closePop = () => {
    if (pop === 'ver' && useHistoryStore.getState().selectedVersion == null) useHistoryStore.getState().setOpen(false)
    setPop(null)
  }

  const problems = counts.error + counts.warning
  const tstate = barState(report)
  const ttext = compactBarText(report, scorecard) || 'Tests'
  const next = (latest?.version_number ?? 0) + 1

  return (
    <>
      <div className="ge-fl ge-draftbar" data-testid="ge-draftbar">
        <div style={{ position: 'relative' }}>
          <button
            ref={verRef}
            type="button"
            className="dv"
            aria-expanded={pop === 'ver'}
            onClick={() => (pop === 'ver' ? closePop() : setPop('ver'))}
            data-testid="ge-versions"
          >
            <span className={cx('d', !dirty && !changes.total && isActive && 'ok')} />
            {changes.total || dirty ? 'Draft' : isActive ? 'Live' : 'Draft'}
            {latest ? (
              <span className="lv">
                v{latest.version_number}
                {isActive ? ' live' : ''}
              </span>
            ) : (
              <span className="lv">never published</span>
            )}
            <ChevronDown className="cv" width={12} height={12} />
          </button>
          <VersionsPopover open={pop === 'ver'} onClose={closePop} anchor={verRef} />
        </div>
        <span className="sep">·</span>
        <div style={{ position: 'relative' }}>
          {changes.total > 0 ? (
            <button ref={chgRef} type="button" className="chg" aria-expanded={pop === 'chg'} onClick={() => setPop(pop === 'chg' ? null : 'chg')} data-testid="ge-changes" data-count={changes.total}>
              {plural(changes.total, 'change')}
            </button>
          ) : (
            <span className="nochg" data-testid="ge-changes" data-count={0}>
              {changes.loading ? '…' : 'no changes'}
            </span>
          )}
          <ChangesPopover open={pop === 'chg'} onClose={() => setPop(null)} anchor={chgRef} changes={changes} version={latest?.version_number ?? null} />
        </div>
        <span className={cx('savest', isSaving && 'ing')} data-testid="editor-saved">
          {isSaving ? 'Saving…' : dirty ? 'Unsaved' : 'Saved'}
        </span>
        {dirty && (
          <button type="button" className="ge-btn sm" onClick={() => void saveWorkflow()} disabled={isSaving} title="Save (⌘S)" data-testid="editor-save">
            Save
          </button>
        )}
        {problems > 0 && (
          <button type="button" className={cx('ge-bchip', counts.error ? 'err' : 'warn')} onClick={() => openDrawer('problems')} data-testid="ge-problems-chip">
            <AlertTriangle /> {problems}
          </button>
        )}
        {hasTests && (
          <button
            type="button"
            className={cx('ge-bchip', tstate === 'pass' && 'ok', tstate === 'fail' && 'err')}
            onClick={() => openDrawer('tests')}
            data-testid="test-bar"
            data-state={tstate}
            title={`${ttext} · open Tests`}
          >
            {testsRunning ? <Loader2 className="animate-spin" /> : <FlaskConical />} {ttext}
          </button>
        )}
        <i className="vs" aria-hidden style={{ width: 1, height: 18, background: 'var(--hairline)' }} />
        <button type="button" className="ge-ib" onClick={() => undo()} disabled={!canUndo} aria-label="Undo" title="Undo (⌘Z)" data-testid="ge-undo">
          <Undo2 />
        </button>
        <button type="button" className="ge-ib" onClick={() => redo()} disabled={!canRedo} aria-label="Redo" title="Redo (⇧⌘Z)" data-testid="ge-redo">
          <Redo2 />
        </button>
        <div ref={pubRef} className="pubs" style={{ position: 'relative' }}>
          <button type="button" className="ge-btn primary" onClick={() => setPop(pop === 'pub' ? null : 'pub')} aria-expanded={pop === 'pub'} data-testid="ge-publish">
            Publish <ChevronDown />
          </button>
          <PublishPop open={pop === 'pub'} onClose={() => setPop(null)} anchor={pubRef} changes={changes.total} next={next} />
        </div>
      </div>
      <CompareBar />
    </>
  )
}

export default DraftBar
