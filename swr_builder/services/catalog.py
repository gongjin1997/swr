"""服务目录：服务的新建/修改/删除、spec 校验、配置修订与对比、密钥版本、可发布的构建。

服务 = 某个项目的应用在项目已分配的某个环境（命名空间）中的一份部署，两种方式：
- 纳管（image_only）：关联集群中已有的 Deployment，发布时只更新容器镜像；
- 托管（managed）：按模板生成并下发 Deployment、Service、Ingress、HPA 与配置对象（见 swr_builder/delivery）。
读取：命名空间上有 namespace.view，或项目上有 app.view；修改需要命名空间上的对应权限。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..access import Principal
from ..delivery.resolve import SCOPE_ORDER, layers
from ..delivery.spec import SERVICE_FILES, normalize_managed_spec, selector_of, object_names
from ..errors import Conflict, Forbidden, NotFound
from ..security import SecretBox
from ..store import Store
from ..validation import DNS_LABEL_RE, reject_unknown, text_field
from . import configsets
from .access import service_or_404
from .environments import merged_profile

SERVICE_RESPONSE_FIELDS = ("id", "name", "project_id", "namespace_id", "app_id", "current_revision_id",
                           "deployed_revision_id", "spec", "created_at", "updated_at")
SECRET_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}")
BUILD_FIELDS = ("id", "app_id", "project_id", "app_name", "status", "image", "digest", "new_tag", "created_at")
_DNS_SUBDOMAIN_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?)*")
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")
# 新建托管服务时可以一并提交的服务层配置
SERVICE_CONFIG_KINDS = {"env": "env", "secret_env": "secret_env", "files": "files"}


def mode_of(service: Dict[str, Any]) -> str:
    return (service.get("spec") or {}).get("mode") or "image_only"


def service_response(service: Dict[str, Any]) -> Dict[str, Any]:
    result = {key: service.get(key) for key in SERVICE_RESPONSE_FIELDS}
    result["mode"] = mode_of(service)
    return result


def normalize_service_spec(value: Any, namespace: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("服务 spec 必须是对象")
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
        raise ValueError("mode 必须是 managed（托管）或 image_only（纳管）")
    return normalize_managed_spec(value, namespace)


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


# ------------------------------------------------------------------ 配置漂移
def config_status(store: Store, services: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """托管服务上次成功发布所用的配置版本与当前版本的差异（各层配置集、环境档案、负载均衡入口）。"""
    managed = [s for s in services if mode_of(s) == "managed"]
    if not managed:
        return {}
    manifests = store.last_success_manifests([s["id"] for s in managed])
    scopes = sorted({scope for s in managed for scope in layers(s)})
    sets = [s for s in store.list_config_sets(scopes) if s.get("version")]
    by_id = {s["id"]: s for s in sets}
    profiles = store.list_environment_profiles()
    ingress_versions = {p["id"]: p["version"] for p in store.list_ingress_profiles()}
    result: Dict[int, Dict[str, Any]] = {}
    for service in managed:
        manifest = manifests.get(service["id"])
        if not manifest:
            result[service["id"]] = {"deployed": False, "pending": False, "changes": []}
            continue
        inputs = manifest["inputs"]
        scope_keys = set(layers(service))
        pins = {p["set_id"]: p for p in inputs.get("config") or []}
        changes: List[Dict[str, Any]] = []
        for pin in pins.values():
            current = by_id.get(pin["set_id"])
            if pin["kind"] in ("files", "secret_files"):
                candidates = [s for s in sets if s["name"] == pin["name"] and s["kind"] == pin["kind"]
                              and (s["scope_type"], s["scope_id"]) in scope_keys
                              and (s["scope_type"] != "service" or s["name"] == SERVICE_FILES)]
                chosen = max(candidates, key=lambda s: SCOPE_ORDER[s["scope_type"]]) if candidates else None
                if chosen and chosen["id"] != pin["set_id"]:
                    current = None
                    changes.append({**pin, "change": "replaced", "current": chosen["version"],
                                    "current_scope": chosen["scope_type"]})
                    continue
            if current is None:
                changes.append({**pin, "change": "removed", "current": None})
            elif current["version"] != pin["version"]:
                changes.append({**pin, "change": "updated", "current": current["version"]})
        for item in sets:
            if item["kind"] in ("env", "secret_env") and (item["scope_type"], item["scope_id"]) in scope_keys \
                    and item["id"] not in pins:
                changes.append({"set_id": item["id"], "scope_type": item["scope_type"], "scope_id": item["scope_id"],
                                "name": item["name"], "kind": item["kind"], "version": None, "change": "added",
                                "current": item["version"]})
        profile = profiles.get(service["namespace_id"])
        if (profile["version"] if profile else 0) != inputs.get("profile_version", 0):
            changes.append({"kind": "profile", "name": "环境档案", "change": "updated", "scope_type": "environment",
                            "version": inputs.get("profile_version", 0), "current": profile["version"] if profile else 0})
        ingress_id = profile["ingress_profile_id"] if profile else None
        if ingress_id != inputs.get("ingress_profile_id") or (
                ingress_id and ingress_versions.get(ingress_id) != inputs.get("ingress_version")):
            changes.append({"kind": "ingress", "name": "负载均衡入口", "change": "updated", "scope_type": "platform",
                            "version": inputs.get("ingress_version"), "current": ingress_versions.get(ingress_id)})
        result[service["id"]] = {"deployed": True, "pending": bool(changes), "changes": changes,
                                 "deployment_id": manifest["deployment_id"]}
    return result


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

    def service_view(self, row: Dict[str, Any], status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ns, project, app = self.namespace(row["namespace_id"]), self.project(row["project_id"]), self.app(row["app_id"])
        view = dict(service_response(row), project_name=project["name"] if project else "",
                    app_name=app["name"] if app else "", cluster_name=ns["cluster_name"] if ns else "",
                    namespace_name=ns["name"] if ns else "", tier=ns["tier"] if ns else "")
        view["workload"] = workload_name(self.store, row, ns)
        if status is not None:
            view["config_status"] = status
        return view


def workload_name(store: Store, service: Dict[str, Any], namespace: Optional[Dict[str, Any]] = None) -> str:
    spec = service.get("spec") or {}
    if mode_of(service) != "managed":
        return (spec.get("target") or {}).get("name", "")
    namespace = namespace or store.get_namespace(service["namespace_id"]) or {}
    row = store.get_environment_profile(service["namespace_id"])
    try:
        return object_names(spec, service["name"], namespace.get("name", ""),
                            merged_profile(row["profile"] if row else None))["deployment"]
    except ValueError:
        return ""


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
    statuses = config_status(store, rows)
    return [lookup.service_view(row, statuses.get(row["id"])) for row in rows]


def get_service(store: Store, me: Principal, service_id: int) -> Dict[str, Any]:
    service = service_or_404(store, me, service_id)
    view = Lookup(store).service_view(service, config_status(store, [service]).get(service_id))
    view["permissions"] = sorted(p for p in ("service.config", "service.create", "service.deploy", "service.operate",
                                             "service.rollback", "service.delete", "secret.manage", "secret.view")
                                 if me.can_namespace(service["namespace_id"], p))
    return view


def require_namespace(me: Principal, service: Dict[str, Any], permission: str, message: str) -> None:
    if not me.can_namespace(service["namespace_id"], permission):
        raise Forbidden(message)


def writable_service(store: Store, me: Principal, service_id: int, permission: str, message: str) -> Dict[str, Any]:
    """读取需要在命名空间上操作的服务：看不到按不存在处理，看得到但没有权限返回 403。"""
    service = service_or_404(store, me, service_id, allow_project_view=False)
    require_namespace(me, service, permission, message)
    return service


# ------------------------------------------------------------------ 新建 / 修订 / 删除
def _service_config(store: Store, box: Optional[SecretBox], raw: Any) -> List[Tuple[str, List[Dict[str, Any]], list]]:
    """新建托管服务时一并提交的服务层配置：{env: [...], secret_env: [...], files: [...]}。先校验，后写入。"""
    if raw in (None, {}):
        return []
    if not isinstance(raw, dict):
        raise ValueError("config 必须是对象")
    reject_unknown(raw, tuple(SERVICE_CONFIG_KINDS), "config 只能包含 env、secret_env、files")
    if raw.get("secret_env") and box is None:
        raise ValueError("缺少加密组件，不能保存敏感配置")
    prepared = []
    for kind in SERVICE_CONFIG_KINDS:
        if raw.get(kind):
            entries, refs = configsets.normalize_entries(store, box, kind, raw[kind], "service")  # type: ignore[arg-type]
            prepared.append((kind, entries, refs))
    return prepared


def create_service(store: Store, me: Principal, data: Any, box: Optional[SecretBox] = None) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("服务请求格式错误")
    reject_unknown(data, {"name", "project_id", "namespace_id", "app_id", "spec", "config"}, "服务请求包含未知字段")
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
    spec = normalize_service_spec(data.get("spec"), namespace)
    managed = spec["mode"] == "managed"
    permission = "service.create" if managed else "service.config"
    if not me.can_namespace(namespace_id, permission):
        raise Forbidden("没有在该环境新建托管服务的权限" if managed else "没有命名空间服务配置权限")
    if not managed and data.get("config"):
        raise ValueError("纳管服务不使用分层配置")
    if managed:
        row = store.get_environment_profile(namespace_id)
        deployment = object_names(spec, name, namespace["name"], merged_profile(row["profile"] if row else None))["deployment"]
        clash = next((s for s in store.list_services(namespace_ids=[namespace_id])
                      if workload_name(store, s, namespace) == deployment), None)
        if clash:
            raise Conflict(f"该环境中的服务 {clash['name']} 已关联 Deployment {deployment}；"
                           "可以在该服务中把它转为托管服务")
    prepared = _service_config(store, box, data.get("config")) if managed else []
    created = store.create_service(name, project_id, namespace_id, app_id, spec, me.id)
    for kind, entries, refs in prepared:
        set_name = configsets.SINGLETON_NAMES.get(kind) or SERVICE_FILES
        store.create_config_set("service", created["id"], set_name, kind, "", entries, refs, "新建服务时创建", me.id)
    return service_response(created)


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
    namespace = store.get_namespace(service["namespace_id"])
    spec = normalize_service_spec(data.get("spec"), namespace)
    if spec["mode"] == "managed":
        if mode_of(service) != "managed" and not me.can_namespace(service["namespace_id"], "service.create"):
            raise Forbidden("转为托管服务需要新建托管服务的权限")
        row = store.get_environment_profile(service["namespace_id"])
        profile = merged_profile(row["profile"] if row else None)
        names = object_names(spec, service["name"], namespace["name"], profile)
        manifest = store.last_success_manifest(service_id)
        deployed = next((o for o in (manifest or {}).get("objects", []) if o["kind"] == "Deployment"), None)
        if deployed and deployed["spec"]["selector"]["matchLabels"] != selector_of(spec, names):
            raise ValueError("Deployment 的 selector 创建后不能修改；需要修改时请新建服务")
    updated, revision = store.update_service_revision(service_id, base_revision, spec, me.id)
    return service_response(updated), revision


def delete_service(store: Store, me: Principal, service_id: int) -> None:
    writable_service(store, me, service_id, "service.delete", "没有服务删除权限")
    if not store.delete_service(service_id):
        raise Conflict("存在进行中的发布，不能删除服务记录")


def set_secret(store: Store, box: SecretBox, me: Principal, service_id: int, key: str, data: Any) -> Dict[str, Any]:
    """纳管服务的密钥：加密保存为新版本，并生成新的配置修订；值不会回显。托管服务改用服务层敏感变量。"""
    value = data.get("value") if isinstance(data, dict) else None
    if not isinstance(data, dict) or set(data) != {"value"} or not isinstance(value, str) or not value \
            or len(value) > 65536:
        raise ValueError("value 必须是 1–65536 字符的非空字符串")
    if not SECRET_KEY_RE.fullmatch(key):
        raise ValueError("Secret key 格式无效")
    service = writable_service(store, me, service_id, "secret.manage", "没有服务密钥管理权限")
    if mode_of(service) == "managed":
        raise ValueError("托管服务的敏感变量在服务的“配置”中维护")
    revision = store.create_secret_revision(service_id, key, box.encrypt(value), me.id)
    if revision is None:
        raise NotFound("服务不存在")
    return {"key": key, "version": revision["secret_versions"][key], "updated_at": revision["created_at"]}


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


def effective_config(store: Store, me: Principal, service_id: int) -> Dict[str, Any]:
    service = service_or_404(store, me, service_id)
    return configsets.effective_config(store, me, service["namespace_id"], service["project_id"], service_id)
