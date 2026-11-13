"""部署管理：服务发布 worker 的生命周期（见 worker.py），以及旧部署目标的保存、脱敏展示与连接测试。

旧部署目标自 v1.2 起不再执行发布（接口返回 410），只保留迁移核对与连接测试。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from . import get_provider
from .worker import ServiceDeploymentWorker


class DeployManager:
    def __init__(self, settings, store, box, events, transport=None, registry=None):
        self.settings, self.store, self.box, self.events, self.transport = settings, store, box, events, transport
        self.service_worker = ServiceDeploymentWorker(settings, store, box, events, transport, registry)

    def start(self) -> None:
        self.store.fail_unfinished_deployments("平台重启，部署状态未知，请确认目标环境后重新操作")
        self.service_worker.start()

    async def stop(self) -> None:
        await self.service_worker.stop()

    def log_path(self, deployment_id: int):
        return self.settings.data_path / "deployments" / str(deployment_id) / "deploy.log"

    # ------------------------------------------------------------------ 旧部署目标
    def decode(self, target: Dict[str, Any]) -> Dict[str, Any]:
        target = deepcopy(target)
        provider = get_provider(target["type"])
        for f in provider.fields:
            if f.type == "secret":
                target["config"][f.key] = self.box.decrypt(target["config"].get(f.key, ""))
        return target

    def view(self, target: Dict[str, Any], manage: bool = False) -> Dict[str, Any]:
        """脱敏展示：无管理权限时不返回配置；有权限时敏感字段只返回是否已设置。"""
        target = deepcopy(target)
        if not manage:
            target.pop("config", None)
            return target
        provider = get_provider(target["type"])
        target["secrets_set"] = {}
        for f in provider.fields:
            if f.type == "secret":
                target["secrets_set"][f.key] = bool(target["config"].get(f.key))
                target["config"][f.key] = ""
        return target

    def save_target(self, data: Dict[str, Any], target_id: Optional[int] = None) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("部署目标格式错误")
        old = self.store.get_deploy_target(target_id) if target_id else None
        if target_id and not old:
            raise ValueError("部署目标不存在")
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 80:
            raise ValueError("目标名称需为 1–80 字符")
        same = self.store.find_deploy_target_by_name(name)
        if same and same["id"] != target_id:
            raise ValueError("部署目标名称已存在")
        provider = get_provider(data.get("type", ""))
        if not provider.available:
            raise ValueError("该适配器仅预留，尚未实现")
        if old and old["type"] != provider.type:
            raise ValueError("不能更换已有目标类型，请新建目标")
        if not isinstance(data.get("config", {}), dict):
            raise ValueError("目标配置需为对象")
        config = dict(data.get("config") or {})
        clear = data.get("clear_secrets") or []
        for f in provider.fields:  # 敏感字段留空表示保留原值，除非明确清空
            if f.type == "secret" and not config.get(f.key) and old and f.key not in clear:
                config[f.key] = self.box.decrypt(old["config"].get(f.key, ""))
        config = provider.validate(config)
        for f in provider.fields:
            if f.type == "secret":
                config[f.key] = self.box.encrypt(config[f.key])
        app_ids = data.get("app_ids") or []
        if not isinstance(app_ids, list) or any(type(x) is not int or not self.store.get_app(x) for x in app_ids):
            raise ValueError("绑定的应用不存在")
        enabled, auto = data.get("enabled", True), data.get("auto_deploy", False)
        if not isinstance(enabled, bool) or not isinstance(auto, bool):
            raise ValueError("开关需为布尔值")
        if auto:
            raise ValueError("v1.2 已停用旧目标自动部署，请使用受 namespace policy 管控的服务发布")
        target = {"name": name, "type": provider.type, "config": config,
                  "environment": str(data.get("environment", ""))[:80], "enabled": enabled, "auto_deploy": False,
                  "app_ids": sorted(set(app_ids))}
        return self.store.save_deploy_target(target, target_id)

    def target(self, target_id: int) -> Dict[str, Any]:
        target = self.store.get_deploy_target(target_id)
        if not target:
            raise ValueError("部署目标不存在")
        return target

    async def test_target(self, target_id: int) -> str:
        if not self.settings.general.deploy_enabled:
            raise ValueError("部署模块已关闭")
        target = self.decode(self.target(target_id))
        return await get_provider(target["type"]).test(target["config"], self.transport)
