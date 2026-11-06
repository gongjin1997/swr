"""HTTP 接口与静态页面。"""
import asyncio
import functools
import hashlib
import logging
import os
from types import SimpleNamespace
from urllib.parse import quote
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.routing import Match

from . import __version__
from .apps import APP_DEFAULTS, MODES, AppValidationError, normalize_app
from .builder import FINAL_STATUSES, BuildManager
from .config import Settings, load_settings
from .dockerfile import extract_zip_safe, import_dockerfile, plan_files, render_dockerfile, safe_filename
from .planner import PlanError, find_latest, image_repo_of, repo_path_of, resolve_plan
from .registry import RegistryClient, RegistryError, normalize_arch, parse_image_ref
from .store import Store, utcnow
from .auth import AuthService, AuthError, SESSION_COOKIE, has_perm
from .authz import (has_project_permission, visible_project_ids, visible_namespace_ids,
                    has_global_permission)
from .security import SecretBox
from .sysconfig import SysConfig
from .events import EventBus
from .deploy.manager import DeployManager
from .routes import mount_routes
from .versioning import is_valid_tag, next_tag, sort_tags_desc

logger = logging.getLogger("swr_builder")
STATIC_DIR = Path(__file__).parent / "static"
CHUNK = 1024 * 1024
PUBLIC_ROUTE = "public"
AUTHENTICATED_ROUTE = "authenticated"

