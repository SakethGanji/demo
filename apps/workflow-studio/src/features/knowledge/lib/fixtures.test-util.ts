import type { EdgeView, NodeView } from '../types'

export function node(key: string, kind: string, over: Partial<NodeView> = {}): NodeView {
  const [, slug] = key.split(':')
  return {
    id: `id-${key}`, key, kind, subkind: null, scope: 'global', visibility: 'project', label: over.label ?? slug.replaceAll('_', ' '),
    summary: '', aliases: [], props: {}, source: 'seed', status: 'active', validated_count: 0, ...over,
  }
}
let eid = 0
export function edge(src: NodeView, rel: string, dst: NodeView, props: Record<string, unknown> = {}): EdgeView {
  return { id: ++eid, src_id: src.id, rel, dst_id: dst.id, props, source: 'seed' }
}

/** A graph shaped like the bank-ops seed: 8 concepts, 3 roles, 12 capabilities of five subkinds, 2 patterns, 1 app. */
export function sampleGraph() {
  const concepts = ['approval', 'escalation', 'sla', 'assignment', 'intake', 'report', 'schedule', 'notification'].map((s, i) =>
    node(`concept:${s}`, 'concept', { props: { kind: ['control', 'process', 'control', 'process', 'process', 'artifact', 'process', 'process'][i], definition: `${s} definition`, slots: [] } }))
  const roles = ['ops_manager', 'ops_analyst', 'ops_head'].map((s) => node(`role:${s}`, 'role'))
  const subs = ['node_type', 'api_operation', 'workflow', 'subworkflow', 'template']
  const caps = Array.from({ length: 12 }, (_, i) => node(`cap:c${i}`, 'capability', { subkind: subs[i % subs.length], label: `Cap ${String(i).padStart(2, '0')}` }))
  const patterns = [node('pattern:p1', 'pattern', { validated_count: 2, source: 'learned' }), node('pattern:p2', 'pattern')]
  const apps = [node('app:a1', 'app')]
  const nodes = [...concepts, ...roles, ...caps, ...patterns, ...apps]
  const edges: EdgeView[] = [
    edge(concepts[1], 'requires', concepts[2]),
    edge(concepts[1], 'requires', concepts[3]),
    edge(concepts[0], 'requires_permission', roles[0], { slot: 'approver_role' }),
    edge(concepts[1], 'requires_permission', roles[2], { slot: 'target_role' }),
    edge(roles[1], 'escalates_to', roles[0]),
    ...caps.map((c, i) => edge(c, 'implements', concepts[i % concepts.length])),
    edge(patterns[0], 'implements', concepts[0]),
    edge(patterns[0], 'implements', concepts[1]),
    edge(patterns[1], 'implements', concepts[3]),
    edge(apps[0], 'implements', concepts[4]),
    edge(apps[0], 'composes', caps[0]),
    edge(caps[1], 'composes', caps[2]),
  ]
  return { nodes, edges, concepts, roles, caps, patterns, apps }
}
