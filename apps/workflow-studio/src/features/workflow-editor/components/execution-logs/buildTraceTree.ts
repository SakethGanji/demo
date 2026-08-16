/**
 * Build Agent Trace Tree
 *
 * Converts flat AgentTraceEvent[] into a nested TraceNode tree for rendering.
 * Groups events by nodeName (root vs child agents), then by iteration,
 * pairs tool_call/tool_result, and recursively builds child agent subtrees.
 */

import type { AgentTraceEvent, TraceNode } from '../../types/workflow';

/** Build a trace tree from flat agent events */
export function buildAgentTraceTree(
  events: AgentTraceEvent[],
  rootNodeName: string,
): TraceNode {
  // Separate events by nodeName
  const eventsByAgent = new Map<string, AgentTraceEvent[]>();
  for (const e of events) {
    const list = eventsByAgent.get(e.nodeName) || [];
    list.push(e);
    eventsByAgent.set(e.nodeName, list);
  }

  const rootEvents = eventsByAgent.get(rootNodeName) || [];

  // Build root agent node
  const children = buildAgentChildren(rootEvents, rootNodeName, eventsByAgent);

  // Compute duration from first to last event
  const timestamps = events.map((e) => e.timestamp).filter(Boolean);
  const duration = timestamps.length >= 2
    ? Math.max(...timestamps) - Math.min(...timestamps)
    : undefined;

  // Count iterations
  const iterations = new Set(
    rootEvents.map((e) => e.data.iteration as number).filter((n) => n != null)
  ).size;

  return {
    kind: 'agent',
    name: rootNodeName,
    duration,
    iterations: iterations || undefined,
    children,
  };
}

function buildAgentChildren(
  agentEvents: AgentTraceEvent[],
  agentName: string,
  allAgentEvents: Map<string, AgentTraceEvent[]>,
): TraceNode[] {
  // Group events by iteration
  const iterationMap = new Map<number, AgentTraceEvent[]>();
  const noIterationEvents: AgentTraceEvent[] = [];

  for (const e of agentEvents) {
    const iter = e.data.iteration as number | undefined;
    if (iter != null) {
      const list = iterationMap.get(iter) || [];
      list.push(e);
      iterationMap.set(iter, list);
    } else {
      noIterationEvents.push(e);
    }
  }

  const children: TraceNode[] = [];

  // Sort iterations
  const sortedIterations = [...iterationMap.keys()].sort((a, b) => a - b);

  for (const iterNum of sortedIterations) {
    const iterEvents = iterationMap.get(iterNum)!;
    const iterChildren = buildIterationChildren(iterEvents, agentName, allAgentEvents);
    children.push({
      kind: 'iteration',
      number: iterNum,
      children: iterChildren,
    });
  }

  // Handle non-iteration events (like child_complete, output_validation at root level)
  for (const e of noIterationEvents) {
    const node = eventToTraceNode(e, agentName, allAgentEvents);
    if (node) children.push(node);
  }

  return children;
}

function buildIterationChildren(
  events: AgentTraceEvent[],
  agentName: string,
  allAgentEvents: Map<string, AgentTraceEvent[]>,
): TraceNode[] {
  const children: TraceNode[] = [];
  // Index tool results by id for pairing
  const toolResults = new Map<string, AgentTraceEvent>();
  for (const e of events) {
    if (e.type === 'agent:tool_result' && e.data.id) {
      toolResults.set(e.data.id as string, e);
    }
  }

  for (const e of events) {
    // Skip tool_result — they get paired into tool_call nodes
    if (e.type === 'agent:tool_result') continue;

    if (e.type === 'agent:tool_call') {
      const callId = e.data.id as string | undefined;
      const result = callId ? toolResults.get(callId) : undefined;
      const toolName = e.data.tool as string;

      // Check if this is a delegation/spawn call
      if (toolName === 'delegate_to_skill' || toolName === 'spawn_agent') {
        const spawnNode = buildSpawnNode(e, result, agentName, allAgentEvents);
        children.push(spawnNode);
      } else {
        // Regular tool call
        const duration = result
          ? result.timestamp - e.timestamp
          : undefined;

        children.push({
          kind: 'tool_call',
          tool: toolName,
          input: e.data.arguments,
          result: result ? parseToolResult(result.data.result) : undefined,
          isError: result ? (result.data.is_error as boolean) : undefined,
          duration: duration && duration > 0 ? duration : undefined,
          id: callId,
        });
      }
    } else {
      const node = eventToTraceNode(e, agentName, allAgentEvents);
      if (node) children.push(node);
    }
  }

  return children;
}

