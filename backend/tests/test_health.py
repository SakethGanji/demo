def test_health_reports_ok(client, data_dir):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]
    assert body["datasets"] >= 2  # the two samples are generated on first start
    assert body["data_dir"] == str(data_dir.resolve())


def test_unknown_route_is_404(client):
    assert client.get("/api/nope").status_code == 404
