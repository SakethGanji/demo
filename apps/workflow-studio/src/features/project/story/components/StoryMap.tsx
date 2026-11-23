/**
 * The map (design `overview-mockup-v3.html` §2): a lane per kind the project has, in flow order, each object an
 * raised glass card on the map's near-clear pane (double glass); wires between them from real relationships,
 * thickness by 24h volume; packets for new runs. Kinds the project lacks sit in a quiet "Add to this project" column.
 */
import { useRef, type CSSProperties, type ReactNode } from 'react'
import { AlertTriangle, LockKeyhole, Plus } from 'lucide-react'
import { MethodBadge } from '@/shared/components/api/MethodBadge'
import { Button } from '@/shared/components/ui/button'
import { Callout } from '@/shared/components/ui/surface'
import { Tip } from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/lib/utils'
import { edgeSignature, useRunPackets, useWires } from '../hooks/useMapWires'
import { KIND_HUE, KIND_LABEL, LANE_HUE, type Fact, type LaneKey, type StoryModel, type StoryObj } from '../lib/model'
import type { RunListItem } from '@/shared/api/types'
import { KindIcon } from './KindIcon'

const ADD_LABEL: Record<LaneKey, string> = {
  apps: 'New interface',
  routes: 'New route',
  modules: 'New module',
  workflows: 'New workflow',
  data: 'New table',
}

/** colour only for trouble; a healthy fact stays muted with a small green dot */
const TONE_TEXT = { ok: '', bad: 'font-medium text-destructive', warn: 'font-medium text-warning-text' } as const

/** lanes share the width when there are three or more; one or two keep a readable width and the
 * "Add to this project" column sits at the far edge instead of a lane stretching across the map */
function columns(lanes: number, absent: boolean): string {
  if (lanes >= 3) return `repeat(${lanes}, minmax(0, 1fr))${absent ? ' 176px' : ''}`
  return `repeat(${Math.max(1, lanes)}, minmax(0, 340px))${absent ? ' minmax(176px, 1fr)' : ''}`
}

export function StoryMap({
  model,
  runs,
  runsLoaded,
  pid,
  selected,
  onSelect,
  add,
  onUploadDataset,
  motionOff,
  failed,
  onRetry,
  children,
}: {
  model: StoryModel
  runs: RunListItem[]
  runsLoaded: boolean
  pid: string
  selected: string | null
  onSelect: (key: string) => void
  add: Record<LaneKey, () => void>
  onUploadDataset: () => void
  motionOff: boolean
  /** lists that failed to load (their lanes may be missing) */
  failed: string[]
  onRetry: () => void
  /** the focus panel, inside the same glass */
  children?: ReactNode
}) {
  const lanes = useRef<HTMLDivElement>(null)
  const wires = useRef<SVGSVGElement>(null)
  const sig = edgeSignature(model.edges)
  useWires(lanes, wires, sig)
  useRunPackets(lanes, wires, runs, runsLoaded, sig, pid, motionOff)

  const cols = columns(model.lanes.length, model.absent.length > 0)
  return (
    <section className="story-map tile relative overflow-hidden px-[22px] pt-[22px] pb-[20px]" aria-label="Project map" data-testid="story-map">
      <div className="story-dots pointer-events-none absolute inset-0" aria-hidden />
      {failed.length > 0 && (
        <Callout tone="error" icon={<AlertTriangle />} className="relative z-[2] mb-4" data-testid="story-map-error">
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="flex-1">Couldn't load {failed.join(', ')}, so the map may be missing some of this project.</span>
            <Button variant="outline" size="xs" onClick={onRetry}>
              Retry
            </Button>
          </span>
        </Callout>
      )}
      <div ref={lanes} className="story-lanes relative" style={{ gridTemplateColumns: cols }}>
        <svg ref={wires} className="story-wires pointer-events-none absolute inset-0 z-0 size-full overflow-visible" aria-hidden />
        {model.lanes.map((lane, li) => (
          <div key={lane.key} className="relative z-[1] flex min-w-0 flex-col gap-3.5" role="group" aria-label={`${lane.title}: ${lane.objs.length}`} data-lane={lane.key}>
            <div className="story-lane-head">
              <KindIcon lane={lane.key} className="size-3.5" />
              <span className="text-foreground/85">{lane.title}</span>
              <span className="text-muted-foreground/70 tabular-nums">{lane.objs.length}</span>
              <Tip label={ADD_LABEL[lane.key]}>
                <button
                  type="button"
                  className="ml-auto grid size-6 place-items-center rounded-ctl text-muted-foreground transition-colors duration-(--d-fast) hover:bg-hairline hover:text-foreground focus-visible:shadow-focus focus-visible:outline-none"
                  aria-label={ADD_LABEL[lane.key]}
                  onClick={add[lane.key]}
                >
                  <Plus className="size-3.5" />
                </button>
              </Tip>
            </div>
            {lane.objs.map((o, oi) => (
              <MapObject key={o.key} obj={o} order={li * 2 + oi} selected={selected === o.key} onSelect={onSelect} />
            ))}
          </div>
        ))}
        {model.absent.length > 0 && (
          <div className="story-absent relative z-[1] flex min-w-0 flex-col" role="group" aria-label="Add to this project">
            <div className="story-lane-head mb-1.5">Add to this project</div>
            {model.absent.map((k) => (
              <AddGhost key={k} lane={k} label={ADD_LABEL[k]} onClick={add[k]} />
            ))}
            {model.absent.includes('data') && <AddGhost lane="data" label="Upload a dataset" onClick={onUploadDataset} />}
          </div>
        )}
      </div>
      {children}
    </section>
  )
}

