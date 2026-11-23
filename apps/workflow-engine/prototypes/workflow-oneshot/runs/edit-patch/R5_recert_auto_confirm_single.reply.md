Changed: Added a "Team of one?" check after grouping, so managers with exactly one person skip the review tasks and go straight to the audit log as `confirmed`; I also gave test 4 a second user so it still reaches the review.
Needs: nothing

```json file="changes.json"
{
 "nodes": [
  {
   "name": "Team of one?",
   "type": "If",
   "parameters": {
    "condition": "{{ length($json.user_ids) == 1 }}"
   }
  },
  {
   "name": "Auto confirm",
   "type": "Set",
   "parameters": {
    "mode": "manual",
    "fields": [
     {"name": "action", "value": "confirmed"},
     {"name": "detail", "value": "single-person team; confirmed automatically"}
    ]
   }
  }
 ],
 "disconnect": [
  {"source_node": "Group by manager", "target_node": "Manager review", "source_output": "main", "target_input": "main"}
 ],
 "connect": [
  {"source_node": "Group by manager", "target_node": "Team of one?", "source_output": "main", "target_input": "main"},
  {"source_node": "Team of one?", "target_node": "Auto confirm", "source_output": "true", "target_input": "main"},
  {"source_node": "Team of one?", "target_node": "Manager review", "source_output": "false", "target_input": "main"},
  {"source_node": "Auto confirm", "target_node": "Write audit", "source_output": "main", "target_input": "main"}
 ],
 "tests": [
  {
   "name": "Answer to the second task counts and nothing is force-revoked",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Read access": {"items": [{"rows": [
     {"user_id": "u1", "user_email": "a@bank.test", "system": "CRM", "manager_email": "m@bank.test"},
     {"user_id": "u2", "user_email": "b@bank.test", "system": "Ledger", "manager_email": "m@bank.test"}
    ], "rowCount": 2}]},
    "Manager review": {"port": "timed_out", "approval": {"decision": "timed_out", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Second review": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"revoke": ""}, "decided_by": "test"}},
    "Reminder email": {"items": [{"success": true}]},
    "Write audit": {"items": [{"success": true}]},
    "Read outcomes": {"items": [{"rows": [{"entity_id": "m@bank.test", "action": "confirmed"}], "rowCount": 1}]},
    "Email summary": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Reminder email", "subject": "request", "op": "count", "value": 1},
    {"node": "Plan from timeout", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email CISO timeout", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Revoke user", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Plan from answer", "path": "action", "op": "eq", "value": "confirmed"}
   ]
  },
  {
   "name": "Single-person team is confirmed without a task",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Read access": {"items": [{"rows": [
     {"user_id": "u1", "user_email": "a@bank.test", "system": "CRM", "manager_email": "solo@bank.test"}
    ], "rowCount": 1}]},
    "Write audit": {"items": [{"success": true}]},
    "Read outcomes": {"items": [{"rows": [{"entity_id": "solo@bank.test", "action": "confirmed"}], "rowCount": 1}]},
    "Email summary": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Team of one?", "port": "true", "op": "count", "value": 1},
    {"node": "Manager review", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Second review", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Reminder email", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Revoke user", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Write audit", "subject": "request", "path": "params.statements.0.params.2", "op": "eq", "value": "confirmed"},
    {"node": "Write audit", "subject": "request", "path": "params.statements.0.params.1", "op": "eq", "value": "solo@bank.test"},
    {"node": "Email summary", "subject": "node", "op": "status", "value": "success"}
   ]
  },
  {
   "name": "Only the single-person manager skips the review",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Read access": {"items": [{"rows": [
     {"user_id": "u9", "user_email": "z@bank.test", "system": "CRM", "manager_email": "solo@bank.test"},
     {"user_id": "u1", "user_email": "a@bank.test", "system": "CRM", "manager_email": "m@bank.test"},
     {"user_id": "u2", "user_email": "b@bank.test", "system": "Ledger", "manager_email": "m@bank.test"}
    ], "rowCount": 3}]},
    "Manager review": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"revoke": ""}, "decided_by": "test"}},
    "Write audit": {"items": [{"success": true}]},
    "Read outcomes": {"items": [{"rows": [
     {"entity_id": "m@bank.test", "action": "confirmed"},
     {"entity_id": "solo@bank.test", "action": "confirmed"}
    ], "rowCount": 2}]},
    "Email summary": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Team of one?", "port": "true", "op": "count", "value": 1},
    {"node": "Team of one?", "port": "false", "op": "count", "value": 1},
    {"node": "Manager review", "port": "approved", "op": "count", "value": 1},
    {"node": "Write audit", "subject": "request", "op": "count", "value": 2},
    {"node": "Revoke user", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email summary", "subject": "node", "op": "status", "value": "success"}
   ]
  }
 ]
}
```
