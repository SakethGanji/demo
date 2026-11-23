Changed: Removed the "Needs risk review?" check and the "Risk review" approval, so "Plan details" now goes straight to "Email offer" for every plan, and rewrote the tests that depended on risk review.
Needs: nothing

```json file="changes.json"
{
 "remove_nodes": ["Needs risk review?", "Risk review"],
 "connect": [
  {"source_node": "Plan details", "target_node": "Email offer", "source_output": "main", "target_input": "main"}
 ],
 "remove_tests": [
  "Holiday of 4 months needs risk, rejection declines",
  "Holiday of exactly 3 months on exactly 50000 skips risk, customer declines"
 ],
 "tests": [
  {
   "name": "Small loan, term extension accepted is applied and recorded",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"loan_id": "L1", "customer_email": "a@x.test", "reason": "job loss"}}]},
   "mocks": {
    "Look up loan": {"items": [{"rows": [{"loan_id": "L1", "customer_id": "C1", "outstanding": "10000", "days_past_due": 30, "status": "active"}], "rowCount": 1}]},
    "Officer assessment": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"plan": "term_extension", "months": 6}, "decided_by": "test"}},
    "Email offer": {"items": [{"success": true}]},
    "Record answer": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"customer_response": "accepted"}, "decided_by": "test"}},
    "Apply restructuring": {"http": {"status": 200, "body": {"loan_id": "L1", "status": "restructured"}}},
    "Record restructure": {"items": [{"success": true}]},
    "Email confirmation": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Reply in review", "subject": "request", "path": "statusCode", "op": "eq", "value": 202},
    {"node": "Email offer", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "a@x.test"},
    {"node": "Apply restructuring", "subject": "request", "path": "body.plan", "op": "eq", "value": "term_extension"},
    {"node": "Apply restructuring", "subject": "request", "path": "body.months", "op": "eq", "value": 6},
    {"node": "Apply restructuring", "subject": "request", "path": "url", "op": "contains", "value": "L1"},
    {"node": "Record restructure", "subject": "node", "op": "status", "value": "success"},
    {"node": "Email confirmation", "subject": "node", "op": "status", "value": "success"},
    {"node": "Record declined", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "Long holiday on a large loan goes straight to the customer and is applied",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"loan_id": "L2", "customer_email": "b@x.test", "reason": "illness"}}]},
   "mocks": {
    "Look up loan": {"items": [{"rows": [{"loan_id": "L2", "customer_id": "C2", "outstanding": "80000", "days_past_due": 60, "status": "active"}], "rowCount": 1}]},
    "Officer assessment": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"plan": "payment_holiday", "months": 6}, "decided_by": "test"}},
    "Email offer": {"items": [{"success": true}]},
    "Record answer": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"customer_response": "accepted"}, "decided_by": "test"}},
    "Apply restructuring": {"http": {"status": 200, "body": {"loan_id": "L2", "status": "restructured"}}},
    "Record restructure": {"items": [{"success": true}]},
    "Email confirmation": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Email offer", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "b@x.test"},
    {"node": "Record answer", "subject": "node", "op": "status", "value": "success"},
    {"node": "Apply restructuring", "subject": "request", "path": "body.plan", "op": "eq", "value": "payment_holiday"},
    {"node": "Apply restructuring", "subject": "request", "path": "body.months", "op": "eq", "value": 6},
    {"node": "Email confirmation", "subject": "node", "op": "status", "value": "success"},
    {"node": "Record declined", "subject": "node", "op": "status", "value": "not_run"}
   ]
  },
  {
   "name": "Any plan goes to the customer, who declines",
   "target": {"kind": "workflow"},
   "input": {"items": [{"body": {"loan_id": "L3", "customer_email": "c@x.test", "reason": "debt"}}]},
   "mocks": {
    "Look up loan": {"items": [{"rows": [{"loan_id": "L3", "customer_id": "C3", "outstanding": "50000", "days_past_due": 10, "status": "active"}], "rowCount": 1}]},
    "Officer assessment": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"plan": "payment_holiday", "months": 3}, "decided_by": "test"}},
    "Email offer": {"items": [{"success": true}]},
    "Record answer": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"customer_response": "declined"}, "decided_by": "test"}},
    "Record declined": {"items": [{"success": true}]},
    "Email declined": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Email offer", "subject": "node", "op": "status", "value": "success"},
    {"node": "Apply restructuring", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Record declined", "subject": "request", "path": "params.statements.0.params.1", "op": "eq", "value": "payment_holiday"},
    {"node": "Email declined", "subject": "node", "op": "status", "value": "success"}
   ]
  }
 ]
}
```
