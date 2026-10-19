/**
 * The Overview's one-paragraph story: what the project has, how its last day went, the first thing that is
 * broken and why, and what the live environment serves. Only the kinds the project has are named, and every
 * count or name is a token the page turns into a button (jump to the object, its lane or a section).
 */
import { formatMs, pluralize } from '../../lib/format'
import type { LaneKey, StoryModel, Tone } from './model'

export type TokTarget =
  | { type: 'obj'; key: string }
  | { type: 'lane'; lane: LaneKey }
  | { type: 'section'; id: 'river' | 'ship' }
  | { type: 'go'; to: 'new-route' | 'new-workflow' | 'test-route' | 'run-workflow' }

export type Seg = { text: string; strong?: boolean } | { tok: string; tone?: Tone; target: TokTarget }

export interface SentenceInput {
  model: StoryModel
  runs24h: number
  failed24h: number
  p50: number | null
  missing: { key: string; missing_in: string[] }[]
  /** the live environment and its active release, when a release environment exists */
  release: { live: string; number: number | null } | null
}

function joinList(parts: Seg[][]): Seg[] {
  if (parts.length <= 1) return parts[0] ?? []
  const out: Seg[] = []
  parts.forEach((p, i) => {
    if (i > 0) out.push({ text: i === parts.length - 1 ? ' and ' : ', ' })
    out.push(...p)
  })
  return out
}

export function buildSentence({ model, runs24h, failed24h, p50, missing, release }: SentenceInput): Seg[] {
  const count = (k: LaneKey) => model.lanes.find((l) => l.key === k)?.objs ?? []
  const has = (k: LaneKey) => count(k).length > 0
  const built: Seg[][] = []
  if (has('apps')) built.push([{ tok: pluralize(count('apps').length, 'screen'), target: { type: 'lane', lane: 'apps' } }])
  if (has('routes')) built.push([{ tok: pluralize(count('routes').length, 'route'), target: { type: 'lane', lane: 'routes' } }])
  if (has('modules')) {
    const n = count('modules').length
    built.push([{ tok: `${n} Python ${n === 1 ? 'module' : 'modules'}`, target: { type: 'lane', lane: 'modules' } }])
  }
  if (has('workflows')) built.push([{ tok: pluralize(count('workflows').length, 'workflow'), target: { type: 'lane', lane: 'workflows' } }])
  if (has('data')) {
    const data = count('data')
    const onlyDatasets = data.every((o) => o.kind === 'dataset')
    built.push([{ tok: pluralize(data.length, onlyDatasets ? 'dataset' : 'data store'), target: { type: 'lane', lane: 'data' } }])
  }

  const out: Seg[] = built.length ? [{ text: 'This project has ' }, ...joinList(built), { text: '.' }] : [{ text: 'This project is empty so far.' }]

  if (runs24h > 0) {
    out.push({ text: ' ' }, { text: pluralize(runs24h, 'run'), strong: true }, { text: ' in the last 24 hours, ' })
    out.push(failed24h ? { tok: `${failed24h} failed`, tone: 'bad', target: { type: 'section', id: 'river' } } : { tok: 'all succeeded', tone: 'ok', target: { type: 'section', id: 'river' } })
    if (p50 != null) out.push({ text: ', typically in ' }, { text: formatMs(p50), strong: true })
    out.push({ text: '.' })
  } else if (has('apps') && !has('routes') && !has('workflows')) {
    out.push(
      { text: ' Nothing calls a backend yet: ' },
      { tok: 'wire a route', target: { type: 'go', to: 'new-route' } },
      { text: ' or ' },
      { tok: 'add a workflow', target: { type: 'go', to: 'new-workflow' } },
      { text: ' behind a screen.' },
    )
  } else if (has('routes')) {
    out.push({ text: ' No runs yet: ' }, { tok: 'test a route', target: { type: 'go', to: 'test-route' } }, { text: ' to see one.' })
  } else if (has('workflows')) {
    out.push({ text: ' No runs yet: ' }, { tok: 'run a workflow', target: { type: 'go', to: 'run-workflow' } }, { text: ' to see one.' })
  } else if (built.length) {
    out.push({ text: ' No runs yet.' })
  }

  // the first broken thing, with its reason when we know it
  const brokenWf = model.lanes.find((l) => l.key === 'workflows')?.objs.find((o) => o.tone === 'bad')
  const brokenRoute = model.lanes.find((l) => l.key === 'routes')?.objs.find((o) => o.tone === 'bad')
  const broken = brokenWf ?? brokenRoute
  let reasonKey: string | undefined
  if (broken) {
    const label = broken.method ? `${broken.method} ${broken.name}` : broken.name
    out.push({ text: ' ' }, { tok: label, tone: 'bad', target: { type: 'obj', key: broken.key } }, { text: ' is failing' })
    if (broken.lock) {
      reasonKey = broken.lock
      const env = missing.find((m) => m.key === broken.lock)?.missing_in[0]
      out.push({ text: `: ${broken.lock} is not set${env ? ` in ${env}` : ''}.` })
    } else if (broken.kind === 'route') {
      const st = model.routeStats.get(broken.id)
      out.push({ text: st?.last.status_code != null ? `: its last call returned ${st.last.status_code}.` : '.' })
    } else {
      const st = model.workflowStats.get(broken.id)
      out.push({ text: st?.last.error_count ? `: ${pluralize(st.last.error_count, 'step')} errored on the last run.` : '.' })
    }
  }

  const miss = missing.find((m) => m.key !== reasonKey && m.missing_in.length)
  if (miss) out.push({ text: ' ' }, { tok: `${miss.key} missing in ${miss.missing_in[0]}`, tone: 'warn', target: { type: 'section', id: 'ship' } }, { text: '.' })

  if (release)
    out.push({ text: ' ' }, { text: release.live, strong: true }, { text: release.number != null ? ` serves release #${release.number}.` : ' has no release yet.' })

  return out
}

/** The sentence as plain text (for the accessible name and copy). */
export const sentenceText = (segs: Seg[]) => segs.map((s) => ('tok' in s ? s.tok : s.text)).join('')
