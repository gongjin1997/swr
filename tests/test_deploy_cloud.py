import asyncio
import hashlib
import hmac
import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from swr_builder.config import Settings
from swr_builder.main import create_app
from swr_builder.security import hash_password
from swr_builder.cloud import HCSClient,HCSProfile,HCSError
from tests.test_accounts_settings import HEAD,login


def setup_app(tmp_path,handler,hcs_handler=None):
    s=Settings();s.build.data_dir=str(tmp_path/'data');s.server.admin_password='AdminPass123'
    app=create_app(s,deploy_transport=httpx.MockTransport(handler),hcs_transport=httpx.MockTransport(hcs_handler or handler))
    return s,app


def seed_job(app,user=None):
    from swr_builder.apps import normalize_app
    store=app.state.store
    a=store.create_app(normalize_app({'name':'demo','repository':'demo','base_image':'repo/base:1'}))
    id=store.create_job(app_id=a['id'],app_name='demo',image='swr.example/org/demo:1.0.1',status='success',
        digest='sha256:'+'ab'*32,created_at='2026-10-23T09:00:00+00:00',app_snapshot=a,
        created_by=(user or store.get_user_by_name('admin'))['id'],new_tag='1.0.1')
    return a,id


def wait_deploy(c,id):
    for _ in range(100):
        r=c.get('/api/deployments/'+str(id)).json()
        if r['status'] in ('success','failed'):return r
        time.sleep(.03)
    raise AssertionError('部署超时')


def test_webhook_hmac_encryption_auto_and_roles(tmp_path):
    seen=[]
    def handler(r):
        seen.append(r)
        assert r.headers['X-SWR-Signature']=='sha256='+hmac.new(b'sign-secret',r.content,hashlib.sha256).hexdigest()
        return httpx.Response(202,json={'accepted':True})
    s,app=setup_app(tmp_path,handler)
    with TestClient(app,headers=HEAD) as c:
        login(c);a,j=seed_job(app)
        target=c.post('/api/deploy/targets',json={'name':'test-gateway','type':'webhook','environment':'test','config':{
            'url':'https://gateway.example/deploy','secret':'sign-secret','headers':'{"Authorization":"Bearer sensitive"}'},
            'app_ids':[a['id']],'auto_deploy':False}).json()
        assert target['id'] and 'sign-secret' not in json.dumps(target) and 'sensitive' not in json.dumps(target)
        saved=app.state.store.get_deploy_target(target['id']);assert saved['config']['secret'].startswith('enc:v2:')
        assert c.post('/api/deploy/targets/'+str(target['id'])+'/test').status_code==200
        assert c.post('/api/deployments',json={'target_id':target['id'],'job_id':j}).status_code==410
        # v1.2 must not let legacy build events bypass service authorization and approval.
        c.portal.call(app.state.ctx.events.emit,'build.succeeded',{'job':app.state.store.get_job(j)})
        assert app.state.store.list_deployments()[1]==0
        assert len(seen)==1  # explicit connection test only
        viewer=app.state.store.create_user(username='viewer1',role='viewer',password_hash=hash_password('Viewer123'),enabled=1,must_change_password=0)
        login(c,'viewer1','Viewer123')
        assert c.get('/api/deploy/targets').status_code==403
        assert c.post('/api/deployments',json={'target_id':target['id'],'job_id':j}).status_code==403
        assert c.post('/api/deploy/targets/'+str(target['id'])+'/test').status_code==403


def test_target_validation_and_failure(tmp_path):
    s,app=setup_app(tmp_path,lambda r:httpx.Response(500,json={'password':'do-not-log-response'}))
    with TestClient(app,headers=HEAD) as c:
        login(c);a,j=seed_job(app)
        assert c.post('/api/deploy/targets',json={'name':'ssh','type':'ssh','config':{}}).status_code==400
        assert c.post('/api/deploy/targets',json={'name':'auto','type':'webhook','config':{'url':'https://x.example'},'auto_deploy':True}).status_code==400
        assert c.post('/api/deploy/targets',json={'name':'bad','type':'webhook','config':{'url':'file:///etc/passwd'}}).status_code==400
        unbound=c.post('/api/deploy/targets',json={'name':'unbound','type':'webhook','config':{'url':'https://x.example'}}).json()
        denied=c.post('/api/deployments',json={'target_id':unbound['id'],'job_id':j})
        assert denied.status_code==410
        t=c.post('/api/deploy/targets',json={'name':'failure','type':'webhook','config':{'url':'https://x.example'},
            'app_ids':[a['id']]}).json()
        assert c.post('/api/deployments',json={'target_id':t['id'],'job_id':j}).status_code==410
        s.general.deploy_enabled=False
        assert c.post('/api/deployments',json={'target_id':t['id'],'job_id':j}).status_code==403


