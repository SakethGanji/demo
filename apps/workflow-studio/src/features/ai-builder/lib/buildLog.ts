/**
 * The live step log (PLAN-MOTION-UI §2.2, "building"): the engine's events as plain sentences, grouped by round.
 * Rows that are about one step carry its `node` name, so hovering a row can light that step on the canvas.
 */
import { roundsFromEvents, type RoundProgress } from '../aiProposalStore';
import { opsFromRoundShape, snapshotFromShape, type ShapeSnapshot } from '../stage/buildOps';
import { parseRoundShape } from '../stage/opsFromEvents';
import type { AiEvent } from '../types';

export type LogKind = 'add' | 'change' | 'remove' | 'link' | 'check' | 'test';
export type LogTone = 'neutral' | 'ok' | 'bad' | 'warn';

export interface LogRow {
  /** stable across polls: `${seq}:${index}` */
  id: string;
  kind: LogKind;
  text: string;
  detail?: string;
  /** the step this row is about (hover target) */
  node?: string;
  /** every step a test touched (hover lights the failing one first) */
  nodes?: string[];
  tone: LogTone;
}

export interface LogRound {
  round: number;
  progress: RoundProgress;
  rows: LogRow[];
}

/** `HttpRequest` -> `Http request` */
export function humanType(type: string): string {
  const spaced = type.replace(/[_-]+/g, ' ').replace(/([a-z0-9])([A-Z])/g, '$1 $2').trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase() : type;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function buildLog(events: AiEvent[], base: ShapeSnapshot | null = null): LogRound[] {
  const progress = new Map(roundsFromEvents(events).map((r) => [r.round, r]));
  const rows = new Map<number, LogRow[]>();
  const push = (round: number, row: LogRow) => {
    const list = rows.get(round) ?? [];
    list.push(row);
    rows.set(round, list);
  };
  let prev: ShapeSnapshot | null = base;

  for (const e of events) {
    const round = typeof e.payload.round === 'number' ? e.payload.round : null;
    if (round === null) continue;
    const id = (i: number) => `${e.seq}:${i}`;

    if (e.event === 'round_shape') {
      const shape = parseRoundShape(e.payload);
      if (!shape) continue;
      let i = 0;
      let links = 0;
      for (const op of opsFromRoundShape(prev, shape)) {
        if (op.kind === 'addNode') push(round, { id: id(i++), kind: 'add', text: `Added ${op.node}`, detail: humanType(op.type), node: op.node, tone: 'ok' });
        else if (op.kind === 'changeNode') push(round, { id: id(i++), kind: 'change', text: `Changed ${op.node}`, detail: op.fields.join(', ') || undefined, node: op.node, tone: 'warn' });
        else if (op.kind === 'removeNode') push(round, { id: id(i++), kind: 'remove', text: `Removed ${op.node}`, node: op.node, tone: 'bad' });
        else if (op.kind === 'addEdge') links++;
      }
      if (links) push(round, { id: id(i++), kind: 'link', text: `Linked ${plural(links, 'pair', 'pairs')} of steps`, tone: 'neutral' });
      prev = snapshotFromShape(shape);
    } else if (e.event === 'checked') {
      const errors = typeof e.payload.errors === 'number' ? e.payload.errors : 0;
      push(round, errors === 0
        ? { id: id(0), kind: 'check', text: 'Checked for mistakes', detail: 'none found', tone: 'ok' }
        : { id: id(0), kind: 'check', text: 'Checked for mistakes', detail: `${plural(errors, 'mistake')} found`, tone: 'bad' });
    } else if (e.event === 'tested') {
      const results = progress.get(round)?.tests ?? [];
      if (results.length) {
        results.forEach((r, i) => push(round, {
          id: id(i), kind: 'test', text: r.name, tone: r.status === 'passed' ? 'ok' : 'bad',
          detail: r.status === 'passed' ? 'passed' : r.failed_nodes.length ? `failed at ${r.failed_nodes.join(', ')}` : r.status === 'errored' ? 'could not run' : 'failed',
          node: r.failed_nodes[0] ?? r.path[r.path.length - 1], nodes: r.path,
        }));
      } else {
        const passed = typeof e.payload.passed === 'number' ? e.payload.passed : 0;
        const failed = (typeof e.payload.failed === 'number' ? e.payload.failed : 0) + (typeof e.payload.errored === 'number' ? e.payload.errored : 0);
        push(round, { id: id(0), kind: 'test', text: 'Ran the tests', detail: `${passed} passing${failed ? `, ${failed} failing` : ''}`, tone: failed ? 'bad' : 'ok' });
      }
    }
  }

  return [...progress.values()].map((p) => ({ round: p.round, progress: p, rows: rows.get(p.round) ?? [] }));
}
