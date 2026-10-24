Changed: The straight-through release limit is now 25,000 instead of 10,000. I renamed the step to "Under 25000?" and updated the tests whose amounts now fall under the new limit.
Needs: nothing

```json file="changes.json"
{
 "rename": {"Under 10000?": "Under 25000?"},
 "nodes": [
  {"name": "Under 25000?", "type": "If", "parameters": {"condition": "{{ float($json.body.amount) < 25000 }}"}}
 ],
 "remove_tests": ["Under 10000 is released without a callback"],
 "tests": [
  {
   "name": "Under 25000 is released without a callback",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"wire_id": "W1", "account_id": "A1", "customer_id": "C1", "amount": 24999.99, "beneficiary_name": "Acme Ltd", "beneficiary_country": "DE"}}]},
   "mocks": {
    "Release wire": {"http": {"status": 200, "body": {"wire_id": "W1", "status": "released"}}},
    "Audit released": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Release wire", "subject": "request", "op": "count", "value": 1},
    {"node": "Audit released", "subject": "node", "op": "status", "value": "success"},
    {"node": "Get customer", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Callback task", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "10000 is now released without a callback",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"wire_id": "W8", "account_id": "A8", "customer_id": "C8", "amount": 10000, "beneficiary_name": "Acme Ltd", "beneficiary_country": "DE"}}]},
   "mocks": {
    "Release wire": {"http": {"status": 200, "body": {"wire_id": "W8", "status": "released"}}},
    "Audit released": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Release wire", "subject": "request", "op": "count", "value": 1},
    {"node": "Callback task", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Screen beneficiary", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "Confirmed, no sanctions hit, under 1M is released",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"wire_id": "W2", "account_id": "A2", "customer_id": "C2", "amount": 25000, "beneficiary_name": "Acme Ltd", "beneficiary_country": "DE"}}]},
   "mocks": {
    "Get customer": {"items": [{"rows": [{"full_name": "Ana Diaz", "phone": "+15550100", "email": "ana@example.test"}], "rowCount": 1}]},
    "Callback task": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"customer_confirmed": "yes"}, "decided_by": "test"}},
    "Screen beneficiary": {"http": {"status": 200, "body": {"hit": false, "list": ""}}},
    "Release wire": {"http": {"status": 200, "body": {"wire_id": "W2", "status": "released"}}},
    "Audit released": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Callback task", "subject": "node", "op": "status", "value": "success"},
    {"node": "Release wire", "subject": "request", "op": "count", "value": 1},
    {"node": "Treasury approval 1", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Hold wire fraud", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "Both callback tasks time out: reminder, hold, email customer",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"wire_id": "W4", "account_id": "A4", "customer_id": "C4", "amount": 30000, "beneficiary_name": "Acme Ltd", "beneficiary_country": "DE"}}]},
   "mocks": {
    "Get customer": {"items": [{"rows": [{"full_name": "Ana Diaz", "phone": "+15550100", "email": "ana@example.test"}], "rowCount": 1}]},
    "Callback task": {"port": "timed_out", "approval": {"decision": "timed_out", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Remind payments lead": {"items": [{"success": true}]},
    "Callback task again": {"port": "timed_out", "approval": {"decision": "timed_out", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Hold wire no callback": {"http": {"status": 200, "body": {"wire_id": "W4", "status": "held"}}},
    "Audit held no callback": {"items": [{"success": true}]},
    "Email customer": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Remind payments lead", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "paylead@bank.test"},
    {"node": "Hold wire no callback", "subject": "request", "op": "count", "value": 1},
    {"node": "Email customer", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "ana@example.test"},
    {"node": "Release wire", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "True match is blocked and a SAR is recorded",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"wire_id": "W5", "account_id": "A5", "customer_id": "C5", "amount": 30000, "beneficiary_name": "Bad Actor", "beneficiary_country": "XX"}}]},
   "mocks": {
    "Get customer": {"items": [{"rows": [{"full_name": "Ana Diaz", "phone": "+15550100", "email": "ana@example.test"}], "rowCount": 1}]},
    "Callback task": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"customer_confirmed": "yes"}, "decided_by": "test"}},
    "Screen beneficiary": {"http": {"status": 200, "body": {"hit": true, "list": "OFAC"}}},
    "MLRO review": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"disposition": "true_match"}, "decided_by": "test"}},
    "Block wire": {"http": {"status": 200, "body": {"wire_id": "W5", "status": "blocked"}}},
    "Audit blocked": {"items": [{"success": true}]},
    "SAR task": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"sar_reference": "SAR-123"}, "decided_by": "test"}},
    "Audit SAR filed": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Block wire", "subject": "request", "op": "count", "value": 1},
    {"node": "Audit SAR filed", "subject": "node", "op": "status", "value": "success"},
    {"node": "Release wire", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "MLRO rejection (timeout) also blocks the wire",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"wire_id": "W6", "account_id": "A6", "customer_id": "C6", "amount": 30000, "beneficiary_name": "Bad Actor", "beneficiary_country": "XX"}}]},
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
    {"node": "Release wire", "subject": "node", "op": "status", "value": "not_run"}
   ]
  }
 ]
}
```
