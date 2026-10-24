Changed: On day 10, the KYC team now gets an approval task (1 day to answer) before a customer is restricted. Approval restricts as before; a rejection or no answer skips the restriction and emails the RM to follow up.
Needs: nothing. `$vars.KYC_TEAM_EMAIL` and `$vars.RM_EMAIL` already exist.

```json file="changes.json"
{
 "nodes": [
  {
   "name": "KYC confirms restriction",
   "type": "Approval",
   "parameters": {
    "title": "Confirm restriction for customer {{ $node[\"Each request\"].json.customer_id }}",
    "context": {
     "Customer": "{{ $node[\"Each request\"].json.customer_id }}",
     "Email": "{{ $node[\"Each request\"].json.email }}",
     "Days open": "{{ $node[\"Each request\"].json.days_open }}",
     "Reminders sent": "{{ $node[\"Each request\"].json.reminders_sent }}"
    },
    "notify_email": "{{ $vars.KYC_TEAM_EMAIL }}",
    "due_in": "1d",
    "on_timeout": "timed_out"
   }
  },
  {
   "name": "Email RM to follow up",
   "type": "SendEmail",
   "parameters": {
    "toEmail": "{{ $vars.RM_EMAIL }}",
    "subject": "Please follow up with customer {{ $node[\"Each request\"].json.customer_id }}",
    "bodyFormat": "plain",
    "body": "Customer {{ $node[\"Each request\"].json.customer_id }} ({{ $node[\"Each request\"].json.email }}) has not provided KYC documents after {{ $node[\"Each request\"].json.days_open }} days. The KYC team did not confirm a restriction (rejected or no answer within a day), so the customer has not been restricted. Please follow up."
   }
  }
 ],
 "disconnect": [
  {"source_node": "Day 10 and 2 reminders?", "target_node": "Restrict customer", "source_output": "true", "target_input": "main"}
 ],
 "connect": [
  {"source_node": "Day 10 and 2 reminders?", "target_node": "KYC confirms restriction", "source_output": "true", "target_input": "main"},
  {"source_node": "KYC confirms restriction", "target_node": "Restrict customer", "source_output": "approved", "target_input": "main"},
  {"source_node": "KYC confirms restriction", "target_node": "Email RM to follow up", "source_output": "rejected", "target_input": "main"},
  {"source_node": "KYC confirms restriction", "target_node": "Email RM to follow up", "source_output": "timed_out", "target_input": "main"},
  {"source_node": "Email RM to follow up", "target_node": "Each request", "source_output": "main", "target_input": "main"}
 ],
 "tests": [
  {
   "name": "Day 10 with two reminders restricts and tells customer and RM",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Get open requests": {"items": [{"rows": [{"id": 5, "customer_id": "C500", "email": "c500@example.com", "requested_on": "2026-10-12", "reminders_sent": 2, "status": "open", "days_open": 10}], "rowCount": 1}]},
    "Check documents": {"http": {"status": 200, "body": {"customer_id": "C500", "uploaded": false}}},
    "KYC confirms restriction": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Restrict customer": {"http": {"status": 200, "body": {"customer_id": "C500"}}},
    "Mark restricted": {"items": [{"success": true}]},
    "Tell customer restricted": {"items": [{"success": true}]},
    "Email RM to call": {"items": [{"success": true}]},
    "Count outcomes": {"items": [{"rows": [{"completed": 0, "reminded": 0, "restricted": 1}], "rowCount": 1}]},
    "Email KYC team": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "KYC confirms restriction", "port": "approved", "op": "count", "value": 1},
    {"node": "Restrict customer", "subject": "request", "op": "count", "value": 1},
    {"node": "Restrict customer", "subject": "request", "path": "url", "op": "contains", "value": "C500"},
    {"node": "Mark restricted", "subject": "node", "op": "status", "value": "success"},
    {"node": "Tell customer restricted", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "c500@example.com"},
    {"node": "Email RM to call", "subject": "node", "op": "status", "value": "success"},
    {"node": "Email RM to follow up", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email second reminder", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "Day 10 rejected by KYC does not restrict and emails RM to follow up",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Get open requests": {"items": [{"rows": [{"id": 5, "customer_id": "C500", "email": "c500@example.com", "requested_on": "2026-10-12", "reminders_sent": 2, "status": "open", "days_open": 10}], "rowCount": 1}]},
    "Check documents": {"http": {"status": 200, "body": {"customer_id": "C500", "uploaded": false}}},
    "KYC confirms restriction": {"port": "rejected", "approval": {"decision": "rejected", "comment": "Customer is travelling", "form_data": {}, "decided_by": "test"}},
    "Email RM to follow up": {"items": [{"success": true}]},
    "Count outcomes": {"items": [{"rows": [{"completed": 0, "reminded": 0, "restricted": 0}], "rowCount": 1}]}
   },
   "expect": [
    {"node": "KYC confirms restriction", "port": "rejected", "op": "count", "value": 1},
    {"node": "Restrict customer", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Mark restricted", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Tell customer restricted", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email RM to call", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email RM to follow up", "subject": "request", "op": "count", "value": 1},
    {"node": "Email RM to follow up", "subject": "request", "path": "params.subject", "op": "contains", "value": "C500"}
   ]
  },
  {
   "name": "Day 10 with no KYC answer does not restrict and emails RM to follow up",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Get open requests": {"items": [{"rows": [{"id": 5, "customer_id": "C500", "email": "c500@example.com", "requested_on": "2026-10-12", "reminders_sent": 2, "status": "open", "days_open": 11}], "rowCount": 1}]},
    "Check documents": {"http": {"status": 200, "body": {"customer_id": "C500", "uploaded": false}}},
    "KYC confirms restriction": {"port": "timed_out", "approval": {"decision": "timed_out", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Email RM to follow up": {"items": [{"success": true}]},
    "Count outcomes": {"items": [{"rows": [{"completed": 0, "reminded": 0, "restricted": 0}], "rowCount": 1}]}
   },
   "expect": [
    {"node": "KYC confirms restriction", "port": "timed_out", "op": "count", "value": 1},
    {"node": "Restrict customer", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Mark restricted", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email RM to follow up", "subject": "request", "op": "count", "value": 1},
    {"node": "Email RM to follow up", "subject": "request", "path": "params.subject", "op": "contains", "value": "C500"}
   ]
  },
  {
   "name": "Day 9 with two reminders does not ask KYC yet",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Get open requests": {"items": [{"rows": [{"id": 7, "customer_id": "C700", "email": "c700@example.com", "requested_on": "2026-10-14", "reminders_sent": 2, "status": "open", "days_open": 9}], "rowCount": 1}]},
    "Check documents": {"http": {"status": 200, "body": {"customer_id": "C700", "uploaded": false}}},
    "Count outcomes": {"items": [{"rows": [{"completed": 0, "reminded": 0, "restricted": 0}], "rowCount": 1}]}
   },
   "expect": [
    {"node": "KYC confirms restriction", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Restrict customer", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email RM to follow up", "subject": "node", "op": "status", "value": "not_run"}
   ]
  }
 ]
}
```
