import { describe, expect, it } from 'vitest';
import type { AppBuildView, PartStatus, PartView } from '../appTypes';
import {
  approvableSpecs, bannerModel, buildOrder, calledBy, canKeep, cronText, failingParts, isAppProposal, keepLabel, keepMode, miniMap, nextPart,
  partSubtitle, progressOf, stageLabel, stageOf, stepOf, summaryLine, valueText,
} from './appState';

const part = (key: string, status: PartStatus, extra: Partial<PartView> = {}): PartView => ({
  key, title: key, role: 'entry', trigger: 'form', concepts: [], depends_on: [], interface: null, cron: null, reuse: null, status,
  proposal_id: `p_${key}`, workflow_id: null, passed: null, accepted_failing: false, tests: null, rounds: null, error: null, ...extra,
});

const app = (status: AppBuildView['status'], parts: PartView[], extra: Partial<AppBuildView> = {}): AppBuildView => ({
  id: 'ab_1', project_id: 'p', status, actor: 'Ana', request: 'x', understanding: null, summary: null, questions: [], plan: null, plan_issues: [],
  adapted_from: null, parts, next_part: null, can_keep: status === 'ready', keep_blockers: [], install_id: null, installed: null, kept_by: null,
  kept_at: null, created_at: null, updated_at: null, ...extra,
});

// the §5.2 plan: two sub-workflows, three entries
const PARTS = [
  part('assign_owner', 'ready', { role: 'subworkflow', trigger: 'execute' }),
  part('build_report', 'ready', { role: 'subworkflow', trigger: 'execute' }),
  part('log_exception', 'ready', { depends_on: ['assign_owner'] }),
  part('weekly_report', 'ready', { trigger: 'cron', cron: '0 8 * * 1', depends_on: ['build_report'] }),
  part('report_view', 'ready', { depends_on: ['build_report'], interface: { kind: 'custom', slug: 'exceptions-report', screen: 'report_table' } }),
];
const allReady = (over: Partial<PartView> = {}) => PARTS.map((p) => ({ ...p, ...over }));

describe('part statuses', () => {
  it.each<[PartStatus, string, string]>([
    ['pending', 'waiting', 'waiting'],
    ['spec_draft', 'spec', 'plan to approve'],
    ['spec_approved', 'approved', 'plan approved'],
    ['building', 'building', 'building…'],
    ['ready', 'built', 'built'],
    ['kept', 'kept', 'kept'],
    ['failed', 'failed', 'failed'],
    ['reused', 'reused', 'reused'],
  ])('%s is %s ("%s")', (status, stage, label) => {
    expect(stageOf({ status })).toBe(stage);
    expect(stageLabel(part('x', status))).toBe(label);
  });

  it('a built part shows its tests ("built · 5/5") and only when it has some', () => {
    expect(stageLabel(part('x', 'ready', { tests: { passed: 5, total: 5 } }))).toBe('built · 5/5');
    expect(stageLabel(part('x', 'ready', { tests: { passed: null, total: 0 } }))).toBe('built');
    expect(stageLabel(part('x', 'ready', { tests: { passed: 4, total: 5 } }))).toBe('built · 4/5');
  });

  it('counts built parts, leaving out a reused one', () => {
    const ps = [part('a', 'ready'), part('b', 'building'), part('c', 'reused'), part('d', 'kept')];
    expect(progressOf(ps)).toEqual({ built: 2, total: 3 });
  });
});

describe('next-part ordering', () => {
  it('puts a dependency before the part that calls it, keeping plan order otherwise', () => {
    const ps = [part('log', 'pending', { depends_on: ['assign'] }), part('assign', 'pending'), part('view', 'pending', { depends_on: ['report'] }), part('report', 'pending')];
    expect(buildOrder(ps)).toEqual(['assign', 'log', 'report', 'view']);
  });

  it('survives a cycle and an unknown dependency', () => {
    const ps = [part('a', 'pending', { depends_on: ['b', 'zzz'] }), part('b', 'pending', { depends_on: ['a'] })];
    expect(buildOrder(ps).sort()).toEqual(['a', 'b']);
  });

  it('builds the first part that is not done, skipping reused and failed parts', () => {
    const ps = [part('a', 'reused'), part('b', 'failed'), part('c', 'ready'), part('d', 'spec_approved'), part('e', 'spec_approved')];
    expect(nextPart(app('specs', ps))?.key).toBe('d');
  });

  it('follows the engine when it names the next part', () => {
    const ps = [part('a', 'spec_approved'), part('b', 'spec_approved')];
    expect(nextPart(app('specs', ps, { next_part: 'b' }))?.key).toBe('b');
  });

  it('never builds a reused part, even when the engine names it', () => {
    const ps = [part('a', 'reused'), part('b', 'spec_approved')];
    expect(nextPart(app('specs', ps, { next_part: 'a' }))?.key).toBe('b');
  });

  it('has no next part when everything is done', () => {
    expect(nextPart(app('ready', allReady()))).toBeNull();
  });

  it('lists the parts whose plan waits for approval', () => {
    const ps = [part('a', 'spec_draft'), part('b', 'spec_approved'), part('c', 'spec_draft', { proposal_id: null })];
    expect(approvableSpecs(app('specs', ps)).map((p) => p.key)).toEqual(['a']);
  });
});

