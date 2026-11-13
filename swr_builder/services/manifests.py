"""托管服务的清单预览、服务端校验（dry-run）、发布快照与接管发布用的线上镜像。

预览不解密敏感值：Secret 中是同样长度的占位内容，展示为"已隐藏"；服务端校验同样使用占位内容。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..access import Principal
from ..delivery.apply import apply_objects
from ..delivery.render import SERVICE_LABEL, mask, object_key, render, to_yaml
from ..delivery.resolve import check_render, resolve
from ..deploy.k8s_client import KubeClient, deployment_path
from ..errors import Forbidden, NotFound, Unprocessable
from ..planner import PlanError, repo_path_of
from ..registry import parse_image_ref
from ..validation import DNS_LABEL_RE, reject_unknown, text_field
from . import configsets
from .access import service_or_404
from .catalog import SERVICE_CONFIG_KINDS, mode_of, normalize_service_spec, writable_service
from .clusters import load_cluster
from .discovery import selector_string, service_target


def _preview_image(ctx, service: Dict[str, Any]) -> str:
    """预览用的镜像：最近一次成功发布的镜像，否则应用最近一次成功构建，否则应用仓库地址加占位标签。"""
    rows, _ = ctx.store.list_deployments(service_id=service["id"], status="success", limit=1, offset=0) \
        if service.get("id") else ([], 0)
    if rows:
        return rows[0]["image"]
    jobs, _ = ctx.store.list_jobs(service["app_id"], "success", 1, 0, [service["project_id"]])
    if jobs and jobs[0].get("image"):
        return jobs[0]["image"]
    app = ctx.store.get_app(service["app_id"]) or {}
    try:
        return f"{ctx.settings.registry.host or 'registry.local'}/{repo_path_of(ctx.settings, app)}:preview"
    except (PlanError, KeyError):
        return "registry.local/preview:latest"


async def _render_preview(ctx, service: Dict[str, Any], spec: Dict[str, Any], *, dry_run: bool,
                          revision_id: Optional[int] = None, image: Optional[str] = None,
                          extra_sets: Optional[List[Dict[str, Any]]] = None,
                          extra_bindings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    namespace = ctx.store.get_namespace(service["namespace_id"])
    image = image or _preview_image(ctx, service)
    result: Dict[str, Any] = {"mode": "managed", "image": image, "revision_id": revision_id, "objects": [], "yaml": "",
                              "errors": [], "warnings": [], "config": [], "resources": [], "dry_run": None}
    try:
        inputs, snapshot = resolve(ctx.store, ctx.box, service, spec, image, revision_id=revision_id,
                                   extra_sets=extra_sets, extra_bindings=extra_bindings)
        objects = render(inputs)
    except ValueError as exc:
        result["errors"] = [str(exc)]
        return result
    result["errors"] = check_render(inputs, namespace)
    result["warnings"] = inputs.warnings
    result["config"] = snapshot["config"]
    result["resources"] = snapshot.get("resources") or []
    masked = mask(objects)
    result["objects"] = [{"key": object_key(o), "kind": o["kind"], "name": o["metadata"]["name"], "yaml": to_yaml([o])}
                         for o in masked]
    result["yaml"] = to_yaml(masked)
    if dry_run and not result["errors"]:
        cluster, credential = load_cluster(ctx.store, ctx.box, namespace["cluster_id"])
        previous = ctx.store.last_success_manifest(service["id"]) if service.get("id") else None
        keys = [(o["kind"], o["metadata"]["name"]) for o in (previous or {}).get("objects", [])]
        async with KubeClient(cluster["server"], credential, ctx.deploy_transport, timeout=30) as kube:
            result["dry_run"] = await apply_objects(kube, namespace["name"], objects, dry_run=True, previous=keys,
                                                    service_id=service.get("id"), secret_values=False)
        own = {(o["kind"], o["metadata"]["name"]) for o in objects if SERVICE_LABEL in (o["metadata"].get("labels") or {})}
        foreign = [f"{r['kind']}/{r['name']}" for r in result["dry_run"]
                   if r["owner"] == "foreign" and (r["kind"], r["name"]) in own]
        if foreign:
            result["warnings"] = [*result["warnings"],
                                  f"集群中已有 {len(foreign)} 个同名对象不由平台管理（{'、'.join(foreign[:6])}"
                                  f"{' 等' if len(foreign) > 6 else ''}）：普通发布会被拒绝；"
                                  "要由平台接管已有服务，请使用“接管发布”或在环境中“导入已有服务”"]
    return result


def _flag(data: Dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"{key} 必须是布尔值")
    return value


async def preview(ctx, me: Principal, service_id: int, data: Any) -> Dict[str, Any]:
    """已有服务的清单：默认当前修订；dry_run=true 时在集群中做服务端校验并给出与线上的差异。"""
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("revision_id", "dry_run", "spec"), "包含不支持的字段")
    dry_run = _flag(data, "dry_run")
    service = (writable_service(ctx.store, me, service_id, "service.config", "服务端校验需要服务配置权限")
               if dry_run or "spec" in data else service_or_404(ctx.store, me, service_id))
    revision_id = data.get("revision_id") or service["current_revision_id"]
    revision = ctx.store.get_revision(service_id, revision_id) if isinstance(revision_id, int) else None
    if not revision:
        raise NotFound("服务修订不存在")
    spec = revision["spec"]
    if "spec" in data:
        # 修改配置时预览尚未保存的规格
        spec = normalize_service_spec(data["spec"], ctx.store.get_namespace(service["namespace_id"]))
        revision_id = None
    if spec.get("mode") != "managed":
        return {"mode": "image_only", "objects": [], "yaml": "", "errors": [], "warnings": [],
                "config": [], "dry_run": None, "image": "", "revision_id": revision["id"]}
    return await _render_preview(ctx, service, spec, dry_run=dry_run, revision_id=revision_id)


def _draft_sets(ctx, raw: Any) -> List[Dict[str, Any]]:
    """新建前预览：把请求中的服务层配置当作 v0 配置集参与合并（不保存）。"""
    if raw in (None, {}):
        return []
    if not isinstance(raw, dict):
        raise ValueError("config 必须是对象")
    reject_unknown(raw, tuple(SERVICE_CONFIG_KINDS), "config 只能包含 env、secret_env、files")
    sets = []
    for index, kind in enumerate(SERVICE_CONFIG_KINDS):
        if raw.get(kind):
            entries, _ = configsets.normalize_entries(ctx.store, ctx.box, kind, raw[kind], "service")
            sets.append({"id": -(index + 1), "scope_type": "service", "scope_id": 0, "kind": kind, "version": 0,
                         "name": configsets.SINGLETON_NAMES.get(kind) or configsets.SERVICE_FILES, "entries": entries})
    return sets


async def preview_new(ctx, me: Principal, data: Any) -> Dict[str, Any]:
    """新建托管服务前预览清单（可选服务端校验）。参数与新建服务相同。"""
    if not isinstance(data, dict):
        raise ValueError("请求体必须是对象")
    reject_unknown(data, ("name", "project_id", "namespace_id", "app_id", "spec", "config", "bindings", "dry_run"),
                   "包含不支持的字段")
    name = text_field(data, "name", 63).lower()
    if not DNS_LABEL_RE.fullmatch(name):
        raise ValueError("服务名称必须是 DNS-1123 label（小写字母、数字和 -）")
    project_id, namespace_id, app_id = data.get("project_id"), data.get("namespace_id"), data.get("app_id")
    if any(type(v) is not int for v in (project_id, namespace_id, app_id)):
        raise ValueError("project_id、namespace_id、app_id 必须是整数")
    namespace, app = ctx.store.get_namespace(namespace_id), ctx.store.get_app(app_id)
    if (not namespace or not app or app["project_id"] != project_id
            or namespace_id not in ctx.store.project_namespace_ids(project_id)):
        raise NotFound("服务所属资源不存在：应用必须属于该项目，命名空间必须已分配给该项目")
    if not me.can_namespace(namespace_id, "service.create"):
        raise Forbidden("没有在该环境新建托管服务的权限")
    spec = normalize_service_spec(data.get("spec"), namespace)
    if spec.get("mode") != "managed":
        raise ValueError("只有托管服务需要预览清单")
    draft = {"id": 0, "name": name, "project_id": project_id, "namespace_id": namespace_id, "app_id": app_id,
             "spec": spec}
    bindings = data.get("bindings") or []
    extra_bindings = []
    if bindings:
        from ..delivery import resources as resource_types
        if not isinstance(bindings, list):
            raise ValueError("bindings 必须是列表")
        for binding in bindings:
            resource_id = binding.get("resource_id")
            alias = binding.get("alias", "")
            resource = ctx.store.get_resource(resource_id)
            if not resource:
                raise NotFound(f"资源 #{resource_id} 不存在")
            from ..delivery.resolve import _build_resource_chain
            chain = _build_resource_chain(ctx.store, resource_id)
            chain_data = [{"resource_id": r["id"], "name": r["name"], "type": r["type"], "version": r.get("version") or 0}
                         for r in chain[1:]]
            extra_bindings.append({
                "resource_id": resource_id,
                "alias": alias,
                "name": resource["name"],
                "type": resource["type"],
                "version": resource.get("version") or 0,
                "chain": chain_data,
            })
    return await _render_preview(ctx, draft, spec, dry_run=_flag(data, "dry_run"),
                                extra_sets=_draft_sets(ctx, data.get("config")), extra_bindings=extra_bindings)


def manifest(ctx, me: Principal, service_id: int, deployment_id: Optional[int] = None) -> Dict[str, Any]:
    """发布快照：某次发布（默认最近一次成功发布）实际下发的对象（敏感值已隐藏）与所用配置版本。"""
    service_or_404(ctx.store, me, service_id)
    if deployment_id is None:
        item = ctx.store.last_success_manifest(service_id)
    else:
        record = ctx.store.get_deployment(deployment_id)
        item = ctx.store.get_deployment_manifest(deployment_id) if record and record["service_id"] == service_id else None
    if not item:
        raise NotFound("没有发布快照")
    return {"deployment_id": item["deployment_id"], "checksum": item["checksum"], "created_at": item["created_at"],
            "config": item["inputs"].get("config") or [], "resources": item["inputs"].get("resources") or [],
            "profile_version": item["inputs"].get("profile_version"),
            "ingress_version": item["inputs"].get("ingress_version"), "yaml": to_yaml(item["objects"]),
            "objects": [{"key": object_key(o), "kind": o["kind"], "name": o["metadata"]["name"]} for o in item["objects"]]}


async def live_image(ctx, service: Dict[str, Any]) -> Dict[str, str]:
    """接管发布使用的镜像：Deployment 中目标容器当前的镜像地址原样沿用（通常是 仓库:版本号），接管不改变镜像。
    同时记录 Pod 正在运行的 digest（Deployment 已按 digest 引用时取引用中的 digest），镜像必须属于服务对应应用的镜像仓库。"""
    if mode_of(service) != "managed":
        raise Unprocessable("只有托管服务需要接管发布")
    namespace = ctx.store.get_namespace(service["namespace_id"])
    name, container = service_target(ctx.store, service)
    cluster, credential = load_cluster(ctx.store, ctx.box, namespace["cluster_id"])
    async with KubeClient(cluster["server"], credential, ctx.deploy_transport) as kube:
        live = await kube.get_json(deployment_path(namespace["name"], name))
        if not live:
            raise Unprocessable(f"集群中没有 Deployment {name}，不需要接管；请直接选择一个构建发布")
        containers = ((live.get("spec") or {}).get("template") or {}).get("spec", {}).get("containers") or []
        image = next((c.get("image", "") for c in containers if c.get("name") == container), "")
        if not image:
            raise Unprocessable(f"Deployment {name} 中没有容器 {container}")
        digests = set()
        if "@sha256:" in image:
            digests.add("sha256:" + image.rsplit("@sha256:", 1)[1])
        else:
            selector = (live.get("spec") or {}).get("selector") or {}
            pods = await kube.list_resources(f"/api/v1/namespaces/{quote(namespace['name'], safe='')}/pods",
                                             {"labelSelector": selector_string(selector)})
            for pod in pods:
                for status in (pod.get("status") or {}).get("containerStatuses") or []:
                    image_id = status.get("imageID") or ""
                    if status.get("name") == container and "@sha256:" in image_id:
                        digests.add("sha256:" + image_id.rsplit("@sha256:", 1)[1])
    if len(digests) != 1:
        raise Unprocessable("无法确定线上镜像的唯一摘要（Pod 未运行，或不同 Pod 运行着不同的镜像）；请选择一个构建发布")
    ref = parse_image_ref(image)
    app = ctx.store.get_app(service["app_id"]) or {}
    try:
        expected = repo_path_of(ctx.settings, app)
    except (PlanError, KeyError):
        expected = ""
    if not ref or (expected and ref.repo != expected):
        raise Unprocessable(f"线上镜像 {image} 不属于应用 {app.get('name', '')} 的镜像仓库 {expected}")
    digest = digests.pop()
    return {"image": image, "digest": digest, "source": image}
