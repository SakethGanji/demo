/** Helpers for the generic `json` parameter field and for params an extension owns. */

/** Text for a json/code editor from any stored value (or the property default). Never throws, always a string. */
export function jsonFieldText(value: unknown, fallback?: unknown): string {
  const v = value === undefined ? fallback : value;
  if (v === undefined) return '';
  if (typeof v === 'string') return v;
  if (v === null) return '';
  try {
    return JSON.stringify(v, null, 2) ?? '';
  } catch {
    return String(v);
  }
}

/** Parameters a node's extension panel renders itself; the generic parameter list skips them. */
const EXTENSION_OWNED: Record<string, string[]> = {
  HttpRequest: ['api_ref', 'path_params'],
};

/** Params the endpoint defines once `api_ref` is set (the method and URL come from the catalog). */
const API_ENDPOINT_DEFINED = ['method', 'url'];

export function isExtensionOwnedParam(nodeType: string, paramName: string, params?: Record<string, unknown>): boolean {
  if (EXTENSION_OWNED[nodeType]?.includes(paramName)) return true;
  return nodeType === 'HttpRequest' && !!params?.api_ref && API_ENDPOINT_DEFINED.includes(paramName);
}