describe('the step the panel is on', () => {
  it('walks answer, plan, specs, build, keep, done', () => {
    expect(stepOf(app('clarify', []))).toBe('answer');
    expect(stepOf(app('planned', []))).toBe('plan');
    expect(stepOf(app('specs', [part('a', 'spec_draft')]))).toBe('specs');
    expect(stepOf(app('specs', [part('a', 'spec_approved')]))).toBe('build');
    expect(stepOf(app('building', [part('a', 'building')]))).toBe('build');
    expect(stepOf(app('ready', allReady()))).toBe('keep');
    expect(stepOf(app('kept', allReady({ status: 'kept' })))).toBe('done');
    expect(stepOf(app('discarded', []))).toBe('closed');
  });
});

describe('the Keep-enabled rule', () => {
  it('is on when every part is built and the engine agrees', () => {
    expect(keepMode(app('ready', allReady()))).toBe('ready');
    expect(canKeep(app('ready', allReady()))).toBe(true);
  });

  it('is off while the engine says can_keep is false (a part is not ready)', () => {
    const notReady = [{ part_key: 'report_view', status: 'building', reason: 'not_ready' }];
    expect(keepMode(app('building', allReady(), { can_keep: false, keep_blockers: notReady }))).toBe('blocked');
    expect(keepMode(app('ready', allReady(), { can_keep: false }))).toBe('blocked');
    expect(canKeep(app('specs', [], { can_keep: false }))).toBe(false);
  });

  it('asks for "keep anyway" when keep_blockers lists a part with a red approved test', () => {
    const red = [{ part_key: 'log_exception', status: 'ready', reason: 'tests_failing' }];
    const a = app('ready', allReady(), { keep_blockers: red });
    expect(keepMode(a)).toBe('override');
    expect(canKeep(a)).toBe(true);
    expect(failingParts(a)).toEqual(['log_exception']);
  });

  it('does not second-guess the engine from the parts (a red part without a blocker is not an override)', () => {
    const ps = allReady();
    ps[2] = { ...ps[2], passed: false };
    expect(keepMode(app('ready', ps))).toBe('ready');
  });

  it('keeps a reused part out of the way: can_keep decides', () => {
    const ps = allReady();
    ps[0] = { ...ps[0], status: 'reused' };
    expect(keepMode(app('ready', ps))).toBe('ready');
  });

  it('labels the button with workflows and tables, not counting a reused part', () => {
    const plan = { v: 1, title: 'T', adapted_from: null, tables: [{ key: 'cases', name: 'exception_cases', columns: [] }], parts: [] };
    expect(keepLabel({ parts: allReady(), plan })).toBe('Keep app (5 workflows, 1 table)');
    const ps = allReady();
    ps[0] = { ...ps[0], status: 'reused' };
    expect(keepLabel({ parts: ps, plan })).toBe('Keep app (4 workflows, 1 table)');
    expect(keepLabel({ parts: [part('a', 'ready')], plan: null })).toBe('Keep app (1 workflow)');
  });
});

