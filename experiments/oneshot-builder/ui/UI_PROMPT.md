You write the one page (App.tsx) for a small internal app at a bank. The backend already exists and is described below. Don't ask questions or plan out loud: reply with one line starting `Built:` that says what the page shows, then the file:

```tsx file="App.tsx"
...
```

Rules:
- `export default function App()`. Import only from "react" and "./kit". The kit's full source is below; use it as your reference for what exists and its exact props.
- Wrap the page in `<AppShell title=... subtitle=...>`. It shows the signed-in user and role.
- Data:
  - Read with `useTable` (table rows, which have every column plus `id`), `useQuery` (a saved query, which returns ONLY the columns listed for it below) and `useRow` (one row plus `_history`).
  - Never call fetch.
  - Everything refreshes by itself after any action.
- Changes go through the backend's actions:
  - Use `<ActionButton action="..." inputs={{ the_id: row.id }} />` for every action.
  - Pass the inputs you know (usually the row id). ActionButton builds a form for the rest from the action's inputs, hides itself from roles that can't run it, and shows errors.
  - Never build your own forms for actions.
- Only show a row's action buttons when its status allows the action. Need a status or id for buttons? Use table rows (filter them in code), not a query, because queries don't return those unless listed.
- Use `<RecordTable table="..." />` for reference tables that people edit directly (the ones with edit_roles). It handles list, search, add, edit and delete with permissions.
- `DataTable` columns:
  - every `key` must be a real column of those rows, unless you give `render`;
  - use `format`: "money", "date", "status" or "number";
  - put row buttons in a `render` column wrapped in `<span className="kit-row" onClick={(e) => e.stopPropagation()}>`.
- Show what matters to each role:
  - lead with `Stats` (counts and amounts that need attention);
  - then `Tabs` for the main jobs (a work queue for approvers, all records, any reports from saved queries);
  - use `me.is("role")` to show role-specific tabs.
- Clicking a row may open a `Modal` with the details and `<History items={row._history} />`.
- Plain, short labels in business language. No emojis. Don't restyle the kit; you may use its class names (kit-row, kit-muted).

Example for a different, simpler app:

```tsx file="App.tsx"
{{EXAMPLE}}
```

The kit (./kit), CSS omitted:

```tsx
{{KIT}}
```

The app the page is for:

What the user asked for: {{REQUEST}}

The backend (tables with columns, saved queries with the columns they return, actions with inputs and who may run them):
```json
{{APP}}
```

A few rows from each table:
```json
{{SAMPLES}}
```
