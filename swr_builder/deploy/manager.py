"""部署任务、目标凭证、事件订阅与执行日志。默认手动部署，自动部署需明确开启并绑定应用。"""
import asyncio
import json
import re
import ssl
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from copy import deepcopy
import httpx
from ..auth import has_perm
from ..authz import has_project_permission, has_namespace_permission
from ..builder import JobLog
from ..registry import parse_image_ref
from ..store import utcnow
from ..policy import deployment_policy_block
from . import get_provider
from .base import DeployContext

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class DeployManager:
    def __init__(self,settings,store,box,events,transport=None):
        self.settings,self.store,self.box,self.events,self.transport = settings,store,box,events,transport
        self.tasks, self.locks = {}, {}
        self.worker_id='service-deployer'
        self.worker_task=None
        events.subscribe('build.succeeded',self.on_build)

    def start(self):
        self.store.fail_unfinished_deployments('平台重启，部署状态未知，请确认目标环境后重新操作')
        self.store.mark_interrupted_service_deployments_unknown('worker interrupted during Kubernetes write; verify cluster state before resubmitting')
        self.worker_task=asyncio.create_task(self._service_worker())

    async def stop(self):
        if self.worker_task:
            self.worker_task.cancel()
            await asyncio.gather(self.worker_task,return_exceptions=True)
            self.worker_task=None
        for t in list(self.tasks.values()): t.cancel()
        if self.tasks: await asyncio.gather(*list(self.tasks.values()),return_exceptions=True)

    def log_path(self,id): return self.settings.data_path / 'deployments' / str(id) / 'deploy.log'

    async def _service_worker(self):
        last_expiry_check=0.0
        while True:
            now=asyncio.get_running_loop().time()
            if now-last_expiry_check>=30:
                cutoff=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat(timespec='seconds')
                self.store.expire_pending_approvals(cutoff)
                last_expiry_check=now
            deployment=self.store.claim_next_service_deployment(self.worker_id,lease_seconds=4500)
            if deployment is None:
                await asyncio.sleep(.5)
                continue
            await self._run_service_deployment(deployment)

    async def _run_service_deployment(self,deployment):
        deployment_id=deployment['id'];token=deployment['fencing_token']
        service=self.store.get_service(deployment['service_id'])
        job=self.store.get_job(deployment['job_id'])
        revision=self.store.get_revision(deployment['service_id'],deployment['revision_id'])
        namespace=self.store.get_namespace(deployment['namespace_id'])
        cluster=self.store.get_cluster_record(namespace['cluster_id']) if namespace else None
        credential={}
        if cluster:
            try:credential=json.loads(self.box.decrypt(cluster['credential_encrypted']))
            except (ValueError,TypeError,json.JSONDecodeError):credential={}
        log=JobLog(self.log_path(deployment_id),[credential.get('token','')])
        write_started=False
        try:
            if not service or not job or not revision or not namespace or not cluster:
                raise ValueError('部署快照引用的服务、构建、修订、命名空间或集群已不存在')
            actor=self.store.get_user(int(deployment['created_by'])) if deployment.get('created_by') else None
            if not actor or not actor['enabled'] or actor['must_change_password'] or not has_namespace_permission(
                self.store,actor,namespace['id'],'service.deploy'):
                raise ValueError('发布发起人已停用或不再拥有该命名空间的发布权限')
            current_policy=namespace['policy']
            blocked=deployment_policy_block(current_policy)
            if blocked:raise ValueError(blocked)
            required=max(1,int(current_policy.get('min_approvals',1))) if current_policy.get('require_approval') or namespace['tier']=='prod' else 0
            if required and self.store.count_approvals(deployment_id,'approved')<required:
                self.store.mark_service_deployment_pending_approval(deployment_id,self.worker_id,token)
                log.info('发布策略已变化，任务退回审批队列')
                return
            image_ref=parse_image_ref(deployment['image'])
            image_org=image_ref.repo.split('/',1)[0] if image_ref else ''
            if not image_org or image_org not in current_policy.get('allowed_registry_orgs',[]):
                raise ValueError('部署镜像组织不符合当前命名空间策略')
            if revision['spec'].get('mode')!='image_only':
                raise ValueError('managed 清单渲染与 Server-Side Apply 尚未在此 worker 阶段启用')
            if cluster['auth_type']!='sa_token' or not credential.get('token'):
                raise ValueError('集群凭据类型不受当前 worker 支持')
            target=revision['spec']['target']
            api_server=cluster['server'].rstrip('/')
            path='/apis/apps/v1/namespaces/'+quote(namespace['name'],safe='')+'/deployments/'+quote(target['name'],safe='')
            verify=ssl.create_default_context(cadata=credential['ca_pem']) if credential.get('ca_pem') else True
            headers={'Authorization':'Bearer '+credential['token']}
            async with httpx.AsyncClient(verify=verify,timeout=30,transport=self.transport,follow_redirects=False) as client:
                if not self.store.service_deployment_lease_valid(deployment_id,self.worker_id,token):
                    raise RuntimeError('deployment lease expired before Kubernetes access')
                response=await client.get(api_server+path,headers=headers)
                if response.status_code!=200:raise ValueError(f'读取 Deployment 失败，Kubernetes API HTTP {response.status_code}')
                current=response.json();spec=current.get('spec',{});template=spec.get('template',{})
                containers=template.get('spec',{}).get('containers',[])
                if not any(item.get('name')==target['container'] for item in containers):
                    raise ValueError('目标 Deployment 中不存在配置的容器')
                latest_namespace=self.store.get_namespace(namespace['id'])
                if not latest_namespace:
                    raise ValueError('目标命名空间已不存在')
                namespace=latest_namespace
                current_policy=namespace['policy']
                actor=self.store.get_user(int(deployment['created_by'])) if deployment.get('created_by') else None
                if not actor or not actor['enabled'] or actor['must_change_password'] or not has_namespace_permission(
                    self.store,actor,namespace['id'],'service.deploy'):
                    raise ValueError('发布发起人已停用或不再拥有该命名空间的发布权限')
                blocked=deployment_policy_block(current_policy)
                if blocked:raise ValueError(blocked)
                required=max(1,int(current_policy.get('min_approvals',1))) if current_policy.get('require_approval') or namespace['tier']=='prod' else 0
                if required and self.store.count_approvals(deployment_id,'approved')<required:
                    if not self.store.mark_service_deployment_pending_approval(deployment_id,self.worker_id,token):
                        raise RuntimeError('deployment lease lost while returning to approval')
                    log.info('发布策略已变化，任务退回审批队列')
                    return
                if not image_org or image_org not in current_policy.get('allowed_registry_orgs',[]):
                    raise ValueError('部署镜像组织不符合当前命名空间策略')
                if not self.store.service_deployment_lease_valid(deployment_id,self.worker_id,token):
                    raise RuntimeError('deployment lease expired before Kubernetes write')
                body={'spec':{'template':{'metadata':{'annotations':{'swr-builder/deployment-id':str(deployment_id),
                    'swr-builder/revision':str(revision['id'])}},'spec':{'containers':[
                    {'name':target['container'],'image':deployment['image']}]}}}}
                write_started=True
                response=await client.patch(api_server+path,headers={**headers,'Content-Type':'application/strategic-merge-patch+json'},json=body)
                if response.status_code not in (200,201):
                    if 400<=response.status_code<500:write_started=False
                    raise ValueError(f'更新 Deployment 失败，Kubernetes API HTTP {response.status_code}')
                updated=response.json();uid=updated.get('metadata',{}).get('uid') or current.get('metadata',{}).get('uid')
                if not self.store.update_service_deployment(deployment_id,self.worker_id,token,status='verifying'):
                    raise RuntimeError('deployment lease lost after Kubernetes write')
                deadline=asyncio.get_running_loop().time()+min(namespace['policy']['rollout_timeout'],3600)
                while asyncio.get_running_loop().time()<deadline:
                    if not self.store.service_deployment_lease_valid(deployment_id,self.worker_id,token):
                        raise RuntimeError('deployment lease expired while waiting for rollout')
                    response=await client.get(api_server+path,headers=headers)
                    if response.status_code!=200:raise RuntimeError(f'发布后读取 Deployment 失败，HTTP {response.status_code}')
                    current=response.json();metadata=current.get('metadata',{});pod_template=current.get('spec',{}).get('template',{})
                    if uid and metadata.get('uid')!=uid:raise ValueError('Deployment 已被删除并重建，发布结果需人工确认')
                    running_containers=pod_template.get('spec',{}).get('containers',[])
                    image=next((item.get('image') for item in running_containers if item.get('name')==target['container']),None)
                    annotations=pod_template.get('metadata',{}).get('annotations',{})
                    if image!=deployment['image'] or annotations.get('swr-builder/deployment-id')!=str(deployment_id):
                        raise ValueError('Deployment 模板已被其他操作覆盖')
                    status=current.get('status',{});desired=current.get('spec',{}).get('replicas',1)
                    generation=metadata.get('generation')
                    observed=generation is None or status.get('observedGeneration',0)>=generation
                    if observed and all(status.get(field,0)==desired for field in ('updatedReplicas','availableReplicas','replicas')):
                        break
                    if any(condition.get('reason')=='ProgressDeadlineExceeded' for condition in status.get('conditions',[])):
                        raise ValueError('Kubernetes rollout ProgressDeadlineExceeded')
                    await asyncio.sleep(2)
                else:
                    raise ValueError('Kubernetes rollout timed out; inspect workload before retrying')
            if self.store.complete_service_deployment(deployment_id,self.worker_id,token,service['id'],revision['id'],
                    'Deployment rollout completed'):
                log.info('Deployment 已达到期望镜像和可用副本数')
        except asyncio.CancelledError:
            if self.store.service_deployment_lease_valid(deployment_id,self.worker_id,token):
                state='unknown' if write_started else 'cancelled'
                self.store.update_service_deployment(deployment_id,self.worker_id,token,status=state,
                    error='worker stopped during external operation; verify cluster state' if write_started else 'worker stopped before cluster write',
                    finished_at=utcnow(),lease_owner=None,lease_until=None)
            raise
        except Exception as exc:
            state='unknown' if write_started else 'failed'
            message=str(exc) if isinstance(exc,ValueError) else ('外部操作结果无法确认，请核对集群后处理' if state=='unknown' else '部署 worker 内部错误')
            log.error(message)
            self.store.update_service_deployment(deployment_id,self.worker_id,token,status=state,error=message[:1000],
                finished_at=utcnow(),lease_owner=None,lease_until=None)
        finally:
            log.close()
            result=self.store.get_deployment(deployment_id)
            if result and result['status'] in {'success','failed','cancelled','unknown','expired'}:
                self.store.add_audit('service-worker','deploy.'+result['status'],str(deployment_id))
                self.events.emit('deploy.succeeded' if result['status']=='success' else 'deploy.failed',{'deployment':result})

    def decode(self,t):
        t = deepcopy(t)
        p = get_provider(t['type'])
        for f in p.fields:
            if f.type == 'secret': t['config'][f.key] = self.box.decrypt(t['config'].get(f.key,''))
        return t

    def view(self,t,manage=False):
        t = deepcopy(t)
        if not manage:
            t.pop('config',None)
            return t
        p = get_provider(t['type'])
        t['secrets_set'] = {}
        for f in p.fields:
            if f.type == 'secret':
                t['secrets_set'][f.key] = bool(t['config'].get(f.key))
                t['config'][f.key] = ''
        return t

    def save_target(self,data,id=None):
        old = self.store.get_deploy_target(id) if id else None
        if id and not old: raise ValueError('部署目标不存在')
        name = str(data.get('name','')).strip()
        if not name or len(name)>80: raise ValueError('目标名称需为 1–80 字符')
        same = self.store.find_deploy_target_by_name(name)
        if same and same['id']!=id: raise ValueError('部署目标名称已存在')
        p = get_provider(data.get('type',''))
        if not p.available: raise ValueError('该适配器仅预留，尚未实现')
        if old and old['type']!=p.type: raise ValueError('不能更换已有目标类型，请新建目标')
        if not isinstance(data.get('config',{}),dict): raise ValueError('目标配置需为对象')
        config = dict(data.get('config') or {})
        clear = data.get('clear_secrets') or []
        for f in p.fields:
            if f.type == 'secret' and not config.get(f.key) and old and f.key not in clear:
                config[f.key] = self.box.decrypt(old['config'].get(f.key,''))
        config = p.validate(config)
        for f in p.fields:
            if f.type == 'secret': config[f.key] = self.box.encrypt(config[f.key])
        app_ids = data.get('app_ids') or []
        if not isinstance(app_ids,list) or any(type(x) is not int or not self.store.get_app(x) for x in app_ids): raise ValueError('绑定的应用不存在')
        enabled,auto = data.get('enabled',True),data.get('auto_deploy',False)
        if not isinstance(enabled,bool) or not isinstance(auto,bool): raise ValueError('开关需为布尔值')
        if auto: raise ValueError('v1.2 已停用旧目标自动部署，请使用受 namespace policy 管控的服务发布规则')
        if auto and not app_ids: raise ValueError('自动部署必须明确绑定应用，不能使用全部应用')
        target = {'name':name,'type':p.type,'config':config,'environment':str(data.get('environment',''))[:80],
            'enabled':enabled,'auto_deploy':auto,'app_ids':list(set(app_ids))}
        return self.store.save_deploy_target(target,id)

    def target(self,id):
        t = self.store.get_deploy_target(id)
        if not t: raise ValueError('部署目标不存在')
        return t

    async def test_target(self,id):
        if not self.settings.general.deploy_enabled: raise ValueError('部署模块已关闭')
        t = self.decode(self.target(id))
        return await get_provider(t['type']).test(t['config'],self.transport)

    def submit(self,target_id,job_id,user,remark='',trigger='manual'):
        if not self.settings.general.deploy_enabled: raise ValueError('部署模块已关闭')
        t = self.target(target_id)
        if not t['enabled']: raise ValueError('目标已停用')
        job = self.store.get_job(job_id)
        if not job or job['status']!='success' or not job.get('image'): raise ValueError('只能部署已成功推送的构建')
        if not t['app_ids'] or job['app_id'] not in t['app_ids']:
            raise ValueError('部署目标必须显式绑定该构建所属应用')
        if not has_project_permission(self.store,user,job['project_id'],'deploy.run'):
            raise ValueError('当前用户没有该项目的部署权限')
        digest = job.get('digest')
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            raise ValueError('成功构建记录缺少有效的 SHA-256 摘要，不能部署')
        image = job['image'].split('@', 1)[0] + '@' + digest
        t = self.decode(t)
        id = self.store.create_deployment(target_id=target_id,target_name=t['name'],target_type=t['type'],environment=t.get('environment',''),
            app_id=job['app_id'],app_name=job['app_name'],job_id=job_id,project_id=job['project_id'],image=image,status='queued',
            trigger=trigger,operator=user.get('display_name') or user['username'],created_by=user['id'],remark=str(remark)[:200])
        task = asyncio.create_task(self.run(id,t,job,user,image))
        self.tasks[id] = task
        task.add_done_callback(lambda _:self.tasks.pop(id,None))
        self.store.add_audit(user['username'],'deploy.submit',str(id),f'target={target_id},job={job_id},trigger={trigger}')
        return id

    async def run(self,id,t,job,user,image):
        p = get_provider(t['type'])
        log = JobLog(self.log_path(id),[t['config'].get(f.key,'') for f in p.fields if f.type=='secret'])
        try:
            async with self.locks.setdefault(t['id'],asyncio.Lock()):
                self.store.update_deployment(id,status='running',started_at=utcnow())
                log.info('开始部署到：'+t['name'])
                ctx = DeployContext(id,image,job.get('app_snapshot') or {},job,t['config'],user['username'],log,self.transport)
                result = await p.deploy(ctx)
                self.store.update_deployment(id,status='success',message=result.message,finished_at=utcnow())
        except asyncio.CancelledError:
            self.store.update_deployment(id,status='failed',error='平台停止，部署状态未知，请检查目标环境',finished_at=utcnow())
        except Exception as exc:
            # 不记录 HTTP 响应正文或凭证，适配器以安全消息反馈错误。
            message = str(exc) if isinstance(exc,ValueError) else '部署内部错误，请检查适配器配置'
            for f in p.fields:
                value = t['config'].get(f.key,'')
                if f.type=='secret' and value: message = message.replace(value,'******')
            log.error(message)
            self.store.update_deployment(id,status='failed',error=message[:1000],finished_at=utcnow())
        finally:
            log.close()
            d = self.store.get_deployment(id)
            self.store.add_audit(user['username'],'deploy.'+d['status'],str(id))
            self.events.emit('deploy.succeeded' if d['status']=='success' else 'deploy.failed',{'deployment':d})

    def on_build(self,event):
        return
