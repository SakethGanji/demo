import { useCallback, useEffect, useMemo, useRef } from 'react'
import { appsApi } from '@/shared/lib/api'
import { useAppDocumentStore, useAppEditorStore, useRuntimeStateStore, useBreakpointStore, useStyleClassStore } from './stores'
import { resolveAllProps, extractDependencies } from './runtime'
import type { ExpressionContext } from './runtime'
import { dispatchEvent } from './runtime'
import { useRepeatScope } from './types'
import type { AppNode, StoreDefinition, WebhookDefinition, EventHandlerConfig, BreakpointId, RepeatScope } from './types'
import { BREAKPOINTS } from './types'

/**
 * Save the current app document. Creates on first save, updates thereafter.
 */
export function useAppSave() {
  const saving = useRef(false)

  const save = useCallback(async () => {
    if (saving.current) return
    saving.current = true

    try {
      const state = useAppDocumentStore.getState()
      const definition = state.toDefinition()

      if (state.appId) {
        await appsApi.update(state.appId, {
          name: state.appName,
          definition,
        })
      } else {
        const result = await appsApi.create({
          name: state.appName,
          definition,
        })
        useAppDocumentStore.getState().setAppId(result.id)
        // Update URL without full navigation
        const url = new URL(window.location.href)
        url.searchParams.set('appId', result.id)
        window.history.replaceState({}, '', url.toString())
      }
    } finally {
      saving.current = false
    }
  }, [])

  return save
}

/**
 * Load an app document on mount. Resets on unmount.
 */
export function useAppLoad(appId?: string) {
  const loadDocument = useAppDocumentStore((s) => s.loadDocument)
  const reset = useAppDocumentStore((s) => s.reset)

  useEffect(() => {
    if (!appId) return

    let cancelled = false
    appsApi.get(appId).then((data) => {
      if (cancelled) return
      const def = data.definition as {
        nodes: Record<string, AppNode>
        rootNodeId: string
        storeDefinitions?: StoreDefinition[]
        webhookDefinitions?: WebhookDefinition[]
      }
      loadDocument({
        nodes: def.nodes,
        rootNodeId: def.rootNodeId,
        appId: data.id,
        appName: data.name,
        storeDefinitions: def.storeDefinitions,
        webhookDefinitions: def.webhookDefinitions,
      })
    })

    return () => {
      cancelled = true
      reset()
    }
  }, [appId, loadDocument, reset])
}

/**
 * Shared hook for input components that expose runtime state.
 * Encapsulates the pattern of reading from runtimeStateStore and
 * writing back via setComponentState.
 *
 * Usage:
 *   const { value, setValue } = useComponentState<string>(id, 'value', props.defaultValue ?? '')
 *   const { value: checked, setValue: setChecked } = useComponentState<boolean>(id, 'checked', false)
 */
export function useComponentState<T>(
  nodeId: string,
  stateKey: string,
  defaultValue: T
): { value: T; setValue: (v: T) => void } {
  const value = useRuntimeStateStore(
    useCallback((s) => (s.componentState[nodeId]?.[stateKey] as T | undefined) ?? defaultValue, [nodeId, stateKey, defaultValue])
  )
  const setValue = useCallback(
    (v: T) => {
      useRuntimeStateStore.getState().setComponentState(nodeId, stateKey, v)
    },
    [nodeId, stateKey]
  )

  return { value, setValue }
}

/**
 * Returns an event handler that dispatches configured actions.
 * When inside a List component, the repeat scope (item/index) is
 * automatically included so event action expressions can reference them.
 */
export function useEventHandlers(nodeId: string): {
  onEvent: (name: string, payload?: unknown) => void
} {
  const eventHandlers = useAppDocumentStore(
    (s) => s.nodes[nodeId]?.props.__eventHandlers as EventHandlerConfig[] | undefined
  )
  const repeatScope = useRepeatScope()

  const onEvent = useCallback(
    (name: string, payload?: unknown) => {
      dispatchEvent(eventHandlers, name, payload, repeatScope ?? undefined)
    },
    [eventHandlers, repeatScope]
  )

  return useMemo(() => ({ onEvent }), [onEvent])
}

/**
 * Read/write a single prop on a node.
 * Breakpoint-aware: when a non-desktop breakpoint is active,
 * writes go to breakpointOverrides instead of base props.
 * Element-state-aware: when a non-default state is active,
 * writes go to stateStyles instead of base props.
 */
