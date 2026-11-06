"""部署适配器注册表。SSH 仅占位，避免未确认运行环境就远程执行。"""
from dataclasses import asdict
from .base import Deployer
from .webhook import WebhookDeployer
from .kubernetes import KubernetesDeployer

class SSHDeployer(Deployer):
    type, label = 'ssh', 'SSH / Docker Compose（预留）'
    available = False
    description = '仅预留扩展点，未实现远程命令、凭证校验或主机操作。'
    async def test(self,*a,**k): raise ValueError('SSH 适配器尚未实现')
    async def deploy(self,*a,**k): raise ValueError('SSH 适配器尚未实现')

PROVIDERS = {}
def register(provider):
    PROVIDERS[provider.type] = provider

def get_provider(type):
    if type not in PROVIDERS: raise ValueError('不支持的部署类型')
    return PROVIDERS[type]

def list_providers():
    return [dict(type=p.type,label=p.label,description=p.description,available=p.available,
        fields=[asdict(f) for f in p.fields]) for p in PROVIDERS.values()]

for provider in (WebhookDeployer(),KubernetesDeployer(),SSHDeployer()): register(provider)
