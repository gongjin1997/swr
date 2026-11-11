"""业务服务层：统一的冲突错误、列表格式、概览与发布审计。"""
from swr_builder.security import hash_password
from tests.test_accounts_settings import login, setup  # noqa: F401  pytest fixture
from tests.test_linked_platform import linked  # noqa: F401  pytest fixture


def test_unique_constraints_are_conflicts_not_server_errors(setup):  # noqa: F811
    _, _, c = setup
    login(c)
    assert c.post('/api/projects', json={'slug': 'dup', 'name': '重复'}).status_code == 201
    again = c.post('/api/projects', json={'slug': 'dup', 'name': '另一个'})
    assert again.status_code == 409 and again.json()['code'] == 'CONFLICT'
    cluster = {'name': 'same', 'server': 'https://kube.example', 'auth_type': 'sa_token', 'credential': {'token': 't'}}
    assert c.post('/api/clusters', json=cluster).status_code == 201
    assert c.post('/api/clusters', json=cluster).status_code == 409


def test_lists_are_pages_and_deletes_return_no_content(setup):  # noqa: F811
    settings, app, c = setup
    settings.registry.address = 'registry.example'
    login(c)
    for path in ('/api/apps', '/api/users', '/api/account/tokens', '/api/account/sessions', '/api/deploy/targets',
                 '/api/deploy/providers', '/api/projects', '/api/clusters', '/api/namespaces', '/api/services',
                 '/api/deployments', '/api/jobs', '/api/bindings', '/api/audit'):
        body = c.get(path).json()
        assert set(body) >= {'items', 'total'} and isinstance(body['items'], list), path
    token = c.post('/api/account/tokens', json={'name': 'tmp', 'expires_days': 1}).json()
    assert c.delete(f"/api/account/tokens/{token['id']}").status_code == 204
    user = c.post('/api/users', json={'username': 'gone', 'password': 'UserPass123'}).json()
    assert c.delete(f"/api/users/{user['id']}").status_code == 204
    team = c.post('/api/projects', json={'slug': 'pages', 'name': '分页',
                                         'registry_orgs': [{'namespace': 'team', 'base_prefixes': []}]}).json()
    created = c.post('/api/apps', json={'name': 'tmp-app', 'organization': 'team', 'repository': 'tmp-app',
                                        'project_id': team['id']})
    assert created.status_code == 201, created.text
    assert c.get('/api/apps', params={'project_id': team['id']}).json()['total'] == 1
    assert c.get('/api/apps', params={'project_id': app.state.store.default_project_id()}).json()['total'] == 0
    assert c.get(f"/api/apps/{created.json()['id']}").json()['image_repo'] == 'registry.example/team/tmp-app'
    assert c.delete(f"/api/apps/{created.json()['id']}").status_code == 204


def test_overview_counts_and_pending_approvals(linked):  # noqa: F811
    _, app, c, _, project, _, namespaces, app_row, services = linked
    store = app.state.store
    publisher = store.create_user(username='pub', role='member', enabled=1, must_change_password=0,
                                  password_hash=hash_password('UserPass123'))
    store.create_binding('user', publisher['id'], 'deployer', 'namespace', namespaces[0]['id'], 1)
    job = store.create_job(app_id=app_row['id'], app_name='支付应用', project_id=project['id'], status='success',
                           image='registry.example/team/payments:1.0.1', digest='sha256:' + 'b1' * 32,
                           created_at='2026-11-10T00:00:00+00:00')
    login(c, 'pub', 'UserPass123')
    submitted = c.post(f"/api/services/{services[0]['id']}/deployments", json={'job_id': job},
                       headers={'Idempotency-Key': 'overview-test-1'})
    assert submitted.status_code == 202, submitted.text
    assert c.get('/api/overview').json()['approvals'] == []  # 发起人不会看到自己的待审批任务
    login(c)
    overview = c.get('/api/overview').json()
    assert overview['projects'] >= 2 and overview['services'] == 2 and overview['jobs']['success'] == 1
    assert [a['id'] for a in overview['approvals']] == [submitted.json()['id']]
    actions = [e['action'] for e in store.list_audit(limit=50)[0]]
    assert actions.count('deploy.submit') == 1
    assert not any(a.startswith('POST /api/services') for a in actions)


def test_namespace_unassign_only_checks_the_projects_own_services(linked):  # noqa: F811
    _, app, c, _, project, _, namespaces, _, _ = linked
    other = c.post('/api/projects', json={'slug': 'other', 'name': '其他'}).json()
    assert c.put(f"/api/projects/{other['id']}/namespaces/{namespaces[0]['id']}", json={}).status_code == 200
    assert c.delete(f"/api/projects/{other['id']}/namespaces/{namespaces[0]['id']}").status_code == 204
    assert c.delete(f"/api/projects/{project['id']}/namespaces/{namespaces[0]['id']}").status_code == 409
    listed = c.get('/api/namespaces').json()['items']
    assert next(n for n in listed if n['id'] == namespaces[0]['id'])['project_ids'] == [project['id']]
