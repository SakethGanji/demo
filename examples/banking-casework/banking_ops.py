import studio
from studio import api, HTTPError


def _connection():
    return f"host=127.0.0.1 port=5432 user={studio.secret('PG_USER')} password={studio.secret('PG_PASSWORD')} dbname={studio.secret('PG_DATABASE')}"


def run_query(sql: str, params: list = None) -> list[dict]:
    if params is None:
        params = []
    res = studio.nodes.postgres(
        connection_string=_connection(),
        operation="query",
        query=sql,
        query_parameters=params,
    )
    if res and isinstance(res, list) and len(res) > 0:
        first = res[0]
        if isinstance(first, dict):
            return first.get("rows", [])
    return []


def _to_int(val, default=0):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _to_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _validate_case_update(body):
    if not isinstance(body, dict):
        raise HTTPError(422, {"error": "invalid_case_update"})

    if "state" not in body or "owner" not in body or "note" not in body:
        raise HTTPError(422, {"error": "invalid_case_update"})

    state = body.get("state")
    owner = body.get("owner")
    note = body.get("note")

    if not isinstance(state, str) or not isinstance(owner, str) or not isinstance(note, str):
        raise HTTPError(422, {"error": "invalid_case_update"})

    if state not in ("open", "in_review", "resolved"):
        raise HTTPError(422, {"error": "invalid_case_update"})

    owner_str = owner.strip()
    if not owner_str or len(owner_str) > 80:
        raise HTTPError(422, {"error": "invalid_case_update"})

    note_str = note.strip()
    if not note_str or len(note_str) > 500:
        raise HTTPError(422, {"error": "invalid_case_update"})

    return state, owner_str, note_str


@api.get("/ops/summary", auth="none", name="opsSummary")
def ops_summary(req):
    sql = """
    SELECT
      (SELECT COUNT(*) FROM bank_transactions) AS transactions,
      (SELECT COALESCE(SUM(amount), 0) FROM bank_transactions) AS volume,
      (SELECT COUNT(*) FROM bank_transactions WHERE status = 'exception') AS exceptions,
      (SELECT COUNT(*) FROM bank_transactions WHERE status = 'pending') AS pending,
      (SELECT COUNT(*) FROM bank_accounts) AS accounts,
      (SELECT COUNT(*) FROM bank_branches) AS branches
    """
    rows = run_query(sql)
    if not rows:
        return {
            "transactions": 0,
            "volume": 0.0,
            "exceptions": 0,
            "pending": 0,
            "accounts": 0,
            "branches": 0,
        }
    r = rows[0]
    return {
        "transactions": _to_int(r.get("transactions")),
        "volume": _to_float(r.get("volume")),
        "exceptions": _to_int(r.get("exceptions")),
        "pending": _to_int(r.get("pending")),
        "accounts": _to_int(r.get("accounts")),
        "branches": _to_int(r.get("branches")),
    }


