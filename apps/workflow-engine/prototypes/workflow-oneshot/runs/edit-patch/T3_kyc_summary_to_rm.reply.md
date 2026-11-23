Changed: The "Email KYC team" summary email now goes to both `$vars.KYC_TEAM_EMAIL` and `$vars.RM_EMAIL`, and I added a test that checks both recipients.
Needs: nothing. Both variables already exist.

```json file="changes.json"
{
 "nodes": [
  {
   "name": "Email KYC team",
   "type": "SendEmail",
   "parameters": {
    "toEmail": "{{ $vars.KYC_TEAM_EMAIL }}, {{ $vars.RM_EMAIL }}",
    "subject": "Daily KYC summary",
    "bodyFormat": "plain",
    "body": "KYC requests today: {{ $json.rows[0].completed }} completed, {{ $json.rows[0].reminded }} reminded, {{ $json.rows[0].restricted }} restricted."
   }
  }
 ],
 "tests": [
  {
   "name": "Daily summary goes to the KYC team and the RM",
   "target": {"kind": "workflow"},
   "input": {"items": [{}]},
   "mocks": {
    "Get open requests": {
     "items": [
      {
       "rows": [
        {"id": 3, "customer_id": "C300", "email": "c300@example.com", "requested_on": "2026-10-19", "reminders_sent": 0, "status": "open", "days_open": 3}
       ],
       "rowCount": 1
      }
     ]
    },
    "Check documents": {"http": {"status": 200, "body": {"customer_id": "C300", "uploaded": false}}},
    "Email first reminder": {"items": [{"success": true}]},
    "Mark first reminder": {"items": [{"success": true}]},
    "Count outcomes": {"items": [{"rows": [{"completed": 0, "reminded": 1, "restricted": 0}], "rowCount": 1}]},
    "Email KYC team": {"items": [{"success": true}]}
   },
   "expect": [
    {"node": "Email KYC team", "subject": "request", "op": "count", "value": 1},
    {"node": "Email KYC team", "subject": "request", "path": "params.toEmail", "op": "contains", "value": "kyc@bank.test"},
    {"node": "Email KYC team", "subject": "request", "path": "params.toEmail", "op": "contains", "value": "rm@bank.test"}
   ]
  }
 ]
}
```
