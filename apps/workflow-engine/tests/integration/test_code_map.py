"""Code-map boundaries: analysis is read-only, scoped, bounded and never executed."""
import hashlib
import pytest
from .journeys._helpers import new_project

pytestmark = pytest.mark.integration


def test_code_map_analyzes_unsaved_source_without_saving_or_executing(api):
    p, _ = new_project(api, 'map')
    module = api.post(f'/projects/{p}/modules', {'name': 'sample', 'source': 'def original(): return 1\n'}).json()['module']
    source = 'raise RuntimeError("never execute")\ndef helper(): return 1\ndef operation(): return helper()\n'
    response = api.post(f'/projects/{p}/modules/code-map', {'source': source, 'module_id': module['id']})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['valid'] and result['source_hash'] == hashlib.sha256(source.encode()).hexdigest()
    assert result['edges'][0]['from'] == 'operation' and result['edges'][0]['to'] == 'helper'
    assert response.headers['cache-control'] == 'no-store'
    saved = api.get(f'/projects/{p}/modules/{module["id"]}').json()
    assert saved['source'] == 'def original(): return 1\n'
    assert saved['source_sha'] == module['source_sha']
    assert api.get(f'/projects/{p}/runs').json() == []


def test_code_map_scope_and_validation(api):
    p, _ = new_project(api, 'mapa')
    other, _ = new_project(api, 'mapb')
    module = api.post(f'/projects/{other}/modules', {'name': 'private', 'source': 'def f(): pass'}).json()['module']
    assert api.post(f'/projects/{p}/modules/code-map', {'source': 'def f(): pass', 'module_id': module['id']}).status_code == 404
    assert api.post('/projects/not-a-project/modules/code-map', {'source': ''}).status_code == 404
    assert api.post(f'/projects/{p}/modules/code-map', {'source': 123}).status_code == 422
    assert api.post(f'/projects/{p}/modules/code-map', {'source': '', 'unexpected': True}).status_code == 422
    assert api.post(f'/projects/{p}/modules/code-map', {'source': '#' + 'x' * 200_000}).status_code == 413
    syntax = api.post(f'/projects/{p}/modules/code-map', {'source': 'def invalid('})
    assert syntax.status_code == 200
    assert not syntax.json()['valid'] and 'functions' not in syntax.json()
    # A new unnamed editor draft needs no saved module id.
    assert api.post(f'/projects/{p}/modules/code-map', {'source': 'def new_function(): pass'}).json()['valid']
