"""纳管集群（SA Token 认证）的 Kubernetes API 客户端。集群管理接口与服务发布 worker 共用。"""
from __future__ import annotations

import ssl
from typing import Any, Dict, Optional
from urllib.parse import quote, urlsplit

import httpx

from ..errors import DomainError, Unavailable


def validate_api_server(server: str) -> None:
    """API Server 必须是不带凭据、查询串和片段的 HTTPS 地址。"""
    parsed = urlsplit(server)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise DomainError("API Server 必须是无凭据的 HTTPS 地址")


def deployment_path(namespace: str, name: str) -> str:
    return "/apis/apps/v1/namespaces/" + quote(namespace, safe="") + "/deployments/" + quote(name, safe="")


class KubeClient:
    """用法：`async with KubeClient(server, credential, transport) as kube: ...`。"""

    def __init__(self, server: str, credential: Dict[str, Any], transport: Any = None, timeout: float = 10):
        self.base = server.rstrip("/")
        self._token = credential["token"]
        self._verify = ssl.create_default_context(cadata=credential["ca_pem"]) if credential.get("ca_pem") else True
        self._transport = transport
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "KubeClient":
        self._client = httpx.AsyncClient(verify=self._verify, timeout=self._timeout, transport=self._transport,
                                         follow_redirects=False)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def auth_headers(self) -> Dict[str, str]:
        return {"Authorization": "Bearer " + self._token}

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        assert self._client is not None, "KubeClient 需要在 async with 中使用"
        headers = {**self.auth_headers, **kwargs.pop("headers", {})}
        return await self._client.request(method, self.base + path, headers=headers, **kwargs)

    async def version(self) -> str:
        """GET /version，返回 gitVersion。连接或响应异常抛 Unavailable。"""
        try:
            response = await self.request("GET", "/version")
        except httpx.HTTPError:
            raise Unavailable("连接集群失败，请检查网络、TLS 和凭据") from None
        if response.status_code != 200:
            raise Unavailable(f"Kubernetes API 返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise Unavailable("Kubernetes API 版本响应格式错误") from None
        version = payload.get("gitVersion") if isinstance(payload, dict) else None
        if not isinstance(version, str):
            raise Unavailable("Kubernetes API 版本响应格式错误")
        return version

    async def namespace_status(self, name: str) -> int:
        try:
            response = await self.request("GET", "/api/v1/namespaces/" + name)
        except httpx.HTTPError:
            raise Unavailable("读取目标 namespace 失败，请检查集群连接") from None
        return response.status_code

    async def access_review(self, namespace: str, group: str, resource: str, verb: str) -> Dict[str, Any]:
        """SelfSubjectAccessReview：当前凭据能否在 namespace 中对 resource 执行 verb。返回 status 对象。"""
        body = {"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview",
                "spec": {"resourceAttributes": {"namespace": namespace, "verb": verb, "group": group,
                                                "resource": resource}}}
        try:
            response = await self.request("POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
                                          headers={"Content-Type": "application/json"}, json=body)
        except httpx.HTTPError:
            raise Unavailable("集群权限自检连接失败") from None
        if response.status_code not in (200, 201):
            raise Unavailable(f"SSAR 请求失败，HTTP {response.status_code}")
        try:
            return response.json().get("status", {})
        except (ValueError, TypeError, AttributeError):
            raise Unavailable("SSAR 响应格式无效") from None
