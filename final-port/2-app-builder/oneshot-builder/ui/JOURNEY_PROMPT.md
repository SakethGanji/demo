You write the end-to-end test for a small internal bank app: a journey that a browser robot clicks through, like the people who will use it. Don't ask questions or plan out loud: reply with one line starting `Covers:` that lists the jobs tested, then the file:

```json file="journey.json"
[...]
```

The step format (from the robot's source):
{{STEPS}}

Rules:
- Test every role's main job end to end through the page, as that role:
  - create a new record;
  - take it through its whole life (submit → approve → complete, and so on);
  - include at least one refusal the business rules require (the error shows and nothing is saved);
  - include at least one button that must be hidden from a role or a status.
- After every change, add an `api` step that checks the stored values. `/api/tables/<table>?<column>=<value>` filters by column.
  - Find new records by a value you typed, never by an id you guess (you don't know the ids the app gives new rows).
  - Expect only what actions.py actually does. Read the code for defaults, such as who becomes the owner or lead when you leave a field empty.
- People:
  - `{"as": "<role>"}` signs in as `<role>.user@bank.example`;
  - add `"user": "<email>"` when a rule depends on who someone is (e.g. the manager of a branch, the owner of an item); use the sample data below for that;
  - remember who created what (someone may not approve their own work).
- Buttons and tabs: use the exact labels in App.tsx.
  - A tab matches by its start ("Approval queue" matches "Approval queue (2)").
  - A row button needs `"row"`: text that appears only in that table row, such as the reference of a record you created.
- Forms: an ActionButton opens a form only when a *required* input wasn't passed in by the page (or the page sets `form`). The form shows every input not passed in; otherwise the button runs straight away, so don't fill or submit anything. Optional inputs can't be filled then.
  - Each field is labelled with the input name with underscores turned into spaces and the first letter capitalised (`invoice_number` → "Invoice number"); `"Vendor"` matches "Vendor id".
  - A dropdown for a reference shows the referenced row's first text column; choose by that text.
  - Choice dropdowns show the value the same way ("needs_review" → "Needs review").
  - Dates are YYYY-MM-DD.
- After a successful action, a toast "<button label>: done" shows. Errors show the backend's message.
- The data starts as the sample rows below, every run.
- Give new records distinctive values starting with E2E so rows are easy to find.
- Aim for 30–60 steps.

Example for a different app:

```json file="journey.json"
{{EXAMPLE}}
```

The app:

What the user asked for: {{REQUEST}}

Backend (tables, saved queries, actions with inputs, roles, descriptions):
```json
{{APP}}
```

The business rules (actions.py):
```python
{{ACTIONS}}
```

Sample rows (row ids are 1, 2, 3… in this order):
```json
{{SAMPLES}}
```

The page (App.tsx):
```tsx
{{PAGE}}
```
