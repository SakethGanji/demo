/** Backend prompt starters (the setup guide's Backend card and the backend editor's empty state). */
import { BarChart3, Globe, Table2 } from 'lucide-react'

export const STARTERS = [
  {
    key: 'crud',
    label: 'Table CRUD',
    icon: Table2,
    prompt: 'Store items in a table. POST /items creates one, GET /items lists them, DELETE /items/{id} removes one.',
  },
  {
    key: 'wrap',
    label: 'Wrap an API',
    icon: Globe,
    prompt: 'Call an external HTTP API with a secret token and return a trimmed response from GET /status.',
  },
  {
    key: 'summary',
    label: 'Summary',
    icon: BarChart3,
    prompt: 'GET /summary returns totals grouped by category from a table.',
  },
]