export function useNodeProp<T = unknown>(
  nodeId: string,
  propKey: string
): [T, (value: T) => void] {
  const activeBreakpoint = useBreakpointStore((s) => s.activeBreakpoint)
  const activeElementState = useAppEditorStore((s) => s.activeElementState)

  // Read: check overrides first, fall back to base
  const value = useAppDocumentStore((s) => {
    const node = s.nodes[nodeId]
    if (!node) return undefined as T

    // Element state override
    if (activeElementState !== 'default') {
      const stateVal = node.stateStyles?.[activeElementState]?.[propKey]
      if (stateVal !== undefined) return stateVal as T
    }

    // Breakpoint override
    if (activeBreakpoint !== 'desktop') {
      const bpVal = node.breakpointOverrides?.[activeBreakpoint]?.[propKey]
      if (bpVal !== undefined) return bpVal as T
    }

    return node.props[propKey] as T
  })

  const setValue = useCallback(
    (newValue: T) => {
      const docStore = useAppDocumentStore.getState()

      if (activeElementState !== 'default') {
        // Write to state styles
        docStore.updateNodeStateStyle(nodeId, activeElementState, { [propKey]: newValue })
      } else if (activeBreakpoint !== 'desktop') {
        // Write to breakpoint overrides
        docStore.updateNodeBreakpointProps(nodeId, activeBreakpoint, { [propKey]: newValue })
      } else {
        // Write to base props
        docStore.updateNodeProps(nodeId, { [propKey]: newValue })
      }
    },
    [nodeId, propKey, activeBreakpoint, activeElementState]
  )

  return [value, setValue]
}

/**
 * Resolves the full prop cascade:
 *   style classes → base props → breakpoint overrides → expressions
 *
 * Expressions and runtime state are resolved in both edit and preview modes.
 */
export function useResolvedProps(
  nodeId: string,
  props: Record<string, unknown>
): Record<string, unknown> {
  const repeatScope = useRepeatScope()

  // Breakpoint
  const activeBreakpoint = useBreakpointStore((s) => s.activeBreakpoint)
  const breakpointOverrides = useAppDocumentStore(
    (s) => s.nodes[nodeId]?.breakpointOverrides
  )

  // Style classes
  const classIds = useAppDocumentStore((s) => s.nodes[nodeId]?.classIds)
  const allClasses = useStyleClassStore((s) => s.classes)

  // Build cascaded props: classes → base → breakpoint overrides
  const cascadedProps = useMemo(() => {
    let result: Record<string, unknown> = {}

    // 1. Apply style classes in order
    if (classIds?.length) {
      for (const classId of classIds) {
        const cls = allClasses.find((c) => c.id === classId)
        if (cls) {
          result = { ...result, ...cls.styles }
        }
      }
    }

    // 2. Apply base props (node props always win over classes)
    result = { ...result, ...props }

    // 3. Apply breakpoint overrides (cascade: desktop < tablet < mobile)
    if (breakpointOverrides && activeBreakpoint !== 'desktop') {
      const bpOrder: BreakpointId[] = BREAKPOINTS.map((b) => b.id)
      const activeIdx = bpOrder.indexOf(activeBreakpoint)
      for (let i = 1; i <= activeIdx; i++) {
        const bpId = bpOrder[i]
        const overrides = breakpointOverrides[bpId]
        if (overrides) {
          result = { ...result, ...overrides }
        }
      }
    }

    return result
  }, [props, classIds, allClasses, breakpointOverrides, activeBreakpoint])

  // Extract which stores/components this node's props reference
  const deps = useMemo(() => extractDependencies(cascadedProps), [cascadedProps])

  // Only subscribe to the specific slices of state that our expressions reference
  const relevantState = useRuntimeStateStore((s) => {
    const result: Record<string, unknown> = {}
    for (const dep of deps.stores) {
      result[`s:${dep}`] = s.globalStores[dep]
    }
    for (const dep of deps.components) {
      result[`c:${dep}`] = s.componentState[dep]
    }
    return result
  })

  // Cache previous resolved result to avoid unnecessary child re-renders
  const prevRef = useRef<{ key: string; result: Record<string, unknown> } | null>(null)

  return useMemo(() => {
    const baseCtx = useRuntimeStateStore.getState().getContext()

    // Merge repeat scope (item/index/items) into expression context
    const ctx: ExpressionContext = repeatScope
      ? { ...baseCtx, item: repeatScope.item, index: repeatScope.index, items: repeatScope.items }
      : baseCtx

    const resolved = resolveAllProps(cascadedProps, ctx)

    // Simple cache: if the JSON is identical, return the same object reference
    const key = JSON.stringify(resolved)
    if (prevRef.current?.key === key) {
      return prevRef.current.result
    }
    prevRef.current = { key, result: resolved }
    return resolved
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cascadedProps, relevantState, repeatScope])
}