ROUTE_PERMISSIONS = {
    ("GET", "/api/auth/config"): PUBLIC_ROUTE,
    ("POST", "/api/auth/login"): PUBLIC_ROUTE,
    ("GET", "/api/auth/me"): AUTHENTICATED_ROUTE,
    ("POST", "/api/auth/logout"): AUTHENTICATED_ROUTE,
    ("PUT", "/api/account/profile"): AUTHENTICATED_ROUTE,
    ("PUT", "/api/account/password"): AUTHENTICATED_ROUTE,
    ("GET", "/api/account/sessions"): AUTHENTICATED_ROUTE,
    ("DELETE", "/api/account/sessions/{id}"): AUTHENTICATED_ROUTE,
    ("GET", "/api/account/tokens"): AUTHENTICATED_ROUTE,
    ("POST", "/api/account/tokens"): AUTHENTICATED_ROUTE,
    ("DELETE", "/api/account/tokens/{id}"): AUTHENTICATED_ROUTE,
    ("GET", "/api/roles"): "user.manage",
    ("GET", "/api/users"): "user.manage",
    ("POST", "/api/users"): "user.manage",
    ("PUT", "/api/users/{id}"): "user.manage",
    ("DELETE", "/api/users/{id}"): "user.manage",
    ("POST", "/api/users/{id}/reset-password"): "user.manage",
    ("GET", "/api/settings"): "settings.manage",
    ("PUT", "/api/settings"): "settings.manage",
    ("POST", "/api/settings/registry/test"): "settings.manage",
    ("GET", "/api/audit"): "audit.view",
    ("GET", "/api/system"): "settings.manage",
    ("GET", "/api/deploy/providers"): "deploy.view",
    ("GET", "/api/deploy/targets"): "deploy.view",
    ("POST", "/api/deploy/targets"): "deploy.manage",
    ("PUT", "/api/deploy/targets/{id}"): "deploy.manage",
    ("DELETE", "/api/deploy/targets/{id}"): "deploy.manage",
    ("POST", "/api/deploy/targets/{id}/test"): "deploy.manage",
    ("POST", "/api/deployments"): "deploy.run",
    ("GET", "/api/deployments"): AUTHENTICATED_ROUTE,
    ("GET", "/api/deployments/{id}"): AUTHENTICATED_ROUTE,
    ("GET", "/api/deployments/{id}/log"): AUTHENTICATED_ROUTE,
    ("POST", "/api/cloud/hcs/test"): "settings.manage",
    ("GET", "/api/cloud/hcs/capabilities"): "settings.manage",
    ("GET", "/api/projects"): "project.view",
    ("POST", "/api/projects"): "project.manage",
    ("GET", "/api/projects/{id}"): "project.view",
    ("PUT", "/api/projects/{id}"): "project.manage",
    ("DELETE", "/api/projects/{id}"): "project.manage",
    ("PUT", "/api/projects/{id}/namespaces/{namespace_id}"): "project.manage",
    ("DELETE", "/api/projects/{id}/namespaces/{namespace_id}"): "project.manage",
    ("GET", "/api/clusters"): "cluster.manage",
    ("POST", "/api/clusters"): "cluster.manage",
    ("GET", "/api/clusters/{id}"): "cluster.manage",
    ("PUT", "/api/clusters/{id}"): "cluster.manage",
    ("POST", "/api/clusters/{id}/test"): "cluster.manage",
    ("POST", "/api/clusters/{id}/rotate"): "cluster.manage",
    ("POST", "/api/clusters/{id}/revoke"): "cluster.manage",
    ("GET", "/api/namespaces"): "namespace.view",
    ("POST", "/api/namespaces"): "cluster.manage",
    ("GET", "/api/namespaces/{id}"): "namespace.view",
    ("PUT", "/api/namespaces/{id}"): "cluster.manage",
    ("POST", "/api/namespaces/{id}/review"): "cluster.manage",
    ("GET", "/api/namespaces/{id}/rbac-yaml"): "cluster.manage",
    ("GET", "/api/services"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services"): AUTHENTICATED_ROUTE,
    ("GET", "/api/services/{id}"): AUTHENTICATED_ROUTE,
    ("PUT", "/api/services/{id}"): AUTHENTICATED_ROUTE,
    ("DELETE", "/api/services/{id}"): AUTHENTICATED_ROUTE,
    ("GET", "/api/services/{id}/revisions"): AUTHENTICATED_ROUTE,
    ("GET", "/api/services/{id}/revisions/diff"): AUTHENTICATED_ROUTE,
    ("GET", "/api/services/{id}/revisions/{revision_id}"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services/{id}/render"): AUTHENTICATED_ROUTE,
    ("PUT", "/api/services/{id}/secrets/{key}"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services/{id}/deployments"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services/{id}/rollback"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services/{id}/restart"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services/{id}/scale"): AUTHENTICATED_ROUTE,
    ("POST", "/api/deployments/{id}/approve"): AUTHENTICATED_ROUTE,
    ("POST", "/api/deployments/{id}/reject"): AUTHENTICATED_ROUTE,
    ("GET", "/api/services"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services"): AUTHENTICATED_ROUTE,
    ("GET", "/api/services/{id}"): AUTHENTICATED_ROUTE,
    ("PUT", "/api/services/{id}"): AUTHENTICATED_ROUTE,
    ("DELETE", "/api/services/{id}"): AUTHENTICATED_ROUTE,
    ("GET", "/api/services/{id}/revisions"): AUTHENTICATED_ROUTE,
    ("GET", "/api/services/{id}/revisions/{revision_id}"): AUTHENTICATED_ROUTE,
    ("GET", "/api/services/{id}/revisions/diff"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services/{id}/render"): AUTHENTICATED_ROUTE,
    ("PUT", "/api/services/{id}/secrets/{key}"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services/{id}/deployments"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services/{id}/rollback"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services/{id}/restart"): AUTHENTICATED_ROUTE,
    ("POST", "/api/services/{id}/scale"): AUTHENTICATED_ROUTE,
    ("POST", "/api/deployments/{id}/approve"): AUTHENTICATED_ROUTE,
    ("POST", "/api/deployments/{id}/reject"): AUTHENTICATED_ROUTE,
    ("GET", "/api/bindings"): AUTHENTICATED_ROUTE,
    ("POST", "/api/bindings"): AUTHENTICATED_ROUTE,
    ("DELETE", "/api/bindings/{id}"): AUTHENTICATED_ROUTE,
    ("GET", "/api/bindable-users"): AUTHENTICATED_ROUTE,
    ("GET", "/api/info"): AUTHENTICATED_ROUTE,
    ("POST", "/api/registry/test"): "settings.manage",
    ("GET", "/api/app-defaults"): "app.view",
    ("GET", "/api/apps"): "app.view",
    ("POST", "/api/apps"): "app.manage",
    ("PUT", "/api/apps/{app_id}"): "app.manage",
    ("DELETE", "/api/apps/{app_id}"): "app.manage",
    ("POST", "/api/dockerfile/import"): "app.manage",
    ("GET", "/api/apps/{app_id}/versions"): "app.view",
    ("POST", "/api/apps/{app_id}/preview"): "app.view",
    ("POST", "/api/builds"): "build.run",
    ("GET", "/api/jobs"): "job.view",
    ("GET", "/api/jobs/{job_id}"): "job.view",
    ("GET", "/api/jobs/{job_id}/log"): "job.view",
    ("POST", "/api/jobs/{job_id}/cancel"): "job.cancel.own",
}


def route_permission(app: FastAPI, path: str, method: str) -> Optional[str]:
    scope = {"type": "http", "path": path, "root_path": "", "method": method}
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        match, _ = route.matches(scope)
        if match is Match.FULL:
            return ROUTE_PERMISSIONS.get((method, route.path))
    return None


def _mask_user(user: str) -> str:
    if "@" in user:
        prefix, ak = user.rsplit("@", 1)
        if len(ak) > 8:
            ak = ak[:4] + "****" + ak[-4:]
        return f"{prefix}@{ak}"
    return user


async def _save_upload(upload: UploadFile, dest: Path, limit: int) -> Tuple[int, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with open(dest, "wb") as fh:
        while True:
            chunk = await upload.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError(f"文件 {upload.filename} 超过大小限制 {limit // 1024 // 1024} MB")
            digest.update(chunk)
            fh.write(chunk)
    if size == 0:
        raise ValueError(f"文件 {upload.filename} 是空文件")
    return size, digest.hexdigest()


def _read_log(path: Path, offset: int, max_bytes: int = 256 * 1024) -> Tuple[str, int, int]:
    if not path.exists():
        return "", 0, 0
    size = path.stat().st_size
    offset = min(max(offset, 0), size)
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read(max_bytes)
    cut = data.rfind(b"\n")
    if cut >= 0:
        data = data[:cut + 1]
    elif len(data) < max_bytes:
        data = b""
    return data.decode("utf-8", "replace"), offset + len(data), size


def create_app(settings: Optional[Settings] = None, registry_transport: Any = None, deploy_transport: Any = None, hcs_transport: Any = None) -> FastAPI:
    settings = settings or load_settings()
    store = Store(settings.data_path / "builder.db")
    box = SecretBox.load(settings.data_path / 'secret.key', os.environ.get('BUILDER_SECRET_KEY',''))
    sysconfig = SysConfig(settings,store,box)
    sysconfig.load()
    registry = RegistryClient(settings.registry, transport=registry_transport)
    events = EventBus()
    manager = BuildManager(settings, store, registry, events)
    auth = AuthService(settings,store)
    deploy = DeployManager(settings,store,box,events,deploy_transport)
    ctx = SimpleNamespace(settings=settings,store=store,registry=registry,manager=manager,auth=auth,
        sysconfig=sysconfig,box=box,events=events,deploy=deploy,registry_transport=registry_transport,
        deploy_transport=deploy_transport,hcs_transport=hcs_transport)
    def on_settings_changed(paths):
        if any(p.startswith('registry.') for p in paths): registry.reconfigure(settings.registry)
        if 'build.max_concurrent' in paths: manager.resize(settings.build.max_concurrent)
        docker_cache['value'] = None
    sysconfig.on_change.append(on_settings_changed)
    docker_cache: Dict[str, Any] = {"at": 0.0, "value": None}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        auth.bootstrap()
        deploy.start()
        await manager.start()
        if settings.apps and not store.list_apps():
            for raw in settings.apps:
                try:
                    store.create_app(normalize_app(raw))
                    logger.info("已从配置文件导入应用 %s", raw.get("name"))
                except (AppValidationError, ValueError) as exc:
                    logger.warning("导入应用 %s 失败：%s", raw.get("name"), exc)
        store.ensure_default_project_registry_orgs(settings.registry.organization)
        yield
        await manager.stop()
        await events.drain()
        await deploy.stop()
        await events.drain()
        await registry.aclose()

    app = FastAPI(title="SWR 镜像构建平台", version=__version__, lifespan=lifespan)
    app.state.settings, app.state.store, app.state.manager = settings, store, manager

    app.state.ctx = ctx

    @app.exception_handler(AuthError)
    async def auth_error(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=exc.status)

    @app.exception_handler(ValueError)
    async def validation_error(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=400)

    @app.middleware('http')
    async def authentication(request: Request,call_next):
        path = request.url.path
        permission = route_permission(app, path, request.method) if path.startswith('/api/') else None
        public = path in ('/login','/healthz','/favicon.ico') or path.startswith('/static/') or permission == PUBLIC_ROUTE
        user = None
        request.state.session_hash = None
        if not public:
            header = request.headers.get('authorization','')
            if header.lower().startswith('bearer '): user = auth.resolve_api_token(header[7:].strip())
            else:
                resolved = auth.resolve_session(request.cookies.get(SESSION_COOKIE,''))
                if resolved: user,request.state.session_hash = resolved
            if not user:
                return JSONResponse({'detail':'请先登录'},status_code=401) if path.startswith('/api/') else RedirectResponse('/login?next='+quote(str(request.url.path)+'?'+str(request.url.query),safe=''),status_code=303)
            request.state.user = user
            if request.state.session_hash and request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('X-Requested-With')!='XMLHttpRequest':
                return JSONResponse({'detail':'缺少安全请求头，请从平台页面操作'},status_code=403)
            if user['must_change_password'] and path.startswith('/api/') and path not in ('/api/auth/me','/api/auth/logout','/api/account/password'):
                return JSONResponse({'detail':'首次登录或重置密码后，请先修改密码','code':'PASSWORD_CHANGE_REQUIRED'},status_code=403)
            if path.startswith('/api/'):
                if permission is None:
                    return JSONResponse({'detail':'API 路由未配置权限，已拒绝访问'},status_code=403)
                scoped_permissions = {'project.view','app.view','app.manage','build.run','job.view','job.cancel.own',
                                      'deploy.run','deploy.view','namespace.view'}
                if permission not in (PUBLIC_ROUTE, AUTHENTICATED_ROUTE, *scoped_permissions) and not has_perm(user['role'],permission) and not has_global_permission(store,user,permission):
                    return JSONResponse({'detail':'没有操作权限'},status_code=403)
                visible=visible_namespace_ids(store,user,permission) if permission=='namespace.view' else visible_project_ids(store,user,permission)
                global_scope = has_global_permission(store,user,permission)
                if permission in scoped_permissions and not visible and not global_scope:
                    return JSONResponse({'detail':'没有可访问的项目资源'},status_code=403)
            if not settings.general.deploy_enabled and path.startswith(('/api/deploy/','/api/deployments')):
                return JSONResponse({'detail':'部署模块已关闭'},status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if path.startswith('/api/') or path in ('/','/login'):
            response.headers['Cache-Control'] = 'no-store'
        if user and request.method not in ('GET','HEAD','OPTIONS') and response.status_code < 400 and not path.startswith(('/api/auth/','/api/account/','/api/users','/api/settings','/api/deployments')):
            store.add_audit(user['username'],request.method+' '+path,ip=request.client.host if request.client else '')
        return response

    mount_routes(app,ctx)

    def current_user(request: Request) -> str:
        user = request.state.user
        return user.get('display_name') or user['username']

    def app_or_404(app_id: int, request: Optional[Request] = None,
                   permission: str = "app.view") -> Dict[str, Any]:
        data = store.get_app(app_id)
        if not data:
            raise HTTPException(404, "应用不存在")
        if request is not None and not has_project_permission(store, request.state.user, data["project_id"], permission):
            raise HTTPException(404, "应用不存在")
        return data

    def job_or_404(job_id: int, request: Optional[Request] = None) -> Dict[str, Any]:
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(404, "构建任务不存在")
        if request is not None and not has_project_permission(store, request.state.user, job["project_id"], "job.view"):
            raise HTTPException(404, "构建任务不存在")
        return job

    def decorate(data: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(data)
        try:
            out["image_repo"] = image_repo_of(settings, data)
        except PlanError:
            out["image_repo"] = ""
        out["effective_organization"] = data.get("organization") or settings.registry.organization
        return out

    def validate_app_image_policy(data: Dict[str, Any], base_image: Optional[str] = None) -> None:
        project = store.get_project(data["project_id"])
        if not project:
            raise HTTPException(404, "项目不存在")
        allowed = project["registry_orgs"]

        def permitted(reference: str) -> bool:
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

        output_ref = f"{settings.registry.host}/{data.get('organization') or settings.registry.organization}/{data['repository']}:policy-check"
        if not permitted(output_ref):
            raise HTTPException(403, "项目未授权该 SWR 组织或镜像仓库前缀")
        configured_base = (base_image or data.get("base_image") or "").strip()
        if configured_base and not permitted(configured_base):
            raise HTTPException(403, "项目未授权该基础镜像来源")

        for template in (data.get("full_template", ""), data.get("incremental_template", "")):
            for line in template.splitlines():
                parts = line.strip().split()
                if not parts or parts[0].upper() != "FROM":
                    continue
                image_index = 1
                while image_index < len(parts) and parts[image_index].startswith("--"):
                    image_index += 1
                if image_index >= len(parts):
                    raise HTTPException(400, "Dockerfile FROM 指令缺少镜像")
                reference = parts[image_index]
                if "{{" in reference or reference.lower() == "scratch":
                    continue
                if not permitted(reference):
                    raise HTTPException(403, "项目未授权 Dockerfile 使用该基础镜像")

    def seed_default_project_policy(data: Dict[str, Any]) -> None:
        if data.get("project_id") != store.default_project_id():
            return
        project = store.get_project(data["project_id"])
        namespaces = {item["namespace"] for item in project["registry_orgs"]}
        output_namespace = data.get("organization") or settings.registry.organization
        if output_namespace and output_namespace not in namespaces:
            store.add_project_registry_org(data["project_id"], output_namespace, [])
            namespaces.add(output_namespace)
        if output_namespace:
            store.add_project_registry_org(data["project_id"], output_namespace, [data["repository"]])
        references = [data.get("base_image", "")]
        for template in (data.get("full_template", ""), data.get("incremental_template", "")):
            for line in template.splitlines():
                fields = line.strip().split()
                if fields and fields[0].upper() == "FROM":
                    index = 1
                    while index < len(fields) and fields[index].startswith("--"):
                        index += 1
                    if index < len(fields) and "{{" not in fields[index]:
                        references.append(fields[index])
        for reference in references:
            parsed = parse_image_ref(reference)
            if not parsed or parsed.host.lower() != settings.registry.host.lower():
                continue
            namespace, sep, repository = parsed.repo.partition("/")
            if sep and namespace and namespace not in namespaces:
                store.add_project_registry_org(data["project_id"], namespace, [repository])
                namespaces.add(namespace)

    async def docker_status() -> Dict[str, Any]:
        now = time.monotonic()
        if docker_cache["value"] and now - docker_cache["at"] < 30:
            return docker_cache["value"]
        value = {"available": False, "version": "", "arch": "", "error": ""}
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                settings.build.docker_bin, "version", "--format", "{{.Server.Version}} {{.Server.Arch}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=manager.docker_env)
            out, err = await asyncio.wait_for(proc.communicate(), 10)
            if proc.returncode == 0:
                parts = out.decode(errors="replace").split()
                value.update(available=True, version=parts[0] if parts else "",
                             arch=normalize_arch(parts[1]) if len(parts) > 1 else "")
            else:
                value["error"] = (err or out).decode(errors="replace").strip()[:300] or "docker 不可用"
        except FileNotFoundError:
            value["error"] = f"找不到 docker 命令：{settings.build.docker_bin}"
        except asyncio.TimeoutError:
            value["error"] = "docker version 超时，请检查 Docker 服务"
            if proc and proc.returncode is None:
                proc.kill()
        docker_cache.update(at=now, value=value)
        return value

    # ------------------------------------------------------------------ 基础信息
    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"ok": True}

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    @app.get("/api/info")
    async def info(request: Request):
        user, password = settings.registry.credentials()
        return {
            "version": __version__,
            "registry": {"address": settings.registry.host, "organization": settings.registry.organization,
                         "username": _mask_user(user), "configured": bool(settings.registry.host and password)},
            "docker": await docker_status(),
            "user": current_user(request),
            "max_upload_mb": settings.build.max_upload_mb,
        }

    @app.post("/api/registry/test")
    async def registry_test():
        try:
            await registry.ping()
        except RegistryError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "message": "连接成功"}

    # ------------------------------------------------------------------ 应用管理
    @app.get("/api/app-defaults")
    async def app_defaults():
        return APP_DEFAULTS

    @app.get("/api/apps")
    async def list_apps(request: Request):
        ids = visible_project_ids(store, request.state.user, "app.view")
        return [decorate(a) for a in store.list_apps(ids)]

    @app.post("/api/apps", status_code=201)
    async def create_app_api(request: Request, payload: Dict[str, Any] = Body(...)):
        default_project = "project_id" not in payload
        project_id = payload.get("project_id", store.default_project_id())
        if type(project_id) is not int or not has_project_permission(store, request.state.user, project_id, "app.manage"):
            raise HTTPException(403 if default_project else 404, "没有应用管理权限" if default_project else "项目不存在")
        try:
            data = normalize_app(payload)
        except AppValidationError as exc:
            raise HTTPException(400, str(exc))
        data["project_id"] = project_id
        seed_default_project_policy(data)
        validate_app_image_policy(data)
        data.pop("project_id", None)
        if store.find_app_by_name(data["name"]):
            raise HTTPException(400, "应用名称已存在")
        return decorate(store.create_app(data, project_id))

    @app.put("/api/apps/{app_id}")
    async def update_app_api(app_id: int, request: Request, payload: Dict[str, Any] = Body(...)):
        existing = app_or_404(app_id, request, "app.manage")
        try:
            data = normalize_app(payload)
        except AppValidationError as exc:
            raise HTTPException(400, str(exc))
        data["project_id"] = existing["project_id"]
        validate_app_image_policy(data)
        data.pop("project_id", None)
        same = store.find_app_by_name(data["name"])
        if same and same["id"] != app_id:
            raise HTTPException(400, "应用名称已存在")
        return decorate(store.update_app(app_id, data, existing["project_id"]))

    @app.delete("/api/apps/{app_id}")
    async def delete_app_api(app_id: int, request: Request):
        app_or_404(app_id, request, "app.manage")
        if not store.delete_app(app_id):
            raise HTTPException(404, "应用不存在")
        return {"ok": True}

    @app.post("/api/dockerfile/import")
    async def import_dockerfile_api(payload: Dict[str, Any] = Body(...)):
        text = str(payload.get("text") or "")
        if not text.strip():
            raise HTTPException(400, "请粘贴 Dockerfile 内容")
        return import_dockerfile(text)

    # ------------------------------------------------------------------ 版本与预览
    @app.get("/api/apps/{app_id}/versions")
    async def versions(app_id: int, request: Request):
        data = app_or_404(app_id, request)
        validate_app_image_policy(data)
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
            upcoming = next_tag(tags, latest, data.get("initial_tag") or "1.0.0", data.get("tag_template") or "")
        except PlanError as exc:
            raise HTTPException(400, str(exc))
        except RegistryError as exc:
            raise HTTPException(502, str(exc))
        ordered = sort_tags_desc(tags, data.get("tag_regex", ""), data.get("tag_template", ""))
        others = sorted(set(tags) - set(ordered))
        return {"image_repo": image_repo_of(settings, data), "total": len(tags), "tags": ordered + others,
                "latest": latest, "latest_info": latest_info, "next_tag": upcoming,
                "max_layers": data.get("max_layers"), "warning": warning}

    @app.post("/api/apps/{app_id}/preview")
    async def preview(app_id: int, request: Request, payload: Dict[str, Any] = Body(default={})):
        data = app_or_404(app_id, request)
        validate_app_image_policy(data)
        mode = payload.get("mode") or data["default_mode"]
        try:
            plan = await resolve_plan(registry, settings, data, mode, payload.get("base_tag"), payload.get("tag"))
            names = [str(n) for n in payload.get("filenames") or []]
            files = plan_files(data["target_dir"], data["target_name"], names, bool(payload.get("extract_zip"))) if names else []
            dockerfile = render_dockerfile(data, plan.mode, base_image=plan.base_image, new_tag=plan.new_tag,
                                           prev_tag=plan.latest or "", image=plan.image)
            validate_app_image_policy(data, plan.base_image)
        except (PlanError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        except RegistryError as exc:
            raise HTTPException(502, str(exc))
        return {"plan": plan.to_dict(), "files": files, "dockerfile": dockerfile}

    # ------------------------------------------------------------------ 构建
    @app.post("/api/builds", status_code=201)
    async def create_build(request: Request,
                           app_id: int = Form(...), mode: str = Form(""), tag: str = Form(""),
                           base_tag: str = Form(""), operator: str = Form(""), remark: str = Form(""),
                           extract_zip: str = Form("false"), files: List[UploadFile] = File(...)):
        data = app_or_404(app_id, request, "build.run")
        validate_app_image_policy(data)
        mode = mode or data["default_mode"]
        tag, base_tag = tag.strip(), base_tag.strip()
        if mode not in MODES:
            raise HTTPException(400, "构建方式只能是 incremental 或 full")
        if tag and not is_valid_tag(tag):
            raise HTTPException(400, "版本号只能包含字母、数字、. _ -，不能以 . 或 - 开头")
        if base_tag and not is_valid_tag(base_tag):
            raise HTTPException(400, "基础版本不合法")
        extract = extract_zip.strip().lower() in ("1", "true", "yes", "on")
        try:
            entries = plan_files(data["target_dir"], data["target_name"], [f.filename or "" for f in files], extract)
            image_repo = image_repo_of(settings, data)
        except (ValueError, PlanError) as exc:
            raise HTTPException(400, str(exc))

        job_id = store.create_job(
            app_id=app_id, app_name=data["name"], repository=image_repo, requested_mode=mode, mode=mode,
            requested_tag=tag or None, requested_base_tag=base_tag or None, status="queued", step="queued",
            operator=current_user(request)[:80], created_by=request.state.user["id"], remark=remark.strip()[:200],
            project_id=data["project_id"], app_snapshot=data, created_at=utcnow())
        context = manager.context_dir(job_id)
        files_dir = context / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        limit = settings.build.max_upload_mb * 1024 * 1024
        saved: List[Dict[str, Any]] = []
        loop = asyncio.get_running_loop()
        try:
            for index, (upload, entry) in enumerate(zip(files, entries)):
                if entry["extract"]:
                    archive = context / f"upload-{index}.zip"
                    size, sha = await _save_upload(upload, archive, limit)
                    names = await loop.run_in_executor(
                        None, functools.partial(extract_zip_safe, archive, files_dir, limit * 4))
                    archive.unlink()
                    saved.append({**entry, "size": size, "sha256": sha, "extracted": len(names), "entries": names[:100]})
                else:
                    size, sha = await _save_upload(upload, files_dir / entry["path"], limit)
                    saved.append({**entry, "size": size, "sha256": sha})
        except ValueError as exc:
            shutil.rmtree(manager.job_dir(job_id), ignore_errors=True)
            store.delete_job(job_id)
            raise HTTPException(400, str(exc))
        finally:
            for upload in files:
                await upload.close()
        store.update_job(job_id, files=saved)
        manager.submit(job_id)
        return {"id": job_id}

    @app.get("/api/jobs")
    async def list_jobs(request: Request, app_id: Optional[int] = None, status: Optional[str] = None,
                        limit: int = 30, offset: int = 0):
        projects = visible_project_ids(store, request.state.user, "job.view")
        if app_id is not None:
            app = app_or_404(app_id, request)
            projects = [app["project_id"]] if app["project_id"] in projects else []
        items, total = store.list_jobs(app_id, status or None, min(max(limit, 1), 100), max(offset, 0), projects)
        return {"items": items, "total": total}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: int, request: Request):
        return job_or_404(job_id, request)

    @app.get("/api/jobs/{job_id}/log")
    async def job_log(job_id: int, request: Request, offset: int = 0):
        job = job_or_404(job_id, request)
        final = job["status"] in FINAL_STATUSES
        text, new_offset, size = _read_log(manager.log_path(job_id), offset)
        return {"text": text, "offset": new_offset, "status": job["status"], "step": job["step"],
                "finished": final and new_offset >= size}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: int,request: Request):
        job = job_or_404(job_id, request)
        user = request.state.user
        project_admin = has_project_permission(store,user,job['project_id'],'job.cancel')
        own_job = has_project_permission(store,user,job['project_id'],'job.cancel.own') and str(job.get('created_by'))==str(user['id'])
        if not project_admin and not own_job:
            raise HTTPException(403,'只能取消自己提交的构建')
        if not await manager.cancel(job_id):
            raise HTTPException(400, "任务已经结束，无法取消")
        return {"ok": True}

    # ------------------------------------------------------------------ 页面
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/login", include_in_schema=False)
    async def login_page():
        return FileResponse(STATIC_DIR / "login.html")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app
