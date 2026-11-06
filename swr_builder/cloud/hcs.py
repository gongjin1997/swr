"""Huawei Cloud Stack 8.5.1：AK/SK 签名换 Token、密码及预置 Token。

默认对 POST /v3/auth/tokens 的实际请求字节做 SDK-HMAC-SHA256 签名。
作用域显式使用 x-project-id / x-domain-id。AK/SK 默认发送空请求体；
现场网关要求 JSON 时可明确配置 aksk_request_body，不推测 identity 方法。
协议实现与真实现场验收分开；不自动回退密码、不盲试请求格式。
"""
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import ssl
from urllib.parse import urlsplit
import httpx
from .signing import sign_aksk


class HCSError(ValueError): pass


@dataclass
class HCSProfile:
    enabled: bool = False
    version: str = '8.5.1'
    region: str = ''
    iam_endpoint: str = ''
    swr_endpoint: str = ''
    cce_endpoint: str = ''
    ecs_endpoint: str = ''
    project_id: str = ''
    project_name: str = ''
    domain_id: str = ''
    domain_name: str = ''
    username: str = ''
    password: str = ''
    auth_mode: str = 'aksk'
    ak: str = ''
    sk: str = ''
    security_token: str = ''
    token_scope: str = 'project'
    aksk_request_body: str = ''
    token: str = ''
    ca_file: str = ''
    insecure: bool = False
    timeout: float = 30.0


def endpoint(value,insecure=False):
    u = urlsplit(value)
    if not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        raise HCSError('服务 Endpoint 需为不含路径、参数或凭证的 HTTPS 地址（可含端口）')
    if u.scheme!='https' and not (insecure and u.scheme=='http'):
        raise HCSError('请使用 HTTPS Endpoint；仅调试时可明确开启 insecure 后使用 HTTP')
    return value.rstrip('/')


