from studio import api, HTTPError
import studio


@api.post("/refunds", name="createRefund")
def create_refund(req):
    body = req.body or {}
    if "reason" not in body or "amount" not in body:
        raise HTTPError(422, {"error": "reason and amount are required"})
    return studio.db.table("refunds").insert({"reason": body["reason"], "amount": body["amount"]})


@api.get("/refunds", name="listRefunds")
def list_refunds(req):
    return studio.db.table("refunds").list()


@api.get("/refunds/stats", name="refundStats")
def refund_stats(req):
    rows = studio.db.table("refunds").list()
    auth = f"Bearer {studio.secret('PAYMENTS_TOKEN')}"
    balance = studio.nodes.http_request(method="GET", url="${E2E_UPSTREAM_URL}/v1/balance",
                                        headers=[{"name": "Authorization", "value": auth}])
    return {"count": len(rows), "total": sum(r.get("amount") or 0 for r in rows),
            "available": (balance[0].get("body") or {}).get("available")}
