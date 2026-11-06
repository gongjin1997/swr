"""对接已有 CI/CD 或部署网关；HTTP 接受成功不代表工作负载已就绪。"""
import hashlib
import hmac
import json
import httpx
from .base import Deployer, FieldSpec, DeployResult, validate_url

class WebhookDeployer(Deployer):
    type, label = 'webhook', 'Webhook / CI-CD 网关'
    description = '发送镜像及构建信息到现有发布系统；成功状态仅表示网关已接受请求。测试也会发送 ping 事件，请使用支持该协议的接收端。'
    fields = [FieldSpec('url','Webhook URL',required=True),
        FieldSpec('headers','请求头（JSON）','secret',False,'','例如 {"Authorization":"Bearer ..."}；加密保存'),
        FieldSpec('secret','HMAC 签名密钥','secret'), FieldSpec('timeout','超时（秒）','int',False,30),
        FieldSpec('verify_tls','校验 TLS 证书','bool',False,True)]

    def validate(self,c):
        c = super().validate(c)
        c['url'] = validate_url(c['url'])
        try:
            headers = json.loads(c['headers'] or '{}')
            if not isinstance(headers,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in headers.items()): raise ValueError()
            if any('\n' in k+v or '\r' in k+v for k,v in headers.items()): raise ValueError()
        except (ValueError,TypeError): raise ValueError('请求头必须为字符串键值对的 JSON 对象')
        return c

    async def send(self,c,payload,transport):
        body = json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        headers = json.loads(c['headers'] or '{}')
        headers['Content-Type'] = 'application/json'
        if c['secret']:
            headers['X-SWR-Signature'] = 'sha256=' + hmac.new(c['secret'].encode(),body,hashlib.sha256).hexdigest()
        try:
            async with httpx.AsyncClient(verify=c['verify_tls'],timeout=c['timeout'],transport=transport,follow_redirects=False) as client:
                r = await client.post(c['url'],headers=headers,content=body)
            if not 200 <= r.status_code < 300: raise ValueError(f'Webhook 返回 HTTP {r.status_code}')
        except httpx.HTTPError: raise ValueError('Webhook 连接失败，请检查地址、网络和证书') from None

    async def test(self,config,transport=None):
        await self.send(config,{'event':'ping','source':'swr-image-builder'},transport)
        return 'ping 请求已被接收端接受'

    async def deploy(self,ctx):
        await self.send(ctx.config,{'event':'deploy','deployment_id':ctx.deployment_id,'image':ctx.image,
            'app':{'id':ctx.app.get('id'),'name':ctx.app.get('name')},'job_id':ctx.job['id'],
            'tag':ctx.job.get('new_tag'),'digest':ctx.job.get('digest'),'operator':ctx.operator},ctx.transport)
        ctx.log.info('Webhook 已接受部署请求；实际发布进度请在接收端确认。')
        return DeployResult('Webhook 请求已接受（不代表工作负载已就绪）')
