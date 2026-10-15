"""Real sandbox -> broker -> Postgres using a write-only project secret."""
import json
import pytest
from .conftest import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, uniq
from .journeys._helpers import needs_bwrap

pytestmark = pytest.mark.integration


@needs_bwrap
def test_saved_postgres_secret_is_used_and_host_binding_is_enforced(api, test_db):
    from psycopg.conninfo import make_conninfo
    p = api.post('/projects', {'name': uniq('pgsecret')}).json()
    secret = make_conninfo(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, dbname=test_db.name)
    r = api.post(f"/projects/{p['id']}/secrets", {
        'key': 'DEMO_DATABASE_URL', 'value': secret, 'environment': 'dev', 'allowed_hosts': [DB_HOST]})
    assert r.status_code == 201, r.text
    saved = r.json()
    assert saved['value'] is None and secret not in r.text
    stored = test_db.query("SELECT value FROM variables WHERE project_id = %s AND key = 'DEMO_DATABASE_URL'", (p['id'],))
    assert len(stored) == 1 and stored[0][0] != secret
    source = '''from studio import api
import studio

@api.get("/dashboard", name="salesDashboard")
def dashboard(req):
    result = studio.nodes.postgres(connection_string=studio.secret("DEMO_DATABASE_URL"),
        operation="query", query="SELECT 3 AS orders, 125.50::numeric AS revenue")
    return result[0]["rows"][0]
'''
    r = api.post(f"/projects/{p['id']}/modules", {'name': 'sales', 'source': source})
    assert r.status_code == 201, r.text
    route = api.get(f"/projects/{p['id']}/routes").json()[0]
    def run():
        return api.post(f"/projects/{p['id']}/routes/{route['id']}/test", {'environment': 'dev'})
    r = run()
    assert r.status_code == 200, r.text
    assert r.json()['status'] == 200, r.text
    assert r.json()['body'] == {'orders': 3, 'revenue': 125.5}
    assert secret not in r.text
    details = api.get(f"/projects/{p['id']}/runs/{r.json()['execution_id']}")
    assert secret not in details.text
    assert secret not in json.dumps(api.get(f"/projects/{p['id']}/secrets").json())
    api.put(f"/projects/{p['id']}/secrets/{saved['id']}", {'allowed_hosts': ['wrong.example.com']})
    denied = run()
    assert denied.json()['status'] != 200, denied.text
    assert 'host_not_allowed' in denied.text
    assert secret not in denied.text
