"""A failed app build must not activate a release or change any published pointer."""
import pytest

from .journeys._helpers import SIMPLE_APP, needs_esbuild, new_project, save_app

pytestmark = pytest.mark.integration


@needs_esbuild
def test_failed_multi_app_promotion_preserves_previous_release_and_all_apps(api):
    project, slug = new_project(api, 'atomic')
    apps = []
    for index in range(2):
        app = api.post(f'/projects/{project}/apps', {'name': f'App {index}', 'definition': {}}).json()['id']
        save_app(api, app, SIMPLE_APP)
        response = api.post(f'/apps/{app}/publish', {'slug': f'{slug}-{index}', 'access': 'public'})
        assert response.status_code == 200, response.text
        apps.append(app)

    def snapshot():
        response = api.post(f'/projects/{project}/releases', {'environment': 'dev'})
        assert response.status_code == 201, response.text
        return response.json()['id']

    def promote(release):
        return api.post(f'/projects/{project}/releases/{release}/promote', {'to_environment': 'prod'})

    initial = promote(snapshot())
    assert initial.status_code == 201, initial.text
    previous = {app: api.get(f'/apps/{app}').json()['published_version']['id'] for app in apps}
    save_app(api, apps[0], 'export default function App(){return <div>New valid version</div>}')
    save_app(api, apps[1], 'export default function App(){return <div></main>}')
    rejected = promote(snapshot())
    assert rejected.status_code == 422, rejected.text
    assert 'release_publish_failed' in rejected.text
    releases = api.get(f'/projects/{project}/releases?environment=prod').json()
    assert [release['id'] for release in releases if release['is_active']] == [initial.json()['id']]
    for app in apps:
        assert api.get(f'/apps/{app}').json()['published_version']['id'] == previous[app]

    # A subsequent valid promotion must update both pointers together.
    save_app(api, apps[1], 'export default function App(){return <div>Repaired version</div>}')
    successful = promote(snapshot())
    assert successful.status_code == 201, successful.text
    for app in apps:
        assert api.get(f'/apps/{app}').json()['published_version']['id'] != previous[app]
