"""统一访问控制：权限目录、请求主体、账号管理与授权规则。"""
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from swr_builder import auth as auth_module
from swr_builder.access import BUILTIN_ROLES, PERMISSIONS, WILDCARD, expand, grantable_permissions, load_principal
from swr_builder.config import Settings
from swr_builder.main import AUTHENTICATED_ROUTE, PUBLIC_ROUTE, ROUTE_PERMISSIONS, create_app
from swr_builder.security import hash_password
from swr_builder.services import releases
from swr_builder.store import IntegrityViolation
from swr_builder.web.guard import requires
from swr_builder.web.permissions import DEPLOY_MODULE_ROUTES, ROUTE_TABLE, collect_route_permissions, match_route
from tests.dbutil import new_store
from tests.test_accounts_settings import HEAD, login

PASSWORD = 'UserPass123'


@pytest.fixture
def platform(tmp_path):
    settings = Settings()
    settings.build.data_dir = str(tmp_path / 'data')
    settings.server.admin_password = 'AdminPass123'
    app = create_app(settings)
    with TestClient(app, headers=HEAD) as client:
        login(client)
        yield app, client


def member(store, name, enabled=1):
    return store.create_user(username=name, role='member', enabled=enabled, must_change_password=0,
                             password_hash=hash_password(PASSWORD))


def project(client, slug):
    response = client.post('/api/projects', json={'slug': slug, 'name': slug.upper(), 'registry_orgs': []})
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------ 权限目录与路由声明
def test_catalog_is_the_single_source_of_route_and_role_permissions():
    for permission in ROUTE_PERMISSIONS.values():
        assert permission in (PUBLIC_ROUTE, AUTHENTICATED_ROUTE) or permission in PERMISSIONS
    for role in BUILTIN_ROLES.values():
        assert role.permissions <= set(PERMISSIONS) | {WILDCARD}
        assert role.effective, role.code
    assert BUILTIN_ROLES['platform_admin'].effective == frozenset(PERMISSIONS)
    assert {'app.view', 'project.view'} <= expand(['app.manage'], 'project')
    assert expand(['deploy.approve'], 'namespace') == {'deploy.approve', 'deploy.view', 'namespace.view'}
    assert expand(['cluster.manage']) == {'cluster.manage', 'namespace.view'}
    assert expand(['settings.manage'], 'project') == frozenset()
    assert expand(['no.such.permission']) == frozenset()
    assert 'settings.manage' not in grantable_permissions('namespace')
    assert 'platform.admin' not in grantable_permissions('global')


def test_routes_must_declare_known_permissions():
    router = APIRouter()

    @router.get('/api/undeclared')
    async def undeclared():
        return {}

    with pytest.raises(RuntimeError, match='未声明权限'):
        collect_route_permissions([router])

    router = APIRouter()

    @router.get('/api/typo')
    @requires('app.veiw')
    async def typo():
        return {}

    with pytest.raises(RuntimeError, match='未登记的权限'):
        collect_route_permissions([router])


def test_deploy_module_routes_are_declared():
    """部署中心开关覆盖的路由：发布记录、审批与日志，旧部署目标，以及服务的发布、回滚、重新发布、接管与扩缩容。
    服务、配置、运行状态和清单预览不受开关影响。"""
    legacy = {(m, p) for m, p, _ in ROUTE_TABLE if p.startswith(('/api/deploy/', '/api/deployments'))}
    releases_routes = {('POST', f'/api/services/{{id}}/{name}')
                       for name in ('deployments', 'rollback', 'restart', 'adopt', 'scale')}
    assert legacy and DEPLOY_MODULE_ROUTES == legacy | releases_routes
    assert ('GET', '/api/services/{id}/runtime') not in DEPLOY_MODULE_ROUTES
    assert match_route('/api/services/7/rollback', 'POST') == (AUTHENTICATED_ROUTE, True)
    assert match_route('/api/services/7', 'GET') == (AUTHENTICATED_ROUTE, False)


def test_deploy_switch_closes_release_routes_but_not_services(platform):
    app, client = platform
    app.state.settings.general.deploy_enabled = False
    response = client.post('/api/services/1/rollback', json={}, headers={'Idempotency-Key': 'switch-off-1'})
    assert response.status_code == 403 and response.json()['code'] == 'MODULE_DISABLED'
    assert client.post('/api/deployments/1/approve', json={}).json()['code'] == 'MODULE_DISABLED'
    assert client.get('/api/services').status_code == 200
    assert client.get('/api/overview').json()['approvals'] == []


