from studio import api, HTTPError
import studio

refunds = studio.db.table("refunds")
UPSTREAM = "${E2E_UPSTREAM_URL}"


@api.post("/refunds", name="createRefund")
def create_refund(req):
    body = req.body or {}
    if "amount" not in body:
        raise HTTPError(422, {"error": "amount is required"})
    return refunds.insert({"reason": body.get("reason", ""), "amount": body["amount"]})


@api.get("/refunds", name="listRefunds")
def list_refunds(req):
    return refunds.list(limit=int(req.query.get("limit", 50)))


@api.get("/refunds/stats", name="refundStats")
def refund_stats(req):
    balance = studio.nodes.http_request(
        method="GET",
        url=f"{UPSTREAM}/v1/balance",
        headers=[{"name": "Authorization", "value": f"Bearer {studio.secret('PAYMENTS_TOKEN')}"}],
    )
    rows = refunds.list(limit=1000)
    by_reason = {}
    for r in rows:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + r["amount"]
    return {"count": len(rows), "by_reason": by_reason, "available": balance[0]["body"]["available"]}
