"""CCE / Kubernetes Deployment 镜像更新适配器。"""
import asyncio
import ssl
import time
import re
from urllib.parse import quote
import httpx
from .base import Deployer, FieldSpec, DeployResult, validate_url

_DNS1123_LABEL_RE = re.compile(r'^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$')
_DNS1123_SUBDOMAIN_RE = re.compile(
    r'^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?)*$'
)


class KubernetesDeployer(Deployer):
    type, label = 'kubernetes', 'Kubernetes'
    description = '更新已存在的 Deployment 指定容器镜像，可等待滚动发布完成。不创建集群或资源；镜像拉取凭证需预先配置。'
    fields = [FieldSpec('api_server','API Server',required=True),FieldSpec('token','ServiceAccount Token','secret',True),
        FieldSpec('ca_cert','CA 证书 PEM','secret'),FieldSpec('insecure','跳过 TLS 校验','bool',False,False),
        FieldSpec('namespace','命名空间',required=True,default='default'),FieldSpec('deployment','Deployment',required=True),
        FieldSpec('container','容器名',required=True),FieldSpec('wait_rollout','等待滚动发布','bool',False,True),
        FieldSpec('timeout','发布超时（秒）','int',False,300)]

    def validate(self,c):
        c = super().validate(c)
        c['api_server'] = validate_url(c['api_server'])
        if not _DNS1123_LABEL_RE.fullmatch(c['namespace']):
            raise ValueError('namespace 必须是长度不超过 63 的 DNS-1123 label')
        if len(c['deployment']) > 253 or not _DNS1123_SUBDOMAIN_RE.fullmatch(c['deployment']):
            raise ValueError('deployment 必须是有效的 DNS 子域名')
        if not _DNS1123_LABEL_RE.fullmatch(c['container']):
            raise ValueError('container 必须是长度不超过 63 的 DNS-1123 label')
        self.tls(c)
        return c

    def tls(self,c):
        if c['insecure']: return False
        if c['ca_cert']:
            try: return ssl.create_default_context(cadata=c['ca_cert'])
            except ssl.SSLError: raise ValueError('CA 证书 PEM 格式错误') from None
        return True

    def client(self,c,transport):
        return httpx.AsyncClient(verify=self.tls(c),headers={'Authorization':'Bearer '+c['token']},
            timeout=min(c['timeout'],30),transport=transport,follow_redirects=False)

    def url(self,c):
        return c['api_server'] + '/apis/apps/v1/namespaces/' + quote(c['namespace'],safe='') + '/deployments/' + quote(c['deployment'],safe='')

    async def request(self,client,method,url,**kw):
        try:
            r = await client.request(method,url,**kw)
            if not 200 <= r.status_code < 300: raise ValueError(f'Kubernetes API 返回 HTTP {r.status_code}，请检查 RBAC 权限和资源名称')
            return r.json()
        except (httpx.HTTPError,KeyError): raise ValueError('Kubernetes API 连接失败，请检查网络、凭证和证书') from None

    def container_exists(self,d,c):
        if not any(x.get('name')==c['container'] for x in d.get('spec',{}).get('template',{}).get('spec',{}).get('containers',[])):
            raise ValueError('指定的容器在 Deployment 中不存在')

    async def test(self,c,transport=None):
        async with self.client(c,transport) as client:
            d = await self.request(client,'GET',self.url(c))
        self.container_exists(d,c)
        return 'Deployment 和容器可读取；实际部署还需要 patch 权限'

    async def deploy(self,ctx):
        c = ctx.config
        async with self.client(c,ctx.transport) as client:
            d = await self.request(client,'GET',self.url(c))
            self.container_exists(d,c)
            body = {'spec':{'template':{'metadata':{'annotations':{'swr-builder/deployment-id':str(ctx.deployment_id)}},
                'spec':{'containers':[{'name':c['container'],'image':ctx.image}]}}}}
            result = await self.request(client,'PATCH',self.url(c),json=body,headers={'Content-Type':'application/strategic-merge-patch+json'})
            initial_uid = d.get('metadata',{}).get('uid')
            result_metadata = result.get('metadata',{})
            expected_uid = result_metadata.get('uid') or initial_uid
            if initial_uid and result_metadata.get('uid') and initial_uid != result_metadata.get('uid'):
                raise ValueError('Deployment 在提交期间已被替换，无法确认发布结果')
            deployment_id = str(ctx.deployment_id)
            ctx.log.info('已提交 Deployment 镜像更新：'+ctx.image)
            if not c['wait_rollout']: return DeployResult('镜像更新已提交（未等待滚动发布）')
            deadline = time.monotonic()+c['timeout']
            while time.monotonic()<deadline:
                d = await self.request(client,'GET',self.url(c))
                spec,st = d.get('spec',{}),d.get('status',{})
                metadata = d.get('metadata',{})
                if expected_uid and metadata.get('uid') != expected_uid:
                    raise ValueError('Deployment 已被替换，已停止等待')
                containers = spec.get('template',{}).get('spec',{}).get('containers',[])
                if not any(x.get('name')==c['container'] and x.get('image')==ctx.image for x in containers):
                    raise ValueError('发布被其他操作覆盖，已停止等待')
                annotations = spec.get('template',{}).get('metadata',{}).get('annotations',{})
                if annotations.get('swr-builder/deployment-id') != deployment_id:
                    raise ValueError('发布已被其他操作覆盖，已停止等待')
                for condition in st.get('conditions',[]):
                    if condition.get('reason')=='ProgressDeadlineExceeded': raise ValueError('Kubernetes 滚动发布超时')
                replicas = spec.get('replicas',1)
                generation = metadata.get('generation')
                generation_observed = generation is None or st.get('observedGeneration',0) >= generation
                if generation_observed and all(st.get(k,0)==replicas for k in ('updatedReplicas','availableReplicas','replicas')):
                    ctx.log.info('滚动发布完成')
                    return DeployResult('滚动发布完成')
                await asyncio.sleep(2)
        raise ValueError('等待滚动发布超时；镜像更新已提交，请到集群确认，不会自动回滚')
