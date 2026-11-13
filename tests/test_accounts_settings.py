import json
import re
from pathlib import Path
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from swr_builder.api import ROUTERS
from swr_builder.main import AUTHENTICATED_ROUTE, PUBLIC_ROUTE, ROUTE_PERMISSIONS, create_app, route_permission
from swr_builder.config import Settings
from swr_builder.security import hash_password, verify_password, SecretBox
from tests.dbutil import new_store

HEAD={'X-Requested-With':'XMLHttpRequest'}

@pytest.fixture
def setup(tmp_path):
    s=Settings();s.build.data_dir=str(tmp_path/'data');s.server.admin_password='AdminPass123'
    app=create_app(s)
    with TestClient(app) as c:
        c.headers.update(HEAD)
        yield s,app,c

def login(c,user='admin',password='AdminPass123'):
    return c.post('/api/auth/login',json={'username':user,'password':password,'remember':True})

def add_user(c,name,role='viewer'):
    r=c.post('/api/users',json={'username':name,'password':'UserPass123','role':role})
    assert r.status_code==201,r.text
    return r.json()


def test_api_routes_are_explicit_and_unknown_routes_fail_closed(setup):
    _,app,c=setup;login(c)
    for router in ROUTERS:
        for route in router.routes:
            if isinstance(route,APIRoute) and route.path.startswith('/api/'):
                for method in route.methods:
                    assert (method,route.path) in ROUTE_PERMISSIONS
                    sample_path=re.sub(r'\{[^}]+\}','1',route.path)
                    assert route_permission(app,sample_path,method)==ROUTE_PERMISSIONS[(method,route.path)]
    assert ROUTE_PERMISSIONS[('GET','/api/auth/me')]==AUTHENTICATED_ROUTE
    assert ROUTE_PERMISSIONS[('POST','/api/auth/login')]==PUBLIC_ROUTE
    assert route_permission(app,'/api/new-feature','GET') is None
    response=c.get('/api/new-feature')
    assert response.status_code==403 and '未配置权限' in response.json()['detail']


def test_auth_config_is_public_without_a_session(setup):
    _,_,c=setup
    response=c.get('/api/auth/config')
    assert response.status_code==200
    assert response.json()['site_name']=='SWR 镜像构建平台'


def test_project_membership_scopes_apps_jobs_and_registry_policy(setup):
    settings,app,c=setup;settings.registry.address='registry.example';login(c)
    project=c.post('/api/projects',json={'slug':'team-red','name':'Team Red','registry_orgs':[
        {'namespace':'red','base_prefixes':['api']}]}).json()
    payload={'name':'red-api','organization':'red','repository':'api/service','base_image':'registry.example/red/base:1'}
    created=c.post('/api/apps',json={**payload,'project_id':project['id']})
    assert created.status_code==403
    project=c.put('/api/projects/'+str(project['id']),json={'slug':'team-red','name':'Team Red','registry_orgs':[
        {'namespace':'red','base_prefixes':['api','base']}]}).json()
    assert c.delete('/api/projects/'+str(app.state.store.default_project_id())).status_code==409
    created=c.post('/api/apps',json={**payload,'project_id':project['id']})
    assert created.status_code==201,created.text
    assert [row['id'] for row in c.get('/api/apps').json()['items']]==[created.json()['id']]
    second=c.post('/api/projects',json={'slug':'team-blue','name':'Team Blue','registry_orgs':[
        {'namespace':'blue','base_prefixes':['worker']}]}).json()
    denied=c.post('/api/apps',json={'name':'blue-worker','organization':'blue','repository':'worker/api',
        'base_image':'registry.example/blue/worker-base:1','project_id':second['id']})
    assert denied.status_code==403
    user=add_user(c,'reddev','developer')
    default_id=app.state.store.default_project_id()
    for row in c.get('/api/bindings').json()['items']:
        if row['subject_id']==user['id'] and row['scope_type']=='project' and row['scope_id']==default_id:
            assert c.delete('/api/bindings/'+str(row['id'])).status_code==204
    binding=c.post('/api/bindings',json={'subject_type':'user','subject_id':user['id'],'role':'developer',
        'scope_type':'project','scope_id':project['id'],'note':'red access'})
    assert binding.status_code==201,binding.text
    login(c,'reddev','UserPass123')
    assert c.put('/api/account/password',json={'old_password':'UserPass123','new_password':'RedDev123'}).status_code==200
    apps=c.get('/api/apps').json()['items']
    assert [row['id'] for row in apps]==[created.json()['id']]
    jobs=app.state.store
    red_job=jobs.create_job(app_id=created.json()['id'],app_name='red-api',project_id=project['id'],
        status='success',created_at='2026-11-06T00:00:00+00:00')
    blue_app=jobs.create_app({'name':'blue-worker','repository':'worker/api'},second['id'])
    assert c.get('/api/apps/'+str(blue_app['id'])+'/versions').status_code==404
    blue_job=jobs.create_job(app_id=blue_app['id'],app_name='blue-worker',project_id=second['id'],
        status='success',created_at='2026-11-06T00:00:01+00:00')
    assert red_job and blue_job
    assert [row['project_id'] for row in c.get('/api/jobs').json()['items']]==[project['id']]
    assert c.get('/api/jobs/'+str(blue_job)).status_code==404