# ------------------------------------------------------------------ 请求主体
def test_principal_scoping_status_and_role_state(tmp_path):
    store = new_store()
    other = store.create_project('other', '其他项目', [])['id']
    default = store.default_project_id()
    cluster = store.create_cluster({'name': 'c', 'auth_type': 'sa_token', 'server': 'https://k.example',
                                    'credential_encrypted': 'x'})
    namespace = store.create_namespace(cluster, 'dev', 'dev', {}, default)
    user = member(store, 'scoped')
    store.create_binding('user', user['id'], 'developer', 'project', default, None)
    store.create_binding('user', user['id'], 'developer', 'global', 0, None)  # 角色与范围不一致：忽略
    store.create_binding('user', user['id'], 'auditor', 'global', 0, None, '2000-01-01T00:00:00+00:00')  # 已过期
    store.save_custom_role('ns_ops', '运维', 'namespace', ['namespace.view', 'service.operate'], '', False)
    store.create_binding('user', user['id'], 'ns_ops', 'namespace', namespace, None)  # 角色已停用

    me = load_principal(store, user)
    assert me.can_project(default, 'build.run') and not me.can_project(other, 'app.view')
    assert me.global_perms == frozenset() and me.project_ids('app.view') == [default]
    assert not me.can_namespace(namespace, 'namespace.view')  # 项目授权不传递到命名空间
    assert me.can_anywhere('app.manage') and not me.can('app.manage')

    store.save_custom_role('ns_ops', '运维', 'namespace', ['namespace.view', 'service.operate'], '', True)
    assert load_principal(store, user).can_namespace(namespace, 'service.operate')

    store.update_user(user['id'], enabled=0)
    assert load_principal(store, store.get_user(user['id'])).all_permissions == frozenset()
    assert load_principal(store, store.get_user(user['id']), include_disabled=True).can_project(default, 'app.view')


def test_legacy_admin_role_is_reconciled_into_bindings(tmp_path):
    """v1.3.0-dev.2 之前 users.role = 'admin' 即管理员。导入 v6 的 SQLite 数据时补齐平台管理员授权，
    并把授权过期时间统一为 UTC。"""
    import sqlite3

    from swr_builder.store import legacy_sqlite
    from swr_builder.store.importer import import_sqlite

    path = tmp_path / 'builder.db'
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(legacy_sqlite.SCHEMA)
    db.executescript(legacy_sqlite.SCHEMA_EXT)
    db.execute("ALTER TABLE jobs ADD COLUMN created_by TEXT")
    db.commit()
    legacy_sqlite.apply_legacy_migrations(db, legacy_sqlite.MIGRATIONS[:6])
    now = '2026-01-01T00:00:00+00:00'
    for name, role in (('lost-admin', 'admin'), ('expired-admin', 'admin'), ('viewer-with-offset', 'member')):
        db.execute("INSERT INTO users(username, password_hash, role, enabled, must_change_password, created_at, updated_at) "
                   "VALUES (?, 'x', ?, 1, 0, ?, ?)", (name, role, now, now))
    ids = {r['username']: r['id'] for r in db.execute("SELECT id, username FROM users")}
    default_project = db.execute("SELECT id FROM projects WHERE slug = 'default'").fetchone()[0]
    db.execute("INSERT INTO role_bindings(subject_type, subject_id, role, scope_type, scope_id, expires_at, created_at) "
               "VALUES ('user', ?, 'platform_admin', 'global', 0, '2000-01-01T00:00:00+00:00', ?)",
               (ids['expired-admin'], now))
    db.execute("INSERT INTO role_bindings(subject_type, subject_id, role, scope_type, scope_id, expires_at, created_at) "
               "VALUES ('user', ?, 'project_viewer', 'project', ?, '2999-01-01T08:00:00+08:00', ?)",
               (ids['viewer-with-offset'], default_project, now))
    db.commit()
    db.close()

    store = new_store()
    import_sqlite(store, path)
    lost, expired, viewer = (store.get_user_by_name(n) for n in ('lost-admin', 'expired-admin', 'viewer-with-offset'))
    assert store.is_platform_admin(lost['id']) and store.is_platform_admin(expired['id'])
    assert not store.is_platform_admin(viewer['id'])
    expiry = [b['expires_at'] for b in store.all_bindings() if b['subject_id'] == viewer['id']]
    assert expiry == ['2999-01-01T00:00:00+00:00']
    # 原文件只读打开，迁移只在副本上执行
    assert [r[0] for r in sqlite3.connect(path).execute('SELECT version FROM schema_migrations ORDER BY version')] \
        == [1, 2, 3, 4, 5, 6]


