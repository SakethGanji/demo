Changed: After the restructuring is applied, recorded and the customer confirmed, the workflow now emails the collections officer with the loan id. The accepted-offer test now checks that email.
Needs: nothing. `$vars.COLLECTIONS_OFFICER_EMAIL` already exists.

```json file="changes.json"
{
 "nodes": [
  {
   "name": "Email collections officer",
   "type": "SendEmail",
   "parameters": {
    "toEmail": "{{ $vars.COLLECTIONS_OFFICER_EMAIL }}",
    "subject": "Loan restructuring applied: {{ $node[\"Plan details\"].json.loan_id }}",
    "bodyFormat": "plain",
    "body": "The restructuring for loan {{ $node[\"Plan details\"].json.loan_id }} has been applied."
   }
  }
 ],
 "connect": [
  {
   "source_node": "Email confirmation",
   "target_node": "Email collections officer",
   "source_output": "main",
   "target_input": "main"
  }
 ],
 "tests": [
  {
   "name": "Small loan, term extension accepted is applied and recorded",
   "target": {"kind": "workflow"},
   "input": {
    "items": [
     {"body": {"loan_id": "L1", "customer_email": "a@x.test", "reason": "job loss"}}
    ]
   },
   "mocks": {
    "Look up loan": {
     "items": [
      {
       "rows": [
        {"loan_id": "L1", "customer_id": "C1", "outstanding": "10000", "days_past_due": 30, "status": "active"}
       ],
       "rowCount": 1
      }
     ]
    },
    "Officer assessment": {
     "port": "approved",
     "approval": {
      "decision": "approved",
      "comment": "",
      "form_data": {"plan": "term_extension", "months": 6},
      "decided_by": "test"
     }
    },
    "Email offer": {"items": [{"success": true}]},
    "Record answer": {
     "port": "approved",
     "approval": {
      "decision": "approved",
      "comment": "",
      "form_data": {"customer_response": "accepted"},
      "decided_by": "test"
     }
    },
    "Apply restructuring": {
     "http": {"status": 200, "body": {"loan_id": "L1", "status": "restructured"}}
    },
    "Record restructure": {"items": [{"success": true}]},
    "Email confirmation": {"items": [{"success": true}]},
    "Email collections officer": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Reply in review", "subject": "request", "path": "statusCode", "op": "eq", "value": 202},
    {"node": "Risk review", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email offer", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "a@x.test"},
    {"node": "Apply restructuring", "subject": "request", "path": "body.plan", "op": "eq", "value": "term_extension"},
    {"node": "Apply restructuring", "subject": "request", "path": "body.months", "op": "eq", "value": 6},
    {"node": "Apply restructuring", "subject": "request", "path": "url", "op": "contains", "value": "L1"},
    {"node": "Record restructure", "subject": "node", "op": "status", "value": "success"},
    {"node": "Email confirmation", "subject": "node", "op": "status", "value": "success"},
    {"node": "Email collections officer", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "collections@bank.test"},
    {"node": "Email collections officer", "subject": "request", "path": "params.body", "op": "contains", "value": "L1"},
    {"node": "Email collections officer", "subject": "request", "path": "params.subject", "op": "contains", "value": "L1"},
    {"node": "Record declined", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "Declined offer does not email collections officer about a restructuring",
   "target": {"kind": "workflow"},
   "input": {
    "items": [
     {"body": {"loan_id": "L6", "customer_email": "f@x.test", "reason": "debt"}}
    ]
   },
   "mocks": {
    "Look up loan": {
     "items": [
      {
       "rows": [
        {"loan_id": "L6", "customer_id": "C6", "outstanding": "7000", "days_past_due": 15, "status": "active"}
       ],
       "rowCount": 1
      }
     ]
    },
    "Officer assessment": {
     "port": "approved",
     "approval": {
      "decision": "approved",
      "comment": "",
      "form_data": {"plan": "term_extension", "months": 6},
      "decided_by": "test"
     }
    },
    "Email offer": {"items": [{"success": true}]},
    "Record answer": {
     "port": "approved",
     "approval": {
      "decision": "approved",
      "comment": "",
      "form_data": {"customer_response": "declined"},
      "decided_by": "test"
     }
    },
    "Record declined": {"items": [{"success": true}]},
    "Email declined": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Apply restructuring", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email collections officer", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email declined", "subject": "node", "op": "status", "value": "success"}
   ]
  }
 ]
}
```