def test_global_admin_can_open_empty_namespace_workspace(setup):
    _,app,c=setup;login(c)
    response=c.get('/api/namespaces')
    assert response.status_code==200 and response.json()=={'items':[],'total':0}
    current=c.get('/api/auth/me').json()
    assert current['projects'] and 'namespace.view' in current['permissions']


def test_platform_admin_binding_authorizes_global_management_routes(setup):
    _,app,c=setup;login(c)
    user=app.state.store.create_user(username='platformop',role='viewer',password_hash=hash_password('Platform123'),
        enabled=1,must_change_password=0)
    app.state.store.delete_user_bindings(user['id'])
    app.state.store.bind_user_role(user['id'],'platform_admin','global',0,app.state.store.get_user_by_name('admin')['id'])
    login(c,'platformop','Platform123')
    assert c.get('/api/clusters').status_code==200
    assert c.get('/api/users').status_code==200
    candidates=c.get('/api/bindable-users')
    assert candidates.status_code==200 and all('email' not in user for user in candidates.json()['items'])
    assert c.get('/api/settings').status_code==200
    assert c.get('/api/projects').json()['items']
    me=c.get('/api/auth/me').json()
    assert me['role']=='platform_admin' and 'cluster.manage' in me['permissions']


def test_login_cookie_csrf_and_revocation(setup):
    _,app,c=setup
    assert c.get('/api/apps').status_code==401
    assert c.get('/',follow_redirects=False).status_code==303
    assert c.get('/login').status_code==200
    assert login(c,password='wrong').status_code==401
    r=login(c);assert r.status_code==200
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=lax' in r.headers['set-cookie']
    assert 'Max-Age' in r.headers['set-cookie']
    assert 'password_hash' not in c.get('/api/auth/me').text
    del c.headers['X-Requested-With']
    assert c.put('/api/account/profile',json={'display_name':'x'}).status_code==403
    c.headers.update(HEAD)
    assert c.put('/api/account/profile',json={'display_name':'维护员'}).status_code==200
    assert c.post('/api/auth/logout').status_code==200
    assert c.get('/api/auth/me').status_code==401

def test_roles_first_login_and_admin_guards(setup):
    _,app,c=setup;login(c)
    admin=c.get('/api/auth/me').json()
    assert c.delete('/api/users/'+str(admin['id'])).status_code==400
    assert c.put('/api/users/'+str(admin['id']),json={'role':'viewer'}).status_code==400
    user=add_user(c,'readuser')
    login(c,'readuser','UserPass123')
    assert c.get('/api/apps').status_code==403
    assert c.get('/api/auth/me').json()['must_change_password']
    assert c.put('/api/account/password',json={'old_password':'UserPass123','new_password':'NewUser123'}).status_code==200
    assert c.get('/api/apps').status_code==200
    assert c.get('/api/settings').status_code==403
    assert c.post('/api/apps',json={}).status_code==403
    assert c.post('/api/builds',json={}).status_code==403
    assert c.post('/api/deployments',json={}).status_code==403
    assert c.get('/api/cloud/hcs/capabilities').status_code==403
    assert c.put('/api/account/password',json={'old_password':'NewUser123','new_password':'weak'}).status_code==400

def test_api_tokens_reset_and_disable(setup):
    _,app,c=setup;login(c)
    u=add_user(c,'developer1','developer')
    login(c,'developer1','UserPass123')
    c.put('/api/account/password',json={'old_password':'UserPass123','new_password':'Developer123'})
    token=c.post('/api/account/tokens',json={'name':'ci','expires_days':30}).json()['token']
    assert token not in c.get('/api/account/tokens').text
    c.cookies.clear()
    c.headers['Authorization']='Bearer '+token
    assert c.get('/api/apps').status_code==200
    assert c.get('/api/users').status_code==403
    assert c.post('/api/account/tokens',json={'name':'no'}).status_code==403
    del c.headers['Authorization'];login(c)
    assert c.post('/api/users/'+str(u['id'])+'/reset-password',json={'password':'ResetPass123'}).status_code==200
    c.headers['Authorization']='Bearer '+token
    assert c.get('/api/apps').status_code==401
    del c.headers['Authorization']
    assert c.put('/api/users/'+str(u['id']),json={'enabled':False,'role':'developer'}).status_code==200
    assert login(c,'developer1','ResetPass123').status_code==403