# ------------------------------------------------------------------ 账号管理
def test_delegated_account_manager_cannot_take_over_stronger_accounts(platform):
    app, c = platform
    store = app.state.store
    assert c.post('/api/role-definitions', json={'code': 'account_manager', 'name': '账号管理员',
                                                 'scope_type': 'global', 'permissions': ['user.manage']}).status_code == 201
    manager, auditor, plain = member(store, 'manager'), member(store, 'auditor1'), member(store, 'plain')
    store.create_binding('user', manager['id'], 'account_manager', 'global', 0, 1)
    store.create_binding('user', auditor['id'], 'auditor', 'global', 0, 1)
    store.create_binding('user', plain['id'], 'developer', 'project', store.default_project_id(), 1)
    login(c, 'manager', PASSWORD)
    for request in (lambda: c.post(f"/api/users/{auditor['id']}/reset-password", json={}),
                    lambda: c.put(f"/api/users/{auditor['id']}", json={'enabled': False}),
                    lambda: c.delete(f"/api/users/{auditor['id']}")):
        assert request().status_code == 403
    assert c.post(f"/api/users/{plain['id']}/reset-password", json={}).status_code == 200
    rows = {row['username']: row for row in c.get('/api/users').json()['items']}
    assert rows['plain']['can_manage'] and not rows['auditor1']['can_manage'] and not rows['admin']['can_manage']
    assert rows['auditor1']['global_roles'] == ['auditor'] and rows['admin']['is_platform_admin']
    # 旧接口的 role=developer 会授予默认项目开发者权限，账号管理员没有该项目的成员管理权限
    assert c.post('/api/users', json={'username': 'sneaky', 'password': PASSWORD, 'role': 'developer'}).status_code == 404
    created = c.post('/api/users', json={'username': 'fresh', 'password': PASSWORD})
    assert created.status_code == 201 and created.json()['grant_count'] == 0 and created.json()['role_label'] == '未授权'
    assert c.post('/api/users', json={'username': 'fresh', 'password': PASSWORD}).status_code == 409


def test_create_account_with_initial_grants_is_atomic(platform):
    app, c = platform
    store = app.state.store
    team = project(c, 'team')
    bad = c.post('/api/users', json={'username': 'half', 'password': PASSWORD, 'grants': [
        {'role': 'developer', 'scope_type': 'project', 'scope_id': team['id']},
        {'role': 'developer', 'scope_type': 'global'}]})
    assert bad.status_code == 400 and store.get_user_by_name('half') is None
    created = c.post('/api/users', json={'username': 'dev1', 'password': PASSWORD, 'display_name': '开发一',
                                         'grants': [{'role': 'developer', 'scope_type': 'project', 'scope_id': team['id'],
                                                     'note': 'initial'}]})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body['grant_count'] == 1 and body['role_label'] == '开发者' and body['must_change_password']
    assert [(b['role'], b['scope_id']) for b in store.list_bindings(user_id=body['id'])] == [('developer', team['id'])]
    admin = c.post('/api/users', json={'username': 'ops-admin', 'password': PASSWORD, 'platform_admin': True}).json()
    assert admin['is_platform_admin'] and store.get_user(admin['id'])['role'] == 'admin'
    legacy = c.post('/api/users', json={'username': 'legacy-viewer', 'password': PASSWORD, 'role': 'viewer'}).json()
    assert [b['role'] for b in store.list_bindings(user_id=legacy['id'])] == ['project_viewer']


