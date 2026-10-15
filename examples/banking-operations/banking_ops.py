from studio import api, HTTPError
import studio

# Change the non-secret database destination for your isolated demo.
DB_HOST = "127.0.0.1"
DB_PORT = 5432


def _query(sql: str, params: list = None) -> list:
    if params is None:
        params = []
    conn_str = f"host={DB_HOST} port={DB_PORT} user={studio.secret('PG_USER')} password={studio.secret('PG_PASSWORD')} dbname={studio.secret('PG_DATABASE')}"
    res = studio.nodes.postgres(
        connection_string=conn_str,
        operation="query",
        query=sql,
        query_parameters=params,
    )
    if res and isinstance(res, list) and len(res) > 0 and "rows" in res[0]:
        return res[0]["rows"]
    return []


@api.get("/ops/summary", auth="none", name="opsSummary")
def ops_summary(req):
    sql = """
        SELECT
            (SELECT COUNT(*)::int FROM bank_transactions) AS transactions,
            COALESCE((SELECT SUM(amount)::float FROM bank_transactions), 0.0) AS volume,
            (SELECT COUNT(*)::int FROM bank_transactions WHERE status = 'exception') AS exceptions,
            (SELECT COUNT(*)::int FROM bank_transactions WHERE status = 'pending') AS pending,
            (SELECT COUNT(*)::int FROM bank_accounts) AS accounts,
            (SELECT COUNT(*)::int FROM bank_branches) AS branches
    """
    rows = _query(sql)
    if rows:
        r = rows[0]
        return {
            "transactions": int(r.get("transactions") or 0),
            "volume": float(r.get("volume") or 0.0),
            "exceptions": int(r.get("exceptions") or 0),
            "pending": int(r.get("pending") or 0),
            "accounts": int(r.get("accounts") or 0),
            "branches": int(r.get("branches") or 0),
        }
    return {
        "transactions": 0,
        "volume": 0.0,
        "exceptions": 0,
        "pending": 0,
        "accounts": 0,
        "branches": 0,
    }


