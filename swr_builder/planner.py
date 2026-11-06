"""构建计划：确定上一版本、实际构建方式、基础镜像和新版本号。

“预览 Dockerfile”和真正的构建任务共用这一套逻辑，保证看到的就是将要执行的。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .config import Settings
from .registry import RegistryClient, RegistryError, normalize_arch, parse_image_ref
from .versioning import candidate_tags, is_valid_tag, latest_by_version, next_tag


class PlanError(Exception):
    """构建条件不满足（消息会直接展示给用户）。"""


@dataclass
class BuildPlan:
    repo_path: str                 # 组织/镜像名
    image_repo: str                # 仓库地址/组织/镜像名
    tags: List[str]
    latest: Optional[str]          # 仓库中的上一版本
    requested_mode: str
    mode: str                      # 实际构建方式（可能被自动切换）
    base_tag: Optional[str]        # 累加构建时基于的版本
    base_image: str
    base_info: Optional[Dict[str, Any]]
    new_tag: str
    expected_arch: Optional[str]
    notes: List[str] = field(default_factory=list)

    @property
    def image(self) -> str:
        return f"{self.image_repo}:{self.new_tag}"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("tags")
        data["tag_count"] = len(self.tags)
        data["image"] = self.image
        return data


def repo_path_of(settings: Settings, app: Dict[str, Any]) -> str:
    org = app.get("organization") or settings.registry.organization
    if not org:
        raise PlanError("没有配置组织：请在应用里或 config.yaml 的 registry.organization 中填写")
    return f"{org}/{app['repository']}"


def image_repo_of(settings: Settings, app: Dict[str, Any]) -> str:
    return f"{settings.registry.host}/{repo_path_of(settings, app)}"


async def find_latest(client: RegistryClient, app: Dict[str, Any], repo_path: str, tags: List[str]) -> Optional[str]:
    regex, template = app.get("tag_regex", ""), app.get("tag_template", "")
    if app.get("latest_strategy") == "time":
        return await client.latest_by_time(repo_path, candidate_tags(tags, regex, template), app.get("platform", ""))
    return latest_by_version(tags, regex, template)


async def _safe_info(client: RegistryClient, repo: str, ref: str, platform: str,
                     notes: List[str], what: str) -> Optional[Dict[str, Any]]:
    try:
        return await client.get_image_info(repo, ref, platform)
    except RegistryError as exc:
        notes.append(f"读取{what}信息失败（不影响构建）：{exc}")
        return None


async def resolve_plan(client: RegistryClient, settings: Settings, app: Dict[str, Any], mode: str,
                       base_tag: Optional[str] = None, new_tag: Optional[str] = None) -> BuildPlan:
    if mode not in ("incremental", "full"):
        raise PlanError("构建方式只能是 incremental（累加）或 full（全量）")
    repo_path = repo_path_of(settings, app)
    image_repo = f"{settings.registry.host}/{repo_path}"
    tags = await client.list_tags(repo_path)
    latest = await find_latest(client, app, repo_path, tags)
    notes: List[str] = []
    requested_mode = mode
    platform = app.get("platform") or ""
    base_tag = (base_tag or "").strip() or None
    if base_tag and base_tag not in tags:
        raise PlanError(f"基础版本 {base_tag} 在仓库中不存在")

    base_info: Optional[Dict[str, Any]] = None
    chosen = base_tag or latest
    if mode == "incremental":
        if not chosen:
            if not app.get("base_image"):
                raise PlanError("仓库里还没有任何版本，应用也没有配置基础镜像，无法进行首次构建")
            notes.append("仓库中还没有历史版本，本次自动改为全量构建（基于基础镜像）")
            mode = "full"
        else:
            base_info = await _safe_info(client, repo_path, chosen, platform, notes, "上一版本镜像")
            limit = int(app.get("max_layers") or 0)
            layers = (base_info or {}).get("layers")
            if limit and layers is not None and layers >= limit:
                if not app.get("base_image"):
                    raise PlanError(f"版本 {chosen} 已有 {layers} 层，达到累加上限 {limit}，"
                                    "请先为应用配置基础镜像，做一次全量构建")
                notes.append(f"版本 {chosen} 已有 {layers} 层，达到累加上限 {limit}，本次自动改为全量构建以重置层数")
                mode = "full"
                base_info = None

    if mode == "full":
        base_image = (app.get("base_image") or "").strip()
        if not base_image and "{{BASE_IMAGE}}" in (app.get("full_template") or ""):
            raise PlanError("应用没有配置基础镜像，无法全量构建")
        used_base_tag = None
        ref = parse_image_ref(base_image) if base_image else None
        if ref and ref.host == settings.registry.host:
            base_info = await _safe_info(client, ref.repo, ref.reference, platform, notes, "基础镜像")
    else:
        used_base_tag = chosen
        base_image = f"{image_repo}:{chosen}"

    requested = (new_tag or "").strip()
    if requested:
        if not is_valid_tag(requested):
            raise PlanError("版本号只能包含字母、数字、. _ -，不能以 . 或 - 开头，最长 128 位")
        if requested in tags:
            raise PlanError(f"版本 {requested} 已存在，请换一个版本号（平台不会覆盖已有版本）")
        final_tag = requested
    else:
        final_tag = next_tag(tags, latest, app.get("initial_tag") or "1.0.0", app.get("tag_template") or "")

    expected_arch: Optional[str] = None
    if platform:
        expected_arch = normalize_arch(platform.split("/")[1])
    elif base_info and not base_info.get("multi_arch"):
        expected_arch = normalize_arch(base_info.get("architecture"))

    return BuildPlan(repo_path=repo_path, image_repo=image_repo, tags=tags, latest=latest,
                     requested_mode=requested_mode, mode=mode, base_tag=used_base_tag, base_image=base_image,
                     base_info=base_info, new_tag=final_tag, expected_arch=expected_arch, notes=notes)
