"""应用运行期上下文：create_app 组装的各个服务，路由通过依赖注入取用。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .auth import AuthService
from .builder import BuildManager
from .config import Settings
from .deploy.manager import DeployManager
from .events import EventBus
from .registry import RegistryClient
from .security import SecretBox
from .services.docker import DockerStatus
from .store import Store
from .sysconfig import SysConfig


@dataclass
class AppContext:
    settings: Settings
    store: Store
    box: SecretBox
    sysconfig: SysConfig
    registry: RegistryClient
    events: EventBus
    manager: BuildManager
    auth: AuthService
    deploy: DeployManager
    docker: DockerStatus
    registry_transport: Any = None
    deploy_transport: Any = None
    hcs_transport: Any = None
