"""真实签名逻辑 + 模拟 IAM 服务，不声称连接真实 Stack。"""
import asyncio
from datetime import datetime,timedelta,timezone
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import quote,unquote
import httpx
import pytest
from fastapi.testclient import TestClient
from swr_builder.cloud import HCSClient,HCSProfile,HCSError
from swr_builder.cloud.signing import sign_aksk
from tests.test_accounts_settings import HEAD,login
from tests.test_deploy_cloud import setup_app

AK='TESTAK1234567890'
SK='fake-SK-only-for-automated-test'
PROJECT='project123'


def profile(**kw):
    return HCSProfile(**dict({'enabled':True,'iam_endpoint':'https://iam.example:26335','cce_endpoint':'https://cce.example',
        'ak':AK,'sk':SK,'project_id':PROJECT},**kw))


def verify_signature(request):
    """服务端独立重算规范请求，不能调用被测 SDK 签名器作自身断言。"""
    auth=request.headers['Authorization']
    assert auth.startswith('SDK-HMAC-SHA256 Access='+AK+', ')
    signed=auth.split('SignedHeaders=',1)[1].split(',',1)[0]
    signature=auth.rsplit('Signature=',1)[1]
    names=signed.split(';')
    assert names==sorted(names) and {'host','content-type','x-sdk-date'}.issubset(names)
    date=request.headers['X-Sdk-Date']
    datetime.strptime(date,'%Y%m%dT%H%M%SZ')
    path='/'.join(quote(v,safe='~') for v in unquote(request.url.path).split('/'))
    if not path.endswith('/'):path+='/'
    assert not request.url.query
    canonical_headers=''.join(name+':'+request.headers[name].strip()+'\n' for name in names)
    canonical='\n'.join([request.method,path,'',canonical_headers,signed,hashlib.sha256(request.content).hexdigest()])
    to_sign='SDK-HMAC-SHA256\n'+date+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
    expected=hmac.new(SK.encode(),to_sign.encode(),hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature,expected)
    assert SK.encode() not in request.content and SK not in str(request.headers)
    return names


def response(token='issued-token',project=PROJECT,domain=None,expires='2099-01-01T00:00:00Z'):
    data={'expires_at':expires}
    if project is not None:data['project']={'id':project}
    if domain is not None:data['domain']={'id':domain}
    return httpx.Response(201,headers={'X-Subject-Token':token},json={'token':data})


def test_official_sdk_fixed_signing_vectors():
    vectors=json.loads((Path(__file__).parent/'signing_vectors.json').read_text())
    for c in vectors['cases']:
        headers={k:v for k,v in c['headers'].items() if k not in ('Host','Content-Type','Content-Length','X-Sdk-Date')}
        got=sign_aksk('POST',c['url'],c['body'].encode(),vectors['ak'],vectors['sk'],headers,'20261023T120000Z')
        assert got['Authorization']==c['authorization']


def test_aksk_exchange_cache_and_concurrency():
    async def run():
        calls=[]
        async def handler(r):
            names=verify_signature(r);calls.append(r)
            assert r.url.path=='/v3/auth/tokens' and r.method=='POST'
            assert r.headers['Host']=='iam.example:26335'
            assert r.headers['X-Project-Id']==PROJECT and 'x-project-id' in names
            assert r.content==b'' and r.headers['Content-Length']=='0'
            assert 'X-Auth-Token' not in r.headers
            await asyncio.sleep(.01)
            return response()
        client=HCSClient(profile(),httpx.MockTransport(handler))
        try:
            tokens=await asyncio.gather(*(client.authenticate() for _ in range(12)))
            assert set(tokens)=={'issued-token'} and len(calls)==1
            assert await client.authenticate()=='issued-token' and len(calls)==1
            client._expires=datetime.now(timezone.utc)+timedelta(seconds=30)
            assert await client.authenticate()=='issued-token' and len(calls)==2
        finally:await client.close()
    asyncio.run(run())


def test_aksk_json_body_and_temporary_credentials():
    async def run():
        def handler(r):
            names=verify_signature(r)
            assert r.headers['X-Security-Token']=='temporary-security-token'
            assert 'x-security-token' in names
            data=json.loads(r.content)
            assert data['auth']['scope']['project']['id']==PROJECT
            assert data['access_key']==AK and data['note']=='中文'
            assert 'password' not in data
            assert int(r.headers['Content-Length'])==len(r.content)
            return response()
        body='{"auth":{"scope":{"project":{"id":"${project_id}"}}},"access_key":"${ak}","note":"中文"}'
        client=HCSClient(profile(security_token='temporary-security-token',aksk_request_body=body),httpx.MockTransport(handler))
        try:assert await client.authenticate()=='issued-token'
        finally:await client.close()
    asyncio.run(run())


