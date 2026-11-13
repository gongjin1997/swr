"""Role lifecycle, cluster discovery and project-to-UAT release integration."""
import asyncio
import json
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from swr_builder.builder import JobLog
from swr_builder.config import Settings
from swr_builder.deploy.k8s_client import KubeClient
from swr_builder.deploy.worker import ReturnedToApproval
from swr_builder.errors import Unavailable
from swr_builder.main import create_app
from swr_builder.security import hash_password
from swr_builder.services.releases import valid_approval_count
from tests.test_accounts_settings import HEAD, login
from tests.test_deploy_cloud import wait_deploy


class KubeFixture:
    def __init__(self):
        self.deny_namespaces = False
        self.image = 'registry.example/team/payments:old'
        self.generation = 1
        self.annotations = {}
        self.requests = []

    def deployment(self):
        return {'metadata': {'name': 'payments', 'uid': 'payments-uid', 'generation': self.generation},
                'spec': {'replicas': 1, 'template': {'metadata': {'labels': {'app': 'payments'}, 'annotations': self.annotations},
                         'spec': {'containers': [{'name': 'api', 'image': self.image,
                                                  'env': [{'name': 'PASSWORD', 'value': 'DO_NOT_EXPOSE'}]}]}}},
                'status': {'replicas': 1, 'readyReplicas': 1, 'updatedReplicas': 1, 'availableReplicas': 1,
                           'observedGeneration': self.generation}}

    def __call__(self, request):
        path = request.url.path
        self.requests.append((request.method, path))
        if path == '/version':
            return httpx.Response(200, json={'gitVersion': 'v1.30.7'})
        if path == '/api/v1/namespaces':
            if self.deny_namespaces:
                return httpx.Response(403)
            return httpx.Response(200, json={'items': [{'metadata': {'name': n}, 'status': {'phase': 'Active'}}
                                                      for n in ['uat', 'prod', 'other']]})
        if path == '/api/v1/nodes':
            return httpx.Response(403)
        if path in ['/api/v1/namespaces/' + n for n in ['uat', 'prod', 'other']]:
            return httpx.Response(200, json={'metadata': {'name': path.rsplit('/', 1)[1]}})
        if path.endswith('/deployments/payments'):
            if request.method == 'PATCH':
                template = json.loads(request.content)['spec']['template']
                self.image = template['spec']['containers'][0]['image']
                self.annotations = template['metadata']['annotations']
                self.generation += 1
            return httpx.Response(200, json=self.deployment())
        if path.endswith('/deployments'):
            items = [self.deployment()]
        elif path.endswith('/services'):
            items = [{'metadata': {'name': 'payments-http'}, 'spec': {'type': 'ClusterIP', 'clusterIP': '10.96.0.9',
                     'selector': {'app': 'payments'}, 'ports': [{'port': 80, 'targetPort': 8080}]}},
                     {'metadata': {'name': 'unrelated'}, 'spec': {'selector': {'app': 'other'}}}]
        elif path.endswith('/pods'):
            items = [{'metadata': {'name': 'payments-abc', 'labels': {'app': 'payments'}},
                      'spec': {'nodeName': 'node-a', 'containers': [{'env': [{'value': 'DO_NOT_EXPOSE'}]}]},
                      'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}]}}]
        elif path.endswith('/ingresses'):
            items = [{'metadata': {'name': 'payments'}, 'spec': {'rules': [{'host': 'payments.example',
                      'http': {'paths': [{'backend': {'service': {'name': 'payments-http'}}}]}}]}}]
        else:
            return httpx.Response(404)
        return httpx.Response(200, json={'items': items})


@pytest.fixture
def linked(tmp_path):
    settings = Settings()
    settings.build.data_dir = str(tmp_path / 'data')
    settings.server.admin_password = 'AdminPass123'
    kube = KubeFixture()
    app = create_app(settings, deploy_transport=httpx.MockTransport(kube))
    with TestClient(app, headers=HEAD) as client:
        login(client)
        project = client.post('/api/projects', json={'slug': 'linked', 'name': '支付项目',
                         'registry_orgs': [{'namespace': 'team', 'base_prefixes': []}]}).json()
        cluster = client.post('/api/clusters', json={'name': '测试集群', 'server': 'https://kube.example',
                         'auth_type': 'sa_token', 'credential': {'token': 'private-token'}}).json()
        namespaces = []
        for name, tier in [('uat', 'uat'), ('prod', 'prod')]:
            response = client.post('/api/namespaces', json={'name': name, 'cluster_id': cluster['id'],
                                   'tier': tier, 'project_id': project['id'], 'policy': {
                                       'require_approval': False, 'allowed_registry_orgs': ['team']}})
            assert response.status_code == 201, response.text
            namespaces.append(response.json())
        app_row = app.state.store.create_app({'name': '支付应用', 'repository': 'payments', 'organization': 'team'}, project['id'])
        services = [client.post('/api/services', json={'name': 'payments-' + ns['name'], 'project_id': project['id'],
                     'namespace_id': ns['id'], 'app_id': app_row['id'], 'spec': {'mode': 'image_only',
                     'target': {'kind': 'Deployment', 'name': 'payments', 'container': 'api'}}}).json() for ns in namespaces]
        yield settings, app, client, kube, project, cluster, namespaces, app_row, services


def user(store, name):
    return store.create_user(username=name, role='viewer', enabled=1, must_change_password=0,
                             password_hash=hash_password('UserPass123'))


def test_discovery_adoption_runtime_and_relation_filters(linked):
    _, app, c, kube, project, cluster, namespaces, app_row, services = linked
    result = c.get(f"/api/clusters/{cluster['id']}/discovery")
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['version'] == 'v1.30.7' and len(data['namespaces']) == 3
    assert data['warnings'] and 'nodes' in data['warnings'][0]
    assert data['namespaces'][0]['managed_id'] == namespaces[0]['id']
    assert set(c.get(f"/api/projects/{project['id']}").json()['namespace_ids']) == {n['id'] for n in namespaces}
    assert namespaces[0]['tier'] == 'staging'
    assert c.get(f"/api/namespaces/{namespaces[0]['id']}/resources").json()['deployments'][0]['containers'][0]['name'] == 'api'
    runtime = c.get(f"/api/services/{services[0]['id']}/runtime")
    assert runtime.status_code == 200 and 'DO_NOT_EXPOSE' not in runtime.text and 'private-token' not in runtime.text
    assert [s['name'] for s in runtime.json()['services']] == ['payments-http']
    assert runtime.json()['pods'][0]['ready'] and runtime.json()['ingresses'][0]['hosts'] == ['payments.example']
    rows = c.get('/api/services', params={'project_id': project['id'], 'namespace_id': namespaces[0]['id'], 'app_id': app_row['id']}).json()['items']
    assert [r['id'] for r in rows] == [services[0]['id']]
    assert rows[0]['project_name'] == '支付项目' and rows[0]['cluster_name'] == '测试集群'
    kube.deny_namespaces = True
    assert c.get(f"/api/clusters/{cluster['id']}/discovery").status_code == 403
    outsider = user(app.state.store, 'outsider')
    login(c, outsider['username'], 'UserPass123')
    assert c.get(f"/api/services/{services[0]['id']}/runtime").status_code == 404
    assert c.get('/api/services').json()['items'] == []
    assert c.get(f"/api/clusters/{cluster['id']}/discovery").status_code == 403


def test_custom_roles_lifecycle_scope_and_immediate_revocation(linked):
    _, app, c, _, _, _, namespaces, _, _ = linked
    store = app.state.store
    reviewer = user(store, 'uat-reviewer')
    role = {'code': 'uat_review', 'name': 'UAT 审批员', 'scope_type': 'namespace',
            'permissions': ['namespace.view', 'deploy.view', 'deploy.approve'], 'description': '预生产审批', 'enabled': True}
    created = c.post('/api/role-definitions', json=role)
    assert created.status_code == 201, created.text
    assert c.post('/api/role-definitions', json=role).status_code == 409
    assert c.post('/api/role-definitions', json={**role, 'code': 'bad_scope', 'permissions': ['settings.manage']}).status_code == 400
    assert c.put('/api/role-definitions/platform_admin', json=role).status_code == 404
    assert c.put('/api/role-definitions/uat_review', json={**role, 'scope_type': 'global'}).status_code == 400
    binding = c.post('/api/bindings', json={'subject_type': 'user', 'subject_id': reviewer['id'], 'role': role['code'],
                      'scope_type': 'namespace', 'scope_id': namespaces[0]['id']})
    assert binding.status_code == 201, binding.text
    assert c.delete('/api/role-definitions/uat_review').status_code == 409
    login(c, reviewer['username'], 'UserPass123')
    profile = c.get('/api/auth/me').json()
    assert 'deploy.approve' in profile['permissions']
    assert [n['id'] for n in profile['namespaces']] == [namespaces[0]['id']]
    assert c.post('/api/role-definitions', json={**role, 'code': 'new_role'}).status_code == 403
    login(c)
    assert c.put('/api/role-definitions/uat_review', json={**role, 'enabled': False}).status_code == 200
    login(c, reviewer['username'], 'UserPass123')
    assert 'deploy.approve' not in c.get('/api/auth/me').json()['permissions']
    login(c)
    assert c.delete(f"/api/bindings/{binding.json()['id']}").status_code == 204
    assert c.delete('/api/role-definitions/uat_review').status_code == 204
    assert any(e['action'] == 'role.update' for e in store.list_audit(limit=1000)[0])


def test_user_management_role_cannot_grant_platform_admin(linked):
    _, app, c, _, _, _, _, _, _ = linked
    store = app.state.store
    manager, target = user(store, 'account-manager'), user(store, 'target-user')
    c.post('/api/role-definitions', json={'code': 'account_manager', 'name': '账号管理员', 'scope_type': 'global',
                                         'permissions': ['user.manage']})
    store.create_binding('user', manager['id'], 'account_manager', 'global', 0, 1)
    login(c, manager['username'], 'UserPass123')
    assert c.post('/api/bindings', json={'subject_type': 'user', 'subject_id': target['id'], 'role': 'platform_admin',
                                       'scope_type': 'global', 'scope_id': 0}).status_code == 403
    assert c.post('/api/users', json={'username': 'rogue-admin', 'password': 'UserPass123', 'role': 'admin'}).status_code == 403
    admin = store.get_user_by_name('admin')
    assert c.post(f"/api/users/{admin['id']}/reset-password", json={}).status_code == 403
    assert c.put(f"/api/users/{target['id']}", json={'role': 'admin'}).status_code == 403
    disabled = user(store, 'disabled-admin')
    admin_binding = store.create_binding('user', disabled['id'], 'platform_admin', 'global', 0, 1)
    store.update_user(disabled['id'], enabled=0)
    assert c.put(f"/api/users/{disabled['id']}", json={'enabled': True}).status_code == 403
    assert c.post(f"/api/users/{disabled['id']}/reset-password", json={}).status_code == 403
    assert c.delete(f'/api/bindings/{admin_binding}').status_code == 404


def test_uat_approval_is_scoped_independent_and_applied(linked):
    _, app, c, kube, project, _, namespaces, app_row, services = linked
    store = app.state.store
    submitter, reviewer, wrong = user(store, 'publisher'), user(store, 'reviewer'), user(store, 'wrong-scope')
    for actor, role, ns in [(submitter, 'deployer', namespaces[0]), (reviewer, 'namespace_approver', namespaces[0]),
                            (wrong, 'namespace_approver', namespaces[1])]:
        store.create_binding('user', actor['id'], role, 'namespace', ns['id'], 1)
    job = store.create_job(app_id=app_row['id'], app_name='支付应用', project_id=project['id'], status='success',
                           image='registry.example/team/payments:1.0.1', digest='sha256:' + 'a1' * 32,
                           created_at='2026-11-10T00:00:00+00:00')
    login(c, submitter['username'], 'UserPass123')
    available = c.get(f"/api/services/{services[0]['id']}/builds")
    assert available.status_code == 200 and [j['id'] for j in available.json()['items']] == [job]
    assert 'app_snapshot' not in available.text
    submitted = c.post(f"/api/services/{services[0]['id']}/deployments", json={'job_id': job}, headers={'Idempotency-Key': 'uat-approval-test'})
    assert submitted.status_code == 202, submitted.text
    deployment = submitted.json()
    assert deployment['status'] == 'pending_approval'
    login(c, wrong['username'], 'UserPass123')
    assert c.post(f"/api/deployments/{deployment['id']}/approve", json={}).status_code == 404
    login(c, reviewer['username'], 'UserPass123')
    listing = c.get('/api/deployments', params={'service_id': services[0]['id']}).json()['items']
    assert len(listing) == 1 and listing[0]['can_approve'] and listing[0]['project_name'] == '支付项目'
    assert c.post(f"/api/deployments/{deployment['id']}/approve", json={'comment': 'UAT 已验收'}).status_code == 200
    completed = wait_deploy(c, deployment['id'])
    assert completed['status'] == 'success', completed
    assert kube.image == 'registry.example/team/payments:1.0.1'  # 按版本号下发


def test_revoked_approval_cannot_pass_worker_precheck(linked):
    _, app, c, _, project, _, namespaces, app_row, services = linked
    c.portal.call(app.state.ctx.deploy.service_worker.stop)
    store = app.state.store
    publisher, reviewer = user(store, 'publisher'), user(store, 'reviewer')
    store.create_binding('user', publisher['id'], 'deployer', 'namespace', namespaces[0]['id'], 1)
    binding = store.create_binding('user', reviewer['id'], 'namespace_approver', 'namespace', namespaces[0]['id'], 1)
    job = store.create_job(app_id=app_row['id'], app_name='api', project_id=project['id'], status='success',
                           image='registry.example/team/payments:1', digest='sha256:' + 'a2' * 32, created_at='2026-11-10T00:00:00Z')
    login(c, publisher['username'], 'UserPass123')
    record = c.post(f"/api/services/{services[0]['id']}/deployments", json={'job_id': job},
                    headers={'Idempotency-Key': 'revoked-approval-test'}).json()
    # Even after acquiring approval permission, the submitter cannot approve their own request.
    store.create_binding('user', publisher['id'], 'namespace_approver', 'namespace', namespaces[0]['id'], 1)
    assert c.post(f"/api/deployments/{record['id']}/approve", json={}).status_code == 403
    login(c, reviewer['username'], 'UserPass123')
    assert c.post(f"/api/deployments/{record['id']}/approve", json={}).status_code == 200
    record = store.get_deployment(record['id'])
    assert valid_approval_count(store, record) == 1
    store.delete_binding(binding)
    assert valid_approval_count(store, record) == 0
    with pytest.raises(ReturnedToApproval):
        app.state.ctx.deploy.service_worker._precheck(record, namespaces[0])


def test_kubernetes_list_pagination_and_invalid_response():
    calls = []
    def handler(request):
        calls.append(request.url.params.get('continue'))
        return httpx.Response(200, json={'items': [{'metadata': {'name': 'second' if calls[-1] else 'first'}}],
                                       'metadata': {'continue': '' if calls[-1] else 'next-page'}})
    async def run():
        async with KubeClient('https://kube.example', {}, httpx.MockTransport(handler)) as kube:
            assert len(await kube.list_resources('/api/v1/namespaces')) == 2
        async with KubeClient('https://kube.example', {}, httpx.MockTransport(lambda r: httpx.Response(200, json={'items': 'bad'}))) as kube:
            with pytest.raises(Unavailable):
                await kube.list_resources('/api/v1/namespaces')
    asyncio.run(run())
    assert calls == ['', 'next-page']


def test_builder_passes_token_auth_mode_to_actual_child_process(linked, tmp_path, monkeypatch):
    settings, app, *_ = linked
    monkeypatch.delenv('BUILDKIT_NO_CLIENT_TOKEN', raising=False)
    manager = app.state.manager
    assert 'BUILDKIT_NO_CLIENT_TOKEN' not in manager.docker_env
    settings.registry.insecure = True
    log = JobLog(tmp_path / 'child.log', [])
    try:
        code, output = asyncio.run(manager._run_cmd(999, [sys.executable, '-c',
            'import os; print(os.environ.get("BUILDKIT_NO_CLIENT_TOKEN"))'], log, 10))
        assert code == 0 and output == '1'
    finally:
        log.close()
