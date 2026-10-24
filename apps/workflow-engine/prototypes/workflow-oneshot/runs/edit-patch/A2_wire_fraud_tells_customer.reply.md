Changed: After the account is frozen and the fraud audit is written, the workflow now emails the customer that the wire is held and their account is frozen. The existing fraud team email still goes out after it.
Needs: nothing

```json file="changes.json"
{
 "nodes": [
  {
   "name": "Email customer frozen",
   "type": "SendEmail",
   "parameters": {
    "toEmail": "{{ $node[\"Gather details\"].json.customer_email }}",
    "subject": "Your wire is on hold and your account is frozen",
    "bodyFormat": "plain",
    "body": "Dear {{ $node[\"Gather details\"].json.customer_name }}, you told us that outgoing wire {{ $node[\"Wire received\"].json.body.wire_id }} is not yours. The wire is held and your account {{ $node[\"Wire received\"].json.body.account_id }} is frozen. Please contact the bank."
   }
  }
 ],
 "connect": [
  {
   "source_node": "Email fraud team",
   "target_node": "Email customer frozen",
   "source_output": "main",
   "target_input": "main"
  }
 ],
 "tests": [
  {
   "name": "Customer says no: hold, freeze and tell fraud team",
   "target": {"kind": "workflow"},
   "input": {
    "items": [
     {
      "body": {
       "wire_id": "W3",
       "account_id": "A3",
       "customer_id": "C3",
       "amount": 50000,
       "beneficiary_name": "Acme Ltd",
       "beneficiary_country": "DE"
      }
     }
    ]
   },
   "mocks": {
    "Get customer": {
     "items": [
      {
       "rows": [
        {"full_name": "Ana Diaz", "phone": "+15550100", "email": "ana@example.test"}
       ],
       "rowCount": 1
      }
     ]
    },
    "Callback task": {
     "port": "approved",
     "approval": {
      "decision": "approved",
      "comment": "",
      "form_data": {"customer_confirmed": "no"},
      "decided_by": "test"
     }
    },
    "Hold wire fraud": {"http": {"status": 200, "body": {"wire_id": "W3", "status": "held"}}},
    "Freeze account": {"http": {"status": 200, "body": {"account_id": "A3", "status": "frozen"}}},
    "Audit fraud": {"items": [{"success": true}]},
    "Email fraud team": {"items": [{"success": true}]},
    "Email customer frozen": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Freeze account", "subject": "request", "op": "count", "value": 1},
    {"node": "Email fraud team", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "fraud@bank.test"},
    {"node": "Email customer frozen", "subject": "request", "op": "count", "value": 1},
    {"node": "Email customer frozen", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "ana@example.test"},
    {"node": "Email customer frozen", "subject": "request", "path": "params.body", "op": "contains", "value": "frozen"},
    {"node": "Screen beneficiary", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Release wire", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "Customer confirms: no frozen-account email",
   "target": {"kind": "workflow"},
   "input": {
    "items": [
     {
      "body": {
       "wire_id": "W8",
       "account_id": "A8",
       "customer_id": "C8",
       "amount": 10000,
       "beneficiary_name": "Acme Ltd",
       "beneficiary_country": "DE"
      }
     }
    ]
   },
   "mocks": {
    "Get customer": {
     "items": [
      {
       "rows": [
        {"full_name": "Ana Diaz", "phone": "+15550100", "email": "ana@example.test"}
       ],
       "rowCount": 1
      }
     ]
    },
    "Callback task": {
     "port": "approved",
     "approval": {
      "decision": "approved",
      "comment": "",
      "form_data": {"customer_confirmed": "yes"},
      "decided_by": "test"
     }
    },
    "Screen beneficiary": {"http": {"status": 200, "body": {"hit": false, "list": ""}}},
    "Release wire": {"http": {"status": 200, "body": {"wire_id": "W8", "status": "released"}}},
    "Audit released": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Release wire", "subject": "request", "op": "count", "value": 1},
    {"node": "Email customer frozen", "subject": "node", "op": "status", "value": "not_run"}
   ]
  }
 ]
}
```