@api.get("/ops/transactions", auth="none", name="opsTransactions")
def ops_transactions(req):
    query_params = req.query if getattr(req, "query", None) else {}

    status_val = str(query_params.get("status") or "").strip()
    region_val = str(query_params.get("region") or "").strip()
    search_val = str(query_params.get("search") or "").strip()

    try:
        page = int(query_params.get("page", 1))
        if page < 1:
            page = 1
    except (ValueError, TypeError):
        page = 1

    try:
        page_size = int(query_params.get("page_size", 25))
        if page_size < 1:
            page_size = 25
        elif page_size > 50:
            page_size = 50
    except (ValueError, TypeError):
        page_size = 25

    offset = (page - 1) * page_size
    search_pattern = f"%{search_val}%" if search_val else ""

    filter_params = [
        status_val,
        status_val,
        status_val,
        region_val,
        region_val,
        region_val,
        search_val,
        search_pattern,
        search_pattern,
        search_pattern,
    ]

    sql_where = """
        WHERE (%s = '' OR %s = 'all' OR t.status = %s)
          AND (%s = '' OR %s = 'all' OR b.region = %s)
          AND (
            %s = '' OR
            LOWER(t.id) LIKE LOWER(%s) OR
            LOWER(t.reference) LIKE LOWER(%s) OR
            LOWER(a.name) LIKE LOWER(%s)
          )
    """

    sql_count = f"""
        SELECT COUNT(*)::int AS total
        FROM bank_transactions t
        JOIN bank_accounts a ON t.account_id = a.id
        JOIN bank_branches b ON a.branch_id = b.id
        {sql_where}
    """
    count_rows = _query(sql_count, filter_params)
    total = int(count_rows[0]["total"]) if count_rows and "total" in count_rows[0] else 0

    sql_data = f"""
        SELECT
            t.id,
            t.account_id,
            a.name AS account_name,
            b.name AS branch_name,
            b.region,
            t.amount::float AS amount,
            t.status,
            t.booked_at::text AS booked_at,
            t.channel,
            t.reference
        FROM bank_transactions t
        JOIN bank_accounts a ON t.account_id = a.id
        JOIN bank_branches b ON a.branch_id = b.id
        {sql_where}
        ORDER BY t.id DESC
        LIMIT %s OFFSET %s
    """
    data_params = filter_params + [page_size, offset]
    data_rows = _query(sql_data, data_params)

    items = []
    for r in data_rows:
        items.append(
            {
                "id": str(r["id"]),
                "account_id": int(r["account_id"]),
                "account_name": str(r.get("account_name") or ""),
                "branch_name": str(r.get("branch_name") or ""),
                "region": str(r.get("region") or ""),
                "amount": float(r.get("amount") or 0.0),
                "status": str(r.get("status") or ""),
                "booked_at": str(r.get("booked_at") or ""),
                "channel": str(r.get("channel") or ""),
                "reference": str(r.get("reference") or ""),
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@api.get("/ops/transactions/{id}", auth="none", name="opsTransaction")
def ops_transaction(req):
    tx_id = req.params.get("id") if getattr(req, "params", None) else None
    if not tx_id:
        raise HTTPError(404, {"error": "not_found"})

    sql_tx = """
        SELECT
            t.id,
            t.account_id,
            a.name AS account_name,
            b.name AS branch_name,
            b.region,
            t.amount::float AS amount,
            t.status,
            t.booked_at::text AS booked_at,
            t.channel,
            t.reference
        FROM bank_transactions t
        JOIN bank_accounts a ON t.account_id = a.id
        JOIN bank_branches b ON a.branch_id = b.id
        WHERE t.id = %s
    """
    tx_rows = _query(sql_tx, [tx_id])
    if not tx_rows:
        raise HTTPError(404, {"error": "not_found"})

    r = tx_rows[0]
    tx_item = {
        "id": str(r["id"]),
        "account_id": int(r["account_id"]),
        "account_name": str(r.get("account_name") or ""),
        "branch_name": str(r.get("branch_name") or ""),
        "region": str(r.get("region") or ""),
        "amount": float(r.get("amount") or 0.0),
        "status": str(r.get("status") or ""),
        "booked_at": str(r.get("booked_at") or ""),
        "channel": str(r.get("channel") or ""),
        "reference": str(r.get("reference") or ""),
    }

    sql_events = """
        SELECT
            e.id,
            e.occurred_at::text AS occurred_at,
            e.message
        FROM bank_events e
        WHERE e.transaction_id = %s
        ORDER BY e.occurred_at ASC, e.id ASC
    """
    event_rows = _query(sql_events, [tx_id])
    events = [
        {
            "id": int(e["id"]),
            "occurred_at": str(e.get("occurred_at") or ""),
            "message": str(e.get("message") or ""),
        }
        for e in event_rows
    ]

    sql_cases = """
        SELECT
            c.id,
            c.severity,
            c.owner,
            c.state,
            c.reason
        FROM bank_cases c
        WHERE c.transaction_id = %s
        ORDER BY c.id ASC
    """
    case_rows = _query(sql_cases, [tx_id])
    cases = [
        {
            "id": str(c["id"]),
            "severity": str(c.get("severity") or ""),
            "owner": str(c.get("owner") or ""),
            "state": str(c.get("state") or ""),
            "reason": str(c.get("reason") or ""),
        }
        for c in case_rows
    ]

    return {"transaction": tx_item, "events": events, "cases": cases}


@api.get("/ops/trends", auth="none", name="opsTrends")
def ops_trends(req):
    sql = """
        SELECT
            booked_at::text AS day,
            SUM(amount)::float AS volume,
            COUNT(*)::int AS count
        FROM bank_transactions
        GROUP BY booked_at
        ORDER BY booked_at
    """
    rows = _query(sql)
    items = [
        {
            "day": str(r.get("day") or ""),
            "volume": float(r.get("volume") or 0.0),
            "count": int(r.get("count") or 0),
        }
        for r in rows
    ]
    return {"items": items}


@api.get("/ops/accounts", auth="none", name="opsAccounts")
def ops_accounts(req):
    sql = """
        SELECT
            a.id::int AS id,
            a.name,
            b.name AS branch_name,
            b.region,
            a.balance::float AS balance,
            COUNT(t.id)::int AS transaction_count
        FROM bank_accounts a
        JOIN bank_branches b ON a.branch_id = b.id
        LEFT JOIN bank_transactions t ON t.account_id = a.id
        GROUP BY a.id, a.name, b.name, b.region, a.balance
        ORDER BY a.id ASC
    """
    rows = _query(sql)
    items = [
        {
            "id": int(r["id"]),
            "name": str(r.get("name") or ""),
            "branch_name": str(r.get("branch_name") or ""),
            "region": str(r.get("region") or ""),
            "balance": float(r.get("balance") or 0.0),
            "transaction_count": int(r.get("transaction_count") or 0),
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
            t.amount::float AS amount,
            a.name AS account_name
        FROM bank_cases c
        JOIN bank_transactions t ON c.transaction_id = t.id
        JOIN bank_accounts a ON t.account_id = a.id
        ORDER BY c.id DESC
    """
    rows = _query(sql)
    items = [
        {
            "id": str(r["id"]),
            "transaction_id": str(r.get("transaction_id") or ""),
            "severity": str(r.get("severity") or ""),
            "owner": str(r.get("owner") or ""),
            "state": str(r.get("state") or ""),
            "reason": str(r.get("reason") or ""),
            "amount": float(r.get("amount") or 0.0),
            "account_name": str(r.get("account_name") or ""),
        }
        for r in rows
    ]
    return {"items": items}
