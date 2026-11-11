"""导入已有服务：分析命名空间（上传的 kubectl 导出，或直接读取集群）→ 用户确认 → 写入平台。

写入的内容：环境档案、负载均衡入口、环境层配置集（共有的环境变量、敏感变量、公共配置文件、证书目录）、
应用（按镜像仓库匹配或新建）与托管服务（服务层配置）。导入只写平台数据，不修改集群；
之后对每个服务发起"接管发布"（沿用线上镜像 digest），由 Server-Side Apply 接管集群中的对象。

分析结果中不包含敏感值：只返回键名、是否与现有配置一致，以及接管后与线上的差异。
"""
from __future__ import annotations

import base64
import hashlib
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from ..access import Principal
from ..delivery.importer import analyze as analyze_objects, parse_documents
from ..delivery.resolve import SCOPE_ORDER
from ..deploy.k8s_client import KubeClient
from ..errors import DomainError, Forbidden, NotFound
from ..registry import parse_image_ref
from ..store import Store
from ..validation import reject_unknown
from . import apps as app_service
from . import catalog, certificates, configsets
from .access import namespace_or_404
from .clusters import load_cluster
from .discovery import _optional
from .environments import merged_profile
from .namespaces import effective_policy

SOURCES = ("yaml", "cluster")


