Changed: The underwriter's first decision deadline is now 3 days instead of 2 (`due_in` on "Underwriter decision").
Needs: nothing

```json file="changes.json"
{"nodes": [{"name": "Underwriter decision", "type": "Approval", "parameters": {"title": "Mortgage decision for {{ $json.application_id }}", "context": {"Amount": "{{ $json.amount }}", "Property value": "{{ $json.property_value }}", "Loan to value": "{{ $json.ltv }}", "Credit score": "{{ $json.score }}"}, "form": [{"name": "approved_amount", "label": "Approved amount", "type": "number", "required": true}, {"name": "conditions", "label": "Conditions", "type": "textarea", "required": false}], "due_in": "3d", "on_timeout": "timed_out", "notify_email": "{{ $vars.UNDERWRITER_EMAIL }}"}}], "tests": []}
```

The existing tests mock "Underwriter decision" and never check `due_in`, so none of them is wrong now. The tests format has no way to assert a node parameter, so I added no new test.
