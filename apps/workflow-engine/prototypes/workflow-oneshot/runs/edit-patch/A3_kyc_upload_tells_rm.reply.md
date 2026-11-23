Changed: Added an "Email RM complete" step after "Thank customer" that emails $vars.RM_EMAIL with the customer id when documents arrive, and updated the two upload tests and added one test for the no-upload case.
Needs: nothing

```json file="changes.json"
{
 "nodes": [
  {
   "name": "Email RM complete",
   "type": "SendEmail",
   "parameters": {
    "toEmail": "{{ $vars.RM_EMAIL }}",
    "subject": "KYC complete for customer {{ $node[\"Each request\"].json.customer_id }}",
    "bodyFormat": "plain",
    "body": "Customer {{ $node[\"Each request\"].json.customer_id }} has provided their KYC documents and the request is complete."
   }
  }
 ],
 "disconnect": [
  {"source_node": "Thank customer", "target_node": "Each request", "source_output": "main", "target_input": "main"}
 ],
 "connect": [
  {"source_node": "Thank customer", "target_node": "Email RM complete", "source_output": "main", "target_input": "main"},
  {"source_node": "Email RM complete", "target_node": "Each request", "source_output": "main", "target_input": "main"}
 ],
 "tests": [
  {
   "name": "Uploaded while restricted lifts restriction and completes",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Get open requests": {"items": [{"rows": [{"id": 1, "customer_id": "C100", "email": "c100@example.com", "requested_on": "2026-10-10", "reminders_sent": 2, "status": "restricted", "days_open": 12}], "rowCount": 1}]},
    "Check documents": {"http": {"status": 200, "body": {"customer_id": "C100", "uploaded": true}}},
    "Lift restriction": {"http": {"status": 200, "body": {"customer_id": "C100"}}},
    "Mark complete": {"items": [{"success": true}]},
    "Thank customer": {"items": [{"success": true}]},
    "Email RM complete": {"items": [{"success": true}]},
    "Count outcomes": {"items": [{"rows": [{"completed": 1, "reminded": 0, "restricted": 0}], "rowCount": 1}]},
    "Email KYC team": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Lift restriction", "subject": "request", "op": "count", "value": 1},
    {"node": "Lift restriction", "subject": "request", "path": "url", "op": "contains", "value": "C100"},
    {"node": "Mark complete", "subject": "node", "op": "status", "value": "success"},
    {"node": "Thank customer", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "c100@example.com"},
    {"node": "Email RM complete", "subject": "request", "op": "count", "value": 1},
    {"node": "Email RM complete", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "rm@bank.test"},
    {"node": "Email RM complete", "subject": "request", "path": "params.body", "op": "contains", "value": "C100"},
    {"node": "Restrict customer", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email KYC team", "subject": "node", "op": "status", "value": "success"}
   ]
  },
  {
   "name": "Uploaded while open completes without lifting",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Get open requests": {"items": [{"rows": [{"id": 2, "customer_id": "C200", "email": "c200@example.com", "requested_on": "2026-10-18", "reminders_sent": 0, "status": "open", "days_open": 4}], "rowCount": 1}]},
    "Check documents": {"http": {"status": 200, "body": {"customer_id": "C200", "uploaded": true}}},
    "Mark complete": {"items": [{"success": true}]},
    "Thank customer": {"items": [{"success": true}]},
    "Email RM complete": {"items": [{"success": true}]},
    "Count outcomes": {"items": [{"rows": [{"completed": 1, "reminded": 0, "restricted": 0}], "rowCount": 1}]},
    "Email KYC team": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Lift restriction", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Mark complete", "subject": "node", "op": "status", "value": "success"},
    {"node": "Thank customer", "subject": "node", "op": "status", "value": "success"},
    {"node": "Email RM complete", "subject": "request", "path": "params.body", "op": "contains", "value": "C200"},
    {"node": "Email first reminder", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "No documents does not email RM complete",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Get open requests": {"items": [{"rows": [{"id": 7, "customer_id": "C700", "email": "c700@example.com", "requested_on": "2026-10-17", "reminders_sent": 1, "status": "open", "days_open": 5}], "rowCount": 1}]},
    "Check documents": {"http": {"status": 200, "body": {"customer_id": "C700", "uploaded": false}}},
    "Count outcomes": {"items": [{"rows": [{"completed": 0, "reminded": 0, "restricted": 0}], "rowCount": 1}]}
   },
   "expect": [
    {"node": "Email RM complete", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Mark complete", "subject": "node", "op": "status", "value": "not_run"}
   ]
  }
 ]
}
```