def aksk_body(profile):
    """不发明身份认证字段；默认空 body。可按现场契约提供不含秘密值的 JSON 模板。"""
    raw = profile.aksk_request_body
    if not raw.strip(): return b''
    if len(raw)>65536: raise HCSError('AK/SK 请求体模板过长')
    try: obj = json.loads(raw)
    except ValueError: raise HCSError('AK/SK 请求体模板必须为 JSON 对象') from None
    if not isinstance(obj,dict): raise HCSError('AK/SK 请求体模板必须为 JSON 对象')
    variables = {'${ak}':profile.ak,'${project_id}':profile.project_id,'${domain_id}':profile.domain_id}
    def resolve(value):
        if isinstance(value,dict):
            if any(k.lower() in ('sk','secret','secret_key','secretaccesskey','password') for k in value):
                raise HCSError('请求体不得包含 SK/密码字段；SK 仅用于本地签名')
            return {k:resolve(v) for k,v in value.items()}
        if isinstance(value,list): return [resolve(v) for v in value]
        if isinstance(value,str):
            if value in variables: return variables[value]
            if '${' in value: raise HCSError('仅支持完整字符串占位符 ${ak}、${project_id}、${domain_id}')
            if profile.sk and profile.sk in value: raise HCSError('请求体不能包含 SK')
        return value
    return json.dumps(resolve(obj),ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')


class HCSClient:
    def __init__(self,profile: HCSProfile,transport=None):
        self.profile = profile
        self._token,self._expires,self._scope_info = '',None,{}
        self._auth_lock = asyncio.Lock()
        verify = not profile.insecure
        if not profile.insecure and profile.ca_file:
            try: verify = ssl.create_default_context(cafile=profile.ca_file)
            except (OSError,ssl.SSLError): raise HCSError('HCS CA 文件无法加载') from None
        self.client = httpx.AsyncClient(verify=verify,timeout=profile.timeout,transport=transport,follow_redirects=False)

    async def close(self): await self.client.aclose()

    def clear_auth(self): self._token,self._expires,self._scope_info = '',None,{}

    def _aksk_request(self,url):
        p = self.profile
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',p.ak or '') or not p.sk:
            raise HCSError('请配置 HCS 专用 AK 和 SK')
        if len(p.sk)>4096: raise HCSError('HCS SK 格式无效')
        if p.token_scope not in ('project','domain'): raise HCSError('Token 作用域必须为 project 或 domain')
        id = p.project_id if p.token_scope=='project' else p.domain_id
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',id or ''):
            raise HCSError('AK/SK 认证需填写所选作用域的项目 ID 或账号 ID，不能用名称代替 ID')
        headers = {'X-Project-Id' if p.token_scope=='project' else 'X-Domain-Id':id}
        if p.security_token:
            if not all(32<=ord(c)<127 for c in p.security_token): raise HCSError('临时凭证 Security Token 格式无效')
            headers['X-Security-Token'] = p.security_token
        body = aksk_body(p)
        try: headers = sign_aksk('POST',url,body,p.ak,p.sk,headers)
        except Exception: raise HCSError('AK/SK 签名失败，请检查凭证格式与官方 SDK 依赖') from None
        return headers,body

    async def authenticate(self):
        p = self.profile
        if p.auth_mode=='token':
            if not p.token: raise HCSError('请配置 HCS Token')
            return p.token
        if p.auth_mode not in ('aksk','password'): raise HCSError('认证模式仅支持 aksk、password 或 token')
        async with self._auth_lock:
            now = datetime.now(timezone.utc)
            if self._token and self._expires and self._expires>now+timedelta(seconds=60): return self._token
            self.clear_auth()
            url = endpoint(p.iam_endpoint,p.insecure)+'/v3/auth/tokens'
            if p.auth_mode=='aksk':
                headers,body = self._aksk_request(url)
            else:
                if not all((p.username,p.password,p.domain_name,p.project_name)):
                    raise HCSError('密码模式需填写 IAM 用户名、密码、所属账号名称和项目名称')
                payload = {'auth':{'identity':{'methods':['password'],'password':{'user':{
                    'name':p.username,'password':p.password,'domain':{'name':p.domain_name}}}},
                    'scope':{'project':{'name':p.project_name}}}}
                body = json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
                headers = {'Content-Type':'application/json'}
            try:
                r = await self.client.post(url,headers=headers,content=body)
                if r.status_code not in (200,201):
                    if r.status_code in (301,302,303,307,308):
                        raise HCSError('IAM 返回重定向，已拒绝转发凭证，请配置最终服务 Endpoint')
                    if r.status_code in (401,403):
                        raise HCSError(f'IAM 认证失败（HTTP {r.status_code}）：检查 AK/SK、项目作用域、网关签名版本、访问权限和构建机 UTC 时间；不会自动回退密码')
                    raise HCSError(f'IAM 取 Token 失败（HTTP {r.status_code}），请核对现场网关的请求体要求和服务状态')
                token = r.headers.get('X-Subject-Token','')
                if not token: raise HCSError('IAM 响应缺少 X-Subject-Token；请确认是 IAM 服务而非 ManageOne 登录门户')
                response = r.json()
                data = response.get('token') if isinstance(response,dict) else None
                if not isinstance(data,dict): raise HCSError('IAM 响应缺少 token 对象')
                expiration = data.get('expires_at')
                expires = datetime.fromisoformat(expiration.replace('Z','+00:00')) if expiration else None
                if expires and expires.tzinfo is None: expires = expires.replace(tzinfo=timezone.utc)
                if expires and expires<=now+timedelta(seconds=5): raise HCSError('IAM 返回的 Token 已过期或即将过期，请确认云端及构建机时间')
                scope_type = p.token_scope if p.auth_mode=='aksk' else 'project'
                scope = data.get(scope_type) or {}
                if not isinstance(scope,dict): raise HCSError('IAM Token 作用域响应格式错误')
                expected = p.project_id if scope_type=='project' else p.domain_id
                if expected and scope.get('id')!=expected:
                    raise HCSError('IAM 返回的 Token 作用域与配置不一致或缺失，已拒绝缓存和使用')
                self._token,self._expires,self._scope_info = token,expires,{'type':scope_type,'id':scope.get('id','')}
                return token
            except httpx.HTTPError: raise HCSError('IAM 连接失败，请检查网络、Endpoint 和 CA 证书') from None
            except (ValueError,TypeError,AttributeError) as exc:
                if isinstance(exc,HCSError): raise
                raise HCSError('IAM 响应 JSON 或 Token 过期时间格式异常') from None

    async def read(self,service,path,params=None):
        """只读内部扩展点，不对前端暴露任意 API 代理。"""
        p = self.profile
        if not p.enabled: raise HCSError('HCS 连接未启用')
        if service not in ('iam','swr','cce','ecs'): raise HCSError('未注册的 HCS 服务')
        if not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path or '\\' in path:
            raise HCSError('API 路径格式无效')
        base = endpoint(getattr(p,service+'_endpoint'),p.insecure)
        for attempt in range(2):
            token = await self.authenticate()
            headers = {'X-Auth-Token':token}
            if p.auth_mode=='aksk' and p.token_scope=='domain':
                if p.domain_id: headers['X-Domain-Id'] = p.domain_id
            elif p.project_id: headers['X-Project-Id'] = p.project_id
            try: r = await self.client.get(base+path,headers=headers,params=params)
            except httpx.HTTPError: raise HCSError('HCS API 连接失败') from None
            if r.status_code==401 and p.auth_mode in ('aksk','password') and attempt==0:
                self.clear_auth()
                continue
            if not 200<=r.status_code<300: raise HCSError(f'HCS API 返回 HTTP {r.status_code}')
            try: return r.json()
            except ValueError: raise HCSError('HCS API 未返回有效 JSON') from None
        raise HCSError('HCS Token 无效')

    async def test(self):
        await self.authenticate()
        if self.profile.auth_mode=='token':
            return {'ok':True,'auth_mode':'token','message':'预置 Token 已装载，但未验证其有效性；需只读服务接口联调。','validated':False}
        method = 'AK/SK 签名换 Token' if self.profile.auth_mode=='aksk' else '密码换 Token'
        return {'ok':True,'auth_mode':self.profile.auth_mode,'message':method+'成功；尚未验证 SWR/CCE/ECS 服务权限。',
            'validated':True,'scope':self._scope_info,'expires_at':self._expires.isoformat() if self._expires else None}