def test_domain_scoped_aksk_exchange():
    async def run():
        def handler(r):
            names=verify_signature(r)
            assert r.headers['X-Domain-Id']=='domain123' and 'x-domain-id' in names
            assert 'X-Project-Id' not in r.headers
            return response(project=None,domain='domain123')
        client=HCSClient(profile(token_scope='domain',domain_id='domain123'),httpx.MockTransport(handler))
        try:
            assert await client.authenticate()=='issued-token'
            assert client._scope_info=={'type':'domain','id':'domain123'}
        finally:await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('changes',[{'ak':''},{'sk':''},{'project_id':''},{'token_scope':'auto'},
    {'ak':'bad\r\nkey'},{'security_token':'bad\nvalue'},{'aksk_request_body':'not-json'},
    {'aksk_request_body':'[]'},{'aksk_request_body':'{"sk":"secret"}'},
    {'aksk_request_body':'{"key":"${sk}"}'},{'aksk_request_body':'{"key":"'+SK+'"}'}])
def test_invalid_aksk_config_never_sends_request(changes):
    async def run():
        def handler(r):raise AssertionError('invalid config must not reach network')
        client=HCSClient(profile(**changes),httpx.MockTransport(handler))
        try:
            with pytest.raises(HCSError):await client.authenticate()
        finally:await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('kind',['401','403','302','500','missing-header','bad-json','missing-scope','wrong-scope','expired'])
def test_token_failures_safe_no_password_fallback(kind):
    async def run():
        seen=[]
        def handler(r):
            verify_signature(r);seen.append(r)
            if kind.isdigit():return httpx.Response(int(kind),headers={'Location':'https://other.example'},text=SK)
            if kind=='missing-header':return httpx.Response(201,json={'token':{}})
            if kind=='bad-json':return httpx.Response(201,headers={'X-Subject-Token':'t'},text=SK)
            if kind=='missing-scope':return response(project=None)
            if kind=='wrong-scope':return response(project='another-project')
            return response(expires='2000-01-01T00:00:00Z')
        client=HCSClient(profile(username='do-not-fallback',password='do-not-fallback-password'),httpx.MockTransport(handler))
        try:
            with pytest.raises(HCSError) as e:await client.authenticate()
            assert SK not in str(e.value) and len(seen)==1
            assert not client._token and client._expires is None
        finally:await client.close()
    asyncio.run(run())


def test_aksk_read_401_refresh_once():
    async def run():
        calls={'auth':0,'read':0}
        def handler(r):
            if r.method=='POST':
                verify_signature(r);calls['auth']+=1
                return response(token='token'+str(calls['auth']))
            calls['read']+=1
            assert 'Authorization' not in r.headers
            assert r.headers['X-Auth-Token']=='token'+str(calls['auth'])
            return httpx.Response(401) if calls['read']==1 else httpx.Response(200,json={'items':[]})
        client=HCSClient(profile(),httpx.MockTransport(handler))
        try:
            assert await client.read('cce','/confirmed/read/path')=={'items':[]}
            assert calls=={'auth':2,'read':2}
        finally:await client.close()
    asyncio.run(run())


def test_aksk_test_api_masking_environment_lock_and_save(tmp_path):
    def handler(r):verify_signature(r);return response()
    settings,app=setup_app(tmp_path,handler)
    with TestClient(app,headers=HEAD) as c:
        login(c)
        assert settings.hcs.auth_mode=='aksk'
        values={'hcs.iam_endpoint':'https://iam.example:26335','hcs.ak':AK,'hcs.sk':SK,'hcs.project_id':PROJECT}
        r=c.post('/api/cloud/hcs/test',json={'values':values})
        assert r.status_code==200 and r.json()['validated'] and r.json()['auth_mode']=='aksk'
        assert 'issued-token' not in r.text and SK not in r.text and AK not in r.text
        assert not settings.hcs.sk # 候选表单测试不落库
        assert c.put('/api/settings',json={'values':values}).status_code==200
        page=c.get('/api/settings').text
        assert AK not in page and SK not in page
        assert app.state.store.get_settings()['hcs.sk'].startswith('enc:v2:')
        settings.env_locked['hcs.sk']='HCS_SK'
        assert c.put('/api/settings',json={'values':{'hcs.sk':'other'}}).status_code==400
        caps=c.get('/api/cloud/hcs/capabilities').json()
        assert any('AK/SK' in x['name'] and x['state']=='implemented' for x in caps['capabilities'])
