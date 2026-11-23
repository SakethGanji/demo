/** Starting templates for a hand-written module (UI-DESIGN §4.3). */

export type TemplateKey = 'empty' | 'crud' | 'external'

export const TEMPLATE_LABELS: Record<TemplateKey, string> = {
  empty: 'Empty',
  crud: 'Table CRUD',
  external: 'External API',
}

const pathOf = (name: string) => `/${name.replace(/_/g, '-')}`

export function templateSource(key: TemplateKey, module: string): string {
  const path = pathOf(module)
  if (key === 'crud')
    return `from studio import api, HTTPError
import studio

items = studio.db.table("${module}")


@api.get("${path}")
def list_items(req):
    return items.list(limit=int(req.query.get("limit", 50)))


@api.post("${path}")
def create_item(req):
    if not isinstance(req.body, dict):
        raise HTTPError(422, {"error": "a JSON object body is required"})
    return items.insert(req.body)


@api.delete("${path}/{id}")
def delete_item(req):
    items.delete(int(req.params["id"]))
    return {"deleted": True}
`
  if (key === 'external')
    return `from studio import api
import studio

BASE = "https://api.example.com"


@api.get("${path}/status")
def status(req):
    res = studio.nodes.http_request(
        method="GET",
        url=f"{BASE}/status",
        headers=[{"name": "Authorization", "value": f"Bearer {studio.secret('API_TOKEN')}"}],
    )
    return res[0]["body"]
`
  return `from studio import api


@api.get("${path}")
def hello(req):
    return {"hello": "world"}
`
}

export const MODULE_NAME_RE = /^[a-z_][a-z0-9_]*$/
export const RESERVED_MODULE_NAMES = new Set(['new', 'generate', 'default', 'studio'])

export function moduleNameError(name: string): string | null {
  if (!name) return 'Enter a module name.'
  if (RESERVED_MODULE_NAMES.has(name)) return `"${name}" is reserved. Use letters, digits and _, starting with a letter.`
  if (!MODULE_NAME_RE.test(name)) return 'Use lowercase letters, digits and _, starting with a letter or _.'
  return null
}