@api.get("/ops/transactions", auth="none", name="opsTransactions")
def ops_transactions(req):
    q = getattr(req, "query", {}) or {}
    status_raw = str(q.get("status", "") or "").strip()
    region_raw = str(q.get("region", "") or "").strip()
    search_raw = str(q.get("search", "") or "").strip()

    status_p = "" if not status_raw or status_raw.lower() == "all" else status_raw.lower()
    region_p = "" if not region_raw or region_raw.lower() == "all" else region_raw.lower()
    search_flag = search_raw if search_raw else ""
    search_like = f"%{search_raw}%" if search_raw else ""

    try:
        page = int(q.get("page", 1))
    except (ValueError, TypeError):
        page = 1
    if page < 1:
        page = 1

    try:
        page_size = int(q.get("page_size", 25))
    except (ValueError, TypeError):
        page_size = 25
    if page_size < 1:
        page_size = 1
    elif page_size > 50:
        page_size = 50

    offset = (page - 1) * page_size

    filter_params = [
        status_p, status_p,
        region_p, region_p,
        search_flag, search_like, search_like, search_like,
    ]

    count_sql = """
    SELECT COUNT(*) AS total
    FROM bank_transactions t
    JOIN bank_accounts a ON t.account_id = a.id
    JOIN bank_branches b ON a.branch_id = b.id
    WHERE (%s = '' OR LOWER(t.status) = LOWER(%s))
      AND (%s = '' OR LOWER(b.region) = LOWER(%s))
      AND (%s = '' OR t.id ILIKE %s OR t.reference ILIKE %s OR a.name ILIKE %s)
    """
    c_rows = run_query(count_sql, filter_params)
    total = _to_int(c_rows[0].get("total")) if c_rows else 0

    list_sql = """
    SELECT
      t.id,
      t.account_id,
      a.name AS account_name,
      b.name AS branch_name,
      b.region,
      t.amount,
      t.status,
      t.booked_at::text AS booked_at,
      t.channel,
      t.reference
    FROM bank_transactions t
    JOIN bank_accounts a ON t.account_id = a.id
    JOIN bank_branches b ON a.branch_id = b.id
    WHERE (%s = '' OR LOWER(t.status) = LOWER(%s))
      AND (%s = '' OR LOWER(b.region) = LOWER(%s))
      AND (%s = '' OR t.id ILIKE %s OR t.reference ILIKE %s OR a.name ILIKE %s)
    ORDER BY t.id DESC
    LIMIT %s OFFSET %s
    """
    rows = run_query(list_sql, filter_params + [page_size, offset])

    items = [
        {
            "id": str(r.get("id")),
            "account_id": _to_int(r.get("account_id")),
            "account_name": str(r.get("account_name", "")),
            "branch_name": str(r.get("branch_name", "")),
            "region": str(r.get("region", "")),
            "amount": _to_float(r.get("amount")),
            "status": str(r.get("status", "")),
            "booked_at": str(r.get("booked_at", "")),
            "channel": str(r.get("channel", "")),
            "reference": str(r.get("reference", "")),
        }
        for r in rows
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@api.get("/ops/transactions/{id}", auth="none", name="opsTransaction")
def ops_transaction(req):
    params = getattr(req, "params", {}) or {}
    txn_id = params.get("id")
    if not txn_id:
        raise HTTPError(404, {"error": "not_found"})

    txn_sql = """
    SELECT
      t.id,
      t.account_id,
      a.name AS account_name,
      b.name AS branch_name,
      b.region,
      t.amount,
      t.status,
      t.booked_at::text AS booked_at,
      t.channel,
      t.reference
    FROM bank_transactions t
    JOIN bank_accounts a ON t.account_id = a.id
    JOIN bank_branches b ON a.branch_id = b.id
    WHERE t.id = %s
    """
    txn_rows = run_query(txn_sql, [str(txn_id)])
    if not txn_rows:
        raise HTTPError(404, {"error": "not_found"})

    r = txn_rows[0]
    transaction = {
        "id": str(r.get("id")),
        "account_id": _to_int(r.get("account_id")),
        "account_name": str(r.get("account_name", "")),
        "branch_name": str(r.get("branch_name", "")),
        "region": str(r.get("region", "")),
        "amount": _to_float(r.get("amount")),
        "status": str(r.get("status", "")),
        "booked_at": str(r.get("booked_at", "")),
        "channel": str(r.get("channel", "")),
        "reference": str(r.get("reference", "")),
    }

    events_sql = """
    SELECT id, occurred_at::text AS occurred_at, message
    FROM bank_events
    WHERE transaction_id = %s
    ORDER BY id ASC
    """
    e_rows = run_query(events_sql, [str(txn_id)])
    events = [
        {
            "id": _to_int(e.get("id")),
            "occurred_at": str(e.get("occurred_at", "")),
            "message": str(e.get("message", "")),
        }
        for e in e_rows
    ]

    cases_sql = """
    SELECT id, severity, owner, state, reason
    FROM bank_cases
    WHERE transaction_id = %s
    ORDER BY id ASC
    """
    c_rows = run_query(cases_sql, [str(txn_id)])
    cases = [
        {
            "id": str(c.get("id")),
            "severity": str(c.get("severity", "")),
            "owner": str(c.get("owner", "")),
            "state": str(c.get("state", "")),
            "reason": str(c.get("reason", "")),
        }
        for c in c_rows
    ]

    return {"transaction": transaction, "events": events, "cases": cases}


@api.get("/ops/trends", auth="none", name="opsTrends")
def ops_trends(req):
    sql = """
    SELECT
      booked_at::text AS day,
      COALESCE(SUM(amount), 0) AS volume,
      COUNT(*) AS count
    FROM bank_transactions
    GROUP BY booked_at
    ORDER BY booked_at
    """
    rows = run_query(sql)
    items = [
        {
            "day": str(r.get("day", "")),
            "volume": _to_float(r.get("volume")),
            "count": _to_int(r.get("count")),
        }
        for r in rows
    ]
    return {"items": items}


@api.get("/ops/accounts", auth="none", name="opsAccounts")
def ops_accounts(req):
    sql = """
    SELECT
      a.id,
      a.name,
      b.name AS branch_name,
      b.region,
      a.balance,
      COUNT(t.id) AS transaction_count
    FROM bank_accounts a
    JOIN bank_branches b ON a.branch_id = b.id
    LEFT JOIN bank_transactions t ON t.account_id = a.id
    GROUP BY a.id, a.name, b.name, b.region, a.balance
    ORDER BY a.id ASC
    """
    rows = run_query(sql)
    items = [
        {
            "id": _to_int(r.get("id")),
            "name": str(r.get("name", "")),
            "branch_name": str(r.get("branch_name", "")),
            "region": str(r.get("region", "")),
            "balance": _to_float(r.get("balance")),
            "transaction_count": _to_int(r.get("transaction_count")),
        }
        for r in rows
    ]
    return {"items": items}


@api.get("/ops/cases", auth="none", name="opsCases")
def ops_cases(req):
    sql = """
    SELECT
      c.id,
      c.transaction_id,
      c.severity,
      c.owner,
      c.state,
      c.reason,
      t.amount,
      a.name AS account_name
    FROM bank_cases c
    JOIN bank_transactions t ON c.transaction_id = t.id
    JOIN bank_accounts a ON t.account_id = a.id
    ORDER BY c.id DESC
    """
    rows = run_query(sql)
    items = [
        {
            "id": str(r.get("id")),
            "transaction_id": str(r.get("transaction_id", "")),
            "severity": str(r.get("severity", "")),
            "owner": str(r.get("owner", "")),
            "state": str(r.get("state", "")),
            "reason": str(r.get("reason", "")),
            "amount": _to_float(r.get("amount")),
            "account_name": str(r.get("account_name", "")),
        }
        for r in rows
    ]
    return {"items": items}


@api.patch("/ops/cases/{id}", auth="none", name="opsUpdateCase")
def ops_update_case(req):
    params = getattr(req, "params", {}) or {}
    case_id = params.get("id")
    if not case_id:
        raise HTTPError(404, {"error": "case_not_found"})

    try:
        body = req.json()
    except Exception:
        raise HTTPError(422, {"error": "invalid_case_update"})

    state, owner, note = _validate_case_update(body)

    sql = """
    WITH changed AS (
      UPDATE bank_cases
      SET state = %s, owner = %s
      WHERE id = %s
      RETURNING id, state, owner
    )
    INSERT INTO bank_case_actions(case_id, state, owner, note)
    SELECT id, state, owner, %s FROM changed
    RETURNING case_id, state, owner, note, occurred_at::text
    """
    rows = run_query(sql, [state, owner, str(case_id), note])
    if not rows:
        raise HTTPError(404, {"error": "case_not_found"})

    r = rows[0]
    return {
        "case": {
            "case_id": str(r.get("case_id", "")),
            "state": str(r.get("state", "")),
            "owner": str(r.get("owner", "")),
            "note": str(r.get("note", "")),
            "occurred_at": str(r.get("occurred_at", "")),
        }
    }


@api.get("/ops/cases/{id}/actions", auth="none", name="opsCaseActions")
def ops_case_actions(req):
    params = getattr(req, "params", {}) or {}
    case_id = params.get("id")
    if not case_id:
        raise HTTPError(404, {"error": "case_not_found"})

    check_sql = "SELECT id FROM bank_cases WHERE id = %s"
    check_rows = run_query(check_sql, [str(case_id)])
    if not check_rows:
        raise HTTPError(404, {"error": "case_not_found"})

    actions_sql = """
    SELECT id, case_id, state, owner, note, occurred_at::text AS occurred_at
    FROM bank_case_actions
    WHERE case_id = %s
    ORDER BY id DESC
    """
    rows = run_query(actions_sql, [str(case_id)])
    items = [
        {
            "id": _to_int(r.get("id")),
            "case_id": str(r.get("case_id", "")),
            "state": str(r.get("state", "")),
            "owner": str(r.get("owner", "")),
            "note": str(r.get("note", "")),
            "occurred_at": str(r.get("occurred_at", "")),
        }
        for r in rows
    ]
    return {"items": items}
