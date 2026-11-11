"""服务目录：服务的新建/修改/删除、spec 校验、配置修订与对比、密钥版本、可发布的构建。

服务 = 某个项目的应用部署到项目已分配的某个命名空间中的一个 Deployment / 容器。
读取：命名空间上有 namespace.view，或项目上有 app.view；修改需要命名空间上的对应权限。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..access import Principal
from ..errors import Conflict, Forbidden, NotFound, NotImplementedYet
from ..security import SecretBox
from ..store import Store
from ..validation import DNS_LABEL_RE, reject_unknown, text_field
from .access import service_or_404
from .namespaces import effective_policy

SERVICE_RESPONSE_FIELDS = ("id", "name", "project_id", "namespace_id", "app_id", "current_revision_id",
                           "deployed_revision_id", "spec", "created_at", "updated_at")
SECRET_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}")
BUILD_FIELDS = ("id", "app_id", "project_id", "app_name", "status", "image", "digest", "new_tag", "created_at")
SPEC_KEYS = {"mode", "target", "workload", "container", "config_files", "service", "ingress", "hpa", "scheduling",
             "image_pull_secrets", "labels", "annotations"}
_DNS_SUBDOMAIN_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?)*")
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")


def service_response(service: Dict[str, Any]) -> Dict[str, Any]:
    return {key: service.get(key) for key in SERVICE_RESPONSE_FIELDS}


def normalize_service_spec(value: Any, namespace: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) - SPEC_KEYS:
        raise ValueError("服务 spec 包含未知字段")
    mode = value.get("mode")
    if mode == "image_only":
        target = value.get("target")
        if set(value) != {"mode", "target"} or not isinstance(target, dict) or set(target) != {"kind", "name", "container"}:
            raise ValueError("image_only spec 只允许 kind/name/container")
        if (target["kind"] != "Deployment" or not isinstance(target["name"], str)
                or not _DNS_SUBDOMAIN_RE.fullmatch(target["name"]) or len(target["name"]) > 253):
            raise ValueError("image_only Deployment 名称无效")
        if not isinstance(target["container"], str) or not _DNS_LABEL_RE.fullmatch(target["container"]):
            raise ValueError("容器名称无效")
        return value
    if mode != "managed":
        raise ValueError("mode 必须是 managed 或 image_only")
    policy = effective_policy(namespace["policy"])
    if "workload" in value:
        workload = value["workload"]
        if not isinstance(workload, dict) or set(workload) - {"kind", "replicas", "strategy", "progress_deadline_seconds",
                                                              "revision_history_limit", "termination_grace_period_seconds"}:
            raise ValueError("workload 配置包含未知字段")
        if workload.get("kind", "Deployment") != "Deployment":
            raise ValueError("当前只支持 Deployment workload")
        replicas = workload.get("replicas", 1)
        if type(replicas) is not int or not 0 <= replicas <= policy["max_replicas"]:
            raise ValueError("副本数超出命名空间策略")
    if "service" in value:
        service = value["service"]
        if not isinstance(service, dict) or set(service) - {"enabled", "type", "ports"}:
            raise ValueError("Service 配置包含未知字段")
        if service.get("type", "ClusterIP") not in policy["service_types"]:
            raise ValueError("Service 类型不符合命名空间策略")
    if "image_pull_secrets" in value:
        names = value["image_pull_secrets"]
        if not isinstance(names, list) or any(name not in policy["image_pull_secrets"] for name in names):
            raise ValueError("imagePullSecrets 不符合命名空间策略")
    if "hpa" in value and value["hpa"].get("enabled") and not policy["hpa_enabled"]:
        raise ValueError("命名空间策略未启用 HPA")
    if "ingress" in value and value["ingress"].get("enabled") and not policy["ingress"].get("enabled"):
        raise ValueError("命名空间策略未启用 Ingress")
    return value


def revision_diff(old: Any, new: Any) -> List[Dict[str, Any]]:
    """逐键比较两个 spec，返回 [{path, old, new}]；path 形如 /target/name。"""
    changes: List[Dict[str, Any]] = []

    def compare(path: str, left: Any, right: Any) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                compare(path + "/" + str(key), left.get(key), right.get(key))
        elif left != right:
            changes.append({"path": path or "/", "old": left, "new": right})

    compare("", old, new)
    return changes


# ------------------------------------------------------------------ 列表与展示
class Lookup:
    """列表里反复用到的项目、应用、命名空间，每个 ID 只查询一次。"""

    def __init__(self, store: Store):
        self.store = store
        self._cache: Dict[Tuple[str, Any], Optional[Dict[str, Any]]] = {}

    def _get(self, kind: str, key: Any, loader) -> Optional[Dict[str, Any]]:
        if (kind, key) not in self._cache:
            self._cache[(kind, key)] = loader(key) if key else None
        return self._cache[(kind, key)]

    def project(self, project_id: Any) -> Optional[Dict[str, Any]]:
        return self._get("project", project_id, self.store.get_project)

    def app(self, app_id: Any) -> Optional[Dict[str, Any]]:
        return self._get("app", app_id, self.store.get_app)

    def namespace(self, namespace_id: Any) -> Optional[Dict[str, Any]]:
        return self._get("namespace", namespace_id, self.store.get_namespace)

    def service_view(self, row: Dict[str, Any]) -> Dict[str, Any]:
        ns, project, app = self.namespace(row["namespace_id"]), self.project(row["project_id"]), self.app(row["app_id"])
        return dict(service_response(row), project_name=project["name"] if project else "",
                    app_name=app["name"] if app else "", cluster_name=ns["cluster_name"] if ns else "",
                    namespace_name=ns["name"] if ns else "", tier=ns["tier"] if ns else "")


def list_services(store: Store, me: Principal, *, project_id: Optional[int] = None,
                  namespace_id: Optional[int] = None, app_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """项目上有 app.view 或命名空间上有 namespace.view 的服务都可见。"""
    projects, namespaces = me.project_ids("app.view"), me.namespace_ids("namespace.view")
    rows = (store.list_services() if projects is None or namespaces is None
            else store.list_services(projects, namespaces))
    rows = [s for s in rows if (project_id is None or s["project_id"] == project_id)
            and (namespace_id is None or s["namespace_id"] == namespace_id)
            and (app_id is None or s["app_id"] == app_id)]
    lookup = Lookup(store)
    return [lookup.service_view(row) for row in rows]


def get_service(store: Store, me: Principal, service_id: int) -> Dict[str, Any]:
    return Lookup(store).service_view(service_or_404(store, me, service_id))


def require_namespace(me: Principal, service: Dict[str, Any], permission: str, message: str) -> None:
    if not me.can_namespace(service["namespace_id"], permission):
        raise Forbidden(message)


def writable_service(store: Store, me: Principal, service_id: int, permission: str, message: str) -> Dict[str, Any]:
    """读取需要在命名空间上操作的服务：看不到按不存在处理，看得到但没有权限返回 403。"""
    service = service_or_404(store, me, service_id, allow_project_view=False)
    require_namespace(me, service, permission, message)
    return service


# ------------------------------------------------------------------ 新建 / 修订 / 删除
def create_service(store: Store, me: Principal, data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("服务请求格式错误")
    reject_unknown(data, {"name", "project_id", "namespace_id", "app_id", "spec"}, "服务请求包含未知字段")
    name = text_field(data, "name", 63).lower()
    if not DNS_LABEL_RE.fullmatch(name):
        raise ValueError("服务名称必须是 DNS-1123 label（小写字母、数字和 -）")
    project_id, namespace_id, app_id = data.get("project_id"), data.get("namespace_id"), data.get("app_id")
    if any(type(v) is not int for v in (project_id, namespace_id, app_id)):
        raise ValueError("project_id、namespace_id、app_id 必须是整数")
    namespace, app = store.get_namespace(namespace_id), store.get_app(app_id)
    if (not namespace or not app or app["project_id"] != project_id
            or namespace_id not in store.project_namespace_ids(project_id)):
        raise NotFound("服务所属资源不存在：应用必须属于该项目，命名空间必须已分配给该项目")
    if not me.can_namespace(namespace_id, "service.config"):
        raise Forbidden("没有命名空间服务配置权限")
    spec = normalize_service_spec(data.get("spec"), namespace)
    return service_response(store.create_service(name, project_id, namespace_id, app_id, spec, me.id))


def update_service_spec(store: Store, me: Principal, service_id: int, data: Any
                        ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """基于 base_revision 创建新的配置修订（乐观锁：base_revision 不是当前修订时返回 409）。"""
    if not isinstance(data, dict):
        raise ValueError("服务更新请求格式错误")
    reject_unknown(data, {"base_revision", "spec"}, "服务更新请求包含未知字段")
    service = writable_service(store, me, service_id, "service.config", "没有命名空间服务配置权限")
    base_revision = data.get("base_revision")
    if type(base_revision) is not int:
        raise ValueError("base_revision 必须是整数")
    spec = normalize_service_spec(data.get("spec"), store.get_namespace(service["namespace_id"]))
    updated, revision = store.update_service_revision(service_id, base_revision, spec, me.id)
    return service_response(updated), revision


def delete_service(store: Store, me: Principal, service_id: int) -> None:
    writable_service(store, me, service_id, "service.delete", "没有服务删除权限")
    if not store.delete_service(service_id):
        raise Conflict("存在进行中的发布，不能删除服务记录")


def set_secret(store: Store, box: SecretBox, me: Principal, service_id: int, key: str, data: Any) -> Dict[str, Any]:
    """密钥加密保存为新版本，并生成新的配置修订；值不会回显。"""
    value = data.get("value") if isinstance(data, dict) else None
    if not isinstance(data, dict) or set(data) != {"value"} or not isinstance(value, str) or not value \
            or len(value) > 65536:
        raise ValueError("value 必须是 1–65536 字符的非空字符串")
    if not SECRET_KEY_RE.fullmatch(key):
        raise ValueError("Secret key 格式无效")
    writable_service(store, me, service_id, "secret.manage", "没有服务密钥管理权限")
    revision = store.create_secret_revision(service_id, key, box.encrypt(value), me.id)
    if revision is None:
        raise NotFound("服务不存在")
    return {"key": key, "version": revision["secret_versions"][key], "updated_at": revision["created_at"]}


def render(store: Store, me: Principal, service_id: int, data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("渲染请求格式错误")
    reject_unknown(data, {"revision_id", "dry_run"}, "渲染请求包含未知字段")
    service = writable_service(store, me, service_id, "service.config", "没有服务配置权限")
    revision_id, dry_run = data.get("revision_id"), data.get("dry_run", True)
    if type(revision_id) is not int or not isinstance(dry_run, bool):
        raise ValueError("revision_id 或 dry_run 格式错误")
    revision = store.get_revision(service_id, revision_id)
    if not revision:
        raise NotFound("服务修订不存在")
    if revision["spec"].get("mode") != "image_only":
        raise NotImplementedYet("managed 服务的清单渲染尚未开放")
    warnings = ["Kubernetes dry-run 尚未执行；清单渲染与现场 API 校验将在部署 worker 阶段完成。"] if dry_run else []
    return {"revision_id": revision_id, "valid": True, "resources": [{"kind": "Deployment", "name": service["name"]}],
            "warnings": warnings}


# ------------------------------------------------------------------ 修订与可发布构建
def list_revisions(store: Store, me: Principal, service_id: int) -> List[Dict[str, Any]]:
    service_or_404(store, me, service_id)
    return store.list_revisions(service_id)


def get_revision(store: Store, me: Principal, service_id: int, revision_id: int) -> Dict[str, Any]:
    service_or_404(store, me, service_id)
    revision = store.get_revision(service_id, revision_id)
    if not revision:
        raise NotFound("服务修订不存在")
    return revision


def diff(store: Store, me: Principal, service_id: int, old_id: int, new_id: int) -> Dict[str, Any]:
    service_or_404(store, me, service_id)
    old, new = store.get_revision(service_id, old_id), store.get_revision(service_id, new_id)
    if not old or not new:
        raise NotFound("服务修订不存在")
    return {"from": old_id, "to": new_id, "changes": revision_diff(old["spec"], new["spec"])}


def deployable_builds(store: Store, me: Principal, service_id: int, limit: int, offset: int
                      ) -> Tuple[List[Dict[str, Any]], int]:
    """服务对应应用的成功构建（发布时选择）。"""
    service = service_or_404(store, me, service_id)
    if not (me.can_namespace(service["namespace_id"], "service.deploy")
            or me.can_project(service["project_id"], "job.view")):
        raise Forbidden("没有查看此服务可发布构建的权限")
    rows, total = store.list_jobs(service["app_id"], "success", limit, offset, [service["project_id"]])
    return [{key: row.get(key) for key in BUILD_FIELDS} for row in rows], total
