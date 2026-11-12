"""应用：保存、项目镜像授权校验、版本信息与 Dockerfile 预览。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..access import Principal
from ..apps import AppValidationError, normalize_app, version_carry
from ..config import Settings
from ..dockerfile import plan_files, render_dockerfile
from ..errors import Conflict, DomainError, Forbidden, NotFound, UpstreamError
from ..images import dockerfile_from_images
from ..planner import PlanError, find_latest, image_repo_of, repo_path_of, resolve_plan
from ..registry import RegistryClient, RegistryError, parse_image_ref
from ..store import Store
from ..versioning import next_tag, sort_tags_desc


def decorate(settings: Settings, data: Dict[str, Any]) -> Dict[str, Any]:
    """补充前端展示用的镜像仓库地址与实际组织。"""
    out = dict(data)
    try:
        out["image_repo"] = image_repo_of(settings, data)
    except PlanError:
        out["image_repo"] = ""
    out["effective_organization"] = data.get("organization") or settings.registry.organization
    return out


def _templates(data: Dict[str, Any]) -> List[str]:
    return [data.get("full_template", ""), data.get("incremental_template", "")]


def _image_permitted(settings: Settings, allowed: List[Dict[str, Any]], reference: str) -> bool:
    parsed = parse_image_ref(reference)
    if not parsed or parsed.host.lower() != settings.registry.host.lower():
        return False
    namespace, sep, repository = parsed.repo.partition("/")
    if not sep:
        return False
    for item in allowed:
        if item["namespace"] != namespace:
            continue
        if not item["base_prefixes"]:
            return True
        for prefix in item["base_prefixes"]:
            if repository == prefix or repository.startswith(prefix.rstrip("/") + "/"):
                return True
    return False


def validate_image_policy(store: Store, settings: Settings, data: Dict[str, Any],
                          base_image: Optional[str] = None) -> None:
    """应用的推送目标、基础镜像和 Dockerfile 中的 FROM 镜像都必须在项目授权的 SWR 组织/仓库前缀内。"""
    project = store.get_project(data["project_id"])
    if not project:
        raise NotFound("项目不存在")
    allowed = project["registry_orgs"]
    organization = data.get("organization") or settings.registry.organization
    output_ref = f"{settings.registry.host}/{organization}/{data['repository']}:policy-check"
    if not _image_permitted(settings, allowed, output_ref):
        raise Forbidden("项目未授权该 SWR 组织或镜像仓库前缀")
    configured_base = (base_image or data.get("base_image") or "").strip()
    if configured_base and not _image_permitted(settings, allowed, configured_base):
        raise Forbidden("项目未授权该基础镜像来源")
    for reference in dockerfile_from_images(_templates(data)):
        if reference is None:
            raise DomainError("Dockerfile FROM 指令缺少镜像")
        if "{{" in reference or reference.lower() == "scratch":
            continue
        if not _image_permitted(settings, allowed, reference):
            raise Forbidden("项目未授权 Dockerfile 使用该基础镜像")


def seed_default_project_policy(store: Store, settings: Settings, data: Dict[str, Any]) -> None:
    """默认项目兼容 v1.1 的使用方式：新建应用时自动把其输出仓库和同仓库地址的基础镜像加入项目授权。"""
    if data.get("project_id") != store.default_project_id():
        return
    project_id = data["project_id"]
    project = store.get_project(project_id)
    namespaces = {item["namespace"] for item in project["registry_orgs"]}
    output_namespace = data.get("organization") or settings.registry.organization
    if output_namespace and output_namespace not in namespaces:
        store.add_project_registry_org(project_id, output_namespace, [])
        namespaces.add(output_namespace)
    if output_namespace:
        store.add_project_registry_org(project_id, output_namespace, [data["repository"]])
    references = [data.get("base_image", "")]
    references += [ref for ref in dockerfile_from_images(_templates(data)) if ref and "{{" not in ref]
    for reference in references:
        parsed = parse_image_ref(reference)
        if not parsed or parsed.host.lower() != settings.registry.host.lower():
            continue
        namespace, sep, repository = parsed.repo.partition("/")
        if sep and namespace and namespace not in namespaces:
            store.add_project_registry_org(project_id, namespace, [repository])
            namespaces.add(namespace)


def _normalize(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return normalize_app(payload)
    except AppValidationError as exc:
        raise DomainError(str(exc)) from None


def create_app(store: Store, settings: Settings, me: Principal, payload: Dict[str, Any]) -> Dict[str, Any]:
    default_project = "project_id" not in payload
    project_id = payload.get("project_id", store.default_project_id())
    if type(project_id) is not int or not me.can_project(project_id, "app.manage"):
        if default_project:
            raise Forbidden("没有应用管理权限")
        raise NotFound("项目不存在")
    data = _normalize(payload)
    data["project_id"] = project_id
    seed_default_project_policy(store, settings, data)
    validate_image_policy(store, settings, data)
    data.pop("project_id", None)
    if store.find_app_by_name(data["name"]):
        raise Conflict("应用名称已存在")
    return decorate(settings, store.create_app(data, project_id))


def list_apps(store: Store, settings: Settings, me: Principal, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """当前账号可见的应用；可按项目筛选。"""
    visible = me.project_ids("app.view")
    if project_id is not None:
        visible = [project_id] if me.can_project(project_id, "app.view") else []
    return [decorate(settings, a) for a in store.list_apps(visible)]


def delete_app(store: Store, me: Principal, app_id: int) -> Dict[str, Any]:
    """删除应用记录（不删除 SWR 中的镜像与历史构建记录）；仍有关联服务时不能删除。"""
    data = store.get_app(app_id)
    if not data or not me.can_project(data["project_id"], "app.manage"):
        raise NotFound("应用不存在")
    if store.app_has_services(app_id):
        raise Conflict("应用仍有关联服务，请先删除这些服务")
    store.delete_app(app_id)
    return data


def update_app(store: Store, settings: Settings, existing: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _normalize(payload)
    data["project_id"] = existing["project_id"]
    validate_image_policy(store, settings, data)
    data.pop("project_id", None)
    same = store.find_app_by_name(data["name"])
    if same and same["id"] != existing["id"]:
        raise Conflict("应用名称已存在")
    return decorate(settings, store.update_app(existing["id"], data, existing["project_id"]))


async def versions(store: Store, settings: Settings, registry: RegistryClient, data: Dict[str, Any]) -> Dict[str, Any]:
    """读取仓库中的版本，给出上一版本详情和预计的新版本号。"""
    validate_image_policy(store, settings, data)
    try:
        repo_path = repo_path_of(settings, data)
        tags = await registry.list_tags(repo_path)
        latest = await find_latest(registry, data, repo_path, tags)
        latest_info, warning = None, None
        if latest:
            try:
                latest_info = await registry.get_image_info(repo_path, latest, data.get("platform", ""))
            except RegistryError as exc:
                warning = f"读取上一版本详情失败：{exc}"
        upcoming = next_tag(tags, latest, data.get("initial_tag") or "1.0.0", data.get("tag_template") or "",
                            carry=version_carry(data))
    except PlanError as exc:
        raise DomainError(str(exc)) from None
    except RegistryError as exc:
        raise UpstreamError(str(exc)) from None
    ordered = sort_tags_desc(tags, data.get("tag_regex", ""), data.get("tag_template", ""))
    others = sorted(set(tags) - set(ordered))
    return {"image_repo": image_repo_of(settings, data), "total": len(tags), "tags": ordered + others,
            "latest": latest, "latest_info": latest_info, "next_tag": upcoming,
            "max_layers": data.get("max_layers"), "warning": warning}


async def preview(store: Store, settings: Settings, registry: RegistryClient, data: Dict[str, Any],
                  payload: Dict[str, Any]) -> Dict[str, Any]:
    """按与真实构建相同的规则（resolve_plan）生成构建计划和 Dockerfile。"""
    validate_image_policy(store, settings, data)
    mode = payload.get("mode") or data["default_mode"]
    try:
        plan = await resolve_plan(registry, settings, data, mode, payload.get("base_tag"), payload.get("tag"))
        names = [str(n) for n in payload.get("filenames") or []]
        files = plan_files(data["target_dir"], data["target_name"], names, bool(payload.get("extract_zip"))) if names else []
        dockerfile = render_dockerfile(data, plan.mode, base_image=plan.base_image, new_tag=plan.new_tag,
                                       prev_tag=plan.latest or "", image=plan.image)
        validate_image_policy(store, settings, data, plan.base_image)
    except (PlanError, ValueError) as exc:
        raise DomainError(str(exc)) from None
    except RegistryError as exc:
        raise UpstreamError(str(exc)) from None
    return {"plan": plan.to_dict(), "files": files, "dockerfile": dockerfile}
