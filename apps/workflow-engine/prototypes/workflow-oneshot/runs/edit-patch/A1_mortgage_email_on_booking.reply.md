Changed: Added an "Email underwriters booked" step after "Mark booked" that emails $vars.UNDERWRITER_EMAIL the application id and the booked amount. "Email welcome" still runs as before, and the two booking tests now cover the new email.
Needs: nothing

```json file="changes.json"
{
 "nodes": [
  {
   "name": "Email underwriters booked",
   "type": "SendEmail",
   "parameters": {
    "toEmail": "{{ $vars.UNDERWRITER_EMAIL }}",
    "subject": "Mortgage booked: {{ $node[\"Gather facts\"].json.application_id }}",
    "bodyFormat": "plain",
    "body": "Mortgage application {{ $node[\"Gather facts\"].json.application_id }} has been booked for {{ $node[\"Capture decision\"].json.approved_amount }}."
   }
  }
 ],
 "connect": [
  {
   "source_node": "Mark booked",
   "target_node": "Email underwriters booked",
   "source_output": "main",
   "target_input": "main"
  }
 ],
 "tests": [
  {
   "name": "Score 579 is declined",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"application_id": "A1", "applicant_email": "a@x.test", "amount": 300000, "property_value": 400000}}]},
   "mocks": {
    "Get credit score": {"http": {"status": 200, "body": {"applicant_id": "A1", "score": 579}}},
    "Email decline": {"items": [{"success": true}]},
    "Mark declined": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Underwriter decision", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email decline", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "a@x.test"},
    {"node": "Mark declined", "subject": "request", "path": "params.statements.0.params.0", "op": "eq", "value": "declined"},
    {"node": "Send for signature", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email underwriters booked", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "Score 580 and exactly 500000 books without committee",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"application_id": "A2", "applicant_email": "b@x.test", "amount": 500000, "property_value": 700000}}]},
   "mocks": {
    "Get credit score": {"http": {"status": 200, "body": {"applicant_id": "A2", "score": 580}}},
    "Underwriter decision": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"approved_amount": 500000, "conditions": "Valuation"}, "decided_by": "test"}},
    "Email offer": {"items": [{"success": true}]},
    "Send for signature": {"http": {"status": 200, "body": {"envelope_id": "env1"}}},
    "Get signature status": {"http": {"status": 200, "body": {"envelope_id": "env1", "status": "signed"}}},
    "Book mortgage": {"http": {"status": 200, "body": {"mortgage_id": "M1"}}},
    "Mark booked": {"items": [{"success": true}]},
    "Email welcome": {"items": [{"success": true}]},
    "Email underwriters booked": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Committee member 1", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Book mortgage", "subject": "request", "path": "body.amount", "op": "eq", "value": 500000},
    {"node": "Mark booked", "subject": "request", "path": "params.statements.1.params.2", "op": "eq", "value": "booked"},
    {"node": "Email welcome", "subject": "node", "op": "status", "value": "success"},
    {"node": "Email underwriters booked", "subject": "request", "op": "count", "value": 1},
    {"node": "Email underwriters booked", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "underwriters@bank.test"},
    {"node": "Email underwriters booked", "subject": "request", "path": "params.body", "op": "contains", "value": "A2"},
    {"node": "Email underwriters booked", "subject": "request", "path": "params.body", "op": "contains", "value": "500000"},
    {"node": "Cancel envelope", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "Over 500000 needs two committee approvals",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"application_id": "A3", "applicant_email": "c@x.test", "amount": 700000, "property_value": 900000}}]},
   "mocks": {
    "Get credit score": {"http": {"status": 200, "body": {"applicant_id": "A3", "score": 700}}},
    "Underwriter decision": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"approved_amount": 600000, "conditions": "None"}, "decided_by": "test"}},
    "Committee member 1": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Committee member 2": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Email offer": {"items": [{"success": true}]},
    "Send for signature": {"http": {"status": 200, "body": {"envelope_id": "env3"}}},
    "Get signature status": {"http": {"status": 200, "body": {"envelope_id": "env3", "status": "signed"}}},
    "Book mortgage": {"http": {"status": 200, "body": {"mortgage_id": "M3"}}},
    "Mark booked": {"items": [{"success": true}]},
    "Email welcome": {"items": [{"success": true}]},
    "Email underwriters booked": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Committee member 2", "subject": "node", "op": "status", "value": "success"},
    {"node": "Book mortgage", "subject": "request", "path": "body.amount", "op": "eq", "value": 600000},
    {"node": "Email underwriters booked", "subject": "request", "path": "params.body", "op": "contains", "value": "A3"},
    {"node": "Email underwriters booked", "subject": "request", "path": "params.body", "op": "contains", "value": "600000"}
   ]
  }
 ]
}
```
