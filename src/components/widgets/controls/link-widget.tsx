import type { WidgetComponentProps } from "../widget-registry";

/**
 * DirectionalLinkWidget — one-way property synchronization (source → target).
 * Renders nothing. Sync logic runs at the store level.
 */
export function DirectionalLinkWidget(_props: WidgetComponentProps) {
  return null;
}

/**
 * LinkWidget — bidirectional property synchronization (source ↔ target).
 * Renders nothing. Sync logic runs at the store level.
 */
export function LinkWidget(_props: WidgetComponentProps) {
  return null;
}