function AddGhost({ lane, label, onClick }: { lane: LaneKey; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="story-ghost flex items-center gap-2.5 py-2 text-left text-[12.5px] text-muted-foreground focus-visible:rounded-ctl focus-visible:shadow-focus focus-visible:outline-none"
      style={{ '--c': LANE_HUE[lane] } as CSSProperties}
      data-testid={`story-add-${lane}`}
    >
      <KindIcon lane={lane} className="size-3.5" />
      {label}
      <Plus className="ml-auto size-3.5 opacity-0 transition-opacity duration-(--d-fast)" aria-hidden />
    </button>
  )
}

/** a path or file name may wrap only after `/`, `_` or `.` (never inside `{oid}` or a word) */
function breakable(name: string): ReactNode[] {
  return name.split(/(?<=[/_.])/).flatMap((part, i) => (i ? [<wbr key={i} />, part] : [part]))
}

function FactText({ f }: { f: Fact }) {
  const dot = f.tone === 'ok' ? 'bg-success' : f.tone === 'bad' ? 'bg-destructive' : f.tone === 'warn' ? 'bg-warning' : null
  return (
    <>
      {dot && <span className={cn('mr-1.5 inline-block size-1.5 rounded-full align-middle', dot)} aria-hidden />}
      {f.text}
    </>
  )
}

function MapObject({ obj, order, selected, onSelect }: { obj: StoryObj; order: number; selected: boolean; onSelect: (key: string) => void }) {
  const hue = obj.tone === 'bad' ? 'var(--destructive)' : KIND_HUE[obj.kind]
  const label = `${KIND_LABEL[obj.kind]} ${obj.method ? `${obj.method} ` : ''}${obj.name}: ${obj.facts.map((f) => f.text).join(', ')}`
  return (
    <button
      type="button"
      className="story-obj story-raised story-in relative flex w-full items-start gap-2.5 text-left focus-visible:shadow-focus focus-visible:outline-none"
      style={{ '--c': hue, '--i': Math.min(order, 12) } as CSSProperties}
      data-tone={obj.tone === 'bad' ? 'bad' : undefined}
      aria-pressed={selected}
      aria-label={label}
      data-obj={obj.key}
      data-busy={obj.busy}
      data-testid={`story-obj-${obj.kind}-${obj.id}`}
      onClick={() => onSelect(obj.key)}
    >
      <span className="story-plate">
        <KindIcon kind={obj.kind} />
        <span className="story-ring" />
        <span className="story-spark" />
        {obj.lock && (
          <span className="story-lock" title={`Needs ${obj.lock}`}>
            <LockKeyhole />
          </span>
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-start gap-1.5 text-[13px] leading-[18px] font-[560]" title={obj.name}>
          {obj.method && <MethodBadge method={obj.method} size="sm" className="mt-px shrink-0" />}
          <span className={cn('story-name min-w-0', obj.mono && 'font-mono text-[12px]')}>
            {obj.mono ? breakable(obj.name) : obj.name}
          </span>
        </span>
        <span className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] leading-4 text-muted-foreground">
          {obj.facts.map((f, i) => (
            <span key={i} className={cn('max-w-full truncate whitespace-nowrap', f.tone && TONE_TEXT[f.tone])}>
              <FactText f={f} />
            </span>
          ))}
        </span>
      </span>
    </button>
  )
}