def test_lockout_and_generated_initial_password(tmp_path):
    s=Settings();s.build.data_dir=str(tmp_path/'data');s.security.max_login_failures=2
    app=create_app(s)
    with TestClient(app,headers=HEAD) as c:
        path=s.data_path/'admin-initial-password.txt'
        assert path.exists() and path.stat().st_mode&0o777==0o600
        initial=path.read_text().split('初始密码：')[1].splitlines()[0]
        assert login(c,password='bad').status_code==401
        assert login(c,password='bad').status_code==429
        assert login(c,password=initial).status_code==429
        app.state.ctx.auth.unlock('admin')
        assert login(c,password=initial).status_code==200
        assert c.put('/api/account/password',json={'old_password':initial,'new_password':'SecurePass123'}).status_code==200
        assert not path.exists()

def test_settings_encryption_persistence_and_env_lock(setup):
    s,app,c=setup;login(c)
    assert c.put('/api/settings',json={'values':{'registry.password':'registry-secret','general.site_name':'企业镜像中心'}}).status_code==200
    data=c.get('/api/settings').text
    assert 'registry-secret' not in data
    assert s.registry.password=='registry-secret'
    saved=app.state.store.get_settings()['registry.password'];assert saved.startswith('enc:v2:')
    s.env_locked['registry.password']='SWR_PASSWORD'
    assert c.put('/api/settings',json={'values':{'registry.password':'new'}}).status_code==400
    s.env_locked.clear()
    assert c.put('/api/settings',json={'values':{'registry.password':''}}).status_code==200
    assert s.registry.password=='registry-secret'
    assert c.put('/api/settings',json={'values':{'build.max_concurrent':0}}).status_code==400
    assert c.put('/api/settings',json={'values':{'registry.address':'evil/x'}}).status_code==400
    assert c.put('/api/settings',json={'values':{'registry.ca_file':'/nonexistent/ca.pem'}}).status_code == 400
    assert s.registry.ca_file==''
    s2=Settings();s2.build.data_dir=s.build.data_dir
    app2=create_app(s2)
    with TestClient(app2):
        assert s2.registry.password=='registry-secret' and s2.general.site_name=='企业镜像中心'
    assert c.put('/api/settings',json={'clear':['registry.password']}).status_code==200
    assert not s.registry.password
    assert any(x['action']=='settings.update' for x in app.state.store.list_audit()[0])

def test_password_and_secret_tampering(tmp_path):
    h=hash_password('abc12345');assert verify_password('abc12345',h) and not verify_password('wrong',h)
    box=SecretBox.load(tmp_path/'key');v=box.encrypt('secret');assert box.decrypt(v)=='secret'
    with pytest.raises(Exception):box.decrypt(v[:-6]+'AAAAAA')

def test_v1_sqlite_import_preserves_apps_jobs_and_roles(tmp_path):
    """v1.0 的 SQLite 数据库经 db import-sqlite 导入：原 ID 保留、旧角色转为授权、原文件不被修改。"""
    import sqlite3
    from swr_builder.store import utcnow
    from swr_builder.store.importer import import_sqlite
    from swr_builder.store.legacy_sqlite import SCHEMA
    from swr_builder.store.migrations import MIGRATIONS, schema_versions
    path=tmp_path/'old.db'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, display_name TEXT, email TEXT, password_hash TEXT NOT NULL, role TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, must_change_password INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_login_at TEXT, password_changed_at TEXT)")
        db.execute("INSERT INTO apps(name,data,created_at,updated_at) VALUES(?,?,?,?)",('old-app','{"name":"old-app","repository":"old-app"}',utcnow(),utcnow()))
        db.execute("INSERT INTO jobs(app_name,status,created_at) VALUES(?,?,?)",('old-app','success',utcnow()))
        db.execute("INSERT INTO users(username,password_hash,role,created_at,updated_at) VALUES(?,?,?,?,?)",('old-admin','hash','admin',utcnow(),utcnow()))
        db.execute("INSERT INTO users(username,password_hash,role,created_at,updated_at) VALUES(?,?,?,?,?)",('old-dev','hash','developer',utcnow(),utcnow()))
    original=path.read_bytes()
    store=new_store()
    result=import_sqlite(store,path)
    assert result['tables']['apps']==1 and result['tables']['users']==2 and result['tables']['jobs']==1
    assert path.read_bytes()==original
    assert store.list_apps()[0]['name']=='old-app'
    assert store.list_jobs()[0][0]['status']=='success'
    assert 'created_by' in store.get_job(1)
    assert store.count_users()==2 and store.get_user_by_name('OLD-ADMIN')['id']==1
    default_project=store.default_project_id()
    assert store.get_app(1)['project_id']==default_project
    bindings=store.all_bindings()
    assert any(b['subject_id']==1 and b['role']=='platform_admin' and b['scope_type']=='global' for b in bindings)
    assert any(b['subject_id']==2 and b['role']=='developer' and b['scope_id']==default_project for b in bindings)
    assert schema_versions(store)==[v for v,_,_ in MIGRATIONS]
    assert store.create_app({'name':'new-app','repository':'new-app'})['id']==2  # 自增序列已重置
    with pytest.raises(RuntimeError,match='不是空库'):
        import_sqlite(store,path)
    assert len(store.list_projects())==1