describe('words', () => {
  const plan = {
    v: 1, title: 'Operational exceptions', adapted_from: null, tables: [{ key: 'cases', name: 'exception_cases', columns: [] }],
    parts: [
      { key: 'assign_owner', role: 'subworkflow' as const, title: 'Assign owner', trigger: 'execute', concepts: [], depends_on: [] },
      { key: 'log_exception', role: 'entry' as const, title: 'Log an exception', trigger: 'form', concepts: [], depends_on: ['assign_owner'] },
    ],
    screens: ['form: Log an exception', 'page: Exceptions report (table)'],
  };

  it('summarises the plan', () => {
    expect(summaryLine(plan)).toBe('2 parts · 1 table · 2 screens');
    expect(summaryLine({ parts: [plan.parts[0]], tables: [], screens: [] })).toBe('1 part · 0 tables');
  });

  it('names who calls a sub-workflow', () => {
    expect(calledBy(plan, 'assign_owner')).toEqual(['Log an exception']);
    expect(partSubtitle(plan.parts[0], plan)).toBe('sub-workflow · called by Log an exception');
    expect(partSubtitle(plan.parts[1], plan)).toBe('form');
  });

  it('describes a cron entry and a page', () => {
    expect(partSubtitle({ key: 'w', role: 'entry', trigger: 'cron', cron: '0 8 * * 1', interface: null }, plan)).toBe('cron Mon 08:00');
    expect(partSubtitle({ key: 'v', role: 'entry', trigger: 'form', cron: null, interface: { kind: 'custom', slug: 's', screen: 'report_table' } }, plan)).toBe('page (table)');
  });

  it('turns a cron into a sentence', () => {
    expect(cronText('0 8 * * 1')).toBe('Mondays 08:00');
    expect(cronText('30 17 * * 5', true)).toBe('Fri 17:30');
    expect(cronText('0 8 * * *')).toBe('daily 08:00');
    expect(cronText('*/5 * * * *')).toBe('*/5 * * * *');
  });

  it('reads slot values as a person does', () => {
    expect(valueText('role:ops_head')).toBe('ops_head');
    expect(valueText('0 8 * * 1')).toBe('Mondays 08:00');
    expect(valueText('48h')).toBe('48h');
    expect(valueText(['a', 'role:b'])).toBe('a, b');
    expect(valueText(null)).toBe('none');
  });
});

describe('isAppProposal', () => {
  const u = (shape: 'app' | 'workflow', questions: unknown[] = []) => ({ shape, questions }) as never;
  it('is an app when the shape is app and the shape question is not open', () => {
    expect(isAppProposal({ understanding: u('app'), app_build_id: null })).toBe(true);
    expect(isAppProposal({ understanding: u('workflow'), app_build_id: null })).toBe(false);
    expect(isAppProposal(null)).toBe(false);
  });
  it('waits for the person while "build it as an app?" is open in the questions', () => {
    const q = { id: 'q_shape', answer: null, required: false };
    expect(isAppProposal({ understanding: u('app', [q]), app_build_id: null, status: 'clarify' })).toBe(false);
    expect(isAppProposal({ understanding: u('app', [{ ...q, answer: 'app' }]), app_build_id: null, status: 'clarify' })).toBe(true);
    // nothing blocked, so the default (app) stands
    expect(isAppProposal({ understanding: u('app', [q]), app_build_id: null, status: 'spec_draft' })).toBe(true);
  });
  it('is an app when it is a part of one', () => {
    expect(isAppProposal({ understanding: undefined, app_build_id: 'ab_1' })).toBe(true);
  });
});

describe('the mini map', () => {
  it('puts entries left, sub-workflows right and draws one edge per dependency', () => {
    const m = miniMap({
      tables: [{ key: 'cases', name: 'exception_cases', columns: [] }],
      parts: [
        { key: 'assign_owner', role: 'subworkflow', title: 'Assign owner', trigger: 'execute', concepts: [], depends_on: [] },
        { key: 'log_exception', role: 'entry', title: 'Log', trigger: 'form', concepts: [], depends_on: ['assign_owner'], tables: ['cases'] },
        { key: 'weekly', role: 'entry', title: 'Weekly', trigger: 'cron', concepts: [], depends_on: ['assign_owner', 'gone'] },
      ],
    });
    const x = (k: string) => m.nodes.find((n) => n.key === k)!.x;
    expect(x('log_exception')).toBeLessThan(x('assign_owner'));
    expect(m.edges).toEqual([{ from: 'log_exception', to: 'assign_owner' }, { from: 'weekly', to: 'assign_owner' }]);
    expect(m.links).toEqual([{ from: 'log_exception', table: 'cases' }]);
    expect(m.nodes.find((n) => n.key === 'weekly')!.kind).toBe('cron');
    for (const n of m.nodes) { expect(n.y).toBeGreaterThanOrEqual(0); expect(n.y).toBeLessThanOrEqual(m.h); }
  });

  it('is empty for no plan', () => {
    expect(miniMap(null).nodes).toEqual([]);
  });
});

describe('banner model is covered in AdaptedFromBanner.test.ts', () => {
  it('exists', () => expect(typeof bannerModel).toBe('function'));
});
