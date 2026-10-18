import { describe, expect, it } from 'vitest';
import {
  badgeText, contractStatus, invalidFieldNames, missingRequired, parseVersion, pruneInputs, readCallParams, readTriggerFields, versionLabel,
} from './subworkflowParams';
import { isExtensionOwnedParam } from '../../../lib/jsonParam';

describe('parseVersion', () => {
  it('reads draft, latest and numbers', () => {
    expect(parseVersion('draft')).toBe('draft');
    expect(parseVersion('LATEST')).toBe('latest');
    expect(parseVersion(3)).toBe(3);
    expect(parseVersion('v4')).toBe(4);
    expect(parseVersion('7')).toBe(7);
  });
  it('falls back to latest on junk', () => {
    expect(parseVersion('banana')).toBe('latest');
    expect(parseVersion(0)).toBe('latest');
    expect(parseVersion(1.5)).toBe('latest');
    expect(parseVersion(undefined)).toBe('latest');
  });
});

describe('readCallParams', () => {
  it('reads a node with no version as legacy: draft, pass through', () => {
    const p = readCallParams({ workflowId: 'wf_1' });
    expect(p).toMatchObject({ legacy: true, version: 'draft', inputMode: 'passThrough', workflowId: 'wf_1', mode: 'once', timeoutSeconds: 300 });
  });
  it('reads a v2 node', () => {
    const p = readCallParams({ workflowId: 'wf_1', version: 2, inputMode: 'map', inputs: { a: '{{ $json.a }}' }, includeItem: true, mode: 'each', timeoutSeconds: 9000 });
    expect(p).toMatchObject({ legacy: false, version: 2, inputMode: 'map', includeItem: true, mode: 'each', timeoutSeconds: 3600 });
    expect(p.inputs).toEqual({ a: '{{ $json.a }}' });
  });
  it('accepts inputs stored as a JSON string and drops a bad one', () => {
    expect(readCallParams({ version: 'latest', inputs: '{"a":"x"}' }).inputs).toEqual({ a: 'x' });
    expect(readCallParams({ version: 'latest', inputs: '{nope' }).inputs).toEqual({});
  });
  it('survives no params at all', () => {
    expect(readCallParams(undefined).workflowId).toBe('');
  });
});

describe('labels', () => {
  it('formats versions and the badge', () => {
    expect(versionLabel(3)).toBe('v3');
    expect(versionLabel('latest')).toBe('latest');
    expect(badgeText('Refund eligibility', 3)).toBe('Refund eligibility · v3');
    expect(badgeText('Refund eligibility', 'draft')).toBe('Refund eligibility · draft');
    expect(badgeText(undefined, 'latest')).toBe('latest');
  });
});

describe('input mapping', () => {
  const fields = [
    { name: 'amount', type: 'number', required: true },
    { name: 'note', type: 'string', required: false },
  ];
  it('lists required fields with no expression', () => {
    expect(missingRequired(fields, {}, false)).toEqual(['amount']);
    expect(missingRequired(fields, { amount: '  ' }, false)).toEqual(['amount']);
    expect(missingRequired(fields, { amount: '{{ $json.amount }}' }, false)).toEqual([]);
  });
  it('treats the incoming item as a source when it is included', () => {
    expect(missingRequired(fields, {}, true)).toEqual([]);
  });
  it('drops mapped keys the child no longer declares', () => {
    expect(pruneInputs({ amount: 'a', gone: 'b' }, fields)).toEqual({ amount: 'a' });
  });
});

describe('trigger fields', () => {
  it('normalises stored fields', () => {
    expect(readTriggerFields([{ name: 'a', type: 'number', required: true }, { name: 'b', type: 'weird' }, null])).toEqual([
      { name: 'a', type: 'number', required: true, description: '' },
      { name: 'b', type: 'string', required: false, description: '' },
    ]);
    expect(readTriggerFields('nope')).toEqual([]);
  });
  it('flags empty and repeated names', () => {
    const f = readTriggerFields([{ name: 'a' }, { name: '' }, { name: 'a' }, { name: 'b' }]);
    expect([...invalidFieldNames(f)]).toEqual([1, 2]);
  });
});

describe('contractStatus', () => {
  it('is typed when any field is declared', () => {
    expect(contractStatus({ input_fields: [1], output_fields: [] })).toBe('typed');
    expect(contractStatus({ input_fields: [], output_fields: ['x'] })).toBe('typed');
    expect(contractStatus({ input_fields: [], output_fields: [] })).toBe('untyped');
    expect(contractStatus(undefined)).toBe('untyped');
  });
});

describe('extension-owned parameters', () => {
  it('hides what the sub-workflow panels render and keeps the rest in the generic form', () => {
    for (const p of ['workflowId', 'version', 'inputMode', 'inputs', 'includeItem', 'mode', 'timeoutSeconds']) {
      expect(isExtensionOwnedParam('ExecuteWorkflow', p)).toBe(true);
    }
    expect(isExtensionOwnedParam('ExecuteWorkflow', 'customInput')).toBe(false);
    expect(isExtensionOwnedParam('ExecuteWorkflow', 'outputMode')).toBe(false);
    expect(isExtensionOwnedParam('ExecuteWorkflowTrigger', 'fields')).toBe(true);
    expect(isExtensionOwnedParam('ExecuteWorkflowTrigger', 'inputSchema')).toBe(false);
  });
});