def test_updating_an_account_keeps_its_grants(platform):
    app, c = platform
    store = app.state.store
    team = project(c, 'keep')
    user = member(store, 'keeper')
    store.update_user(user['id'], display_name='保留的名字')
    store.create_binding('user', user['id'], 'developer', 'project', team['id'], 1)
    promoted = c.put(f"/api/users/{user['id']}", json={'platform_admin': True})
    assert promoted.status_code == 200 and promoted.json()['is_platform_admin']
    assert promoted.json()['display_name'] == '保留的名字'
    demoted = c.put(f"/api/users/{user['id']}", json={'role': 'viewer'})  # 旧参数只影响平台管理员身份
    assert demoted.status_code == 200 and not demoted.json()['is_platform_admin']
    assert [b['role'] for b in store.list_bindings(user_id=user['id'])] == ['developer']
    assert store.get_user(user['id'])['role'] == 'member'
    detail = c.get(f"/api/users/{user['id']}").json()
    assert detail['access']['projects'][0]['id'] == team['id'] and 'build.run' in detail['access']['projects'][0]['perms']
    assert detail['bindings'][0]['scope_label'] == 'KEEP' and detail['bindings'][0]['subject']['username'] == 'keeper'
    actions = [(e['action'], e['detail']) for e in store.list_audit(limit=20)[0]]
    assert ('user.update', 'platform_admin') in actions


def test_platform_admin_continuity(platform):
    app, c = platform
    store = app.state.store
    admin = store.get_user_by_name('admin')
    own = next(b for b in store.list_bindings(user_id=admin['id']) if b['role'] == 'platform_admin')
    assert c.put(f"/api/users/{admin['id']}", json={'enabled': False}).status_code == 400
    assert c.put(f"/api/users/{admin['id']}", json={'platform_admin': False}).status_code == 400
    assert c.delete(f"/api/bindings/{own['id']}").status_code == 400
    second = c.post('/api/users', json={'username': 'second', 'password': PASSWORD, 'platform_admin': True}).json()
    store.update_user(second['id'], must_change_password=0)
    login(c, 'second', PASSWORD)
    assert c.delete(f"/api/bindings/{own['id']}").status_code == 204  # 另一名管理员仍在
    assert not store.is_platform_admin(admin['id']) and store.get_user(admin['id'])['role'] == 'member'
    mine = next(b for b in store.list_bindings(user_id=second['id']) if b['role'] == 'platform_admin')
    assert c.delete(f"/api/bindings/{mine['id']}").status_code == 400
    assert c.put(f"/api/users/{second['id']}", json={'platform_admin': False}).status_code == 400


# ------------------------------------------------------------------ 授权规则
def test_project_admin_can_only_grant_weaker_roles_in_own_project(platform):
    app, c = platform
    store = app.state.store
    mine, theirs = project(c, 'mine'), project(c, 'theirs')
    lead, dev = member(store, 'lead'), member(store, 'dev')
    lead_binding = store.create_binding('user', lead['id'], 'project_admin', 'project', mine['id'], 1)
    assert c.post('/api/role-definitions', json={'code': 'release_lead', 'name': '发布负责人', 'scope_type': 'project',
                                                 'permissions': ['project.member.manage', 'app.view']}).status_code == 201
    login(c, 'lead', PASSWORD)

    def grant(role, scope_type='project', scope_id=mine['id']):
        return c.post('/api/bindings', json={'subject_type': 'user', 'subject_id': dev['id'], 'role': role,
                                             'scope_type': scope_type, 'scope_id': scope_id})

    granted = grant('developer')
    assert granted.status_code == 201, granted.text
    assert grant('project_admin').status_code == 403
    assert grant('release_lead').status_code == 403
    assert grant('developer', scope_id=theirs['id']).status_code == 404
    assert grant('auditor', 'global', 0).status_code == 403
    options = c.get('/api/bindings/options').json()
    assert options['global'] is None and options['namespace'] == []
    assert [(p['id'], sorted(p['roles'])) for p in options['project']] == [(mine['id'], ['developer', 'project_viewer'])]
    rows = c.get('/api/bindings').json()['items']
    assert {r['scope_id'] for r in rows} == {mine['id']}
    assert {r['id']: r['can_revoke'] for r in rows} == {lead_binding: False, granted.json()['id']: True}
    assert c.delete(f'/api/bindings/{lead_binding}').status_code == 404
    assert c.delete(f"/api/bindings/{granted.json()['id']}").status_code == 204
    assert c.get('/api/users').status_code == 403


