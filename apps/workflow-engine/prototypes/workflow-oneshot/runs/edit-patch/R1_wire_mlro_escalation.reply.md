Changed: The MLRO review now sends a 4-hour timeout to a new "Head of compliance review" task, which has 2 hours and the same true match or false positive choice. If the head of compliance also times out, or rejects, the wire is blocked.
Needs: nothing. `$vars.HEAD_OF_COMPLIANCE_EMAIL` already exists.

```json file="changes.json"
{
 "nodes": [
  {
   "name": "MLRO review",
   "type": "Approval",
   "parameters": {
    "title": "Sanctions hit on wire {{ $node[\"Wire received\"].json.body.wire_id }}",
    "context": {
     "Beneficiary": "{{ $node[\"Wire received\"].json.body.beneficiary_name }}",
     "Country": "{{ $node[\"Wire received\"].json.body.beneficiary_country }}",
     "Amount": "{{ $node[\"Wire received\"].json.body.amount }}",
     "List": "{{ $json.body.list }}"
    },
    "form": [
     {
      "name": "disposition",
      "label": "Disposition",
      "type": "select",
      "options": ["false_positive", "true_match"],
      "required": true
     }
    ],
    "due_in": "4h",
    "on_timeout": "timed_out",
    "notify_email": "{{ $vars.MLRO_EMAIL }}"
   }
  },
  {
   "name": "Head of compliance review",
   "type": "Approval",
   "parameters": {
    "title": "Sanctions hit on wire {{ $node[\"Wire received\"].json.body.wire_id }} (escalated, MLRO did not decide)",
    "context": {
     "Beneficiary": "{{ $node[\"Wire received\"].json.body.beneficiary_name }}",
     "Country": "{{ $node[\"Wire received\"].json.body.beneficiary_country }}",
     "Amount": "{{ $node[\"Wire received\"].json.body.amount }}",
     "List": "{{ $json.body.list }}"
    },
    "form": [
     {
      "name": "disposition",
      "label": "Disposition",
      "type": "select",
      "options": ["false_positive", "true_match"],
      "required": true
     }
    ],
    "due_in": "2h",
    "on_timeout": "timed_out",
    "notify_email": "{{ $vars.HEAD_OF_COMPLIANCE_EMAIL }}"
   }
  }
 ],
 "connect": [
  {"source_node": "MLRO review", "target_node": "Head of compliance review", "source_output": "timed_out", "target_input": "main"},
  {"source_node": "Head of compliance review", "target_node": "True match?", "source_output": "approved", "target_input": "main"},
  {"source_node": "Head of compliance review", "target_node": "Block wire", "source_output": "rejected", "target_input": "main"},
  {"source_node": "Head of compliance review", "target_node": "Block wire", "source_output": "timed_out", "target_input": "main"}
 ],
 "remove_tests": ["MLRO rejection (timeout) also blocks the wire"],
 "tests": [
  {
   "name": "MLRO rejection blocks the wire without escalating",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"wire_id": "W6", "account_id": "A6", "customer_id": "C6", "amount": 20000, "beneficiary_name": "Bad Actor", "beneficiary_country": "XX"}}]},
   "mocks": {
    "Get customer": {"items": [{"rows": [{"full_name": "Ana Diaz", "phone": "+15550100", "email": "ana@example.test"}], "rowCount": 1}]},
    "Callback task": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"customer_confirmed": "yes"}, "decided_by": "test"}},
    "Screen beneficiary": {"http": {"status": 200, "body": {"hit": true, "list": "OFAC"}}},
    "MLRO review": {"port": "rejected", "approval": {"decision": "rejected", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Block wire": {"http": {"status": 200, "body": {"wire_id": "W6", "status": "blocked"}}},
    "Audit blocked": {"items": [{"success": true}]},
    "SAR task": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"sar_reference": "SAR-456"}, "decided_by": "test"}},
    "Audit SAR filed": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Block wire", "subject": "node", "op": "status", "value": "success"},
    {"node": "Head of compliance review", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Release wire", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "MLRO times out, head of compliance confirms true match: blocked",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"wire_id": "W8", "account_id": "A8", "customer_id": "C8", "amount": 20000, "beneficiary_name": "Bad Actor", "beneficiary_country": "XX"}}]},
   "mocks": {
    "Get customer": {"items": [{"rows": [{"full_name": "Ana Diaz", "phone": "+15550100", "email": "ana@example.test"}], "rowCount": 1}]},
    "Callback task": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"customer_confirmed": "yes"}, "decided_by": "test"}},
    "Screen beneficiary": {"http": {"status": 200, "body": {"hit": true, "list": "OFAC"}}},
    "MLRO review": {"port": "timed_out", "approval": {"decision": "timed_out", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Head of compliance review": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"disposition": "true_match"}, "decided_by": "test"}},
    "Block wire": {"http": {"status": 200, "body": {"wire_id": "W8", "status": "blocked"}}},
    "Audit blocked": {"items": [{"success": true}]},
    "SAR task": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"sar_reference": "SAR-800"}, "decided_by": "test"}},
    "Audit SAR filed": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Head of compliance review", "subject": "node", "op": "status", "value": "success"},
    {"node": "Block wire", "subject": "request", "op": "count", "value": 1},
    {"node": "Audit SAR filed", "subject": "node", "op": "status", "value": "success"},
    {"node": "Release wire", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "MLRO times out, head of compliance says false positive: released",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"wire_id": "W9", "account_id": "A9", "customer_id": "C9", "amount": 20000, "beneficiary_name": "Acme Ltd", "beneficiary_country": "DE"}}]},
   "mocks": {
    "Get customer": {"items": [{"rows": [{"full_name": "Ana Diaz", "phone": "+15550100", "email": "ana@example.test"}], "rowCount": 1}]},
    "Callback task": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"customer_confirmed": "yes"}, "decided_by": "test"}},
    "Screen beneficiary": {"http": {"status": 200, "body": {"hit": true, "list": "EU"}}},
    "MLRO review": {"port": "timed_out", "approval": {"decision": "timed_out", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Head of compliance review": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"disposition": "false_positive"}, "decided_by": "test"}},
    "Release wire": {"http": {"status": 200, "body": {"wire_id": "W9", "status": "released"}}},
    "Audit released": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Release wire", "subject": "request", "op": "count", "value": 1},
    {"node": "Block wire", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "MLRO and head of compliance both time out: blocked",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"wire_id": "W10", "account_id": "A10", "customer_id": "C10", "amount": 20000, "beneficiary_name": "Bad Actor", "beneficiary_country": "XX"}}]},
   "mocks": {
    "Get customer": {"items": [{"rows": [{"full_name": "Ana Diaz", "phone": "+15550100", "email": "ana@example.test"}], "rowCount": 1}]},
    "Callback task": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"customer_confirmed": "yes"}, "decided_by": "test"}},
    "Screen beneficiary": {"http": {"status": 200, "body": {"hit": true, "list": "OFAC"}}},
    "MLRO review": {"port": "timed_out", "approval": {"decision": "timed_out", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Head of compliance review": {"port": "timed_out", "approval": {"decision": "timed_out", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Block wire": {"http": {"status": 200, "body": {"wire_id": "W10", "status": "blocked"}}},
    "Audit blocked": {"items": [{"success": true}]},
    "SAR task": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"sar_reference": "SAR-1000"}, "decided_by": "test"}},
    "Audit SAR filed": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Block wire", "subject": "request", "op": "count", "value": 1},
    {"node": "Audit blocked", "subject": "node", "op": "status", "value": "success"},
    {"node": "Release wire", "subject": "node", "op": "status", "value": "not_run"}
   ]
  }
 ]
}
```
