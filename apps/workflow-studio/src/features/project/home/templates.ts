/**
 * The three "Start something new" templates. `crud` reuses the backend template; the other two are
 * small modules written here in the same style. Choosing one creates the project, seeds an unsaved
 * module draft (like the New module dialog) and opens the backend editor on it.
 */
import type { Column } from '@/shared/api/types'
import type { TemplateArtKind } from './art'

export interface HomeTemplate {
  key: TemplateArtKind
  title: string
  blurb: string
  facts: string
  /** project name and module name the template creates */
  projectName: string
  module: string
  source: () => string
  /** tables the module uses, created with the project (the analyser can't infer columns from a pass-through body) */
  tables: Array<{ name: string; columns: Column[] }>
}

const webhookSource = `from studio import api, HTTPError
import studio

events = studio.db.table("events")


@api.post("/hooks/stripe")
def receive_event(req):
    if not isinstance(req.body, dict):
        raise HTTPError(422, {"error": "a JSON object body is required"})
    return events.insert({"payload": req.body})
`

const jobSource = `from studio import api
import studio

rates = studio.db.table("rates")


@api.post("/sync-rates")
def sync_rates(req):
    res = studio.nodes.http_request(method="GET", url="https://api.example.com/rates")
    return rates.insert({"payload": res[0]["body"]})
`

const crudSource = `from studio import api, HTTPError
import studio

items = studio.db.table("items")


@api.get("/items")
def list_items(req):
    return items.list(limit=int(req.query.get("limit", 50)))


@api.post("/items")
def create_item(req):
    if not isinstance(req.body, dict):
        raise HTTPError(422, {"error": "a JSON object body is required"})
    return items.insert(req.body)


@api.put("/items/{id}")
def update_item(req):
    if not isinstance(req.body, dict):
        raise HTTPError(422, {"error": "a JSON object body is required"})
    return items.update(int(req.params["id"]), req.body)


@api.delete("/items/{id}")
def delete_item(req):
    items.delete(int(req.params["id"]))
    return {"deleted": True}
`

export const HOME_TEMPLATES: HomeTemplate[] = [
  {
    key: 'webhook',
    title: 'Webhook receiver',
    blurb: 'Accept events from Stripe, GitHub or Slack and store them.',
    facts: '1 route · 1 table',
    projectName: 'Webhook receiver',
    module: 'webhooks',
    source: () => webhookSource,
    tables: [{ name: 'events', columns: [{ name: 'payload', type: 'json', required: true }] }],
  },
  {
    key: 'crud',
    title: 'CRUD API',
    blurb: 'Create, read, update and delete rows in a table.',
    facts: '4 routes · 1 table',
    projectName: 'CRUD API',
    module: 'items',
    source: () => crudSource,
    tables: [{ name: 'items', columns: [{ name: 'name', type: 'string', required: true }] }],
  },
  {
    key: 'cron',
    title: 'Scheduled job',
    blurb: 'Call an API from a route and keep the results.',
    facts: '1 route · 1 table',
    projectName: 'Scheduled job',
    module: 'rates',
    source: () => jobSource,
    tables: [{ name: 'rates', columns: [{ name: 'payload', type: 'json', required: true }] }],
  },
]
