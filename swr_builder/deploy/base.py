"""部署适配器协议。扩展时实现 Deployer 并注册，不侵入构建流程。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict
from urllib.parse import urlsplit

@dataclass
class FieldSpec:
    key: str
    label: str
    type: str = 'str'
    required: bool = False
    default: Any = ''
    help: str = ''

@dataclass
class DeployContext:
    deployment_id: int
    image: str
    app: Dict[str, Any]
    job: Dict[str, Any]
    config: Dict[str, Any]
    operator: str
    log: Any
    transport: Any = None

@dataclass
class DeployResult:
    message: str

class Deployer(ABC):
    type = ''
    label = ''
    description = ''
    available = True
    fields = []

    def validate(self, config):
        out = {}
        for f in self.fields:
            value = config.get(f.key, f.default)
            if f.type == 'bool':
                if not isinstance(value,bool): raise ValueError(f'{f.label} 需要布尔值')
            elif f.type == 'int':
                if isinstance(value,bool): raise ValueError(f'{f.label} 需要数字')
                n = float(value)
                if not n.is_integer(): raise ValueError(f'{f.label} 需要整数')
                value = int(n)
                if not 1 <= value <= 3600: raise ValueError(f'{f.label} 范围为 1–3600')
            else:
                if not isinstance(value,str) or len(value)>65536: raise ValueError(f'{f.label} 内容无效')
                value = value.strip() if f.type != 'secret' else value
            if f.required and not value: raise ValueError(f'请填写{f.label}')
            out[f.key] = value
        return out

    @abstractmethod
    async def test(self, config, transport=None): pass

    @abstractmethod
    async def deploy(self, ctx: DeployContext) -> DeployResult: pass

    async def rollback(self, ctx):
        raise NotImplementedError('回滚能力为扩展预留；当前版本请选择历史成功构建手动部署。')

def validate_url(value):
    u = urlsplit(value)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.fragment:
        raise ValueError('地址必须是 HTTP/HTTPS URL，不支持内嵌用户名、密码或片段')
    return value.rstrip('/')
