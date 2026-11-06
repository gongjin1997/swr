"""应用（一个应用对应 SWR 中的一个镜像仓库）的默认值与校验。"""
from __future__ import annotations

import posixpath
import re
from typing import Any, Dict

from .dockerfile import DEFAULT_FULL_TEMPLATE, DEFAULT_INCREMENTAL_TEMPLATE, PLACEHOLDERS, template_placeholders
from .versioning import is_valid_tag, validate_tag_template

_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
REPO_RE = re.compile(rf"^{_COMPONENT}(?:/{_COMPONENT})*$")
ORG_RE = re.compile(rf"^{_COMPONENT}$")
CHOWN_RE = re.compile(r"^[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)?$")
PLATFORM_RE = re.compile(r"^[a-z0-9]+/[a-z0-9_]+(?:/[a-z0-9]+)?$")
SAFE_NAME_RE = re.compile(r'^[^/\\"\x00-\x1f]+$')
FROM_RE = re.compile(r"^\s*FROM\s", re.IGNORECASE | re.MULTILINE)

MODES = ("incremental", "full")
TEMPLATE_KEYS = ("full_template", "incremental_template")

APP_DEFAULTS: Dict[str, Any] = {
    "name": "",
    "description": "",
    "organization": "",            # 为空时使用 config.yaml 里的默认组织
    "repository": "",              # SWR 中的镜像名
    "base_image": "",              # 全量构建 / 首次构建使用的基础镜像
    "default_mode": "incremental",
    "target_dir": "/app",
    "target_name": "",             # 只上传一个文件时重命名为该文件名
    "chown": "",
    "platform": "",                # 如 linux/arm64，构建机与目标架构不一致时填写
    "latest_strategy": "version",  # version：版本号最大；time：最近推送
    "tag_regex": "",
    "tag_template": "",
    "initial_tag": "1.0.0",
    "max_layers": 100,             # 上一版本层数达到该值时自动改为全量构建（Docker 上限 127 层）
    "push_latest": False,
    "full_template": DEFAULT_FULL_TEMPLATE,
    "incremental_template": DEFAULT_INCREMENTAL_TEMPLATE,
}


class AppValidationError(ValueError):
    pass


def _to_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def normalize_app(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise AppValidationError("请求格式错误")
    app: Dict[str, Any] = {}
    for key, default in APP_DEFAULTS.items():
        value = data.get(key, default)
        if value is None:
            value = default
        if isinstance(default, bool):
            value = _to_bool(value)
        elif isinstance(default, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise AppValidationError(f"{key} 必须是整数")
        elif key in TEMPLATE_KEYS:
            value = str(value).replace("\r\n", "\n")
        else:
            value = str(value).strip()
        app[key] = value

    if not app["name"]:
        raise AppValidationError("请填写应用名称")
    if len(app["name"]) > 64:
        raise AppValidationError("应用名称不能超过 64 个字符")
    if not app["repository"]:
        raise AppValidationError("请填写镜像名称")
    if not REPO_RE.match(app["repository"]):
        raise AppValidationError("镜像名称只能包含小写字母、数字和 . _ -，并且以字母或数字开头和结尾")
    if app["organization"] and not ORG_RE.match(app["organization"]):
        raise AppValidationError("组织名称只能包含小写字母、数字和 . _ -")
    if app["default_mode"] not in MODES:
        raise AppValidationError("默认构建方式只能是 incremental（累加）或 full（全量）")
    if app["latest_strategy"] not in ("version", "time"):
        raise AppValidationError("上一版本判定方式只能是 version 或 time")

    target_dir = app["target_dir"] or "/app"
    if not target_dir.startswith("/"):
        raise AppValidationError("目标目录必须是绝对路径，例如 /app")
    if ".." in target_dir.split("/") or not SAFE_NAME_RE.match(target_dir.replace("/", "x")):
        raise AppValidationError("目标目录包含不允许的字符")
    app["target_dir"] = posixpath.normpath(target_dir)
    if app["target_name"] and (not SAFE_NAME_RE.match(app["target_name"]) or app["target_name"] in (".", "..")):
        raise AppValidationError("目标文件名不能包含 / \\ 引号等特殊字符")
    if app["chown"] and not CHOWN_RE.match(app["chown"]):
        raise AppValidationError("文件属主格式应为 用户 或 用户:组，例如 nonroot:nonroot")
    if app["platform"] and not PLATFORM_RE.match(app["platform"]):
        raise AppValidationError("构建平台格式应为 os/arch，例如 linux/arm64")
    if app["base_image"] and re.search(r"\s", app["base_image"]):
        raise AppValidationError("基础镜像地址不能包含空格")
    if app["tag_regex"]:
        try:
            re.compile(app["tag_regex"])
        except re.error as exc:
            raise AppValidationError(f"标签过滤正则有误：{exc}")
    if app["tag_template"]:
        try:
            validate_tag_template(app["tag_template"])
        except ValueError as exc:
            raise AppValidationError(str(exc))
    app["initial_tag"] = app["initial_tag"] or "1.0.0"
    if not is_valid_tag(app["initial_tag"]):
        raise AppValidationError("首个版本号不合法")
    if not 0 <= app["max_layers"] <= 127:
        raise AppValidationError("累加层数上限需要在 0~127 之间（0 表示不限制）")

    for key in TEMPLATE_KEYS:
        if not app[key].strip():
            app[key] = APP_DEFAULTS[key]
        label = "全量构建模板" if key == "full_template" else "累加构建模板"
        unknown = sorted(set(template_placeholders(app[key])) - set(PLACEHOLDERS))
        if unknown:
            raise AppValidationError(f"{label}中有不支持的变量：" + "、".join("{{%s}}" % u for u in unknown))
        if not FROM_RE.search(app[key]):
            raise AppValidationError(f"{label}缺少 FROM 指令")
        if "COPY_FILES" not in template_placeholders(app[key]) and "files/" not in app[key]:
            raise AppValidationError(f"{label}需要包含 {{{{COPY_FILES}}}}，否则上传的文件不会进入镜像")
    if "BASE_IMAGE" not in template_placeholders(app["incremental_template"]):
        raise AppValidationError("累加构建模板必须以 FROM {{BASE_IMAGE}} 开始（基于上一版本）")
    if (app["default_mode"] == "full" and not app["base_image"]
            and "BASE_IMAGE" in template_placeholders(app["full_template"])):
        raise AppValidationError("默认全量构建时需要填写基础镜像")
    return app