def test_kubernetes_patch_and_rollout(tmp_path):
    state={'image':'old','generation':1,'replicas':1,'annotations':{},'uid':'deployment-uid','hpa_changed':False}
    def handler(r):
        assert r.headers['authorization']=='Bearer kube-secret'
        assert r.url.path=='/apis/apps/v1/namespaces/default/deployments/demo'
        if r.method=='PATCH':
            assert r.headers['content-type']=='application/strategic-merge-patch+json'
            b=json.loads(r.content)
            state['image']=b['spec']['template']['spec']['containers'][0]['image']
            state['annotations']=b['spec']['template']['metadata']['annotations'];state['generation']=2
        elif state['image']!='old' and not state['hpa_changed']:
            state['generation']=3;state['replicas']=2;state['hpa_changed']=True
        replicas=state['replicas']
        return httpx.Response(200,json={'metadata':{'generation':state['generation'],'uid':state['uid']},
            'spec':{'replicas':replicas,'template':{'metadata':{'annotations':state['annotations']},'spec':{
                'containers':[{'name':'app','image':state['image']}]}}},'status':{'observedGeneration':state['generation'],
            'updatedReplicas':replicas,'availableReplicas':replicas,'replicas':replicas}})
    s,app=setup_app(tmp_path,handler)
    with TestClient(app,headers=HEAD) as c:
        login(c);a,j=seed_job(app)
        t=c.post('/api/deploy/targets',json={'name':'cluster','type':'kubernetes','config':{'api_server':'https://kube.example',
            'token':'kube-secret','namespace':'default','deployment':'demo','container':'app'},'app_ids':[a['id']]}).json()
        assert c.post('/api/deploy/targets/'+str(t['id'])+'/test').status_code==200
        assert c.post('/api/deployments',json={'target_id':t['id'],'job_id':j}).status_code==410
        assert state['image']=='old'


def test_kubernetes_name_validation():
    from swr_builder.deploy.kubernetes import KubernetesDeployer
    provider=KubernetesDeployer()
    config={'api_server':'https://kube.example','token':'token','namespace':'a','deployment':'api.prod',
        'container':'web'}
    assert provider.validate(config)['namespace']=='a'
    for namespace in ('a.b','A','-bad','bad-','x'*64):
        with pytest.raises(ValueError):
            provider.validate({**config,'namespace':namespace})


