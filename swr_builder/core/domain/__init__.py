"""核心领域模型：部署单元（DeploymentUnit）。

对应 XAC 的 DU 概念，是部署的最小单位。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class DeploymentUnit:
    """部署单元（对应 XAC 的 DU）"""
    id: int
    application_id: int
    name: str
    namespace_id: int
    spec: Dict[str, Any]  # K8s 部署规格
    attributes: Dict[str, Any]  # DU 自身属性（对应 XAC DU_ATTR_T）

    # 关联
    resource_bindings: List[ResourceBinding]
    config_layers: List[ConfigLayer]

    @property
    def full_name(self) -> str:
        """完整名称：应用名.DU名"""
        return f"{self.application_id}.{self.name}"


@dataclass
class ResourceBinding:
    """资源绑定（对应 XAC DU_RESOURCE_T）"""
    du_id: int
    resource_id: int
    alias: str
    resource: Optional[Resource] = None  # 延迟加载


@dataclass
class Resource:
    """资源（对应 XAC RESOURCE_T）"""
    id: int
    type: str
    name: str
    scope_type: str  # platform, environment
    scope_id: int
    parent_id: Optional[int]
    attributes: Dict[str, Any]
    version: int

    # 父链（延迟加载）
    parent: Optional[Resource] = None
    children: List[Resource] = None


@dataclass
class ConfigLayer:
    """配置层（对应 XAC APP_CONFIG_T）"""
    scope_type: str  # platform, project, environment, du
    scope_id: int
    config_type: str  # global(6/7), du-global(4/5), default(2/3), env, secret_env
    entries: Dict[str, ConfigEntry]
    version: int


@dataclass
class ConfigEntry:
    """配置条目"""
    key: str
    value: Any
    locked: bool = False
    secret: bool = False
    description: str = ""
