"""SWR 镜像仓库客户端。

SWR 兼容 Docker Registry HTTP API V2。这里直接使用 docker login 的用户名/密钥，
走标准 Token 鉴权流程（401 → WWW-Authenticate → 换取 Bearer Token），
不需要额外申请 IAM Token；同样适用于 Harbor、registry:2 等仓库。
"""
from __future__ import annotations
from copy import deepcopy

import asyncio
import base64
import json
import re
import ssl
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import RegistryConfig
from .versioning import parse_created

MANIFEST_TYPES = (
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
)
_CHALLENGE_PARAM = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?')


class RegistryError(Exception):
    """访问镜像仓库失败（消息会直接展示给用户）。"""


@dataclass
class ImageRef:
    host: str
    repo: str
    reference: str


def parse_image_ref(ref: str) -> Optional[ImageRef]:
    """swr.xxx/metaerp_sdk/java:8-huawei-r30-arm64 → (swr.xxx, metaerp_sdk/java, 8-huawei-r30-arm64)"""
    ref = (ref or "").strip()
    if not ref:
        return None
    name, digest = ref, None
    if "@" in ref:
        name, digest = ref.split("@", 1)
    tag = None
    if name.rfind(":") > name.rfind("/"):
        name, tag = name.rsplit(":", 1)
    first, sep, rest = name.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        host, repo = first, rest
    else:
        host, repo = "docker.io", name if "/" in name else f"library/{name}"
    return ImageRef(host=host, repo=repo, reference=digest or tag or "latest")


def normalize_arch(arch: Optional[str]) -> Optional[str]:
    if not arch:
        return None
    value = arch.strip().lower()
    return {"aarch64": "arm64", "x86_64": "amd64", "x86-64": "amd64"}.get(value, value)


def _error_codes(resp: httpx.Response) -> List[str]:
    try:
        data = resp.json()
    except ValueError:
        return []
    errors = data.get("errors") if isinstance(data, dict) else None
    return [str(e.get("code", "")) for e in errors or [] if isinstance(e, dict)]


def _pick_platform(entries: List[Dict[str, Any]], platform: str) -> Optional[Dict[str, Any]]:
    valid = [e for e in entries
             if (e.get("platform") or {}).get("architecture") not in (None, "", "unknown")]
    if platform:
        parts = platform.split("/")
        want_os = parts[0]
        want_arch = normalize_arch(parts[1]) if len(parts) > 1 else None
        want_variant = parts[2] if len(parts) > 2 else None
        for entry in valid:
            p = entry.get("platform") or {}
            if p.get("os") != want_os or normalize_arch(p.get("architecture")) != want_arch:
                continue
            if want_variant and p.get("variant") and p.get("variant") != want_variant:
                continue
            return entry
    if valid:
        return valid[0]
    return entries[0] if entries else None