def test_cluster_namespace_adoption_review_and_scoped_visibility(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/version'):
            return httpx.Response(200,json={'gitVersion':'v1.29.1'})
        if request.url.path=='/api/v1/namespaces/dev':
            return httpx.Response(200,json={'metadata':{'name':'dev'}})
        if request.url.path.endswith('/selfsubjectaccessreviews'):
            return httpx.Response(201,json={'status':{'allowed':True}})
        return httpx.Response(404)
    s,app=setup_app(tmp_path,handler)
    with TestClient(app,headers=HEAD) as c:
        login(c)
        created=c.post('/api/clusters',json={'name':'test-cluster','server':'https://kube.example:6443',
            'auth_type':'sa_token','credential':{'token':'kube-secret','ca_pem':''}})
        assert created.status_code==201,created.text
        assert 'kube-secret' not in created.text
        cluster_id=created.json()['id']
        bad_policy=c.post('/api/namespaces',json={'name':'bad','cluster_id':cluster_id,'tier':'dev','policy':{
            'freeze':{'enabled':True,'until':'not-a-date','reason':'freeze'}}})
        assert bad_policy.status_code==400
        bad_zone=c.post('/api/namespaces',json={'name':'bad-zone','cluster_id':cluster_id,'tier':'dev','policy':{
            'deploy_windows':[{'days':[1],'start':'09:00','end':'17:00','tz':'Invalid/Zone'}]}})
        assert bad_zone.status_code==400
        bad_days=c.post('/api/namespaces',json={'name':'bad-days','cluster_id':cluster_id,'tier':'dev','policy':{
            'deploy_windows':[{'days':[0],'start':'09:00','end':'17:00','tz':'UTC'}]}})
        assert bad_days.status_code==400
        bad_window=c.post('/api/namespaces',json={'name':'bad-window','cluster_id':cluster_id,'tier':'dev','policy':{
            'deploy_windows':[{'days':[1],'start':'09:00','end':'09:00','tz':'UTC'}]}})
        assert bad_window.status_code==400
        encrypted=app.state.store.get_cluster_record(cluster_id)['credential_encrypted']
        assert encrypted.startswith('enc:v2:') and 'kube-secret' not in encrypted
        assert c.post(f'/api/clusters/{cluster_id}/test',json={}).json()['version']=='v1.29.1'
        ns=c.post('/api/namespaces',json={'name':'dev','cluster_id':cluster_id,'tier':'dev','policy':{
            'allowed_registry_orgs':['team-a'],'service_types':['ClusterIP'],'allow_auto_deploy':False}})
        assert ns.status_code==201,ns.text
        namespace_id=ns.json()['id']
        review=c.post(f'/api/namespaces/{namespace_id}/review',json={})
        assert review.status_code==200 and review.json()['ok']
        assert len([r for r in calls if r.url.path.endswith('/selfsubjectaccessreviews')])>10
        project=c.post('/api/projects',json={'slug':'ops-team','name':'Ops Team','registry_orgs':[]}).json()
        linked=c.put(f"/api/projects/{project['id']}/namespaces/{namespace_id}",json={})
        assert linked.status_code==200 and linked.json()['namespace_ids']==[namespace_id]
        user=app.state.store.create_user(username='nsviewer',role='viewer',password_hash=hash_password('Viewer123'),
            enabled=1,must_change_password=0)
        grant=c.post('/api/bindings',json={'subject_type':'user','subject_id':user['id'],'role':'namespace_viewer',
            'scope_type':'namespace','scope_id':namespace_id})
        assert grant.status_code==201,grant.text
        login(c,'nsviewer','Viewer123')
        assert [x['id'] for x in c.get('/api/namespaces').json()['items']]==[namespace_id]
        assert c.get(f'/api/namespaces/{namespace_id}').status_code==200
        assert c.get('/api/clusters').status_code==403


@pytest.mark.parametrize('kube_auth', ['sa_token', 'client_cert'])
@pytest.mark.parametrize('tier', ['prod', 'dev'])
def test_service_revisions_secrets_idempotent_deployment_and_approval(tmp_path,kube_auth,tier):
    deployment_state={'image':'old','generation':1,'annotations':{},'uid':'payments-uid'}
    seen_auth=[]
    def handler(request):
        if request.url.path.endswith('/version'):
            return httpx.Response(200,json={'gitVersion':'v1.29.1'})
        if request.url.path in ('/api/v1/namespaces/prod','/api/v1/namespaces/dev'):
            return httpx.Response(200,json={'metadata':{'name':'prod'}})
        if request.url.path.endswith('/selfsubjectaccessreviews'):
            return httpx.Response(201,json={'status':{'allowed':True}})
        if request.url.path.endswith('/deployments/payments'):
            seen_auth.append(request.headers.get('authorization'))
            if kube_auth=='sa_token':
                assert request.headers.get('authorization')=='Bearer cluster-token'
            else:
                assert 'authorization' not in request.headers
            if request.method=='PATCH':
                body=json.loads(request.content)
                deployment_state['image']=body['spec']['template']['spec']['containers'][0]['image']
                deployment_state['annotations']=body['spec']['template']['metadata']['annotations']
                deployment_state['generation']=2
            replicas=1
            return httpx.Response(200,json={'metadata':{'uid':deployment_state['uid'],
                'generation':deployment_state['generation']},'spec':{'replicas':replicas,
                'template':{'metadata':{'annotations':deployment_state['annotations']},
                    'spec':{'containers':[{'name':'api','image':deployment_state['image']}]}}},
                'status':{'observedGeneration':deployment_state['generation'],'updatedReplicas':replicas,
                    'availableReplicas':replicas,'replicas':replicas}})
        return httpx.Response(404)
    s,app=setup_app(tmp_path,handler)
    with TestClient(app,headers=HEAD) as c:
        login(c)
        project=c.post('/api/projects',json={'slug':'payments','name':'Payments','registry_orgs':[{'namespace':'team','base_prefixes':['api']}]}).json()
        app_row=app.state.store.create_app({'name':'payments-api','organization':'team','repository':'api/payments'},project['id'])
        if kube_auth=='sa_token':
            cluster_data={'name':'prod-cluster','server':'https://kube.example',
                'auth_type':'sa_token','credential':{'token':'cluster-token'}}
        else:
            import base64,yaml
            from tests.test_kubeconfig import _client_certificate_data
            cert,key=_client_certificate_data()
            cluster_data={'name':'prod-cluster','config':yaml.safe_dump({
                'apiVersion':'v1','kind':'Config','current-context':'prod',
                'clusters':[{'name':'cluster','cluster':{'server':'https://kube.example'}}],
                'users':[{'name':'deployer','user':{'client-certificate-data':cert,'client-key-data':key}}],
                'contexts':[{'name':'prod','context':{'cluster':'cluster','user':'deployer'}}]})}
        cluster=c.post('/api/clusters',json=cluster_data)
        assert cluster.status_code==201,cluster.text
        cluster=cluster.json()
        namespace=c.post('/api/namespaces',json={'name':'prod','cluster_id':cluster['id'],'tier':tier,'policy':{
            'require_approval':True,'min_approvals':1,'allowed_registry_orgs':['team']}}).json()
        c.put(f"/api/projects/{project['id']}/namespaces/{namespace['id']}",json={})
        service=c.post('/api/services',json={'name':'payments','project_id':project['id'],'namespace_id':namespace['id'],
            'app_id':app_row['id'],'spec':{'mode':'image_only','target':{'kind':'Deployment','name':'payments','container':'api'}}})
        assert service.status_code==201,service.text
        service=service.json();revision=service['current_revision_id']
        updated=c.put(f"/api/services/{service['id']}",json={'base_revision':revision,'spec':service['spec']})
        assert updated.status_code==200,updated.text
        next_revision=updated.json()['revision']['id']
        conflict=c.put(f"/api/services/{service['id']}",json={'base_revision':revision,'spec':service['spec']})
        assert conflict.status_code==409
        secret=c.put(f"/api/services/{service['id']}/secrets/password",json={'value':'super-secret-value'})
        assert secret.status_code==200 and 'super-secret-value' not in secret.text
        assert 'super-secret-value' not in app.state.store.get_revision(service['id'],secret.json()['version'])['spec'].__str__()
        submitter=app.state.store.create_user(username='deployer1',role='viewer',enabled=1,must_change_password=0,
            password_hash=hash_password('Deployer123'))
        grant=c.post('/api/bindings',json={'subject_type':'user','subject_id':submitter['id'],'role':'namespace_owner',
            'scope_type':'namespace','scope_id':namespace['id']})
        assert grant.status_code==201,grant.text
        job=app.state.store.create_job(app_id=app_row['id'],app_name='payments-api',project_id=project['id'],
            image='registry.example/team/api/payments:1.0.1',digest='sha256:'+'cd'*32,status='success',
            new_tag='1.0.1',created_at='2026-11-06T00:00:00+00:00')
        login(c,'deployer1','Deployer123')
        headers={'Idempotency-Key':'payments-deploy-0001'}
        policy=app.state.store.get_namespace(namespace['id'])['policy']
        policy['freeze']={'enabled':True,'until':'2099-01-01T00:00:00Z','reason':'maintenance'}
        app.state.store.update_namespace(namespace['id'],tier,policy)
        blocked=c.post(f"/api/services/{service['id']}/deployments",
            headers={'Idempotency-Key':'payments-deploy-frozen'},json={'job_id':job,'revision_id':next_revision})
        assert blocked.status_code==403 and 'freeze' in blocked.json()['detail']
        policy['freeze']={'enabled':False,'until':None,'reason':''}
        app.state.store.update_namespace(namespace['id'],tier,policy)
        response=c.post(f"/api/services/{service['id']}/deployments",headers=headers,json={'job_id':job,'revision_id':next_revision})
        assert response.status_code==202,response.text
        deployment=response.json()
        assert deployment['status']=='pending_approval' and deployment['digest']=='sha256:'+'cd'*32
        assert app.state.store.get_deployment(deployment['id'])['image']=='registry.example/team/api/payments@sha256:'+'cd'*32
        replay=c.post(f"/api/services/{service['id']}/deployments",headers=headers,json={'job_id':job,'revision_id':next_revision})
        assert replay.status_code==202 and replay.json()['id']==deployment['id']
        mismatch=c.post(f"/api/services/{service['id']}/deployments",headers=headers,json={'job_id':job,'revision_id':revision})
        assert mismatch.status_code==409
        mismatch_remark=c.post(f"/api/services/{service['id']}/deployments",headers=headers,json={
            'job_id':job,'revision_id':next_revision,'remark':'different payload'})
        assert mismatch_remark.status_code==409
        assert c.get('/api/auth/me').json()['username']=='deployer1'
        assert str(app.state.store.get_deployment(deployment['id'])['created_by'])==str(submitter['id'])
        assert c.post(f"/api/deployments/{deployment['id']}/approve",json={'comment':'self'}).status_code==404
        approver=app.state.store.create_user(username='approver1',role='viewer',enabled=1,must_change_password=0,
            password_hash=hash_password('Approver123'))
        c.cookies.clear();login(c)
        grant=c.post('/api/bindings',json={'subject_type':'user','subject_id':approver['id'],
            'role':'deployment_approver','scope_type':'global','scope_id':0})
        assert grant.status_code==201,grant.text
        c.cookies.clear();login(c,'approver1','Approver123')
        approved=c.post(f"/api/deployments/{deployment['id']}/approve",json={'comment':'reviewed'})
        if tier=='prod':
            assert approved.status_code==404
            c.cookies.clear();login(c)
            approved=c.post(f"/api/deployments/{deployment['id']}/approve",json={'comment':'platform administrator reviewed'})
        assert approved.status_code==200,approved.text
        assert approved.json()['status']=='queued'
        assert len(approved.json()['approvals'])==1
        c.cookies.clear();login(c)
        completed=wait_deploy(c,deployment['id'])
        assert completed['status']=='success',completed
        assert deployment_state['image']=='registry.example/team/api/payments@sha256:'+'cd'*32
        assert len(seen_auth)>=2


@pytest.mark.parametrize('freeze_during_read',[False,True])
def test_service_worker_rechecks_policy_before_kubernetes_write(tmp_path,freeze_during_read):
    state={'image':'old','generation':1,'annotations':{},'uid':'deploy-uid','patches':0,
        'namespace_id':None,'policy_changed':False}
    app=None
    def handler(request):
        nonlocal app
        if request.url.path.endswith('/version'):
            return httpx.Response(200,json={'gitVersion':'v1.29.1'})
        if request.url.path=='/api/v1/namespaces/dev':
            return httpx.Response(200,json={'metadata':{'name':'dev'}})
        if request.url.path.endswith('/selfsubjectaccessreviews'):
            return httpx.Response(201,json={'status':{'allowed':True}})
        if request.url.path.endswith('/deployments/payments'):
            if request.method=='PATCH':
                state['patches']+=1
                body=json.loads(request.content)
                state['image']=body['spec']['template']['spec']['containers'][0]['image']
                state['annotations']=body['spec']['template']['metadata']['annotations']
                state['generation']=2
            elif freeze_during_read and not state['policy_changed']:
                policy=app.state.store.get_namespace(state['namespace_id'])['policy']
                policy['freeze']={'enabled':True,'until':'2099-01-01T00:00:00Z','reason':'changed during read'}
                app.state.store.update_namespace(state['namespace_id'],'dev',policy)
                state['policy_changed']=True
            return httpx.Response(200,json={'metadata':{'uid':state['uid'],'generation':state['generation']},
                'spec':{'replicas':1,'template':{'metadata':{'annotations':state['annotations']},'spec':{
                    'containers':[{'name':'api','image':state['image']}]}}},
                'status':{'observedGeneration':state['generation'],'updatedReplicas':1,'availableReplicas':1,'replicas':1}})
        return httpx.Response(404)
    s,app=setup_app(tmp_path,handler)
    with TestClient(app,headers=HEAD) as c:
        login(c)
        project=c.post('/api/projects',json={'slug':'worker','name':'Worker','registry_orgs':[{'namespace':'team','base_prefixes':['api']}]}).json()
        app_row=app.state.store.create_app({'name':'payments-api','organization':'team','repository':'api/payments'},project['id'])
        cluster=c.post('/api/clusters',json={'name':'worker-cluster','server':'https://kube.example',
            'auth_type':'sa_token','credential':{'token':'cluster-secret'}}).json()
        namespace=c.post('/api/namespaces',json={'name':'dev','cluster_id':cluster['id'],'tier':'dev','policy':{
            'require_approval':False,'allowed_registry_orgs':['team']}}).json()
        state['namespace_id']=namespace['id']
        c.put(f"/api/projects/{project['id']}/namespaces/{namespace['id']}",json={})
        service=c.post('/api/services',json={'name':'payments','project_id':project['id'],'namespace_id':namespace['id'],
            'app_id':app_row['id'],'spec':{'mode':'image_only','target':{'kind':'Deployment','name':'payments','container':'api'}}}).json()
        job=app.state.store.create_job(app_id=app_row['id'],app_name='payments-api',project_id=project['id'],
            image='registry.example/team/api/payments:2.0.0',digest='sha256:'+'ef'*32,status='success',
            new_tag='2.0.0',created_at='2026-11-06T00:00:00+00:00')
        response=c.post(f"/api/services/{service['id']}/deployments",headers={'Idempotency-Key':'worker-deploy-0001'},
            json={'job_id':job})
        assert response.status_code==202,response.text
        deployment=wait_deploy(c,response.json()['id'])
        expected='registry.example/team/api/payments@sha256:'+'ef'*32
        if freeze_during_read:
            assert deployment['status']=='failed',deployment
            assert state['image']=='old' and state['patches']==0
            assert state['policy_changed']
            return
        assert deployment['status']=='success',deployment
        assert deployment['image']==expected and state['image']==expected
        assert state['patches']==1
        assert state['annotations']['swr-builder/deployment-id']==str(deployment['id'])
        assert app.state.store.get_service(service['id'])['deployed_revision_id']==service['current_revision_id']


def test_hcs_iam_test_and_encrypted_config(tmp_path):
    calls=[]
    def handler(r):
        calls.append(r)
        assert r.url.path=='/v3/auth/tokens'
        b=json.loads(r.content)
        assert b['auth']['identity']['password']['user']['domain']['name']=='tenant'
        assert b['auth']['scope']['project']['name']=='project1'
        return httpx.Response(201,headers={'X-Subject-Token':'sensitive-token'},json={'token':{'expires_at':'2099-01-01T00:00:00Z','project':{'id':'p1'}}})
    s,app=setup_app(tmp_path,handler)
    values={'hcs.auth_mode':'password','hcs.iam_endpoint':'https://iam.example','hcs.username':'test','hcs.password':'iam-secret',
        'hcs.domain_name':'tenant','hcs.project_name':'project1','hcs.project_id':'p1'}
    with TestClient(app,headers=HEAD) as c:
        login(c)
        r=c.post('/api/cloud/hcs/test',json={'values':values});assert r.status_code==200 and r.json()['validated']
        assert 'sensitive-token' not in r.text
        assert not s.hcs.password # 测试表单不落库
        assert c.put('/api/settings',json={'values':values}).status_code==200
        assert 'iam-secret' not in c.get('/api/settings').text
        assert app.state.store.get_settings()['hcs.password'].startswith('enc:v2:')
        assert c.get('/api/cloud/hcs/capabilities').json()['enabled'] is False


def test_hcs_read_token_refresh_and_no_redirect():
    async def run():
        counts={'auth':0,'reads':0}
        def handler(r):
            if r.method=='POST':
                counts['auth']+=1
                return httpx.Response(201,headers={'X-Subject-Token':'token'+str(counts['auth'])},json={'token':{'expires_at':'2099-01-01T00:00:00Z','project':{'id':'p1'}}})
            counts['reads']+=1
            assert r.headers['X-Project-Id']=='p1'
            return httpx.Response(401) if counts['reads']==1 else httpx.Response(200,json={'items':[]})
        p=HCSProfile(auth_mode='password',enabled=True,iam_endpoint='https://iam.example',cce_endpoint='https://cce.example',username='user',password='secret',domain_name='tenant',project_name='project',project_id='p1')
        client=HCSClient(p,httpx.MockTransport(handler))
        try:
            assert await client.read('cce','/approved/path')=={'items':[]}
            assert counts=={'auth':2,'reads':2}
            with pytest.raises(HCSError):await client.read('cce','//evil.example')
            with pytest.raises(HCSError):await client.read('unregistered','/path')
        finally:await client.close()
    asyncio.run(run())