def test_binding_list_names_scopes_and_expiry(platform):
    app, c = platform
    store = app.state.store
    team = project(c, 'names')
    user = member(store, 'named')
    response = c.post('/api/bindings', json={'subject_type': 'user', 'subject_id': user['id'], 'role': 'project_viewer',
                                             'scope_type': 'project', 'scope_id': team['id'],
                                             'expires_at': '2999-06-01T08:00:00+08:00'})
    assert response.status_code == 201 and response.json()['expires_at'] == '2999-06-01T00:00:00+00:00'
    assert c.post('/api/bindings', json={'subject_type': 'user', 'subject_id': user['id'], 'role': 'project_viewer',
                                         'scope_type': 'project', 'scope_id': team['id']}).status_code == 409
    store.create_binding('user', user['id'], 'developer', 'project', team['id'], 1, '2000-01-01T00:00:00+00:00')
    rows = c.get('/api/bindings', params={'user_id': user['id']}).json()['items']
    assert [(r['role_name'], r['status'], r['scope_label']) for r in rows] == [
        ('项目只读', 'active', 'NAMES'), ('开发者', 'expired', 'NAMES')]
    assert rows[0]['subject']['username'] == 'named' and rows[0]['created_by_username'] == 'admin'
    assert not load_principal(store, user).can_project(team['id'], 'app.manage')
    audit = next(e for e in store.list_audit(limit=10)[0] if e['action'] == 'binding.create')
    assert 'role=project_viewer(项目只读)' in audit['detail'] and f"scope=project:{team['id']}" in audit['detail']


def test_session_profile_separates_global_and_scoped_permissions(platform):
    app, c = platform
    store = app.state.store
    team = project(c, 'profile')
    user = member(store, 'profiled')
    store.create_binding('user', user['id'], 'developer', 'project', team['id'], 1)
    login(c, 'profiled', PASSWORD)
    me = c.get('/api/auth/me').json()
    assert me['global_permissions'] == [] and 'app.manage' in me['permissions'] and not me['is_platform_admin']
    assert [p['id'] for p in me['projects']] == [team['id']] and 'build.run' in me['projects'][0]['perms']
    assert me['namespaces'] == [] and me['role_label'] == '开发者'
    assert c.get('/api/bindings').status_code == 403  # 没有任何成员管理范围


# ------------------------------------------------------------------ 其他健壮性修复
def test_submitter_is_never_offered_self_approval(tmp_path):
    store = new_store()
    cluster = store.create_cluster({'name': 'c', 'auth_type': 'sa_token', 'server': 'https://k.example',
                                    'credential_encrypted': 'x'})
    namespace = store.create_namespace(cluster, 'uat', 'staging', {}, store.default_project_id())
    submitter = member(store, 'submitter')
    store.create_binding('user', submitter['id'], 'namespace_approver', 'namespace', namespace, None)
    deployment_id = store.create_deployment(target_name='svc', image='r/x@sha256:' + 'a' * 64, status='pending_approval',
                                            namespace_id=namespace, created_by=submitter['id'])
    record = store.get_deployment(deployment_id)
    assert record['created_by'] == submitter['id']  # BIGINT 列
    me = load_principal(store, submitter)
    assert me.can_namespace(namespace, 'deploy.approve')
    assert not releases.can_decide(store, me, record) and not releases.can_decide(store, me, record, 'rejected')


def test_login_failure_tracking_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_module, 'MAX_TRACKED_LOGIN_KEYS', 20)
    settings = Settings()
    settings.build.data_dir = str(tmp_path / 'data')
    service = auth_module.AuthService(settings, new_store())
    for index in range(200):
        with pytest.raises(auth_module.AuthError):
            service.authenticate(f'nobody-{index}', 'wrong-password', '10.0.0.1')
    assert len(service._failures) <= 20


def test_invalid_cluster_ca_is_a_validation_error(platform):
    _, c = platform
    response = c.post('/api/clusters', json={'name': 'bad-ca', 'server': 'https://kube.example', 'auth_type': 'sa_token',
                                             'credential': {'token': 'abc', 'ca_pem': 'not a certificate'}})
    assert response.status_code == 400 and 'CA' in response.json()['detail']


def test_unique_binding_violation_is_a_conflict(tmp_path):
    store = new_store()
    user = member(store, 'dup')
    store.create_binding('user', user['id'], 'auditor', 'global', 0, None)
    with pytest.raises(Exception) as caught:
        store.create_binding('user', user['id'], 'auditor', 'global', 0, None)
    assert getattr(caught.value, 'status', None) == 409 and not isinstance(caught.value, IntegrityViolation)
