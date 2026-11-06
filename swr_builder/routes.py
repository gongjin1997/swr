"""认证、个人账号、管理后台与部署 API。访问控制在主应用中统一执行。"""
import platform
import re
import shutil
import ssl
import json
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import httpx
from fastapi import Body, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from . import __version__
from .auth import AuthError, SESSION_COOKIE, USERNAME_RE, ROLES, ROLE_PERMISSIONS, PERMISSION_LABELS, public_user, permissions_of, has_perm
from .authz import (has_project_permission, visible_project_ids, has_namespace_permission,
                    visible_namespace_ids, PROJECT_ROLE_PERMISSIONS, NAMESPACE_ROLE_PERMISSIONS,
                    GLOBAL_ROLE_PERMISSIONS, has_global_permission)
from .apps import ORG_RE, REPO_RE
from .security import hash_password
from .registry import RegistryClient, RegistryError, parse_image_ref
from .deploy import list_providers
from .policy import deployment_policy_block
from .cloud import HCSClient


def mount_routes(app,ctx):
    s,store,auth,sysconfig,deploy = ctx.settings,ctx.store,ctx.auth,ctx.sysconfig,ctx.deploy

    def me(request): return request.state.user
    def ip(request): return request.client.host if request.client else ''
    def audit(request,action,target='',detail=''):
        store.add_audit(me(request)['username'],action,str(target),detail,ip(request))
    def user_or_404(id):
        u = store.get_user(id)
        if not u: raise HTTPException(404,'账号不存在')
        return u
    def text(data,key,limit=80):
        v = data.get(key,'')
        if not isinstance(v,str) or len(v)>limit: raise ValueError(f'{key} 内容无效（最多 {limit} 字符）')
        return v.strip()

    def project_or_404(project_id,request,permission='project.view'):
        project=store.get_project(project_id)
        if not project or not has_project_permission(store,me(request),project_id,permission):
            raise HTTPException(404,'项目不存在')
        return project

    def normalize_registry_orgs(value):
        if not isinstance(value,list): raise ValueError('registry_orgs 必须是数组')
        normalized=[];seen=set()
        for item in value:
            if not isinstance(item,dict) or set(item)-{'namespace','base_prefixes'}: raise ValueError('镜像组织配置格式错误')
            namespace=item.get('namespace');prefixes=item.get('base_prefixes',[])
            if not isinstance(namespace,str) or not ORG_RE.fullmatch(namespace): raise ValueError('SWR 组织名称无效')
            if namespace in seen: raise ValueError('SWR 组织不能重复')
            seen.add(namespace)
            if not isinstance(prefixes,list) or any(not isinstance(p,str) or not REPO_RE.fullmatch(p) for p in prefixes):
                raise ValueError('镜像仓库前缀无效')
            normalized.append({'namespace':namespace,'base_prefixes':sorted(set(prefixes))})
        return normalized

    def binding_view(binding):
        return {key:binding[key] for key in ('id','subject_type','subject_id','role','scope_type','scope_id','expires_at','note')}

    def can_manage_binding(request,binding):
        user=me(request)
        if has_global_permission(store,user,'user.manage'): return True
        if binding['scope_type']=='project':
            return has_project_permission(store,user,binding['scope_id'],'project.member.manage')
        if binding['scope_type']=='namespace':
            return has_namespace_permission(store,user,binding['scope_id'],'namespace.member.manage')
        return False

    def cluster_credential(data):
        if not isinstance(data,dict) or set(data)-{'token','ca_pem'}: raise ValueError('当前仅支持 SA Token 凭据（token、ca_pem）')
        token=data.get('token');ca_pem=data.get('ca_pem','')
        if not isinstance(token,str) or not token or len(token)>65536: raise ValueError('SA Token 无效')
        if not isinstance(ca_pem,str) or len(ca_pem)>1024*1024: raise ValueError('CA 证书无效')
        if ca_pem:
            try: ssl.create_default_context(cadata=ca_pem)
            except ssl.SSLError: raise ValueError('CA 证书 PEM 格式错误') from None
        return {'token':token,'ca_pem':ca_pem}

    def public_cluster(cluster):
        if not cluster: raise HTTPException(404,'集群不存在')
        return {key:cluster.get(key) for key in ('id','name','auth_type','server','cce_cluster_id',
            'credential_expires_at','cert_duration_days','status')}

    def cluster_config(cluster_id,credential_override=None):
        cluster=store.get_cluster_record(cluster_id)
        if not cluster: raise HTTPException(404,'集群不存在')
        if cluster['auth_type']!='sa_token': raise HTTPException(501,'当前版本仅实现 SA Token 接入，证书认证待现场适配')
        encrypted=credential_override if credential_override is not None else cluster['credential_encrypted']
        try: credential=json.loads(ctx.box.decrypt(encrypted))
        except (ValueError,TypeError,json.JSONDecodeError): raise HTTPException(500,'集群凭据无法解密') from None
        return cluster,credential

    async def test_cluster_connection(cluster,credential):
        parsed=urlsplit(cluster['server'])
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise HTTPException(400,'API Server 必须是无凭据的 HTTPS 地址')
        try:
            verify=ssl.create_default_context(cadata=credential['ca_pem']) if credential.get('ca_pem') else True
            async with httpx.AsyncClient(verify=verify,timeout=10,transport=getattr(ctx,'deploy_transport',None),follow_redirects=False) as client:
                response=await client.get(cluster['server'].rstrip('/')+'/version',headers={'Authorization':'Bearer '+credential['token']})
            if response.status_code!=200: raise HTTPException(503,f'Kubernetes API 返回 HTTP {response.status_code}')
            payload=response.json()
            version=payload.get('gitVersion') if isinstance(payload,dict) else None
            if not isinstance(version,str): raise HTTPException(503,'Kubernetes API 版本响应格式错误')
            return version
        except httpx.HTTPError: raise HTTPException(503,'连接集群失败，请检查网络、TLS 和凭据') from None

    POLICY_KEYS={'require_approval','min_approvals','allow_auto_deploy','allowed_registry_orgs','service_types',
        'ingress','hpa_enabled','resources','max_replicas','require_readiness_probe','image_pull_secrets',
        'managed_pull_secret','deploy_windows','freeze','auto_rollback','rollout_timeout'}

    def normalize_namespace_policy(data):
        if not isinstance(data,dict) or set(data)-POLICY_KEYS: raise ValueError('命名空间策略包含未知字段')
        policy={'require_approval':True,'min_approvals':1,'allow_auto_deploy':False,'allowed_registry_orgs':[],
            'service_types':['ClusterIP'],'ingress':{'enabled':False,'host_suffixes':[],'ingress_class':''},
            'hpa_enabled':False,'resources':{'require_limits':True},'max_replicas':10,
            'require_readiness_probe':True,'image_pull_secrets':[],'managed_pull_secret':False,
            'deploy_windows':[],'freeze':{'enabled':False,'until':None,'reason':''},'auto_rollback':False,'rollout_timeout':600}
        policy.update(data)
        for key in ('require_approval','allow_auto_deploy','hpa_enabled','require_readiness_probe','managed_pull_secret','auto_rollback'):
            if not isinstance(policy[key],bool): raise ValueError(f'{key} 必须是布尔值')
        if type(policy['min_approvals']) is not int or not 1<=policy['min_approvals']<=5: raise ValueError('min_approvals 范围为 1–5')
        if type(policy['max_replicas']) is not int or not 1<=policy['max_replicas']<=1000: raise ValueError('max_replicas 范围为 1–1000')
        if type(policy['rollout_timeout']) is not int or not 30<=policy['rollout_timeout']<=3600: raise ValueError('rollout_timeout 范围为 30–3600')
        if not isinstance(policy['allowed_registry_orgs'],list) or any(not isinstance(x,str) or not ORG_RE.fullmatch(x) for x in policy['allowed_registry_orgs']): raise ValueError('allowed_registry_orgs 无效')
        if not isinstance(policy['service_types'],list) or not policy['service_types'] or any(x not in {'ClusterIP','NodePort','LoadBalancer'} for x in policy['service_types']): raise ValueError('service_types 无效')
        if not isinstance(policy['image_pull_secrets'],list) or any(not isinstance(x,str) or not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?',x) for x in policy['image_pull_secrets']): raise ValueError('image_pull_secrets 无效')
        for key,default in (('ingress',{'enabled':False,'host_suffixes':[],'ingress_class':''}),('resources',{'require_limits':True}),('freeze',{'enabled':False,'until':None,'reason':''})):
            if not isinstance(policy[key],dict): raise ValueError(f'{key} 必须是对象')
            policy[key]={**default,**policy[key]}
        if set(policy['ingress'])-{'enabled','host_suffixes','ingress_class'}: raise ValueError('ingress 策略字段无效')
        ingress=policy['ingress']
        if not isinstance(ingress['enabled'],bool):raise ValueError('ingress.enabled 必须为布尔值')
        if not isinstance(ingress['host_suffixes'],list) or any(not isinstance(x,str) or len(x)>254 or
            not re.fullmatch(r'\.[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?',x) for x in ingress['host_suffixes']):
            raise ValueError('ingress.host_suffixes 必须是带前导点的 DNS 后缀')
        if not isinstance(ingress['ingress_class'],str) or len(ingress['ingress_class'])>253:
            raise ValueError('ingress_class 无效')
        if set(policy['resources'])-{'cpu_request_max','cpu_limit_max','memory_request_max','memory_limit_max','require_limits'}: raise ValueError('resources 策略字段无效')
        if not isinstance(policy['resources']['require_limits'],bool): raise ValueError('resources.require_limits 必须为布尔值')
        if set(policy['freeze'])-{'enabled','until','reason'} or not isinstance(policy['freeze']['enabled'],bool): raise ValueError('freeze 策略字段无效')
        if policy['freeze']['until'] is not None:
            if not isinstance(policy['freeze']['until'],str):raise ValueError('freeze.until 必须是 ISO 8601 时间')
            try:until=datetime.fromisoformat(policy['freeze']['until'].replace('Z','+00:00'))
            except ValueError:raise ValueError('freeze.until 必须是 ISO 8601 时间') from None
            if until.tzinfo is None:raise ValueError('freeze.until 必须包含时区')
        if not isinstance(policy['freeze']['reason'],str) or len(policy['freeze']['reason'])>300:raise ValueError('freeze.reason 最多 300 字符')
        quantity=re.compile(r'^(?:[0-9]+(?:\.[0-9]+)?|[0-9]+m)(?:Ki|Mi|Gi|Ti|Pi|Ei)?$')
        for key,value in policy['resources'].items():
            if key=='require_limits':continue
            if not isinstance(value,str) or not quantity.fullmatch(value):raise ValueError(f'resources.{key} Kubernetes quantity 无效')
        if not isinstance(policy['deploy_windows'],list): raise ValueError('deploy_windows 必须是数组')
        for window in policy['deploy_windows']:
            if not isinstance(window,dict) or set(window)!={'days','start','end','tz'}:raise ValueError('deploy_windows 项必须包含 days/start/end/tz')
            if not isinstance(window['days'],list) or not window['days'] or any(type(day) is not int or not 1<=day<=7 for day in window['days']):raise ValueError('deploy_windows.days 必须为 ISO 星期编号 1–7 的非空数组')
            if any(not isinstance(window[key],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',window[key]) for key in ('start','end')):raise ValueError('deploy_windows start/end 必须为 HH:MM')
            if window['start']==window['end']:raise ValueError('deploy_windows.start 与 end 不能相同')
            try:ZoneInfo(window['tz'])
            except (ZoneInfoNotFoundError,TypeError):raise ValueError('deploy_windows.tz 必须是有效 IANA 时区') from None
        return policy

    @app.get('/api/auth/config')
    async def auth_config():
        return {'site_name':s.general.site_name,'version':__version__}

    @app.post('/api/auth/login')
    async def login(request: Request, data: dict = Body(...)):
        if request.headers.get('X-Requested-With')!='XMLHttpRequest': raise HTTPException(403,'请从登录页面提交')
        name = text(data,'username',32)
        password = data.get('password','')
        if not isinstance(password,str) or len(password)>128: raise HTTPException(400,'密码格式无效')
        try:
            u = auth.authenticate(name,password,ip(request))
        except AuthError:
            store.add_audit(name,'auth.login_failed',ip=ip(request))
            raise
        remember = data.get('remember',False) is True
        token,seconds = auth.create_session(u['id'],remember,ip(request),request.headers.get('user-agent',''))
        response = JSONResponse(public_user(u))
        response.set_cookie(SESSION_COOKIE,token,httponly=True,secure=s.server.secure_cookie,samesite='lax',
            max_age=seconds if remember else None,path='/')
        store.add_audit(u['username'],'auth.login',ip=ip(request))
        return response

    @app.get('/api/auth/me')
    async def auth_me(request: Request):
        user=me(request)
        bindings=store.list_bindings(user_id=user['id'])
        effective_permissions=set(permissions_of(user['role']) if user['role']=='admin' else [])
        for binding in bindings:
            grants=(GLOBAL_ROLE_PERMISSIONS if binding['scope_type']=='global' else
                (PROJECT_ROLE_PERMISSIONS if binding['scope_type']=='project' else NAMESPACE_ROLE_PERMISSIONS))
            granted=grants.get(binding['role'],set())
            effective_permissions.update(permissions_of('admin') if '*' in granted else granted)
        projects=[]
        for project_id in visible_project_ids(store,user):
            project=store.get_project(project_id)
            roles=[b['role'] for b in bindings if b['scope_type']=='project' and b['scope_id']==project_id]
            perms=sorted({p for role in roles for p in PROJECT_ROLE_PERMISSIONS.get(role,set())})
            effective_permissions.update(perms)
            projects.append({'id':project_id,'name':project['name'],'perms':perms})
        namespaces=[]
        for namespace_id in visible_namespace_ids(store,user):
            namespace=store.get_namespace(namespace_id)
            roles=[b['role'] for b in bindings if b['scope_type']=='namespace' and b['scope_id']==namespace_id]
            perms=sorted({p for role in roles for p in NAMESPACE_ROLE_PERMISSIONS.get(role,set())})
            effective_permissions.update(perms)
            namespaces.append({'id':namespace_id,'name':namespace['name'],'cluster':namespace['cluster_name'],
                'tier':namespace['tier'],'perms':perms})
        public=public_user(user)
        global_roles={b['role'] for b in bindings if b['scope_type']=='global'}
        if 'platform_admin' in global_roles:
            public.update(role='platform_admin',role_label='平台管理员')
        elif 'auditor' in global_roles:
            public.update(role='auditor',role_label='审计员')
        return dict(public,permissions=sorted(effective_permissions),site_name=s.general.site_name,
            deploy_enabled=s.general.deploy_enabled,bindings=[binding_view(b) for b in bindings],projects=projects,namespaces=namespaces)

    @app.post('/api/auth/logout')
    async def logout(request: Request):
        auth.logout(getattr(request.state,'session_hash',None))
        audit(request,'auth.logout')
        response = JSONResponse({'ok':True})
        response.delete_cookie(SESSION_COOKIE,path='/',secure=s.server.secure_cookie,httponly=True,samesite='lax')
        return response

    @app.put('/api/account/profile')
    async def profile(request: Request,data: dict = Body(...)):
        u = store.update_user(me(request)['id'],display_name=text(data,'display_name') or me(request)['username'],email=text(data,'email',254))
        audit(request,'account.profile')
        return public_user(u)

    @app.put('/api/account/password')
    async def password(request: Request,data: dict = Body(...)):
        old,new = data.get('old_password',''),data.get('new_password','')
        if not isinstance(old,str) or not isinstance(new,str) or len(old)>128 or len(new)>128: raise ValueError('密码格式无效')
        auth.change_password(me(request),old,new,getattr(request.state,'session_hash',None))
        audit(request,'account.password')
        return {'ok':True}

    @app.get('/api/account/sessions')
    async def sessions(request: Request):
        return [dict(id=r['token_hash'],ip=r['ip'],user_agent=r['user_agent'],created_at=r['created_at'],
            last_seen_at=r['last_seen_at'],expires_at=r['expires_at'],current=r['token_hash']==getattr(request.state,'session_hash',None))
            for r in store.list_sessions(me(request)['id'])]

    @app.delete('/api/account/sessions/{id}')
    async def revoke_session(id: str,request: Request):
        r = store.get_session(id)
        if not r or r['user_id']!=me(request)['id']: raise HTTPException(404,'会话不存在')
        store.delete_session(id)
        audit(request,'account.session_revoke')
        return {'ok':True}

    @app.get('/api/account/tokens')
    async def tokens(request: Request): return store.list_api_tokens(me(request)['id'])

    @app.post('/api/account/tokens',status_code=201)
    async def create_token(request: Request,data: dict = Body(...)):
        if not getattr(request.state,'session_hash',None): raise HTTPException(403,'请使用网页登录创建令牌')
        name = text(data,'name')
        if not name: raise ValueError('请填写令牌名称')
        days = data.get('expires_days',30)
        if type(days) is not int or not 1<=days<=365: raise ValueError('有效期为 1–365 天')
        token,id = auth.create_api_token(me(request)['id'],name,days)
        audit(request,'account.token_create',id)
        return {'id':id,'token':token}

    @app.delete('/api/account/tokens/{id}')
    async def revoke_token(id: int,request: Request):
        if not store.delete_api_token(id,me(request)['id']): raise HTTPException(404,'令牌不存在')
        audit(request,'account.token_revoke',id)
        return {'ok':True}

    @app.get('/api/roles')
    async def roles():
        return {'roles':[{'value':r,'label':l,'permissions':permissions_of(r)} for r,l in ROLES.items()], 'permissions':PERMISSION_LABELS}

    @app.get('/api/users')
    async def users(): return [public_user(u) for u in store.list_users()]

    @app.post('/api/users',status_code=201)
    async def create_user(request: Request,data: dict = Body(...)):
        name = text(data,'username',32)
        if not USERNAME_RE.fullmatch(name): raise ValueError('用户名为 2–32 位字母、数字、_ . @ -，以字母或数字开头')
        if store.get_user_by_name(name): raise ValueError('用户名已存在')
        role = data.get('role','developer')
        if not isinstance(role,str) or role not in ROLES: raise ValueError('角色无效')
        password = data.get('password','')
        if not isinstance(password,str): raise ValueError('密码格式无效')
        auth.validate_password(password)
        u = store.create_user(username=name,display_name=text(data,'display_name') or name,email=text(data,'email',254),
            role=role,password_hash=hash_password(password),enabled=1,must_change_password=1)
        audit(request,'user.create',name)
        return public_user(u)

    def protect_user(request,u,role,enabled,delete=False):
        if u['id']==me(request)['id'] and (delete or role!=u['role'] or not enabled): raise ValueError('不能删除、停用或更改自己的角色')
        if store.is_platform_admin(u['id']) and u['enabled'] and (delete or role!='admin' or not enabled) and not store.count_platform_admins(u['id']):
            raise ValueError('至少保留一个启用的管理员')

    @app.put('/api/users/{id}')
    async def update_user(id: int,request: Request,data: dict = Body(...)):
        u = user_or_404(id)
        role,enabled = data.get('role',u['role']),data.get('enabled',bool(u['enabled']))
        if not isinstance(role,str) or role not in ROLES or not isinstance(enabled,bool): raise ValueError('角色或启用状态无效')
        protect_user(request,u,role,enabled)
        result = store.update_user(id,display_name=text(data,'display_name') or u['username'],email=text(data,'email',254),role=role,enabled=int(enabled))
        if role!=u['role']:
            store.delete_user_bindings(id)
            if role=='admin':
                store.bind_user_role(id,'platform_admin','global',0,me(request)['id'],'legacy administrator role')
            elif role=='developer':
                store.bind_user_role(id,'developer','project',store.default_project_id(),me(request)['id'],'default project membership')
            elif role=='viewer':
                store.bind_user_role(id,'project_viewer','project',store.default_project_id(),me(request)['id'],'default project membership')
        if not enabled or role!=u['role']:
            store.delete_user_sessions(id)
            store.delete_user_api_tokens(id)
        audit(request,'user.update',u['username'])
        return public_user(result)

    @app.delete('/api/users/{id}')
    async def delete_user(id: int,request: Request):
        u = user_or_404(id)
        protect_user(request,u,u['role'],u['enabled'],True)
        store.delete_user(id)
        audit(request,'user.delete',u['username'])
        return {'ok':True}

    @app.post('/api/users/{id}/reset-password')
    async def reset_password(id: int,request: Request,data: dict = Body(default={})):
        u = user_or_404(id)
        if id==me(request)['id']: raise ValueError('请在“我的账号”修改自己的密码')
        pw = data.get('password')
        if pw is not None and not isinstance(pw,str): raise ValueError('密码格式无效')
        result = auth.reset_password(id,pw)
        auth.unlock(u['username'])
        audit(request,'user.reset_password',u['username'])
        return {'password':result,'must_change_password':True}

    @app.get('/api/projects')
    async def projects(request: Request):
        ids=set(visible_project_ids(store,me(request)))
        items=[p for p in store.list_projects() if p['id'] in ids]
        return {'items':items,'total':len(items)}

    @app.post('/api/projects',status_code=201)
    async def create_project(request: Request,data: dict = Body(...)):
        if set(data)-{'slug','name','registry_orgs'}: raise ValueError('项目请求包含未知字段')
        slug=text(data,'slug',63).lower();name=text(data,'name',64)
        if not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?',slug): raise ValueError('项目 slug 必须是 DNS-1123 label')
        if not name: raise ValueError('请填写项目名称')
        project=store.create_project(slug,name,normalize_registry_orgs(data.get('registry_orgs',[])))
        audit(request,'project.create',project['id'],f'slug={slug}')
        return project

    @app.get('/api/projects/{id}')
    async def get_project(id: int,request: Request):
        return project_or_404(id,request)

    @app.put('/api/projects/{id}')
    async def update_project(id: int,request: Request,data: dict = Body(...)):
        if set(data)-{'slug','name','registry_orgs'}: raise ValueError('项目请求包含未知字段')
        project_or_404(id,request,'project.manage')
        slug=text(data,'slug',63).lower();name=text(data,'name',64)
        if store.get_project(id)['slug']=='default' and slug!='default':raise HTTPException(400,'默认项目 slug 不能修改')
        if not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?',slug): raise ValueError('项目 slug 必须是 DNS-1123 label')
        if not name: raise ValueError('请填写项目名称')
        result=store.update_project(id,slug,name,normalize_registry_orgs(data.get('registry_orgs',[])))
        audit(request,'project.update',id)
        return result

    @app.delete('/api/projects/{id}',status_code=204)
    async def delete_project(id: int,request: Request):
        project_or_404(id,request,'project.manage')
        if not store.delete_project(id): raise HTTPException(409,'项目仍有关联应用或任务，不能删除')
        audit(request,'project.delete',id)
        return Response(status_code=204)

    @app.put('/api/projects/{id}/namespaces/{namespace_id}')
    async def assign_project_namespace(id: int,namespace_id: int,request: Request,data: dict = Body(...)):
        if data: raise ValueError('请求体必须为空对象')
        project_or_404(id,request,'project.manage')
        if not store.get_namespace(namespace_id): raise HTTPException(404,'命名空间不存在')
        store.assign_namespace(id,namespace_id)
        audit(request,'project.namespace.assign',f'{id}:{namespace_id}')
        return store.get_project(id)

    @app.delete('/api/projects/{id}/namespaces/{namespace_id}',status_code=204)
    async def unassign_project_namespace(id: int,namespace_id: int,request: Request):
        project_or_404(id,request,'project.manage')
        if not store.unassign_namespace(id,namespace_id): raise HTTPException(409,'命名空间不存在或仍有关联服务')
        audit(request,'project.namespace.unassign',f'{id}:{namespace_id}')
        return Response(status_code=204)

    @app.get('/api/clusters')
    async def clusters():
        items=[public_cluster(row) for row in store.list_clusters()]
        return {'items':items,'total':len(items)}

    @app.post('/api/clusters',status_code=201)
    async def create_cluster(request: Request,data: dict = Body(...)):
        allowed={'name','server','auth_type','credential','cce_cluster_id','cert_duration_days'}
        if set(data)-allowed: raise ValueError('集群请求包含未知字段')
        name=text(data,'name',80);server=text(data,'server',2048)
        parsed=urlsplit(server)
        if not name or parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('集群名称无效或 API Server 不是安全 HTTPS 地址')
        auth_type=data.get('auth_type')
        if auth_type!='sa_token': raise HTTPException(501,'当前版本先实现 SA Token 接入；证书认证需要现场适配')
        credential=cluster_credential(data.get('credential'))
        duration=data.get('cert_duration_days',30)
        if type(duration) is not int or not 1<=duration<=90: raise ValueError('凭据有效期范围为 1–90 天')
        encrypted=ctx.box.encrypt(json.dumps(credential,ensure_ascii=False))
        cluster_id=store.create_cluster({'name':name,'server':server.rstrip('/'),'auth_type':auth_type,
            'credential_encrypted':encrypted,'cce_cluster_id':text(data,'cce_cluster_id',128) or None,
            'cert_duration_days':duration})
        audit(request,'cluster.create',cluster_id,f'auth_type={auth_type}')
        return public_cluster(store.get_cluster(cluster_id))

    @app.get('/api/clusters/{id}')
    async def get_cluster(id: int):
        cluster=store.get_cluster(id)
        if not cluster: raise HTTPException(404,'集群不存在')
        return public_cluster(cluster)

    @app.put('/api/clusters/{id}')
    async def update_cluster(id: int,request: Request,data: dict = Body(...)):
        allowed={'name','server','auth_type','credential','cce_cluster_id','cert_duration_days'}
        if set(data)-allowed: raise ValueError('集群请求包含未知字段')
        existing=store.get_cluster_record(id)
        if not existing: raise HTTPException(404,'集群不存在')
        name=text(data,'name',80);server=text(data,'server',2048);parsed=urlsplit(server)
        if not name or parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('集群名称无效或 API Server 不是安全 HTTPS 地址')
        auth_type=data.get('auth_type',existing['auth_type'])
        if auth_type!='sa_token': raise HTTPException(501,'当前版本先实现 SA Token 接入；证书认证需要现场适配')
        encrypted=existing['credential_encrypted']
        if 'credential' in data:
            credential=cluster_credential(data['credential'])
            encrypted=ctx.box.encrypt(json.dumps(credential,ensure_ascii=False))
        duration=data.get('cert_duration_days',existing['cert_duration_days'])
        if type(duration) is not int or not 1<=duration<=90: raise ValueError('凭据有效期范围为 1–90 天')
        updated={'name':name,'server':server.rstrip('/'),'auth_type':auth_type,'credential_encrypted':encrypted,
            'cce_cluster_id':text(data,'cce_cluster_id',128) or existing['cce_cluster_id'],
            'credential_expires_at':existing['credential_expires_at'],'cert_duration_days':duration,'status':'unverified'}
        store.update_cluster(id,updated)
        audit(request,'cluster.update',id)
        return public_cluster(store.get_cluster(id))

    @app.post('/api/clusters/{id}/test')
    async def test_cluster(id: int,request: Request,data: dict = Body(...)):
        if data: raise ValueError('请求体必须为空对象')
        cluster,credential=cluster_config(id)
        version=await test_cluster_connection(cluster,credential)
        store.set_cluster_status(id,'ready')
        audit(request,'cluster.test',id,f'version={version}')
        return {'ok':True,'version':version,'warnings':[]}

    @app.post('/api/clusters/{id}/rotate')
    async def rotate_cluster_credential(id: int,request: Request,data: dict = Body(...)):
        if set(data)-{'credential'} or 'credential' not in data: raise ValueError('请提供新凭据')
        cluster=store.get_cluster_record(id)
        if not cluster: raise HTTPException(404,'集群不存在')
        credential=cluster_credential(data['credential'])
        candidate=ctx.box.encrypt(json.dumps(credential,ensure_ascii=False))
        version=await test_cluster_connection(cluster,credential)
        store.update_cluster(id,{**cluster,'credential_encrypted':candidate,'status':'ready'})
        audit(request,'cluster.credential.rotate',id,f'version={version}')
        return {'id':id,'status':'ready','credential_expires_at':None}

    @app.post('/api/clusters/{id}/revoke')
    async def revoke_cluster_credential(id: int,data: dict = Body(...)):
        cluster=store.get_cluster(id)
        if not cluster: raise HTTPException(404,'集群不存在')
        if cluster['auth_type']!='cce_cert': raise HTTPException(400,'只有 CCE 签发证书支持撤销')
        raise HTTPException(501,'CCE 证书撤销尚未接入现场 CCE API')

    @app.get('/api/namespaces')
    async def namespaces(request: Request,cluster_id: int = None,tier: str = None):
        if tier is not None and tier not in {'dev','test','staging','prod'}: raise ValueError('tier 无效')
        visible=set(visible_namespace_ids(store,me(request)))
        items=store.list_namespaces(sorted(visible),cluster_id,tier)
        return {'items':items,'total':len(items)}

    @app.post('/api/namespaces',status_code=201)
    async def create_namespace(request: Request,data: dict = Body(...)):
        if set(data)-{'name','cluster_id','tier','policy'}: raise ValueError('命名空间请求包含未知字段')
        name=text(data,'name',63).lower();cluster_id=data.get('cluster_id');tier=data.get('tier')
        if not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?',name): raise ValueError('namespace 必须是 DNS-1123 label')
        if type(cluster_id) is not int or not store.get_cluster(cluster_id): raise ValueError('集群不存在')
        if tier not in {'dev','test','staging','prod'}: raise ValueError('tier 必须为 dev/test/staging/prod')
        policy=normalize_namespace_policy(data.get('policy'))
        cluster,credential=cluster_config(cluster_id)
        version=await test_cluster_connection(cluster,credential)
        verify=ssl.create_default_context(cadata=credential['ca_pem']) if credential.get('ca_pem') else True
        async with httpx.AsyncClient(verify=verify,timeout=10,transport=getattr(ctx,'deploy_transport',None),follow_redirects=False) as client:
            try:
                response=await client.get(cluster['server'].rstrip('/')+'/api/v1/namespaces/'+name,
                    headers={'Authorization':'Bearer '+credential['token']})
            except httpx.HTTPError:raise HTTPException(503,'读取目标 namespace 失败，请检查集群连接') from None
        if response.status_code==404: raise HTTPException(400,'目标 namespace 在集群中不存在；本接口只纳管现有 namespace')
        if response.status_code!=200: raise HTTPException(503,f'读取目标 namespace 失败，HTTP {response.status_code}')
        namespace_id=store.create_namespace(cluster_id,name,tier,policy)
        store.set_cluster_status(cluster_id,'ready')
        audit(request,'namespace.adopt',namespace_id,f'cluster={cluster_id} version={version}')
        return store.get_namespace(namespace_id)

    @app.get('/api/namespaces/{id}')
    async def get_namespace(id: int,request: Request):
        namespace=store.get_namespace(id)
        if not namespace or not has_namespace_permission(store,me(request),id,'namespace.view'):
            raise HTTPException(404,'命名空间不存在')
        return namespace

    @app.put('/api/namespaces/{id}')
    async def update_namespace(id: int,request: Request,data: dict = Body(...)):
        if set(data)-{'tier','policy'}: raise ValueError('命名空间请求包含未知字段')
        current=store.get_namespace(id)
        if not current: raise HTTPException(404,'命名空间不存在')
        tier=data.get('tier',current['tier'])
        if tier not in {'dev','test','staging','prod'}: raise ValueError('tier 无效')
        policy=normalize_namespace_policy(data.get('policy',current['policy']))
        store.update_namespace(id,tier,policy)
        audit(request,'namespace.policy.update',id)
        return store.get_namespace(id)

    @app.post('/api/namespaces/{id}/review')
    async def review_namespace(id: int,request: Request,data: dict = Body(...)):
        if data: raise ValueError('请求体必须为空对象')
        namespace=store.get_namespace(id)
        if not namespace: raise HTTPException(404,'命名空间不存在')
        cluster,credential=cluster_config(namespace['cluster_id'])
        checks=[];warnings=[]
        resources=[('apps','deployments',v) for v in ('get','list','watch','create','patch','update','delete')]
        resources += [('',r,v) for r in ('services','configmaps') for v in ('get','list','watch','create','patch','update','delete')]
        resources += [('',r,v) for r in ('pods','events') for v in ('get','list','watch')]
        resources += [('', 'pods/log','get'),('','secrets','create'),('','secrets','patch'),('','secrets','update')]
        if namespace['policy'].get('hpa_enabled'):resources += [('autoscaling','horizontalpodautoscalers',v) for v in ('get','list','watch','create','patch','update','delete')]
        if namespace['policy'].get('ingress',{}).get('enabled'):resources += [('networking.k8s.io','ingresses',v) for v in ('get','list','watch','create','patch','update','delete')]
        verify=ssl.create_default_context(cadata=credential['ca_pem']) if credential.get('ca_pem') else True
        async with httpx.AsyncClient(verify=verify,timeout=10,transport=getattr(ctx,'deploy_transport',None),follow_redirects=False) as client:
            for group,resource,verb in resources:
                body={'apiVersion':'authorization.k8s.io/v1','kind':'SelfSubjectAccessReview','spec':{'resourceAttributes':
                    {'namespace':namespace['name'],'verb':verb,'group':group,'resource':resource}}}
                try:
                    response=await client.post(cluster['server'].rstrip('/')+'/apis/authorization.k8s.io/v1/selfsubjectaccessreviews',
                        headers={'Authorization':'Bearer '+credential['token'],'Content-Type':'application/json'},json=body)
                except httpx.HTTPError:raise HTTPException(503,'集群权限自检连接失败') from None
                if response.status_code!=201 and response.status_code!=200:raise HTTPException(503,f'SSAR 请求失败，HTTP {response.status_code}')
                try:status=response.json().get('status',{})
                except (ValueError,TypeError):raise HTTPException(503,'SSAR 响应格式无效') from None
                allowed=status.get('allowed') is True
                checks.append({'group':group,'resource':resource,'verb':verb,'allowed':allowed})
                if status.get('evaluationError'):warnings.append(f"{resource}/{verb}: {status['evaluationError'][:200]}")
        audit(request,'namespace.review',id,f'allowed={all(c["allowed"] for c in checks)}')
        return {'ok':all(c['allowed'] for c in checks) and not warnings,'checks':checks,'warnings':warnings}

    @app.get('/api/namespaces/{id}/rbac-yaml')
    async def namespace_rbac_yaml(id: int):
        namespace=store.get_namespace(id)
        if not namespace: raise HTTPException(404,'命名空间不存在')
        name=namespace['name']
        yaml=("apiVersion: rbac.authorization.k8s.io/v1\nkind: Role\nmetadata:\n  name: swr-builder-deployer\n"
            f"  namespace: {name}\nrules:\n  - apiGroups: [\"apps\"]\n    resources: [\"deployments\"]\n"
            "    verbs: [\"get\", \"list\", \"watch\", \"create\", \"patch\", \"update\", \"delete\"]\n"
            "  - apiGroups: [\"\"]\n    resources: [\"services\", \"configmaps\"]\n"
            "    verbs: [\"get\", \"list\", \"watch\", \"create\", \"patch\", \"update\", \"delete\"]\n"
            "  - apiGroups: [\"\"]\n    resources: [\"pods\", \"events\"]\n    verbs: [\"get\", \"list\", \"watch\"]\n"
            "  - apiGroups: [\"\"]\n    resources: [\"pods/log\"]\n    verbs: [\"get\"]\n")
        return {'yaml':yaml,'warning':'权限模板供集群管理员审核；写 Secret 权限与 Pod 创建存在间接读取 Secret 的风险。'}

    def service_or_404(service_id,request,permission=None,allow_project_view=True):
        service=store.get_service(service_id)
        if not service:raise HTTPException(404,'服务不存在')
        namespace_ok=has_namespace_permission(store,me(request),service['namespace_id'],permission or 'namespace.view')
        project_ok=allow_project_view and has_project_permission(store,me(request),service['project_id'],'app.view')
        if not namespace_ok and not project_ok:raise HTTPException(404,'服务不存在')
        return service

    def normalize_service_spec(value,namespace):
        if not isinstance(value,dict) or set(value)-{'mode','target','workload','container','config_files','service','ingress','hpa','scheduling','image_pull_secrets','labels','annotations'}:
            raise ValueError('服务 spec 包含未知字段')
        mode=value.get('mode')
        if mode=='image_only':
            if set(value)!={'mode','target'} or not isinstance(value.get('target'),dict) or set(value['target'])!={'kind','name','container'}:
                raise ValueError('image_only spec 只允许 kind/name/container')
            target=value['target']
            if target['kind']!='Deployment' or not isinstance(target['name'],str) or not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?)*',target['name']) or len(target['name'])>253:
                raise ValueError('image_only Deployment 名称无效')
            if not isinstance(target['container'],str) or not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?',target['container']):
                raise ValueError('容器名称无效')
            return value
        if mode!='managed':raise ValueError('mode 必须是 managed 或 image_only')
        policy=namespace['policy']
        if 'workload' in value:
            workload=value['workload']
            if not isinstance(workload,dict) or set(workload)-{'kind','replicas','strategy','progress_deadline_seconds','revision_history_limit','termination_grace_period_seconds'}:
                raise ValueError('workload 配置包含未知字段')
            if workload.get('kind','Deployment')!='Deployment':raise ValueError('当前只支持 Deployment workload')
            replicas=workload.get('replicas',1)
            if type(replicas) is not int or not 0<=replicas<=policy['max_replicas']:raise ValueError('副本数超出命名空间策略')
        if 'service' in value:
            service=value['service']
            if not isinstance(service,dict) or set(service)-{'enabled','type','ports'}:raise ValueError('Service 配置包含未知字段')
            if service.get('type','ClusterIP') not in policy['service_types']:raise ValueError('Service 类型不符合命名空间策略')
        if 'image_pull_secrets' in value:
            names=value['image_pull_secrets']
            if not isinstance(names,list) or any(name not in policy['image_pull_secrets'] for name in names):raise ValueError('imagePullSecrets 不符合命名空间策略')
        if 'hpa' in value and value['hpa'].get('enabled') and not policy['hpa_enabled']:raise ValueError('命名空间策略未启用 HPA')
        if 'ingress' in value and value['ingress'].get('enabled') and not policy['ingress'].get('enabled'):raise ValueError('命名空间策略未启用 Ingress')
        return value

    def service_response(service):
        return {key:service.get(key) for key in ('id','name','project_id','namespace_id','app_id','current_revision_id','deployed_revision_id','spec')}

    def can_service_config(request,service):
        return has_namespace_permission(store,me(request),service['namespace_id'],'service.config')

    def can_service_operation(request,service,permission):
        return has_namespace_permission(store,me(request),service['namespace_id'],permission)

    @app.get('/api/services')
    async def services(request: Request,project_id: int = None,namespace_id: int = None):
        projects=set(visible_project_ids(store,me(request),'app.view'))
        namespaces=set(visible_namespace_ids(store,me(request),'namespace.view'))
        if project_id is not None:
            projects &= {project_id}
            namespaces=set()
        if namespace_id is not None:
            namespaces &= {namespace_id}
            projects=set()
        rows=store.list_services(sorted(projects),sorted(namespaces))
        return {'items':[service_response(row) for row in rows],'total':len(rows)}

    @app.post('/api/services',status_code=201)
    async def create_service(request: Request,data: dict = Body(...)):
        if set(data)-{'name','project_id','namespace_id','app_id','spec'}:raise ValueError('服務請求包含未知字段')
        name=text(data,'name',63).lower()
        if not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?',name):raise ValueError('服務名稱必須是 DNS-1123 label')
        project_id=data.get('project_id');namespace_id=data.get('namespace_id');app_id=data.get('app_id')
        if any(type(v) is not int for v in (project_id,namespace_id,app_id)):raise ValueError('project_id、namespace_id、app_id 必須是整數')
        namespace=store.get_namespace(namespace_id);app_record=store.get_app(app_id)
        if not namespace or not app_record:raise HTTPException(404,'服務所屬資源不存在')
        if app_record['project_id']!=project_id or namespace_id not in store.project_namespace_ids(project_id):raise HTTPException(404,'服務所屬資源不存在')
        if not has_namespace_permission(store,me(request),namespace_id,'service.config'):raise HTTPException(403,'沒有命名空間服務配置權限')
        spec=normalize_service_spec(data.get('spec'),namespace)
        service=store.create_service(name,project_id,namespace_id,app_id,spec,me(request)['id'])
        audit(request,'service.create',service['id'],f'project={project_id};namespace={namespace_id};app={app_id}')
        return service_response(service)

    @app.get('/api/services/{id}')
    async def get_service(id: int,request: Request):
        return service_response(service_or_404(id,request))

    @app.put('/api/services/{id}')
    async def update_service(id: int,request: Request,data: dict = Body(...)):
        if set(data)-{'base_revision','spec'}:raise ValueError('服務更新請求包含未知字段')
        service=service_or_404(id,request,allow_project_view=False)
        if not can_service_config(request,service):raise HTTPException(403,'沒有命名空間服務配置權限')
        base_revision=data.get('base_revision')
        if type(base_revision) is not int:raise ValueError('base_revision 必須是整數')
        namespace=store.get_namespace(service['namespace_id']);spec=normalize_service_spec(data.get('spec'),namespace)
        try:
            result=store.update_service_revision(id,base_revision,spec,me(request)['id'])
        except RuntimeError as exc:
            if str(exc)=='REVISION_CONFLICT':raise HTTPException(409,'REVISION_CONFLICT') from None
            raise
        audit(request,'service.revision.create',result[1]['id'],f'service={id}')
        return {'service':service_response(result[0]),'revision':result[1]}

    @app.delete('/api/services/{id}',status_code=204)
    async def delete_service(id: int,request: Request):
        service=service_or_404(id,request,allow_project_view=False)
        if not can_service_operation(request,service,'service.delete'):raise HTTPException(403,'沒有服務刪除權限')
        if not store.delete_service(id):raise HTTPException(409,'存在活动部署，不能删除服务记录')
        audit(request,'service.delete',id)
        return Response(status_code=204)

    @app.get('/api/services/{id}/revisions')
    async def service_revisions(id: int,request: Request):
        service=service_or_404(id,request)
        rows=store.list_revisions(id)
        return {'items':rows,'total':len(rows)}

    @app.get('/api/services/{id}/revisions/diff')
    async def service_revision_diff(id: int,request: Request,from_revision: int = Query(...,alias='from'),to_revision: int = Query(...,alias='to')):
        service_or_404(id,request)
        old=store.get_revision(id,from_revision);new=store.get_revision(id,to_revision)
        if not old or not new:raise HTTPException(404,'服務修訂不存在')
        changes=[]
        def compare(path,left,right):
            if isinstance(left,dict) and isinstance(right,dict):
                for key in sorted(set(left)|set(right)):
                    compare(path+'/'+str(key),left.get(key),right.get(key))
            elif left!=right:changes.append({'path':path or '/', 'old':left,'new':right})
        compare('',old['spec'],new['spec'])
        return {'from':from_revision,'to':to_revision,'changes':changes}

    @app.get('/api/services/{id}/revisions/{revision_id}')
    async def get_service_revision(id: int,revision_id: int,request: Request):
        service_or_404(id,request)
        revision=store.get_revision(id,revision_id)
        if not revision:raise HTTPException(404,'服務修訂不存在')
        return revision

    @app.post('/api/services/{id}/render')
    async def render_service(id: int,request: Request,data: dict = Body(...)):
        if set(data)-{'revision_id','dry_run'}:raise ValueError('渲染請求包含未知字段')
        service=service_or_404(id,request,allow_project_view=False)
        if not can_service_config(request,service):raise HTTPException(403,'沒有服務配置權限')
        revision_id=data.get('revision_id');dry_run=data.get('dry_run',True)
        if type(revision_id) is not int or not isinstance(dry_run,bool):raise ValueError('revision_id 或 dry_run 格式錯誤')
        revision=store.get_revision(id,revision_id)
        if not revision:raise HTTPException(404,'服務修訂不存在')
        if revision['spec'].get('mode')!='image_only':raise HTTPException(501,'managed manifest rendering is not enabled')
        kinds=['Deployment']
        return {'revision_id':revision_id,'valid':True,'resources':[{'kind':kind,'name':service['name']} for kind in kinds],
            'warnings':['Kubernetes dry-run 尚未执行；清单渲染与现场 API 校验将在部署 worker 阶段完成。'] if dry_run else []}

    @app.put('/api/services/{id}/secrets/{key}')
    async def update_service_secret(id: int,key: str,request: Request,data: dict = Body(...)):
        if set(data)!={'value'} or not isinstance(data['value'],str) or not data['value'] or len(data['value'])>65536:
            raise ValueError('value 必须是 1–65536 字符的非空字符串')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,127}',key):raise ValueError('Secret key 格式无效')
        service=service_or_404(id,request,allow_project_view=False)
        if not can_service_operation(request,service,'secret.manage'):raise HTTPException(403,'没有服务密钥管理权限')
        encrypted=ctx.box.encrypt(data['value'])
        revision=store.create_secret_revision(id,key,encrypted,me(request)['id'])
        audit(request,'service.secret.update',id,f'key={key};version={revision["secret_versions"][key]}')
        return {'key':key,'version':revision['secret_versions'][key],'updated_at':revision['created_at']}

    def queue_service_deployment(service,job,revision,actor,kind,idempotency_key,remark=''):
        if revision['spec'].get('mode')!='image_only':
            raise HTTPException(501,'managed 服务的清单渲染与 SSA 尚未开放')
        digest=job.get('digest')
        if not isinstance(digest,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):
            raise HTTPException(422,'成功构建记录缺少有效的 SHA-256 摘要')
        namespace=store.get_namespace(service['namespace_id'])
        policy=namespace['policy']
        needs_approval=policy['require_approval'] or namespace['tier']=='prod'
        status='pending_approval' if needs_approval else 'queued'
        image_ref=parse_image_ref(job['image'])
        if not image_ref:raise HTTPException(422,'成功构建记录的镜像引用无效')
        image=image_ref.host+'/'+image_ref.repo+'@'+digest
        existing=store.find_deployment_idempotency(actor['id'],service['id'],idempotency_key)
        if existing:
            request_hash=deployment_request_hash(kind,{'job_id':job['id'],'revision_id':revision['id'],'remark':remark})
            if existing['request_hash']!=request_hash:
                raise HTTPException(409,'幂等键已用于不同的部署请求')
            return existing
        blocked=deployment_policy_block(policy)
        if blocked:raise HTTPException(403,blocked)
        try:
            deployment=store.create_idempotent_service_deployment(target_id=None,target_name=service['name'],
                target_type='kubernetes',environment=store.get_namespace(service['namespace_id'])['tier'],
                app_id=service['app_id'],app_name=job['app_name'],job_id=job['id'],image=image,
                project_id=service['project_id'],service_id=service['id'],namespace_id=service['namespace_id'],
                revision_id=revision['id'],kind=kind,digest=digest,idempotency_key=idempotency_key,
                request_hash=deployment_request_hash(kind,{'job_id':job['id'],'revision_id':revision['id'],'remark':remark}),
                policy_snapshot=json.dumps(policy,ensure_ascii=False),status=status,trigger='manual',
                operator=actor.get('display_name') or actor['username'],created_by=actor['id'],remark=remark[:200])
        except Exception as exc:
            if 'UNIQUE constraint failed' in str(exc):raise HTTPException(409,'部署请求冲突，请使用新的幂等键') from None
            raise
        store.add_audit(actor['username'],'deploy.submit',str(deployment['id']),
            f'service={service["id"]};kind={kind};status={status};revision={revision["id"]}')
        return deployment

    def deployment_request_hash(kind,payload):
        raw=json.dumps({'kind':kind,'payload':payload},sort_keys=True,separators=(',',':'),ensure_ascii=False)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def deployment_response(deployment):
        return {key:deployment.get(key) for key in ('id','service_id','job_id','revision_id','kind','status','digest','created_at')}

    def resolve_service_revision(service,revision_id=None,use_latest_config=False):
        selected=revision_id
        if selected is None:
            selected=service['current_revision_id'] if use_latest_config or service['deployed_revision_id'] is None else service['deployed_revision_id']
        revision=store.get_revision(service['id'],selected) if selected is not None else None
        if not revision:raise HTTPException(404,'服務修訂不存在')
        return revision

    @app.post('/api/services/{id}/deployments',status_code=202)
    async def submit_service_deployment(id: int,request: Request,data: dict = Body(...),
                                        idempotency_key: str = Header(...,alias='Idempotency-Key')):
        if set(data)-{'job_id','revision_id','use_latest_config','remark'}:raise ValueError('部署請求包含未知字段')
        if not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}',idempotency_key):raise ValueError('Idempotency-Key 格式无效')
        service=service_or_404(id,request,allow_project_view=False)
        if not can_service_operation(request,service,'service.deploy'):raise HTTPException(403,'没有服务发布权限')
        job_id=data.get('job_id')
        if type(job_id) is not int:raise ValueError('job_id 必须是整数')
        job=store.get_job(job_id)
        if not job or job['status']!='success' or job['app_id']!=service['app_id'] or job['project_id']!=service['project_id']:
            raise HTTPException(422,'构建必须成功且属于服务对应的应用和项目')
        use_latest=data.get('use_latest_config',False)
        if not isinstance(use_latest,bool):raise ValueError('use_latest_config 必须是布尔值')
        revision=resolve_service_revision(service,data.get('revision_id'),use_latest)
        deployment=queue_service_deployment(service,job,revision,me(request),'deploy',idempotency_key,text(data,'remark',200))
        return deployment_response(deployment)

    @app.post('/api/services/{id}/rollback',status_code=202)
    async def rollback_service(id: int,request: Request,data: dict = Body(...),
                               idempotency_key: str = Header(...,alias='Idempotency-Key')):
        if set(data)-{'target_deployment_id','remark'}:raise ValueError('回滚请求包含未知字段')
        service=service_or_404(id,request,allow_project_view=False)
        if not can_service_operation(request,service,'service.rollback'):raise HTTPException(403,'没有服务回滚权限')
        target_id=data.get('target_deployment_id')
        if type(target_id) is not int:raise ValueError('target_deployment_id 必须是整数')
        target=store.get_deployment(target_id)
        if not target or target['service_id']!=id or target['status']!='success':raise HTTPException(404,'成功部署记录不存在')
        job=store.get_job(target['job_id']);revision=store.get_revision(id,target['revision_id'])
        if not job or not revision:raise HTTPException(409,'历史发布引用已不可用')
        deployment=queue_service_deployment(service,job,revision,me(request),'rollback',idempotency_key,text(data,'remark',200))
        return deployment_response(deployment)

    @app.post('/api/services/{id}/restart',status_code=202)
    async def restart_service(id: int,request: Request,data: dict = Body(...),
                              idempotency_key: str = Header(...,alias='Idempotency-Key')):
        if set(data)-{'remark'}:raise ValueError('重启请求包含未知字段')
        service=service_or_404(id,request,allow_project_view=False)
        if not can_service_operation(request,service,'service.operate'):raise HTTPException(403,'没有服务运维权限')
        rows,total=store.list_deployments(app_id=service['app_id'],project_ids=[service['project_id']],limit=100,offset=0)
        previous=next((d for d in rows if d['service_id']==id and d['status']=='success'),None)
        if not previous:raise HTTPException(409,'服务没有成功部署记录')
        job=store.get_job(previous['job_id']);revision=store.get_revision(id,previous['revision_id'])
        deployment=queue_service_deployment(service,job,revision,me(request),'restart',idempotency_key,text(data,'remark',200))
        return deployment_response(deployment)

    @app.post('/api/services/{id}/scale',status_code=202)
    async def scale_service(id: int,request: Request,data: dict = Body(...),
                            idempotency_key: str = Header(...,alias='Idempotency-Key')):
        if set(data)-{'replicas','base_revision','remark'}:raise ValueError('扩缩容请求包含未知字段')
        service=service_or_404(id,request,allow_project_view=False)
        if not can_service_operation(request,service,'service.operate'):raise HTTPException(403,'没有服务运维权限')
        raise HTTPException(501,'controlled scaling is not enabled until managed workloads are supported')

    @app.get('/api/bindings')
    async def bindings(request: Request):
        user=me(request)
        if has_global_permission(store,user,'user.manage'):
            rows=store.all_bindings()
        else:
            managed=set(visible_project_ids(store,user,'project.member.manage'))
            managed_namespaces=set(visible_namespace_ids(store,user,'namespace.member.manage'))
            rows=[b for b in store.all_bindings() if (b['scope_type']=='project' and b['scope_id'] in managed) or
                (b['scope_type']=='namespace' and b['scope_id'] in managed_namespaces)]
        items=[binding_view(row) for row in rows]
        return {'items':items,'total':len(items)}

    @app.get('/api/bindable-users')
    async def bindable_users(request: Request):
        user=me(request)
        if not has_global_permission(store,user,'user.manage') and not visible_project_ids(store,user,'project.member.manage') and not visible_namespace_ids(store,user,'namespace.member.manage'):
            raise HTTPException(403,'没有任何可管理的成员范围')
        items=[{'id':row['id'],'username':row['username'],'display_name':row.get('display_name') or row['username'],
            'enabled':bool(row['enabled'])} for row in store.list_users() if row['enabled']]
        return {'items':items,'total':len(items)}

    @app.post('/api/bindings',status_code=201)
    async def create_binding(request: Request,data: dict = Body(...)):
        allowed={'subject_type','subject_id','role','scope_type','scope_id','expires_at','note'}
        if set(data)-allowed: raise ValueError('授权请求包含未知字段')
        if data.get('subject_type')!='user': raise ValueError('当前只支持直接绑定用户')
        subject_id=data.get('subject_id');scope_id=data.get('scope_id',0);scope_type=data.get('scope_type');role=data.get('role')
        if type(subject_id) is not int or not store.get_user(subject_id): raise ValueError('用户不存在')
        if type(scope_id) is not int: raise ValueError('scope_id 格式错误')
        allowed_roles={'global':{'platform_admin','auditor'},'project':{'project_admin','developer','project_viewer'},
                       'namespace':{'namespace_owner','deployer','namespace_viewer'}}
        if scope_type not in allowed_roles or role not in allowed_roles[scope_type]: raise ValueError('角色与授权范围不匹配')
        actor=me(request)
        if scope_type=='global':
            if not has_global_permission(store,actor,'user.manage'): raise HTTPException(403,'只有平台管理员可以授予全局角色')
            scope_id=0
        elif scope_type=='project':
            project_or_404(scope_id,request,'project.member.manage')
            if role=='project_admin' and not has_global_permission(store,actor,'user.manage'):
                raise HTTPException(403,'项目成员管理员不能授予同级管理员角色')
        elif scope_type=='namespace':
            namespace=store.get_namespace(scope_id)
            if not namespace or not has_namespace_permission(store,actor,scope_id,'namespace.member.manage'):
                raise HTTPException(404,'命名空间不存在')
            if role=='namespace_owner' and not has_global_permission(store,actor,'user.manage'):
                raise HTTPException(403,'命名空间管理员不能授予同级管理员角色')
        else:
            raise HTTPException(400,'授权范围无效')
        expires=data.get('expires_at')
        if expires is not None:
            if not isinstance(expires,str): raise ValueError('expires_at 必须为 ISO 8601 时间')
            try: parsed=datetime.fromisoformat(expires.replace('Z','+00:00'))
            except ValueError: raise ValueError('expires_at 必须为 ISO 8601 时间') from None
            if parsed.tzinfo is None or parsed <= datetime.now(timezone.utc): raise ValueError('授权过期时间必须为未来的带时区时间')
        try:
            binding_id=store.create_binding('user',subject_id,role,scope_type,scope_id,actor['id'],expires,text(data,'note',200))
        except Exception as exc:
            if 'UNIQUE constraint failed: role_bindings' in str(exc):raise HTTPException(409,'该用户已拥有相同范围和角色') from None
            raise
        binding=store.get_binding(binding_id)
        audit(request,'binding.create',binding_id,f'{scope_type}:{scope_id} role={role}')
        return binding_view(binding)

    @app.delete('/api/bindings/{id}',status_code=204)
    async def delete_binding(id: int,request: Request):
        binding=store.get_binding(id)
        if not binding or not can_manage_binding(request,binding): raise HTTPException(404,'授权不存在')
        if binding['scope_type']=='global' and binding['role']=='platform_admin' and not store.count_platform_admins(binding['subject_id']):
            raise HTTPException(400,'至少保留一名平台管理员')
        store.delete_binding(id)
        audit(request,'binding.delete',id)
        return Response(status_code=204)

    @app.get('/api/settings')
    async def settings_view(): return sysconfig.view()

    @app.put('/api/settings')
    async def settings_update(request: Request,data: dict = Body(...)):
        values,clear = data.get('values',{}),data.get('clear',[])
        if not isinstance(values,dict) or not isinstance(clear,list) or not all(isinstance(p,str) for p in clear): raise ValueError('设置格式错误')
        paths = set(values) | set(clear)
        if ctx.manager._tasks and any(p.startswith(('registry.','build.')) for p in paths):
            raise ValueError('存在未结束的构建，请等待后再修改仓库或构建设置')
        changed = sysconfig.update(values,clear,me(request)['username'])
        audit(request,'settings.update',detail=','.join(changed))
        return {'ok':True,'changed':changed}

    @app.post('/api/settings/registry/test')
    async def registry_test(data: dict = Body(default={})):
        cfg = sysconfig.candidate_registry(data.get('values',{}),data.get('clear',[]))
        client = RegistryClient(cfg,transport=ctx.registry_transport)
        try:
            await client.ping()
            return {'ok':True,'message':'连接成功（仅验证 Registry API；不验证 Docker 推送权限）'}
        except RegistryError as exc: return {'ok':False,'message':str(exc)}
        finally: await client.aclose()

    @app.get('/api/audit')
    async def audit_view(limit: int = 50,offset: int = 0,username: str = '',action: str = ''):
        items,total = store.list_audit(min(max(limit,1),100),max(offset,0),username,action)
        return {'items':items,'total':total}

    @app.get('/api/system')
    async def system():
        disk = shutil.disk_usage(s.data_path)
        return {'version':__version__,'python':platform.python_version(),'data_dir':str(s.data_path),
            'disk':{'total':disk.total,'free':disk.free},'users':store.count_users(),'jobs':store.count_jobs(),
            'config_priority':'环境变量 > 页面设置（数据库） > config.yaml','deployment_preview':True}

    @app.get('/api/deploy/providers')
    async def providers(): return list_providers()

    @app.get('/api/deploy/targets')
    async def targets(request: Request):
        return [deploy.view(t,has_perm(me(request)['role'],'deploy.manage')) for t in store.list_deploy_targets()]

    @app.post('/api/deploy/targets',status_code=201)
    async def create_target(data: dict = Body(...)):
        return deploy.view(deploy.save_target(data),True)

    @app.put('/api/deploy/targets/{id}')
    async def update_target(id: int,data: dict = Body(...)):
        if any(store.get_deployment(d)['target_id']==id for d in deploy.tasks): raise ValueError('该目标有部署正在进行，请稍后再修改')
        return deploy.view(deploy.save_target(data,id),True)

    @app.delete('/api/deploy/targets/{id}')
    async def delete_target(id: int):
        if any(store.get_deployment(d)['target_id']==id for d in deploy.tasks): raise ValueError('该目标有部署正在进行')
        if not store.delete_deploy_target(id): raise HTTPException(404,'目标不存在')
        return {'ok':True}

    @app.post('/api/deploy/targets/{id}/test')
    async def test_target(id: int): return {'ok':True,'message':await deploy.test_target(id)}

    @app.post('/api/deployments',status_code=201)
    async def create_deployment(request: Request,data: dict = Body(...)):
        audit(request,'deploy.legacy_blocked',detail='migrate this target to a scoped service revision')
        raise HTTPException(410,'旧部署目标不再执行发布。请在资源管理创建服务并通过 namespace 授权与审批队列发布。')

    async def decide_deployment(id,request,data,decision):
        if set(data)-{'comment'}:raise ValueError('审批请求包含未知字段')
        deployment_record=store.get_deployment(id)
        if not deployment_record or not deployment_record.get('service_id'):raise HTTPException(404,'发布任务不存在')
        service=store.get_service(deployment_record['service_id'])
        if not service or not has_namespace_permission(store,me(request),service['namespace_id'],'deploy.approve'):
            raise HTTPException(404,'发布任务不存在')
        if deployment_record['status']!='pending_approval':raise HTTPException(409,'发布任务不在待审批状态')
        actor=me(request)
        snapshot=json.loads(deployment_record.get('policy_snapshot') or '{}')
        required=max(1,int(snapshot.get('min_approvals',1)))
        try:
            result=store.record_approval_transition(id,actor['id'],decision,text(data,'comment',500),required)
        except LookupError:raise HTTPException(404,'发布任务不存在') from None
        except PermissionError:raise HTTPException(403,'发布发起人不能审批自己的任务') from None
        except FileExistsError:raise HTTPException(409,'当前用户已审批该任务') from None
        except RuntimeError:raise HTTPException(409,'发布任务不在待审批状态') from None
        audit(request,'deploy.'+decision,id)
        result['approvals']=store.list_approvals(id)
        return result

    @app.post('/api/deployments/{id}/approve')
    async def approve_deployment(id: int,request: Request,data: dict = Body(...)):
        return await decide_deployment(id,request,data,'approved')

    @app.post('/api/deployments/{id}/reject')
    async def reject_deployment(id: int,request: Request,data: dict = Body(...)):
        return await decide_deployment(id,request,data,'rejected')

    @app.get('/api/deployments')
    async def deployments(request: Request,target_id: int = None,app_id: int = None,limit: int = 30,offset: int = 0):
        projects=visible_project_ids(store,me(request),'deploy.view')
        namespaces=visible_namespace_ids(store,me(request),'namespace.view')
        items,total = store.list_deployments(target_id,app_id,min(max(limit,1),100),max(offset,0),projects,namespaces)
        return {'items':items,'total':total}

    @app.get('/api/deployments/{id}')
    async def deployment(id: int,request: Request):
        d = store.get_deployment(id)
        if not d or not has_project_permission(store,me(request),d.get('project_id') or 0,'deploy.view'):
            if not d or not d.get('service_id') or not has_namespace_permission(store,me(request),d.get('namespace_id') or 0,'namespace.view'):
                raise HTTPException(404,'部署记录不存在')
        d['approvals']=store.list_approvals(id) if d.get('service_id') else []
        return d

    @app.get('/api/deployments/{id}/log')
    async def deployment_log(id: int,request: Request,offset: int = 0):
        from .main import _read_log
        d = await deployment(id,request)
        text,new,size = _read_log(deploy.log_path(id),offset)
        return {'text':text,'offset':new,'status':d['status'],'finished':d['status'] in ('success','failed','cancelled','rejected','expired','unknown') and new>=size}

    @app.post('/api/cloud/hcs/test')
    async def hcs_test(data: dict = Body(default={})):
        cfg = sysconfig.candidate_hcs(data.get('values',{}),data.get('clear',[]))
        client = HCSClient(cfg,transport=getattr(ctx,'hcs_transport',None))
        try: return await client.test()
        finally: await client.close()

    @app.get('/api/cloud/hcs/capabilities')
    async def hcs_capabilities():
        return {'platform':'Huawei Cloud Stack','version':s.hcs.version,'enabled':s.hcs.enabled,
            'capabilities':[
                {'name':'IAM AK/SK 签名换 Token / 密码 / 预置 Token','state':'implemented','live_verified':False},
                {'name':'SWR Registry V2 镜像构建与推送','state':'implemented','live_verified':False},
                {'name':'Kubernetes Deployment 更新（直连集群）','state':'implemented','live_verified':False},
                {'name':'SWR / CCE / ECS 管理 API','state':'reserved','live_verified':False},
                {'name':'ManageOne SSO、审批和自动回滚','state':'reserved','live_verified':False}],
            'notice':'尚未连接用户的 Stack 环境；implemented 表示已有代码及模拟测试，不是生产兼容性认证。'}