function buildSpawnNode(
  callEvent: AgentTraceEvent,
  resultEvent: AgentTraceEvent | undefined,
  parentName: string,
  allAgentEvents: Map<string, AgentTraceEvent[]>,
): TraceNode {
  const toolName = callEvent.data.tool as string;
  const args = callEvent.data.arguments as Record<string, unknown> | undefined;
  const skill = toolName === 'delegate_to_skill'
    ? (args?.skill_name as string) || (args?.skill as string)
    : undefined;
  const task = (args?.task as string) || '';

  // Find child agent events by matching name pattern
  // Skill: "ParentAgent/skill:skillName"
  // Spawn: "ParentAgent/agentLabel"
  let childName: string | undefined;
  if (skill) {
    childName = `${parentName}/skill:${skill}`;
  } else {
    // For spawn_agent, try to find matching child events
    const agentLabel = (args?.agent_name as string) || (args?.name as string) || '';
    if (agentLabel) {
      childName = `${parentName}/${agentLabel}`;
    }
  }

  const spawnChildren: TraceNode[] = [];
  if (childName) {
    const childEvents = allAgentEvents.get(childName) || [];
    if (childEvents.length > 0) {
      // Build child agent subtree
      const childAgentChildren = buildAgentChildren(childEvents, childName, allAgentEvents);

      // Find child_complete event for metadata
      const childComplete = childEvents.find((e) => e.type === 'agent:child_complete');

      const childTimestamps = childEvents.map((e) => e.timestamp).filter(Boolean);
      const childDuration = childTimestamps.length >= 2
        ? Math.max(...childTimestamps) - Math.min(...childTimestamps)
        : undefined;
      const childIterations = childComplete
        ? (childComplete.data.iterations as number)
        : new Set(childEvents.map((e) => e.data.iteration as number).filter((n) => n != null)).size;

      spawnChildren.push({
        kind: 'agent',
        name: skill ? `Skill: ${skill}` : (args?.agent_name as string) || 'Sub-agent',
        duration: childDuration,
        iterations: childIterations || undefined,
        children: childAgentChildren,
      });
    }
  }

  // Extract input/result/duration from call/result events
  const input = args;
  const result = resultEvent ? parseToolResult(resultEvent.data.result) : undefined;
  const duration = resultEvent
    ? resultEvent.timestamp - callEvent.timestamp
    : undefined;

  return {
    kind: 'spawn',
    skill,
    task,
    input,
    result,
    duration: duration && duration > 0 ? duration : undefined,
    children: spawnChildren,
  };
}

function eventToTraceNode(
  event: AgentTraceEvent,
  _agentName: string,
  _allAgentEvents: Map<string, AgentTraceEvent[]>,
): TraceNode | null {
  switch (event.type) {
    case 'agent:thinking': {
      // Strip <plan>...</plan> and <reflect>...</reflect> blocks —
      // they're shown as separate Plan/Reflect rows already
      let text = (event.data.content as string) || '';
      text = text.replace(/<plan>[\s\S]*?<\/plan>/gi, '').replace(/<reflect>[\s\S]*?<\/reflect>/gi, '').trim();
      if (!text) return null; // nothing left after stripping
      return { kind: 'thinking', content: text };
    }
    case 'agent:plan':
      return { kind: 'plan', content: (event.data.plan as string) || '' };
    case 'agent:reflect':
      return { kind: 'reflect', content: (event.data.reflection as string) || '' };
    case 'agent:response':
      return { kind: 'response', content: (event.data.content as string) || '' };
    case 'agent:output_validation':
      return {
        kind: 'validation',
        status: (event.data.status as string) || 'unknown',
        errors: event.data.errors as string[] | undefined,
      };
    case 'agent:spawn':
      // Redundant with tool_call rows (delegate_to_skill / spawn_agent) — skip
      return null;
    case 'agent:child_complete':
      // Absorbed into spawn nodes during tree building — skip standalone
      return null;
    case 'agent:token':
      // Streaming tokens — not rendered in trace
      return null;
    default:
      return null;
  }
}

/** Try to parse a tool result string as JSON, fallback to string */
function parseToolResult(result: unknown): unknown {
  if (typeof result === 'string') {
    try {
      return JSON.parse(result);
    } catch {
      return result;
    }
  }
  return result;
}
