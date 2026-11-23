Changed: After each successful revoke, the user now gets an email naming the system they lost access to. To make that possible, the manager grouping and both plan steps now carry each user's email and systems through to the revoke list.
Needs: nothing

```json file="changes.json"
{
 "nodes": [
  {
   "name": "Group by manager",
   "type": "Code",
   "parameters": {
    "code": "groups = {}\norder = []\nfor it in items:\n    r = it['json']\n    m = r['manager_email']\n    if m not in groups:\n        groups[m] = []\n        order.append(m)\n    groups[m].append({'user_id': r['user_id'], 'user_email': r['user_email'], 'system': r['system']})\nout = []\nfor m in order:\n    us = groups[m]\n    ids = []\n    lines = []\n    for u in us:\n        ids.append(u['user_id'])\n        lines.append(u['user_id'] + ' (' + u['system'] + ')')\n    out.append(new_item({'manager_email': m, 'user_ids': ids, 'users': us, 'listing': '\\n'.join(lines)}))\nreturn out"
   }
  },
  {
   "name": "Plan from answer",
   "type": "Code",
   "parameters": {
    "code": "out = []\nfor it in items:\n    j = it['json']\n    fd = (j.get('approval') or {}).get('form_data') or {}\n    raw = fd.get('revoke') or ''\n    ids = []\n    for p in str(raw).split(','):\n        p = p.strip()\n        if p != '' and p in j['user_ids'] and p not in ids:\n            ids.append(p)\n    rev = []\n    for i in ids:\n        email = ''\n        systems = []\n        for u in j['users']:\n            if u['user_id'] == i:\n                email = u['user_email']\n                systems.append(u['system'])\n        rev.append({'user_id': i, 'manager_email': j['manager_email'], 'user_email': email, 'system': ', '.join(systems)})\n    if len(ids) == 0:\n        action = 'confirmed'\n        detail = 'nobody revoked'\n    else:\n        action = 'revoked_some'\n        detail = 'revoked: ' + ', '.join(ids)\n    out.append(new_item({'manager_email': j['manager_email'], 'action': action, 'detail': detail, 'revoke_items': rev}))\nreturn out"
   }
  },
  {
   "name": "Plan from timeout",
   "type": "Code",
   "parameters": {
    "code": "out = []\nfor it in items:\n    j = it['json']\n    ids = []\n    for i in j['user_ids']:\n        if i not in ids:\n            ids.append(i)\n    rev = []\n    for i in ids:\n        email = ''\n        systems = []\n        for u in j['users']:\n            if u['user_id'] == i:\n                email = u['user_email']\n                systems.append(u['system'])\n        rev.append({'user_id': i, 'manager_email': j['manager_email'], 'user_email': email, 'system': ', '.join(systems)})\n    out.append(new_item({'manager_email': j['manager_email'], 'action': 'revoked_all_timeout', 'detail': 'no answer after two tasks; revoked: ' + ', '.join(ids), 'revoke_items': rev}))\nreturn out"
   }
  },
  {
   "name": "Keep user details",
   "type": "Set",
   "parameters": {
    "mode": "manual",
    "fields": [
     {"name": "user_email", "value": "{{ at($node[\"Split revokes\"].data, $itemIndex).user_email }}"},
     {"name": "system", "value": "{{ at($node[\"Split revokes\"].data, $itemIndex).system }}"}
    ]
   }
  },
  {
   "name": "Email user",
   "type": "SendEmail",
   "parameters": {
    "toEmail": "{{ $json.user_email }}",
    "subject": "Your access to {{ $json.system }} has been revoked",
    "bodyFormat": "plain",
    "body": "As part of the quarterly access recertification, your access to {{ $json.system }} has been revoked. If you still need it, please contact your manager."
   }
  }
 ],
 "connect": [
  {"source_node": "Revoke user", "target_node": "Keep user details", "source_output": "main", "target_input": "main"},
  {"source_node": "Keep user details", "target_node": "Email user", "source_output": "main", "target_input": "main"}
 ],
 "tests": [
  {
   "name": "Manager revokes one user",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Read access": {"items": [{"rows": [
     {"user_id": "u1", "user_email": "a@bank.test", "system": "CRM", "manager_email": "m@bank.test"},
     {"user_id": "u2", "user_email": "b@bank.test", "system": "Ledger", "manager_email": "m@bank.test"}
    ], "rowCount": 2}]},
    "Manager review": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"revoke": "u2"}, "decided_by": "test"}},
    "Revoke user": {"http": {"status": 200, "body": {"user_id": "u2"}}},
    "Email user": {"items": [{"success": true}]},
    "Write audit": {"items": [{"success": true}]},
    "Read outcomes": {"items": [{"rows": [{"entity_id": "m@bank.test", "action": "revoked_some"}], "rowCount": 1}]},
    "Email summary": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Plan from answer", "path": "action", "op": "eq", "value": "revoked_some"},
    {"node": "Revoke user", "subject": "request", "op": "count", "value": 1},
    {"node": "Revoke user", "subject": "request", "path": "url", "op": "contains", "value": "u2"},
    {"node": "Email user", "subject": "request", "op": "count", "value": 1},
    {"node": "Email user", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "b@bank.test"},
    {"node": "Email user", "subject": "request", "path": "params.subject", "op": "contains", "value": "Ledger"},
    {"node": "Email user", "subject": "request", "path": "params.body", "op": "contains", "value": "Ledger"},
    {"node": "Write audit", "subject": "request", "path": "params.statements.0.params.2", "op": "eq", "value": "revoked_some"}
   ]
  },
  {
   "name": "No answer twice revokes everyone and alerts CISO",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Read access": {"items": [{"rows": [
     {"user_id": "u1", "user_email": "a@bank.test", "system": "CRM", "manager_email": "m@bank.test"},
     {"user_id": "u2", "user_email": "b@bank.test", "system": "Ledger", "manager_email": "m@bank.test"}
    ], "rowCount": 2}]},
    "Manager review": {"port": "timed_out", "approval": {"decision": "timed_out", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Second review": {"port": "timed_out", "approval": {"decision": "timed_out", "comment": "", "form_data": {}, "decided_by": "test"}},
    "Reminder email": {"items": [{"success": true}]},
    "Email CISO timeout": {"items": [{"success": true}]},
    "Revoke user": {"http": {"status": 200, "body": {"user_id": "x"}}},
    "Email user": {"items": [{"success": true}]},
    "Write audit": {"items": [{"success": true}]},
    "Read outcomes": {"items": [{"rows": [{"entity_id": "m@bank.test", "action": "revoked_all_timeout"}], "rowCount": 1}]},
    "Email summary": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Reminder email", "subject": "request", "op": "count", "value": 1},
    {"node": "Plan from timeout", "path": "action", "op": "eq", "value": "revoked_all_timeout"},
    {"node": "Revoke user", "subject": "request", "op": "count", "value": 2},
    {"node": "Email user", "subject": "request", "op": "count", "value": 2},
    {"node": "Email user", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "a@bank.test"},
    {"node": "Email user", "subject": "request", "path": "params.body", "op": "contains", "value": "CRM"},
    {"node": "Email user", "subject": "request", "path": "params.toEmail", "op": "eq", "value": "b@bank.test", "item": "any"},
    {"node": "Email CISO timeout", "subject": "request", "path": "params.toEmail", "op": "contains", "value": "ciso@bank.test"},
    {"node": "Email CISO timeout", "subject": "request", "path": "params.subject", "op": "contains", "value": "m@bank.test"},
    {"node": "Write audit", "subject": "request", "path": "params.statements.0.params.2", "op": "eq", "value": "revoked_all_timeout"}
   ]
  },
  {
   "name": "Users who keep access get no email",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Read access": {"items": [{"rows": [
     {"user_id": "u1", "user_email": "a@bank.test", "system": "CRM", "manager_email": "m@bank.test"}
    ], "rowCount": 1}]},
    "Manager review": {"port": "approved", "approval": {"decision": "approved", "comment": "", "form_data": {"revoke": ""}, "decided_by": "test"}},
    "Write audit": {"items": [{"success": true}]},
    "Read outcomes": {"items": [{"rows": [{"entity_id": "m@bank.test", "action": "confirmed"}], "rowCount": 1}]},
    "Email summary": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Revoke user", "subject": "node", "op": "status", "value": "not_run"},
    {"node": "Email user", "subject": "node", "op": "status", "value": "not_run"}
   ]
  }
 ]
}
```