class RegistryClient:
    def __init__(self, cfg: RegistryConfig, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._transport = transport
        self._retired: List[httpx.AsyncClient] = []
        self._setup(cfg)

    def _setup(self, cfg: RegistryConfig) -> None:
        cfg = deepcopy(cfg)
        username, password = cfg.credentials()
        verify: Any = True
        if cfg.insecure:
            verify = False
        elif cfg.ca_file:
            try:
                verify = ssl.create_default_context(cafile=cfg.ca_file)
            except (OSError, ssl.SSLError) as exc:
                raise RegistryError(f"无法加载 CA 证书 {cfg.ca_file}：{exc}") from exc
        kwargs: Dict[str, Any] = {"timeout": cfg.timeout, "follow_redirects": True, "verify": verify}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        client = httpx.AsyncClient(**kwargs)
        self.cfg, self.username, self.password, self._client = cfg, username, password, client
        self._tokens: Dict[str, Tuple[str, float]] = {}
        self._basic = False

    def reconfigure(self, cfg: RegistryConfig) -> None:
        """系统设置修改后切换到新的仓库配置；旧连接在退出时关闭（可能还有请求在使用）。"""
        old = self._client
        self._setup(cfg)
        self._retired.append(old)

    async def aclose(self) -> None:
        for client in [self._client, *self._retired]:
            await client.aclose()
        self._retired.clear()

    # ------------------------------------------------------------------ 鉴权
    def _basic_header(self) -> str:
        raw = f"{self.username}:{self.password}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _cached_auth(self, scope: str) -> Optional[str]:
        if self._basic:
            return self._basic_header()
        item = self._tokens.get(scope)
        if item and item[1] > time.time():
            return item[0]
        return None

    async def _authorize(self, challenge: str, scope: str) -> str:
        scheme = challenge.split(" ", 1)[0].strip().lower()
        if scheme == "basic":
            if not self.username:
                raise RegistryError("镜像仓库要求登录，但没有配置用户名/密钥")
            self._basic = True
            return self._basic_header()
        if scheme != "bearer":
            raise RegistryError(f"不支持的仓库鉴权方式：{challenge or '（空）'}")
        params = dict(_CHALLENGE_PARAM.findall(challenge))
        realm = params.get("realm")
        if not realm:
            raise RegistryError("仓库返回的鉴权信息缺少 realm")
        query: Dict[str, str] = {}
        if params.get("service"):
            query["service"] = params["service"]
        token_scope = params.get("scope") or scope
        if token_scope:
            query["scope"] = token_scope
        if self.username:
            query["account"] = self.username
        auth = (self.username, self.password) if self.username else None
        try:
            resp = await self._client.get(realm, params=query, auth=auth)
        except httpx.HTTPError as exc:
            raise RegistryError(f"连接仓库鉴权服务失败：{exc}") from exc
        if resp.status_code in (401, 403):
            raise RegistryError("SWR 鉴权失败：用户名或密钥不正确（需要 docker login 用的长期有效登录密钥）")
        if resp.status_code >= 400:
            raise RegistryError(f"获取仓库 Token 失败：HTTP {resp.status_code} {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise RegistryError("仓库鉴权服务返回了无法解析的内容") from exc
        token = data.get("token") or data.get("access_token")
        if not token:
            raise RegistryError("仓库鉴权服务没有返回 token")
        try:
            expires = int(data.get("expires_in") or 60)
        except (TypeError, ValueError):
            expires = 60
        value = f"Bearer {token}"
        self._tokens[scope] = (value, time.time() + max(expires - 10, 10))
        return value

    async def _request(self, method: str, url: str, scope: str = "",
                       headers: Optional[Dict[str, str]] = None,
                       params: Optional[Dict[str, str]] = None) -> httpx.Response:
        if url.startswith("/"):
            url = self.cfg.base_url + url
        hdrs = dict(headers or {})
        cached = self._cached_auth(scope)
        if cached:
            hdrs["Authorization"] = cached
        try:
            resp = await self._client.request(method, url, headers=hdrs, params=params)
            if resp.status_code == 401:
                hdrs["Authorization"] = await self._authorize(resp.headers.get("www-authenticate", ""), scope)
                resp = await self._client.request(method, url, headers=hdrs, params=params)
        except httpx.HTTPError as exc:
            raise RegistryError(f"无法连接镜像仓库 {self.cfg.host}：{exc}") from exc
        return resp

    # ------------------------------------------------------------------ API
    async def ping(self) -> Dict[str, Any]:
        if not self.cfg.host:
            raise RegistryError("没有配置镜像仓库地址（registry.address）")
        resp = await self._request("GET", "/v2/")
        if resp.status_code == 200:
            return {"ok": True}
        if resp.status_code in (401, 403):
            raise RegistryError("SWR 鉴权失败：用户名或密钥不正确")
        raise RegistryError(f"镜像仓库返回异常：HTTP {resp.status_code}")

    async def list_tags(self, repo: str) -> List[str]:
        """列出镜像仓库的全部标签；仓库不存在时返回空列表（首次构建）。"""
        scope = f"repository:{repo}:pull"
        url = f"/v2/{repo}/tags/list"
        params: Optional[Dict[str, str]] = {"n": "1000"}
        tags: List[str] = []
        seen = set()
        for page in range(500):
            resp = await self._request("GET", url, scope, params=params)
            if resp.status_code == 400 and params:
                params = None  # 个别仓库不支持 n 参数，去掉后重试
                resp = await self._request("GET", url, scope)
            codes = _error_codes(resp) if resp.status_code >= 400 else []
            if resp.status_code == 404 or "NAME_UNKNOWN" in codes:
                if page == 0:
                    return []
                break
            if resp.status_code in (401, 403):
                raise RegistryError(f"没有镜像仓库 {repo} 的读取权限（或鉴权失败），请检查账号在该组织下的权限")
            if resp.status_code != 200:
                raise RegistryError(f"读取 {repo} 的标签失败：HTTP {resp.status_code} {resp.text[:200]}")
            for tag in resp.json().get("tags") or []:
                if tag not in seen:
                    seen.add(tag)
                    tags.append(tag)
            match = _LINK_NEXT.search(resp.headers.get("link", ""))
            if not match:
                break
            url = match.group(1)
            if not url.startswith(("http://", "https://", "/")):
                url = "/" + url
            params = None
        return tags

    async def get_manifest(self, repo: str, reference: str, platform: str = "") -> Tuple[Dict[str, Any], str, bool]:
        """返回 (镜像清单, digest, 是否多架构镜像)。多架构镜像会按 platform 选出对应的清单。"""
        scope = f"repository:{repo}:pull"
        resp = await self._request("GET", f"/v2/{repo}/manifests/{reference}", scope,
                                   headers={"Accept": ", ".join(MANIFEST_TYPES)})
        if resp.status_code == 404:
            raise RegistryError(f"镜像 {repo}:{reference} 不存在")
        if resp.status_code in (401, 403):
            raise RegistryError(f"没有镜像 {repo} 的读取权限")
        if resp.status_code != 200:
            raise RegistryError(f"读取镜像清单失败：HTTP {resp.status_code}")
        try:
            manifest = json.loads(resp.content)
        except ValueError as exc:
            raise RegistryError("镜像清单无法解析") from exc
        digest = resp.headers.get("docker-content-digest", "")
        if isinstance(manifest.get("manifests"), list):
            chosen = _pick_platform(manifest["manifests"], platform)
            if not chosen or not chosen.get("digest"):
                raise RegistryError(f"镜像 {repo}:{reference} 中没有可用的架构")
            sub, sub_digest, _ = await self.get_manifest(repo, chosen["digest"], platform)
            return sub, sub_digest, True
        return manifest, digest, False

    async def get_image_info(self, repo: str, reference: str, platform: str = "") -> Dict[str, Any]:
        """读取镜像的层数、压缩后大小、架构、创建时间。"""
        manifest, digest, multi_arch = await self.get_manifest(repo, reference, platform)
        info: Dict[str, Any] = {"tag": reference, "digest": digest, "layers": None, "size": None,
                                "created": None, "architecture": None, "os": None, "multi_arch": multi_arch}
        if manifest.get("schemaVersion") == 1:
            info["layers"] = len(manifest.get("fsLayers") or [])
            info["architecture"] = normalize_arch(manifest.get("architecture"))
            return info
        layers = manifest.get("layers") or []
        info["layers"] = len(layers)
        info["size"] = sum(int(layer.get("size") or 0) for layer in layers)
        config_digest = (manifest.get("config") or {}).get("digest")
        if config_digest:
            resp = await self._request("GET", f"/v2/{repo}/blobs/{config_digest}", f"repository:{repo}:pull")
            if resp.status_code == 200:
                try:
                    cfg = json.loads(resp.content)
                except ValueError:
                    cfg = {}
                info["created"] = cfg.get("created")
                info["architecture"] = normalize_arch(cfg.get("architecture"))
                info["os"] = cfg.get("os")
                labels = (cfg.get("config") or {}).get("Labels") or {}
                info["labels"] = {k: v for k, v in labels.items() if k.startswith("swr-builder.")}
        return info

    async def latest_by_time(self, repo: str, tags: List[str], platform: str = "",
                             concurrency: int = 8) -> Optional[str]:
        """按镜像创建时间找出最近推送的标签（每个标签需要两次请求，标签很多时会慢一些）。"""
        semaphore = asyncio.Semaphore(concurrency)

        async def created(tag: str) -> Optional[float]:
            async with semaphore:
                try:
                    info = await self.get_image_info(repo, tag, platform)
                except RegistryError:
                    return None
                return parse_created(info.get("created"))

        stamps = await asyncio.gather(*(created(t) for t in tags))
        best: Optional[Tuple[float, str]] = None
        for tag, stamp in zip(tags, stamps):
            if stamp is not None and (best is None or stamp > best[0]):
                best = (stamp, tag)
        return best[1] if best else None
