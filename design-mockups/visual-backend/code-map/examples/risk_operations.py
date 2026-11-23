"""Illustrative complex application for source analysis, not a deployed service.

Connector signatures below illustrate integration boundaries; they are not verified
SDK contracts. The prototype parses this file and never executes it.
"""
import studio
from studio import api


@api.post('/cases/review')
def review_case(request):
    case = load_case(request['case_id'])
    assessment = assess_risk(case)
    return finalize_review(case, assessment)


@api.post('/cases')
def create_case(request):
    payload = validate_case(request)
    case = save_case(payload)
    write_audit('case.created', case)
    return case


@api.get('/cases')
def list_cases(request):
    filters = validate_filters(request)
    return query_database('SELECT * FROM cases WHERE status = %(status)s', filters)


@api.get('/portfolio/summary')
def portfolio_summary(request):
    rows = query_database('SELECT * FROM risk_exposure', {})
    metrics = aggregate_exposure(rows)
    return format_dashboard(metrics)


@api.post('/cases/reconcile')
def reconcile_cases(request):
    rows = query_database('SELECT * FROM pending_cases', {})
    results = []
    for case in rows:
        try:
            results.append(finalize_review(case, assess_risk(case)))
        except ValueError:
            write_audit('case.reconciliation_failed', case)
    return results


@api.get('/cases/activity')
def case_activity(request):
    case = load_case(request['case_id'])
    return search_activity(case)


def load_case(case_id):
    rows = query_database('SELECT * FROM cases WHERE id = %(id)s', {'id': case_id})
    if not rows:
        raise ValueError('Case not found')
    return rows[0]


def assess_risk(case):
    activity = search_activity(case)
    policy = load_policy(case['region'])
    score = calculate_score(case, activity, policy)
    return {'case_id': case['id'], 'score': score, 'requires_review': score >= policy['threshold']}


def finalize_review(case, assessment):
    decision = save_decision(case, assessment)
    write_audit('case.reviewed', decision)
    if assessment['requires_review']:
        notify_reviewer(case, assessment)
    return decision


def validate_case(payload):
    required = ('customer_id', 'amount', 'region')
    for field in required:
        if field not in payload:
            raise ValueError('Missing required field: ' + field)
    if float(payload['amount']) <= 0:
        raise ValueError('Amount must be positive')
    return {key: payload[key] for key in required}


def validate_filters(request):
    status = request.get('status', 'pending')
    if status not in ('pending', 'reviewed', 'escalated'):
        raise ValueError('Unsupported status')
    return {'status': status}


def save_case(payload):
    return query_database('INSERT INTO cases (payload) VALUES (%(payload)s) RETURNING *', {'payload': payload})


def save_decision(case, assessment):
    return query_database('INSERT INTO decisions (case_id, score) VALUES (%(id)s, %(score)s) RETURNING *',
                          {'id': case['id'], 'score': assessment['score']})


def search_activity(case):
    return studio.nodes.http_request(
        url=studio.secret('SPLUNK_SEARCH_URL'),
        headers={'Authorization': studio.secret('SPLUNK_TOKEN')},
        body={'customer_id': case['customer_id'], 'window': '30d'})


def load_policy(region):
    rows = query_database('SELECT * FROM risk_policy WHERE region = %(region)s', {'region': region})
    if not rows:
        raise ValueError('No policy configured')
    return rows[0]


def calculate_score(case, activity, policy):
    score = float(case['amount']) / policy['amount_limit']
    for event in activity:
        if event.get('flagged'):
            score += policy['flag_weight']
    # A supplied callback deliberately remains unresolved in the source graph.
    custom_rule = policy.get('custom_rule')
    if custom_rule:
        score = custom_rule(score, case)
    return min(100, max(0, score))


def write_audit(event, record):
    return studio.nodes.http_request(
        url=studio.secret('AUDIT_INGEST_URL'),
        body={'event': event, 'record': record})


def notify_reviewer(case, assessment):
    message = format_notification(case, assessment)
    return studio.nodes.http_request(
        url=studio.secret('NOTIFICATION_URL'), body=message)


def format_notification(case, assessment):
    return {'subject': 'Review required', 'case_id': case['id'], 'score': assessment['score']}


def query_database(sql, parameters):
    return studio.nodes.postgres(
        query=sql, parameters=parameters,
        database=studio.secret('RISK_DATABASE'),
        user=studio.secret('RISK_DB_USER'), password=studio.secret('RISK_DB_PASSWORD'))


def aggregate_exposure(rows):
    totals = {}
    for row in rows:
        region = row['region']
        totals[region] = totals.get(region, 0) + row['exposure']
    return totals


def format_dashboard(metrics):
    return {'regions': [{'region': key, 'exposure': value} for key, value in metrics.items()]}