# ------------------------------------------------------------------ 读取对象
async def _cluster_objects(ctx, namespace: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    cluster, credential = load_cluster(ctx.store, ctx.box, namespace["cluster_id"])
    name = quote(namespace["name"], safe="")
    warnings: List[str] = []
    paths = [("Deployment", f"/apis/apps/v1/namespaces/{name}/deployments", True),
             ("Service", f"/api/v1/namespaces/{name}/services", True),
             ("ConfigMap", f"/api/v1/namespaces/{name}/configmaps", True),
             ("Secret", f"/api/v1/namespaces/{name}/secrets", True),
             ("Ingress", f"/apis/networking.k8s.io/v1/namespaces/{name}/ingresses", False),
             ("HorizontalPodAutoscaler", f"/apis/autoscaling/v2/namespaces/{name}/horizontalpodautoscalers", False),
             ("Pod", f"/api/v1/namespaces/{name}/pods", False)]
    objects: List[Dict[str, Any]] = []
    async with KubeClient(cluster["server"], credential, ctx.deploy_transport, timeout=30) as kube:
        for kind, path, required in paths:
            if required:
                items = await kube.list_resources(path)
            else:
                items = await _optional(warnings, lambda path=path: kube.list_resources(path))
            objects += [dict(item, kind=kind) for item in items]
    return objects, warnings


async def load_objects(ctx, namespace: Dict[str, Any], data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    source = data.get("source", "yaml")
    if source not in SOURCES:
        raise ValueError("source 必须是 yaml（上传导出文件）或 cluster（读取集群）")
    if source == "yaml":
        return parse_documents(data.get("yaml") or ""), []
    return await _cluster_objects(ctx, namespace)


# ------------------------------------------------------------------ 分析
def _text(value: bytes) -> Tuple[str, str]:
    try:
        return value.decode("utf-8"), "text"
    except UnicodeDecodeError:
        return base64.b64encode(value).decode("ascii"), "base64"


def _plain_entries(store: Store, box, entries: List[Dict[str, Any]], secret: bool) -> Dict[str, bytes]:
    result = {}
    for entry in entries:
        if secret:
            plain = configsets._plain(store, box, entry)
            result[entry["key"]] = base64.b64decode(plain["value"]) if plain["encoding"] == "base64" else \
                plain["value"].encode("utf-8")
        else:
            result[entry["key"]] = entry.get("value", "").encode("utf-8")
    return result


class _Context:
    """分析与导入共用的平台现状：环境层、上层配置、已有服务与应用。"""

    def __init__(self, ctx, me: Principal, namespace: Dict[str, Any], project_id: Optional[int]):
        store = ctx.store
        self.ctx, self.store, self.me, self.namespace, self.project_id = ctx, store, me, namespace, project_id
        self.policy = effective_policy(namespace["policy"])
        scopes = [("platform", 0), ("environment", namespace["id"])] + ([("project", project_id)] if project_id else [])
        self.sets = [s for s in store.list_config_sets(scopes) if s.get("version")]
        self.services = store.list_services(namespace_ids=[namespace["id"]])
        self.workloads = {catalog.workload_name(store, s, namespace): s for s in self.services}
        self.apps = store.list_apps([project_id]) if project_id else []
        self.profile_row = store.get_environment_profile(namespace["id"])
        self.ingress_profiles = store.list_ingress_profiles()

    def env_set(self, kind: str) -> Optional[Dict[str, Any]]:
        return next((s for s in self.sets if s["scope_type"] == "environment" and s["kind"] == kind), None)

    def uppers(self, kind: str) -> List[Dict[str, Any]]:
        return sorted((s for s in self.sets if s["kind"] == kind and s["scope_type"] in ("platform", "project")),
                      key=lambda s: SCOPE_ORDER[s["scope_type"]])

    def file_set(self, name: str) -> Optional[Dict[str, Any]]:
        candidates = [s for s in self.sets if s["name"] == name and s["kind"] in ("files", "secret_files")]
        return max(candidates, key=lambda s: SCOPE_ORDER[s["scope_type"]]) if candidates else None

    def match_app(self, image: str) -> Dict[str, Any]:
        ref = parse_image_ref(image)
        if not ref:
            return {}
        organization, _, repository = ref.repo.partition("/")
        for app in self.apps:
            if app["repository"] == repository and (app.get("organization") or self.ctx.settings.registry.organization) \
                    == organization:
                return {"id": app["id"], "name": app["name"]}
        return {"create": {"repository": repository, "organization": organization}}


def _env_plan(context: _Context, kind: str, lifted: Dict[str, bytes]) -> Tuple[Dict[str, bytes], Dict[str, bytes]]:
    """环境层导入后的内容：已有的键保持不变，上提的键中环境层没有的追加。返回 (导入后的环境层, 需要追加的键)。"""
    existing = context.env_set(kind)
    current = _plain_entries(context.store, context.ctx.box, existing["entries"], kind == "secret_env") if existing else {}
    additions = {k: v for k, v in lifted.items() if k not in current}
    return {**current, **additions}, additions


def _effective(context: _Context, kind: str, environment: Dict[str, bytes],
               service: Dict[str, bytes]) -> Tuple[Dict[str, bytes], List[str]]:
    merged: Dict[str, bytes] = {}
    locked = set()
    for upper in context.uppers(kind):
        for entry in upper["entries"]:
            if entry["key"] in locked:
                continue
            merged[entry["key"]] = _plain_entries(context.store, context.ctx.box, [entry], kind == "secret_env")[entry["key"]]
            if entry.get("locked"):
                locked.add(entry["key"])
    blocked = []
    for layer in (environment, service):
        for key, value in layer.items():
            if key in locked:
                if merged.get(key) != value:
                    blocked.append(key)
                continue
            merged[key] = value
    return merged, blocked


def _service_plan(context: _Context, row: Dict[str, Any], env_layers: Dict[str, Dict[str, bytes]]) -> Dict[str, Any]:
    """服务层配置 = 线上的值去掉与导入后环境层相同的键；并计算接管后生效配置与线上的差异。"""
    plan: Dict[str, Any] = {"config": {}, "diff": {}}
    for kind, live_key in (("env", "live_env"), ("secret_env", "live_secret_env")):
        live = row[live_key]
        environment = env_layers[kind]
        own = {k: v for k, v in live.items() if environment.get(k) != v}
        effective, blocked = _effective(context, kind, environment, own)
        plan["config"][kind] = own
        plan["diff"][kind] = {
            "added": sorted(k for k in effective if k not in live),
            "changed": sorted(k for k in live if k in effective and effective[k] != live[k]),
            "locked": sorted(blocked),
        }
    plan["config"]["files"] = row["config"]["files"]
    return plan


def _policy_problems(context: _Context, rows: List[Dict[str, Any]]) -> List[str]:
    policy, problems = context.policy, []
    types = sorted({r["spec"]["service"]["type"] for r in rows if r["spec"]["service"]["enabled"]})
    disallowed = [t for t in types if t not in policy["service_types"]]
    if disallowed:
        problems.append(f"命名空间策略不允许 {'、'.join(disallowed)} 类型的 Service，需要平台管理员在策略中放开")
    if any(r["spec"]["ingress"]["enabled"] for r in rows) and not policy["ingress"].get("enabled"):
        problems.append("命名空间策略未启用 Ingress，需要平台管理员在策略中启用")
    if any(r["spec"]["hpa"]["enabled"] for r in rows) and not policy["hpa_enabled"]:
        problems.append("命名空间策略未启用 HPA，需要平台管理员在策略中启用")
    orgs = sorted({(parse_image_ref(r["image"]).repo.split("/", 1)[0] if parse_image_ref(r["image"]) else "")
                   for r in rows} - set(policy["allowed_registry_orgs"]))
    if orgs:
        problems.append(f"镜像组织 {'、'.join(o or '（无）' for o in orgs)} 不在命名空间策略允许的范围内，接管发布会被拒绝")
    high = [r["deployment"] for r in rows if (r["spec"].get("replicas") or 0) > policy["max_replicas"]
            or (r["spec"]["hpa"]["enabled"] and r["spec"]["hpa"]["max_replicas"] > policy["max_replicas"])]
    if high:
        problems.append(f"{'、'.join(high)} 的副本数超过命名空间策略上限 {policy['max_replicas']}")
    return problems


def _report(context: _Context, analysis: Dict[str, Any], load_warnings: List[str]) -> Dict[str, Any]:
    store, namespace = context.store, context.namespace
    env_layers, env_additions = {}, {}
    for kind in ("env", "secret_env"):
        env_layers[kind], env_additions[kind] = _env_plan(context, kind, analysis[kind])
    services = []
    for row in analysis["services"]:
        item: Dict[str, Any] = {k: row.get(k) for k in ("deployment", "name", "supported", "reason", "warnings", "objects")}
        if not row["supported"]:
            item["action"] = "unsupported"
            services.append(item)
            continue
        existing = context.workloads.get(row["deployment"])
        item["action"] = ("skip" if existing and catalog.mode_of(existing) == "managed" else
                          "convert" if existing else "create")
        item["existing_service"] = {"id": existing["id"], "name": existing["name"]} if existing else None
        if existing:
            item["name"] = existing["name"]
        elif any(s["name"] == row["name"] for s in context.services):
            item["name_conflict"] = True
        plan = _service_plan(context, row, env_layers)
        item.update(image=row["image"], digest=row["digests"][0] if len(row["digests"]) == 1 else None,
                    digests=len(row["digests"]), spec=row["spec"],
                    app=context.match_app(row["image"]) if not existing else
                    {"id": existing["app_id"], "name": (store.get_app(existing["app_id"]) or {}).get("name", "")},
                    config={"env": sorted(plan["config"]["env"]), "secret_env": sorted(plan["config"]["secret_env"]),
                            "files": sorted(plan["config"]["files"])},
                    effective=plan["diff"])
        if not row["digests"]:
            item["warnings"] = item["warnings"] + ["导出中没有正在运行的 Pod，接管发布时会读取集群中的镜像摘要"]
        services.append(item)
    files = []
    for entry in analysis["files"]:
        current = context.file_set(entry["name"])
        same = None
        if current and current["kind"] == entry["kind"]:
            same = _plain_entries(store, context.ctx.box, current["entries"], entry["kind"] == "secret_files") == entry["entries"]
        files.append({"name": entry["name"], "kind": entry["kind"], "keys": sorted(entry["entries"]),
                      "used_by": entry["used_by"], "size": sum(len(v) for v in entry["entries"].values()),
                      "existing": {"id": current["id"], "scope_type": current["scope_type"], "version": current["version"],
                                   "same": same} if current else None})
    proposal = analysis["profile"]
    saved = merged_profile(context.profile_row["profile"]) if context.profile_row else None
    ingress = analysis["ingress"]
    reuse = next((p for p in context.ingress_profiles if ingress and p["spec"]["elb_id"] == ingress["elb_id"]
                  and p["spec"]["port"] == ingress["port"] and p["spec"]["ingress_class"] == ingress["ingress_class"]
                  and p["spec"].get("certificate_ids") == ingress["certificate_ids"]), None)
    env_conflicts = {}
    for kind in ("env", "secret_env"):
        existing = context.env_set(kind)
        current = _plain_entries(store, context.ctx.box, existing["entries"], kind == "secret_env") if existing else {}
        env_conflicts[kind] = sorted(k for k, v in analysis[kind].items() if k in current and current[k] != v)
    supported = [r for r in analysis["services"] if r["supported"]]
    return {
        "namespace_id": namespace["id"], "namespace_name": namespace["name"], "project_id": context.project_id,
        "source_namespace": analysis["source_namespace"], "counts": analysis["counts"], "ignored": analysis["ignored"],
        "profile": {"proposed": proposal, "saved": saved, "version": context.profile_row["version"] if context.profile_row else 0,
                    "same": saved == proposal if saved else False},
        "ingress": {"proposed": ingress, "reuse": {"id": reuse["id"], "name": reuse["name"]} if reuse else None,
                    "current_id": context.profile_row["ingress_profile_id"] if context.profile_row else None},
        "env": {"keys": {k: _text(v)[0] for k, v in analysis["env"].items()},
                "additions": sorted(env_additions["env"]), "conflicts": env_conflicts["env"],
                "existing": bool(context.env_set("env"))},
        "secret_env": {"keys": sorted(analysis["secret_env"]), "additions": sorted(env_additions["secret_env"]),
                       "conflicts": env_conflicts["secret_env"], "existing": bool(context.env_set("secret_env"))},
        "files": files, "services": services, "unused": analysis["unused"],
        "warnings": load_warnings + analysis["warnings"], "blockers": _policy_problems(context, supported),
        "permissions": {"environment_config": context.me.can_namespace(namespace["id"], "environment.config"),
                        "service_create": context.me.can_namespace(namespace["id"], "service.create"),
                        "certificate_manage": context.me.can("certificate.manage"),
                        "app_manage": bool(context.project_id) and context.me.can_project(context.project_id, "app.manage")},
    }


def _request(store: Store, me: Principal, namespace_id: int, data: Any, apply: bool) -> Tuple[Dict[str, Any], Optional[int]]:
    namespace = namespace_or_404(store, me, namespace_id)
    if not me.can_namespace(namespace_id, "service.create"):
        raise Forbidden("导入服务需要在该环境新建托管服务的权限")
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    allowed = ["source", "yaml", "project_id"]
    if apply:
        allowed += ["services", "save_profile", "ingress", "save_config", "overwrite_files", "use_certificates"]
    reject_unknown(data, allowed, "包含不支持的字段")
    project_id = data.get("project_id")
    if project_id is not None:
        if not isinstance(project_id, int) or isinstance(project_id, bool) or not store.get_project(project_id) \
                or not me.can_project(project_id, "project.view"):
            raise NotFound("项目不存在")
        if namespace_id not in store.project_namespace_ids(project_id):
            raise ValueError("该环境没有分配给所选项目，请先在项目中分配环境")
    elif apply:
        raise ValueError("请选择服务所属的项目")
    return namespace, project_id


async def analyze(ctx, me: Principal, namespace_id: int, data: Any) -> Dict[str, Any]:
    namespace, project_id = _request(ctx.store, me, namespace_id, data, apply=False)
    objects, warnings = await load_objects(ctx, namespace, data)
    analysis = analyze_objects(objects, namespace["name"])
    return _report(_Context(ctx, me, namespace, project_id), analysis, warnings)


# ------------------------------------------------------------------ 导入
def _entries(data: Dict[str, bytes], secret: bool, files: bool = False) -> List[Dict[str, Any]]:
    entries = []
    for key, value in sorted(data.items()):
        text, encoding = _text(value)
        if secret:
            entries.append({"key": key, "value": text, **({"encoding": "base64"} if encoding == "base64" else {})})
        elif encoding == "base64":
            raise ValueError(f"{key} 不是文本内容，不能保存为配置文件")
        else:
            entries.append({"key": key, "value": text, **({"format": configsets._guess_format(key)} if files else {})})
    return entries


def _certificate_entries(context: _Context, set_name: str, data: Dict[str, bytes], log: List[str]) -> List[Dict[str, Any]]:
    """证书目录导入证书库（按 SHA-256 去重），配置条目引用证书；无法识别或没有权限时直接保存文件内容。"""
    store, box, me = context.store, context.ctx.box, context.me
    library = {c["sha256"]: c for c in store.list_certificates()}
    names = {c["name"] for c in library.values()}
    entries = []
    for key, value in sorted(data.items()):
        digest = hashlib.sha256(value).hexdigest()
        found = library.get(digest)
        if not found:
            name = f"{key}（{set_name}）"
            suffix = 2
            while name in names:
                name, suffix = f"{key}（{set_name} #{suffix}）", suffix + 1
            try:
                created = certificates.create_certificate(store, box, me.id, {
                    "name": name[:128], "filename": key, "content_base64": base64.b64encode(value).decode("ascii"),
                    "description": f"导入自 {context.namespace['name']} 的 {set_name}"})
            except (ValueError, RuntimeError, DomainError) as exc:
                log.append(f"{set_name}/{key} 没有导入证书库（{exc}），直接保存文件内容")
                entries += _entries({key: value}, secret=True)
                continue
            found = {"id": created["id"], "name": created["name"]}
            library[digest] = found
            names.add(created["name"])
        entries.append({"key": key, "certificate_id": found["id"]})
    return entries


def _save_set(context: _Context, scope: Tuple[str, int], kind: str, name: str, entries: List[Dict[str, Any]],
              note: str, merge: bool = True) -> Dict[str, Any]:
    """创建配置集，或在已有配置集上追加（merge）/ 整体替换条目，保存为新版本。"""
    store, box, me = context.store, context.ctx.box, context.me
    existing = store.find_config_set(scope[0], scope[1], name)
    if existing and existing["kind"] != kind:
        raise ValueError(f"已有同名但类型不同的配置 {name}")
    if not existing:
        normalized, refs = configsets.normalize_entries(store, box, kind, entries, scope[0])
        configsets._check_locked(store, scope[0], scope[1], kind, normalized)
        set_id = store.create_config_set(scope[0], scope[1], name, kind, "", normalized, refs, note, me.id)
        return {"id": set_id, "name": name, "kind": kind, "version": 1, "action": "create"}
    keep = [{"key": e["key"], "keep": True, "description": e.get("description", ""), "locked": e.get("locked", False)}
            if kind in configsets.SECRET_KINDS else
            {k: e[k] for k in ("key", "value", "format", "description", "locked") if k in e} for e in existing["entries"]]
    present = {e["key"] for e in existing["entries"]}
    combined = keep + [e for e in entries if e["key"] not in present] if merge else entries
    if merge and len(combined) == len(keep):
        return {"id": existing["id"], "name": name, "kind": kind, "version": existing["version"], "action": "unchanged"}
    normalized, refs = configsets.normalize_entries(store, box, kind, combined, scope[0], existing["entries"])
    configsets._check_locked(store, scope[0], scope[1], kind, normalized)
    version = store.add_config_version(existing["id"], existing["version"], normalized, refs, note, me.id)
    return {"id": existing["id"], "name": name, "kind": kind, "version": version, "action": "update"}


async def apply(ctx, me: Principal, namespace_id: int, data: Any) -> Dict[str, Any]:
    """按用户确认的方案写入平台数据。各步骤独立执行，失败的步骤记录在结果中，不影响其他步骤。"""
    store = ctx.store
    namespace, project_id = _request(store, me, namespace_id, data, apply=True)
    selections = data.get("services") or []
    if not isinstance(selections, list) or any(not isinstance(s, dict) for s in selections):
        raise ValueError("services 必须是数组")
    for flag in ("save_profile", "save_config", "overwrite_files", "use_certificates"):
        if not isinstance(data.get(flag, False), bool):
            raise ValueError(f"{flag} 必须是布尔值")
    ingress_choice = data.get("ingress", "none")
    if ingress_choice not in ("create", "reuse", "none"):
        raise ValueError("ingress 必须是 create、reuse 或 none")
    objects, load_warnings = await load_objects(ctx, namespace, data)
    analysis = analyze_objects(objects, namespace["name"])
    context = _Context(ctx, me, namespace, project_id)
    report = _report(context, analysis, load_warnings)
    rows = {r["deployment"]: r for r in analysis["services"] if r["supported"]}
    result: Dict[str, Any] = {"profile": None, "ingress_profile": None, "config_sets": [], "apps": [], "services": [],
                              "errors": [], "notes": []}
    can_config = me.can_namespace(namespace_id, "environment.config")

    # 负载均衡入口与环境档案
    ingress_id = report["ingress"]["current_id"]
    if ingress_choice == "reuse" and report["ingress"]["reuse"]:
        ingress_id = report["ingress"]["reuse"]["id"]
    elif ingress_choice == "create" and analysis["ingress"]:
        if not me.can("certificate.manage"):
            result["errors"].append("没有维护负载均衡入口的权限（certificate.manage），未创建负载均衡入口")
        else:
            from .environments import create_ingress_profile
            name, suffix = f"{namespace['name']} 入口", 2
            taken = {p["name"] for p in context.ingress_profiles}
            while name in taken:
                name, suffix = f"{namespace['name']} 入口 #{suffix}", suffix + 1
            try:
                created = create_ingress_profile(store, me.id, {"name": name, "description": "导入时根据多数 Ingress 生成",
                                                                "spec": analysis["ingress"]})
                ingress_id = created["id"]
                result["ingress_profile"] = {"id": created["id"], "name": created["name"]}
            except (ValueError, DomainError) as exc:
                result["errors"].append(f"创建负载均衡入口失败：{exc}")
    if data.get("save_profile"):
        if not can_config:
            result["errors"].append("没有修改环境档案的权限（environment.config），未保存环境档案")
        else:
            try:
                version = store.save_environment_profile(namespace_id, report["profile"]["version"],
                                                         report["profile"]["proposed"], ingress_id, me.id)
                result["profile"] = {"version": version, "ingress_profile_id": ingress_id}
            except (ValueError, DomainError) as exc:
                result["errors"].append(f"保存环境档案失败：{exc}")

    # 环境层配置
    if data.get("save_config"):
        if not can_config:
            result["errors"].append("没有修改环境配置的权限（environment.config），未导入环境层配置")
        else:
            scope = ("environment", namespace_id)
            steps = [("env", "env", _entries(analysis["env"], secret=False)),
                     ("secret_env", "secret-env", _entries(analysis["secret_env"], secret=True))]
            for kind, name, entries in steps:
                if not entries:
                    continue
                try:
                    result["config_sets"].append(_save_set(context, scope, kind, name, entries, "导入已有服务"))
                except (ValueError, DomainError) as exc:
                    result["errors"].append(f"保存环境层{configsets.KIND_LABELS[kind]}失败：{exc}")
            for entry, info in zip(analysis["files"], report["files"]):
                existing = info["existing"]
                if existing and existing["same"]:
                    result["notes"].append(f"{entry['name']} 与{configsets.SCOPE_LABELS[existing['scope_type']]}层已有配置相同，直接使用")
                    continue
                if existing and existing["scope_type"] == "environment" and not data.get("overwrite_files"):
                    result["notes"].append(f"{entry['name']} 与环境层已有配置不同，按选择保留平台中的版本")
                    continue
                try:
                    if entry["kind"] == "secret_files" and data.get("use_certificates", True) and me.can("certificate.manage"):
                        entries = _certificate_entries(context, entry["name"], entry["entries"], result["notes"])
                    else:
                        entries = _entries(entry["entries"], secret=entry["kind"] == "secret_files", files=True)
                    result["config_sets"].append(_save_set(context, scope, entry["kind"], entry["name"], entries,
                                                           "导入已有服务", merge=False))
                except (ValueError, DomainError) as exc:
                    result["errors"].append(f"保存配置 {entry['name']} 失败：{exc}")
            context = _Context(ctx, me, namespace, project_id)

    # 应用与服务
    env_layers = {kind: _env_plan(context, kind, {})[0] for kind in ("env", "secret_env")}
    for selection in selections:
        deployment = selection.get("deployment")
        row = rows.get(deployment)
        outcome: Dict[str, Any] = {"deployment": deployment, "status": "error", "message": ""}
        result["services"].append(outcome)
        if not row:
            outcome["message"] = "分析结果中没有该工作负载，或模板不支持"
            continue
        try:
            existing = context.workloads.get(deployment)
            if existing and catalog.mode_of(existing) == "managed":
                outcome.update(status="skipped", message="已是托管服务", service_id=existing["id"])
                continue
            plan = _service_plan(context, row, env_layers)
            config = {"env": _entries(plan["config"]["env"], secret=False),
                      "secret_env": _entries(plan["config"]["secret_env"], secret=True),
                      "files": _entries(plan["config"]["files"], secret=False, files=True)}
            if existing:
                namespace_record = store.get_namespace(namespace_id)
                spec = catalog.normalize_service_spec(row["spec"], namespace_record)
                if not me.can_namespace(namespace_id, "service.config"):
                    raise Forbidden("没有服务配置权限")
                store.update_service_revision(existing["id"], existing["current_revision_id"], spec, me.id)
                for kind, entries in config.items():
                    if entries:
                        name = configsets.SINGLETON_NAMES.get(kind) or configsets.SERVICE_FILES
                        _save_set(context, ("service", existing["id"]), kind, name, entries, "转为托管服务", merge=False)
                outcome.update(status="converted", service_id=existing["id"], name=existing["name"])
                continue
            app_id = selection.get("app_id")
            if app_id is None and selection.get("create_app"):
                app_id = _create_app(ctx, me, project_id, row, selection, result)
            app = store.get_app(app_id) if isinstance(app_id, int) else None
            if not app or app["project_id"] != project_id or not me.can_project(project_id, "app.view"):
                raise ValueError("请选择服务所属的应用（或勾选按镜像新建应用）")
            name = (selection.get("name") or row["name"]).strip().lower()
            created = catalog.create_service(store, me, {"name": name, "project_id": project_id,
                                                         "namespace_id": namespace_id, "app_id": app_id,
                                                         "spec": row["spec"], "config": config}, ctx.box)
            outcome.update(status="created", service_id=created["id"], name=created["name"])
        except (ValueError, DomainError) as exc:
            outcome["message"] = str(exc)
    return result


def _create_app(ctx, me: Principal, project_id: int, row: Dict[str, Any], selection: Dict[str, Any],
                result: Dict[str, Any]) -> int:
    ref = parse_image_ref(row["image"])
    if not ref:
        raise ValueError("线上镜像地址无效，不能据此新建应用")
    organization, _, repository = ref.repo.partition("/")
    name = (selection.get("app_name") or row["name"]).strip()
    existing = ctx.store.find_app_by_name(name)
    if existing and existing["project_id"] == project_id and existing["repository"] == repository:
        return existing["id"]
    created = app_service.create_app(ctx.store, ctx.settings, me, {"name": name, "repository": repository,
                                                                    "organization": organization, "project_id": project_id,
                                                                    "description": "导入已有服务时按线上镜像创建"})
    result["apps"].append({"id": created["id"], "name": created["name"]})
    return created["id"]