def test_cluster_namespace_foreign_keys_and_repeat_migrations():
    from swr_builder.store import IntegrityViolation
    from swr_builder.store.migrations import MIGRATIONS, schema_versions
    store=new_store()
    cluster_id=store.create_cluster({'name':'cluster-a','auth_type':'sa_token','server':'https://kube.example',
        'credential_encrypted':'enc:v2:test','status':'ready'})
    namespace_id=store.create_namespace(cluster_id,'dev','dev',{'require_approval':True})
    project_id=store.default_project_id()
    store.assign_namespace(project_id,namespace_id)
    store.assign_namespace(project_id,namespace_id)  # 重复分配被忽略
    assert store.get_project(project_id)['namespace_ids']==[namespace_id]
    with pytest.raises(IntegrityViolation):
        with store._db() as db:
            db.execute('DELETE FROM clusters WHERE id=%s',(cluster_id,))
    again=new_store()  # 再次启动不会重复执行迁移
    assert schema_versions(again)==[v for v,_,_ in MIGRATIONS]
    assert again.get_project(project_id)['namespace_ids']==[namespace_id]

def test_session_ownership_and_own_cancel(setup):
    _,app,c=setup;login(c)
    admin=app.state.store.get_user_by_name('admin')
    admin_session=c.get('/api/account/sessions').json()['items'][0]['id']
    u=app.state.store.create_user(username='devtest',role='developer',enabled=1,must_change_password=0,password_hash=hash_password('Developer123'))
    login(c,'devtest','Developer123')
    assert c.delete('/api/account/sessions/'+admin_session).status_code==404
    from swr_builder.store import utcnow
    own=app.state.store.create_job(status='queued',created_by=u['id'],created_at=utcnow())
    other=app.state.store.create_job(status='queued',created_by=admin['id'],created_at=utcnow())
    assert c.post('/api/jobs/'+str(other)+'/cancel').status_code==403
    assert c.post('/api/jobs/'+str(own)+'/cancel').status_code==200
    assert app.state.store.get_job(own)['status']=='cancelled'

def test_secure_cookie_and_legacy_migration(tmp_path):
    s=Settings();s.build.data_dir=str(tmp_path/'data');s.server.secure_cookie=True;s.server.users={'legacyadmin':'LegacyPass123'}
    app=create_app(s)
    with TestClient(app,base_url='https://testserver',headers=HEAD) as c:
        r=login(c,'legacyadmin','LegacyPass123')
        assert r.status_code==200 and 'Secure' in r.headers['set-cookie']
        assert c.get('/api/auth/me').json()['must_change_password']
        assert c.put('/api/account/password',json={'old_password':'LegacyPass123','new_password':'UpdatedPass123'}).status_code==200
        assert c.get('/api/apps').status_code==200

def test_expired_session_and_token(setup):
    _,app,c=setup;login(c)
    auth=app.state.ctx.auth;store=app.state.store;u=store.get_user_by_name('admin')
    from swr_builder.security import token_hash
    store.create_session(token_hash('expired'),u['id'],'2000-01-01T00:00:00+00:00','','')
    assert auth.resolve_session('expired') is None
    store.create_api_token(u['id'],'expired',token_hash('swrb_expired'),'swrb_exp','2000-01-01T00:00:00+00:00')
    assert auth.resolve_api_token('swrb_expired') is None
    assert c.post('/api/users/'+str(u['id'])+'/reset-password',json={}).status_code==400
