Changed: Both credit committee members now get their approval task at the same time, through one Approval that makes a task per member. The offer goes out only when both approve, and if either rejects the applicant gets one decline. I also removed the old sequential-committee rejection test, which still mocked the deleted nodes. That leftover test caused both failures.
Needs: nothing

```json file="changes.json"
{
 "nodes": [
  {"name": "Split committee seats", "type": "Code", "parameters": {"code": "return [new_item({\"application_id\": json_data[\"application_id\"], \"seat\": 1}), new_item({\"application_id\": json_data[\"application_id\"], \"seat\": 2})]"}},
  {"name": "Committee review", "type": "Approval", "parameters": {
   "per_item": true,
   "title": "Large mortgage approval ({{ $json.seat }} of 2) for {{ $node[\"Gather facts\"].json.application_id }}",
   "context": {
    "Approved amount": "{{ $node[\"Capture decision\"].json.approved_amount }}",
    "Conditions": "{{ $node[\"Capture decision\"].json.conditions }}"
   },
   "four_eyes": true,
   "notify_email": "{{ $vars.CREDIT_COMMITTEE_EMAIL }}"}},
  {"name": "Count approvals", "type": "ItemLists", "parameters": {"operation": "summarize", "summarizeField": "items", "includeCount": true}},
  {"name": "Both approved?", "type": "If", "parameters": {"condition": "{{ $json.count == 2 }}"}},
  {"name": "One decline per application", "type": "ItemLists", "parameters": {"operation": "removeDuplicates", "compareField": "application_id", "keep": "first"}}
 ],
 "remove_nodes": ["Committee member 1", "Committee member 2"],
 "connect": [
  {"source_node": "Over 500000?", "target_node": "Split committee seats", "source_output": "true", "target_input": "main"},
  {"source_node": "Split committee seats", "target_node": "Committee review", "source_output": "main", "target_input": "main"},
  {"source_node": "Committee review", "target_node": "Count approvals", "source_output": "approved", "target_input": "main"},
  {"source_node": "Committee review", "target_node": "One decline per application", "source_output": "rejected", "target_input": "main"},
  {"source_node": "Count approvals", "target_node": "Both approved?", "source_output": "main", "target_input": "main"},
  {"source_node": "Both approved?", "target_node": "Email offer", "source_output": "true", "target_input": "main"},
  {"source_node": "One decline per application", "target_node": "Email decline", "source_output": "main", "target_input": "main"}
 ],
 "remove_tests": ["Committee rejection declines"],
 "tests": [
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
    "Email welcome": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Committee review", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Book mortgage", "subject": "request", "path": "body.amount", "op": "eq", "value": 500000},
    {"node": "Mark booked", "subject": "request", "path": "params.statements.1.params.2", "op": "eq", "value": "booked"},
    {"node": "Email welcome", "subject": "node", "op": "status", "value": "success"},
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
    "Committee review": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Email offer": {"items": [{"success": true}]},
    "Send for signature": {"http": {"status": 200, "body": {"envelope_id": "env3"}}},
    "Get signature status": {"http": {"status": 200, "body": {"envelope_id": "env3", "status": "signed"}}},
    "Book mortgage": {"http": {"status": 200, "body": {"mortgage_id": "M3"}}},
    "Mark booked": {"items": [{"success": true}]},
    "Email welcome": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Split committee seats", "port": "main", "op": "count", "value": 2},
    {"node": "Count approvals", "path": "count", "op": "eq", "value": 2},
    {"node": "Both approved?", "port": "true", "op": "count", "value": 1},
    {"node": "Email offer", "subject": "request", "op": "count", "value": 1},
    {"node": "Book mortgage", "subject": "request", "path": "body.amount", "op": "eq", "value": 600000}
   ]
  },
  {
   "name": "Committee rejection declines once",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"application_id": "A4", "applicant_email": "d@x.test", "amount": 700000, "property_value": 900000}}]},
   "mocks": {
    "Get credit score": {"http": {"status": 200, "body": {"applicant_id": "A4", "score": 700}}},
    "Underwriter decision": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"approved_amount": 600000, "conditions": "None"}, "decided_by": "test"}},
    "Committee review": {"port": "rejected", "approval": {"decision": "rejected", "comment": "too risky", "form_data": {}, "decided_by": "test"}},
    "Email decline": {"items": [{"success": true}]},
    "Mark declined": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Email decline", "subject": "request", "op": "count", "value": 1},
    {"node": "Email decline", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "d@x.test"},
    {"node": "Email offer", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Send for signature", "subject": "node", "op": "status", "value": "not_run"}
   ]
  }
 ]
}
```
